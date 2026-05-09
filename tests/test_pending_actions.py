"""Tests for #57 — PendingAction tracking + outcome classification.

Layers:

1. ``PendingAction`` constructor + ``new()`` defaults
2. ``InMemoryPendingActionStore`` and ``SqlitePendingActionStore`` —
   record / get / pending_on / mark / prune_expired
3. ``KeywordOutcomeClassifier`` — parsing + match priority
4. ``PendingCoordinator`` — record/observe/tick/context_bundle, mood
   delta application, fast-vs-late acknowledgement timing,
   classifier soft-fail on exceptions
5. SQLite persistence survives restart (drops the SoulStore singleton)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


from clanker_soul import (
    EmotionalPhysics,
    InMemoryPendingActionStore,
    KeywordOutcomeClassifier,
    LLMOutcomeClassifier,
    OutcomeClassifier,
    PendingAction,
    PendingCoordinator,
    PendingDeltaConfig,
    SoulState,
    SoulStore,
    SqlitePendingActionStore,
)


def _now(offset: float = 0.0) -> datetime:
    return datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=offset)


# ---------------------------------------------------------------------------
# PendingAction
# ---------------------------------------------------------------------------


class TestPendingAction:
    def test_new_defaults(self):
        snap = {"v": 130, "w": 170}
        p = PendingAction.new(
            kind="direct_message",
            surface_key=("ch", "u"),
            body="hi",
            soul_snapshot=snap,
            expected_response="ack:hi,hello",
            fired_at=_now(0),
        )
        assert p.status == "pending"
        assert p.surface_key == ("ch", "u")
        assert p.body == "hi"
        assert p.soul_snapshot == snap
        assert (p.expires_at - p.fired_at).total_seconds() == 12 * 3600
        assert p.id  # UUID auto-generated

    def test_new_custom_ttl(self):
        p = PendingAction.new(
            kind="post_public",
            surface_key=("x",),
            body=None,
            soul_snapshot={},
            expected_response="ack:reply",
            ttl_seconds=600,
            fired_at=_now(0),
        )
        assert (p.expires_at - p.fired_at).total_seconds() == 600

    def test_new_explicit_id(self):
        p = PendingAction.new(
            kind="x",
            surface_key=("a",),
            body=None,
            soul_snapshot={},
            expected_response="ack:x",
            action_id="custom-id",
        )
        assert p.id == "custom-id"


# ---------------------------------------------------------------------------
# InMemoryPendingActionStore
# ---------------------------------------------------------------------------


class TestInMemoryStore:
    def _store(self):
        return InMemoryPendingActionStore()

    def _action(self, action_id="a", surface=("ch",), expires_at=None):
        return PendingAction.new(
            action_id=action_id,
            kind="direct_message",
            surface_key=surface,
            body="x",
            soul_snapshot={},
            expected_response="ack:hi",
            expires_at=expires_at,
            fired_at=_now(0),
        )

    def test_record_get_round_trip(self):
        s = self._store()
        a = self._action()
        s.record(a)
        got = s.get(a.id)
        assert got is not None
        assert got.id == a.id
        assert got.status == "pending"

    def test_pending_on_filters_by_surface(self):
        s = self._store()
        s.record(self._action("a", surface=("ch1",)))
        s.record(self._action("b", surface=("ch2",)))
        s.record(self._action("c", surface=("ch1",)))
        out = s.pending_on(("ch1",))
        assert {a.id for a in out} == {"a", "c"}

    def test_pending_on_excludes_resolved(self):
        s = self._store()
        s.record(self._action("a"))
        s.record(self._action("b"))
        s.mark("a", "acknowledged")
        out = s.pending_on(("ch",))
        assert {a.id for a in out} == {"b"}

    def test_mark_changes_status(self):
        s = self._store()
        a = self._action()
        s.record(a)
        s.mark(a.id, "ignored")
        assert s.get(a.id).status == "ignored"

    def test_mark_unknown_id_is_noop(self):
        s = self._store()
        s.mark("nonexistent", "ignored")  # must not raise

    def test_prune_expired_marks_and_returns(self):
        s = self._store()
        # one expired, one not
        old = self._action("old", expires_at=_now(-100))
        new = self._action("new", expires_at=_now(+1000))
        s.record(old)
        s.record(new)
        expired = s.prune_expired(_now(0))
        assert {a.id for a in expired} == {"old"}
        assert s.get("old").status == "expired"
        assert s.get("new").status == "pending"


# ---------------------------------------------------------------------------
# SqlitePendingActionStore
# ---------------------------------------------------------------------------


class TestSqliteStore:
    def _action(self, action_id="a", surface=("ch", "u"), expires_at=None):
        return PendingAction.new(
            action_id=action_id,
            kind="direct_message",
            surface_key=surface,
            body="hi",
            soul_snapshot={"v": 130, "w": 170},
            expected_response="ack:hi",
            expires_at=expires_at,
            fired_at=_now(0),
        )

    def test_round_trip_preserves_all_fields(self, tmp_path):
        store = SoulStore(tmp_path / "p.db")
        s = SqlitePendingActionStore(store)
        a = self._action()
        s.record(a)
        got = s.get(a.id)
        assert got is not None
        assert got.id == a.id
        assert got.surface_key == ("ch", "u")
        assert got.soul_snapshot == {"v": 130, "w": 170}
        assert got.body == "hi"
        assert got.expires_at == a.expires_at
        assert got.fired_at == a.fired_at

    def test_pending_on_filters_by_surface_and_status(self, tmp_path):
        store = SoulStore(tmp_path / "p2.db")
        s = SqlitePendingActionStore(store)
        s.record(self._action("a", surface=("c1",)))
        s.record(self._action("b", surface=("c2",)))
        s.record(self._action("c", surface=("c1",)))
        s.mark("c", "acknowledged")
        out = s.pending_on(("c1",))
        assert {x.id for x in out} == {"a"}

    def test_prune_expired(self, tmp_path):
        store = SoulStore(tmp_path / "p3.db")
        s = SqlitePendingActionStore(store)
        s.record(self._action("old", expires_at=_now(-50)))
        s.record(self._action("new", expires_at=_now(+50)))
        expired = s.prune_expired(_now(0))
        assert {a.id for a in expired} == {"old"}
        # Verify status persisted.
        assert s.get("old").status == "expired"
        assert s.get("new").status == "pending"

    def test_persists_across_reopen(self, tmp_path):
        db = tmp_path / "p4.db"
        store1 = SoulStore(db)
        s1 = SqlitePendingActionStore(store1)
        s1.record(self._action("a"))

        # Drop singleton; open fresh.
        SoulStore._instances.pop(str(db), None)
        store2 = SoulStore(db)
        s2 = SqlitePendingActionStore(store2)
        got = s2.get("a")
        assert got is not None
        assert got.id == "a"
        # And pending_on still finds it.
        assert len(s2.pending_on(("ch", "u"))) == 1


# ---------------------------------------------------------------------------
# LLMOutcomeClassifier — uses a stub callable so tests stay hermetic
# ---------------------------------------------------------------------------


class TestLLMClassifier:
    def _pending(self) -> PendingAction:
        return PendingAction.new(
            kind="direct_message",
            surface_key=("c",),
            body="Hey, you OK?",
            soul_snapshot={},
            expected_response="(LLM-classified, expected_response unused)",
            fired_at=_now(0),
        )

    def test_acknowledged(self):
        calls = []

        def stub(system: str, user: str) -> str:
            calls.append((system, user))
            return "acknowledged"

        clf = LLMOutcomeClassifier(call_model=stub)
        out = clf.classify(self._pending(), {"text": "I'm fine, thanks."})
        assert out == "acknowledged"
        # System prompt + user prompt both reached the model.
        assert len(calls) == 1
        assert "classifier" in calls[0][0].lower()
        assert "Hey, you OK?" in calls[0][1]
        # Last raw response captured.
        assert clf.last_raw_response == "acknowledged"

    def test_ignored(self):
        clf = LLMOutcomeClassifier(call_model=lambda s, u: "ignored")
        assert clf.classify(self._pending(), {"text": "anything"}) == "ignored"

    def test_mixed(self):
        clf = LLMOutcomeClassifier(call_model=lambda s, u: "mixed")
        assert clf.classify(self._pending(), {"text": "anything"}) == "mixed"

    def test_unrelated(self):
        clf = LLMOutcomeClassifier(call_model=lambda s, u: "unrelated")
        assert clf.classify(self._pending(), {"text": "anything"}) == "unrelated"

    def test_label_in_longer_response(self):
        # Some models prefix or pad. The classifier substring-matches.
        clf = LLMOutcomeClassifier(
            call_model=lambda s, u: "I'd say that's acknowledged.",
        )
        assert clf.classify(self._pending(), {"text": "x"}) == "acknowledged"

    def test_label_priority_acknowledged_beats_ignored(self):
        # If a confused model emits both labels, label_priority order
        # decides — acknowledged wins by default.
        clf = LLMOutcomeClassifier(
            call_model=lambda s, u: "acknowledged or ignored, not sure",
        )
        assert clf.classify(self._pending(), {"text": "x"}) == "acknowledged"

    def test_no_recognised_label_returns_unrelated(self):
        clf = LLMOutcomeClassifier(call_model=lambda s, u: "I don't know.")
        assert clf.classify(self._pending(), {"text": "x"}) == "unrelated"

    def test_empty_response_returns_unrelated(self):
        clf = LLMOutcomeClassifier(call_model=lambda s, u: "")
        assert clf.classify(self._pending(), {"text": "x"}) == "unrelated"

    def test_call_model_raises_returns_unrelated(self):
        def boom(s, u):
            raise RuntimeError("model down")

        clf = LLMOutcomeClassifier(call_model=boom)
        # Must not raise.
        assert clf.classify(self._pending(), {"text": "x"}) == "unrelated"
        # last_raw_response cleared on exception path.
        assert clf.last_raw_response is None

    def test_error_sentinel_string_returns_unrelated(self):
        clf = LLMOutcomeClassifier(
            call_model=lambda s, u: "[LLM-ERROR: Timeout: read timed out]",
        )
        assert clf.classify(self._pending(), {"text": "x"}) == "unrelated"

    def test_custom_system_prompt(self):
        captured = {}

        def stub(system: str, user: str) -> str:
            captured["system"] = system
            return "acknowledged"

        clf = LLMOutcomeClassifier(
            call_model=stub,
            system_prompt="custom: just decide ack or ignore",
        )
        clf.classify(self._pending(), {"text": "x"})
        assert captured["system"] == "custom: just decide ack or ignore"

    def test_pending_with_no_body(self):
        p = PendingAction.new(
            kind="withdraw",
            surface_key=("c",),
            body=None,
            soul_snapshot={},
            expected_response="(unused)",
        )
        captured = {}

        def stub(system: str, user: str) -> str:
            captured["user"] = user
            return "unrelated"

        clf = LLMOutcomeClassifier(call_model=stub)
        clf.classify(p, {"text": "anything"})
        # User prompt must include a body placeholder rather than crash.
        assert "(no body)" in captured["user"]


# ---------------------------------------------------------------------------
# KeywordOutcomeClassifier
# ---------------------------------------------------------------------------


class TestKeywordClassifier:
    def _pending(self, expected: str = "ack:hi,hello;ignore:cancel,no") -> PendingAction:
        return PendingAction.new(
            kind="direct_message",
            surface_key=("c",),
            body="hi",
            soul_snapshot={},
            expected_response=expected,
            fired_at=_now(0),
        )

    def test_acknowledged_match(self):
        clf = KeywordOutcomeClassifier()
        assert clf.classify(self._pending(), {"text": "Hi there!"}) == "acknowledged"

    def test_ignored_match(self):
        clf = KeywordOutcomeClassifier()
        assert clf.classify(self._pending(), {"text": "no thanks, cancel"}) == "ignored"

    def test_unrelated(self):
        clf = KeywordOutcomeClassifier()
        out = clf.classify(self._pending(), {"text": "the weather is nice"})
        assert out == "unrelated"

    def test_empty_text_unrelated(self):
        clf = KeywordOutcomeClassifier()
        assert clf.classify(self._pending(), {"text": ""}) == "unrelated"

    def test_no_text_unrelated(self):
        clf = KeywordOutcomeClassifier()
        assert clf.classify(self._pending(), {}) == "unrelated"

    def test_priority_ack_beats_ignore(self):
        clf = KeywordOutcomeClassifier()
        # Both keywords present; ack has priority.
        out = clf.classify(self._pending(), {"text": "hi cancel"})
        assert out == "acknowledged"

    def test_mixed_label(self):
        clf = KeywordOutcomeClassifier()
        p = self._pending(expected="ack:thanks;mixed:maybe,kinda")
        assert clf.classify(p, {"text": "I'm not sure, kinda"}) == "mixed"


# ---------------------------------------------------------------------------
# PendingCoordinator — full loop including mood deltas
# ---------------------------------------------------------------------------


def _physics() -> EmotionalPhysics:
    return EmotionalPhysics(soul=SoulState())


class _StubClassifier:
    """Test classifier with a configurable response per pending id."""

    def __init__(self, responses: dict[str, str]):
        self._responses = responses

    def classify(self, pending, observation):
        return self._responses.get(pending.id, "unrelated")


class TestCoordinator:
    def _coord(
        self, classifier: OutcomeClassifier | None = None, cfg: PendingDeltaConfig | None = None
    ):
        physics = _physics()
        store = InMemoryPendingActionStore()
        clf = classifier or KeywordOutcomeClassifier()
        return PendingCoordinator(
            physics=physics,
            store=store,
            classifier=clf,
            delta_config=cfg,
        )

    def test_record_persists_to_store(self):
        c = self._coord()
        p = PendingAction.new(
            kind="direct_message",
            surface_key=("ch",),
            body="hi",
            soul_snapshot={},
            expected_response="ack:hi",
        )
        c.record(p)
        assert c.store.get(p.id) is not None

    def test_delta_scale_default_is_ten(self):
        cfg = PendingDeltaConfig()
        assert cfg.delta_scale == 10.0

    def test_delta_scale_custom_value_amplifies_score(self):
        # acknowledged_fast default is V=+6. With delta_scale=10
        # Score.v = 128 + 6*10 = 188. With delta_scale=20, 128 + 6*20 = 248.
        cfg_default = PendingDeltaConfig()
        cfg_doubled = PendingDeltaConfig(delta_scale=20.0)
        c1 = self._coord(cfg=cfg_default)
        c2 = self._coord(cfg=cfg_doubled)
        for c, label in ((c1, "p1"), (c2, "p2")):
            p = PendingAction.new(
                action_id=label,
                kind="direct_message",
                surface_key=("ch",),
                body="hi",
                soul_snapshot={},
                expected_response="ack:hi",
                fired_at=_now(0),
            )
            c.record(p)
            results = c.observe(("ch",), {"text": "hi"}, now=_now(60))
            assert results[0].score is not None
        # The doubled-scale Score should land further from neutral.
        # We can't read the raw Score directly off the second coord
        # without re-fetching, so use the returned ResolutionResult.
        # Re-run with deterministic capture.
        c_default = self._coord(cfg=cfg_default)
        c_doubled = self._coord(cfg=cfg_doubled)
        for c in (c_default, c_doubled):
            p = PendingAction.new(
                kind="direct_message",
                surface_key=("ch",),
                body="hi",
                soul_snapshot={},
                expected_response="ack:hi",
                fired_at=_now(0),
            )
            c.record(p)
        r_default = c_default.observe(("ch",), {"text": "hi"}, now=_now(60))[0]
        r_doubled = c_doubled.observe(("ch",), {"text": "hi"}, now=_now(60))[0]
        # Both lift V; the doubled-scale Score is further from neutral.
        assert r_default.score is not None and r_doubled.score is not None
        assert r_default.score.v == 128 + 6 * 10  # 188
        assert r_doubled.score.v == 128 + 6 * 20  # 248

    def test_delta_scale_clamps_to_255(self):
        # An extreme delta_scale clamps cleanly to [0, 255] without crashing.
        cfg = PendingDeltaConfig(delta_scale=1000.0)
        c = self._coord(cfg=cfg)
        p = PendingAction.new(
            kind="direct_message",
            surface_key=("ch",),
            body="hi",
            soul_snapshot={},
            expected_response="ack:hi",
            fired_at=_now(0),
        )
        c.record(p)
        r = c.observe(("ch",), {"text": "hi"}, now=_now(60))[0]
        assert r.score is not None
        assert r.score.v == 255  # +6 * 1000 → way past 255, clamped

    def test_delta_scale_fractional_rounds_correctly(self):
        # Default Score field type is int — fractional scale must round
        # rather than throw a type error.
        cfg = PendingDeltaConfig(delta_scale=2.5)
        c = self._coord(cfg=cfg)
        p = PendingAction.new(
            kind="direct_message",
            surface_key=("ch",),
            body="hi",
            soul_snapshot={},
            expected_response="ack:hi",
            fired_at=_now(0),
        )
        c.record(p)
        r = c.observe(("ch",), {"text": "hi"}, now=_now(60))[0]
        # 6 * 2.5 = 15.0 → 128 + 15 = 143
        assert r.score is not None
        assert r.score.v == 128 + 15

    def test_observe_acknowledged_applies_fast_delta(self):
        c = self._coord()
        p = PendingAction.new(
            action_id="p1",
            kind="direct_message",
            surface_key=("ch",),
            body="hi",
            soul_snapshot={},
            expected_response="ack:hi",
            fired_at=_now(0),
        )
        c.record(p)
        results = c.observe(("ch",), {"text": "hi"}, now=_now(60))
        assert len(results) == 1
        r = results[0]
        assert r.outcome == "acknowledged"
        assert r.resolved_status == "acknowledged"
        assert r.score is not None
        # Fast: V should be > 128 (lifted)
        assert r.score.v > 128
        assert any("FAST" in p for p in r.score.patterns)
        # Store row updated.
        assert c.store.get("p1").status == "acknowledged"

    def test_observe_acknowledged_late_uses_smaller_delta(self):
        cfg = PendingDeltaConfig(fast_threshold_seconds=10)
        c = self._coord(cfg=cfg)
        p = PendingAction.new(
            action_id="p",
            kind="direct_message",
            surface_key=("ch",),
            body="hi",
            soul_snapshot={},
            expected_response="ack:hi",
            fired_at=_now(0),
        )
        c.record(p)
        # 5 minutes later — past the 10s threshold = late
        results = c.observe(("ch",), {"text": "hi"}, now=_now(300))
        r = results[0]
        assert any("LATE" in pat for pat in r.score.patterns)

    def test_observe_ignored_applies_negative_delta(self):
        clf = _StubClassifier({"p": "ignored"})
        c = self._coord(classifier=clf)
        p = PendingAction.new(
            action_id="p",
            kind="direct_message",
            surface_key=("ch",),
            body="hi",
            soul_snapshot={},
            expected_response="ack:hi",
        )
        c.record(p)
        results = c.observe(("ch",), {"text": "anything"})
        r = results[0]
        assert r.outcome == "ignored"
        assert r.resolved_status == "ignored"
        # Default ignored deltas dip V/W
        assert r.score.v < 128
        assert r.score.w < 128
        assert "PENDING_IGNORED" in r.score.patterns

    def test_observe_unrelated_no_delta_and_no_status_change(self):
        clf = _StubClassifier({"p": "unrelated"})
        c = self._coord(classifier=clf)
        p = PendingAction.new(
            action_id="p",
            kind="direct_message",
            surface_key=("ch",),
            body="hi",
            soul_snapshot={},
            expected_response="ack:hi",
        )
        c.record(p)
        results = c.observe(("ch",), {"text": "totally different"})
        assert len(results) == 1
        assert results[0].outcome == "unrelated"
        assert results[0].score is None
        # Status unchanged.
        assert c.store.get("p").status == "pending"

    def test_observe_classifier_exception_is_caught(self):
        class BoomClassifier:
            def classify(self, pending, observation):
                raise RuntimeError("classifier crashed")

        c = self._coord(classifier=BoomClassifier())
        p = PendingAction.new(
            action_id="p",
            kind="x",
            surface_key=("ch",),
            body="x",
            soul_snapshot={},
            expected_response="ack:x",
        )
        c.record(p)
        # Must not raise; outcome treated as unrelated.
        results = c.observe(("ch",), {"text": "x"})
        assert len(results) == 1
        assert results[0].outcome == "unrelated"
        assert results[0].score is None

    def test_tick_expires_and_applies_expired_delta(self):
        c = self._coord()
        old = PendingAction.new(
            action_id="old",
            kind="direct_message",
            surface_key=("ch",),
            body="hi",
            soul_snapshot={},
            expected_response="ack:hi",
            fired_at=_now(-1000),
            expires_at=_now(-100),
        )
        new = PendingAction.new(
            action_id="new",
            kind="direct_message",
            surface_key=("ch",),
            body="hi",
            soul_snapshot={},
            expected_response="ack:hi",
            expires_at=_now(+1000),
        )
        c.record(old)
        c.record(new)
        results = c.tick(now=_now(0))
        assert len(results) == 1
        r = results[0]
        assert r.pending.id == "old"
        assert r.resolved_status == "expired"
        assert r.score is not None
        assert "PENDING_EXPIRED" in r.score.patterns
        # New one is still pending.
        assert c.store.get("new").status == "pending"

    def test_context_bundle_empty(self):
        c = self._coord()
        bundle = c.context_bundle(("ch",), now=_now(0))
        assert bundle == {"pending_count": 0, "oldest_age_seconds": None, "kinds": []}

    def test_context_bundle_with_pendings(self):
        c = self._coord()
        c.record(
            PendingAction.new(
                action_id="a",
                kind="direct_message",
                surface_key=("ch",),
                body="x",
                soul_snapshot={},
                expected_response="ack:x",
                fired_at=_now(-300),
            )
        )
        c.record(
            PendingAction.new(
                action_id="b",
                kind="post_public",
                surface_key=("ch",),
                body="x",
                soul_snapshot={},
                expected_response="ack:x",
                fired_at=_now(-100),
            )
        )
        bundle = c.context_bundle(("ch",), now=_now(0))
        assert bundle["pending_count"] == 2
        assert bundle["oldest_age_seconds"] == 300
        assert bundle["kinds"] == ["direct_message", "post_public"]

    def test_observe_only_classifies_unresolved_pendings(self):
        c = self._coord(classifier=_StubClassifier({"a": "acknowledged", "b": "acknowledged"}))
        c.record(
            PendingAction.new(
                action_id="a",
                kind="x",
                surface_key=("ch",),
                body="x",
                soul_snapshot={},
                expected_response="ack:x",
                fired_at=_now(0),
            )
        )
        c.record(
            PendingAction.new(
                action_id="b",
                kind="x",
                surface_key=("ch",),
                body="x",
                soul_snapshot={},
                expected_response="ack:x",
                fired_at=_now(0),
            )
        )
        # Mark "a" as already resolved before observing.
        c.store.mark("a", "ignored")
        results = c.observe(("ch",), {"text": "yes"})
        # Only "b" should appear in results.
        assert {r.pending.id for r in results} == {"b"}


# ---------------------------------------------------------------------------
# SoulPlugin.build_pending_coordinator
# ---------------------------------------------------------------------------


class TestSoulPluginIntegration:
    def test_build_pending_coordinator_default_durable(self, tmp_path):
        from clanker_soul import SoulPlugin

        with SoulPlugin(agent_id="p1", db_path=tmp_path / "p.db") as plugin:
            coord = plugin.build_pending_coordinator(
                classifier=KeywordOutcomeClassifier(),
            )
            assert isinstance(coord.store, SqlitePendingActionStore)
            assert coord.delta_config is not None
            # Round-trip — ingestion goes into plugin.physics.
            p = PendingAction.new(
                kind="direct_message",
                surface_key=("ch",),
                body="hi",
                soul_snapshot=plugin.snapshot(),
                expected_response="ack:hi",
            )
            coord.record(p)
            results = coord.observe(("ch",), {"text": "hi"})
            assert results[0].outcome == "acknowledged"
            assert plugin.physics.mood is not None

    def test_build_pending_coordinator_in_memory(self, tmp_path):
        from clanker_soul import SoulPlugin

        with SoulPlugin(agent_id="p2", db_path=tmp_path / "p2.db") as plugin:
            coord = plugin.build_pending_coordinator(
                classifier=KeywordOutcomeClassifier(),
                durable=False,
            )
            assert isinstance(coord.store, InMemoryPendingActionStore)

    def test_build_pending_coordinator_custom_store(self, tmp_path):
        from clanker_soul import SoulPlugin

        custom = InMemoryPendingActionStore()
        with SoulPlugin(agent_id="p3", db_path=tmp_path / "p3.db") as plugin:
            coord = plugin.build_pending_coordinator(
                classifier=KeywordOutcomeClassifier(),
                store=custom,
            )
            assert coord.store is custom
