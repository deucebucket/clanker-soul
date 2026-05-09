"""Tests for M3.3 — SQLite persistence for prompt_corpus + face_recency,
``SoulPlugin(extra_corpus=, replace_corpus=)`` constructor args, and
``face_id`` stamping on the pulse log.

Layers covered:

1. ``CorpusStore`` — face round-trip, retire, replace_all, count.
2. ``PersistentRecencyLog`` — note_fired persists; reload preloads.
3. ``SoulPlugin`` — first-run seeds defaults, extras augment, replace
   wipes-and-replaces, second open preserves operator changes.
4. Cooldown semantics — survive a process restart simulated by closing
   the plugin and re-opening with a fresh instance.
5. ``pulse_log.face_id`` — engine fires, the SQL row carries the face id.
"""

from __future__ import annotations

import asyncio

import pytest

from clanker_soul import (
    ActionOutcome,
    CorpusStore,
    DEFAULT_FACES,
    PersistentRecencyLog,
    PromptFace,
    PulseAction,
    PulseConfig,
    PulseEngine,
    PulseTarget,
    SoulPlugin,
    SoulStore,
    VadugwiPredicate,
)


SOUL_DEFAULT = {
    "v": 145,
    "a": 110,
    "d": 160,
    "u": 80,
    "g": 130,
    "w": 175,
    "i": 135,
}


def _face(
    face_id: str,
    *,
    trigger: str = "distress",
    template: str = "x",
    motif: str = "informational",
    cooldown: int = 30,
) -> PromptFace:
    return PromptFace(
        id=face_id,
        trigger_kinds=frozenset({trigger}),
        cooldown_seconds=cooldown,
        motif=motif,
        template=template,
    )


# ---------------------------------------------------------------------------
# CorpusStore — face CRUD
# ---------------------------------------------------------------------------


class TestCorpusStore:
    def test_round_trip_preserves_all_fields(self, tmp_path):
        store = SoulStore(tmp_path / "rt.db")
        cs = CorpusStore(store)
        face = PromptFace(
            id="t.x",
            trigger_kinds=frozenset({"distress", "trauma_pressure"}),
            vadugwi_predicates=(
                VadugwiPredicate("V", "<=", 100, "mood"),
                VadugwiPredicate("W", ">=", 150, "soul"),
            ),
            situation_tags=frozenset({"autonomy_idle", "post_conversation"}),
            situation_match="all",
            memory_anchor="topic.test",
            cooldown_seconds=900,
            base_weight=1.5,
            motif="relational",
            template="hello {trigger}",
            branch_keys=frozenset({"core.distress.parent"}),
        )
        cs.save_face(face, source="default")

        loaded = cs.load_faces()
        assert len(loaded) == 1
        got = loaded[0]
        assert got.id == "t.x"
        assert got.trigger_kinds == frozenset({"distress", "trauma_pressure"})
        assert got.vadugwi_predicates == face.vadugwi_predicates
        assert got.situation_tags == face.situation_tags
        assert got.situation_match == "all"
        assert got.memory_anchor == "topic.test"
        assert got.cooldown_seconds == 900
        assert got.base_weight == pytest.approx(1.5)
        assert got.motif == "relational"
        assert got.template == "hello {trigger}"
        assert got.branch_keys == frozenset({"core.distress.parent"})

    def test_save_faces_bulk_inserts_each(self, tmp_path):
        store = SoulStore(tmp_path / "bulk.db")
        cs = CorpusStore(store)
        cs.save_faces([_face("a"), _face("b"), _face("c")], source="default")
        assert cs.count_faces() == 3
        ids = {f.id for f in cs.load_faces()}
        assert ids == {"a", "b", "c"}

    def test_retire_face_excludes_from_load(self, tmp_path):
        store = SoulStore(tmp_path / "rt.db")
        cs = CorpusStore(store)
        cs.save_faces([_face("keep"), _face("retire")], source="default")
        cs.retire_face("retire")
        ids = {f.id for f in cs.load_faces()}
        assert ids == {"keep"}
        # Retired row still in DB, just hidden from active reads.
        assert cs.count_faces() == 1
        assert cs.count_faces(include_retired=True) == 2

    def test_replace_all_clears_then_inserts(self, tmp_path):
        store = SoulStore(tmp_path / "rep.db")
        cs = CorpusStore(store)
        cs.save_faces([_face("a"), _face("b"), _face("c")], source="default")
        assert cs.count_faces() == 3
        cs.replace_all([_face("only_one")], source="host")
        ids = {f.id for f in cs.load_faces()}
        assert ids == {"only_one"}
        assert cs.count_faces(include_retired=True) == 1

    def test_corrupt_row_skipped_not_raised(self, tmp_path):
        store = SoulStore(tmp_path / "bad.db")
        cs = CorpusStore(store)
        cs.save_face(_face("good"))
        # Inject a malformed JSON in trigger_kinds field.
        with store.lock:
            store.connection.execute(
                "INSERT INTO prompt_corpus "
                "(id, trigger_kinds, vadugwi_predicates, situation_tags, "
                " situation_match, memory_anchor, cooldown_seconds, "
                " base_weight, motif, template, branch_keys, source, "
                " created_at, retired_at) "
                "VALUES ('bad', 'NOT JSON', '[]', '[]', 'any', NULL, "
                "        0, 1.0, 'informational', 'x', '[]', 'default', "
                "        0.0, NULL)",
            )
            store.connection.commit()
        # load_faces should warn-and-skip the bad row, return only "good".
        loaded = cs.load_faces()
        ids = {f.id for f in loaded}
        assert ids == {"good"}


# ---------------------------------------------------------------------------
# PersistentRecencyLog — preload + persist
# ---------------------------------------------------------------------------


class TestPersistentRecencyLog:
    def test_note_fired_persists(self, tmp_path):
        store = SoulStore(tmp_path / "rec.db")
        cs = CorpusStore(store)
        log = PersistentRecencyLog(cs, agent_id="a1")
        log.note_fired("face.x", now=100.0)
        # Row landed.
        rows = cs.load_recency("a1")
        assert "face.x" in rows
        assert rows["face.x"][0] == pytest.approx(100.0)
        assert rows["face.x"][1] == 1

    def test_preload_restores_last_fired(self, tmp_path):
        store = SoulStore(tmp_path / "preload.db")
        cs = CorpusStore(store)
        log1 = PersistentRecencyLog(cs, agent_id="a1")
        log1.note_fired("face.x", now=500.0)
        log1.note_fired("face.y", now=600.0)

        # Fresh instance — preload pulls both rows.
        log2 = PersistentRecencyLog(cs, agent_id="a1")
        assert log2.seconds_since("face.x", now=700.0) == pytest.approx(200.0)
        assert log2.seconds_since("face.y", now=700.0) == pytest.approx(100.0)

    def test_per_agent_isolation(self, tmp_path):
        store = SoulStore(tmp_path / "iso.db")
        cs = CorpusStore(store)
        log_a = PersistentRecencyLog(cs, agent_id="a")
        log_b = PersistentRecencyLog(cs, agent_id="b")
        log_a.note_fired("face.x", now=100.0)
        # B should not see A's fire.
        assert log_b.seconds_since("face.x", now=200.0) is None

    def test_fire_count_increments(self, tmp_path):
        store = SoulStore(tmp_path / "count.db")
        cs = CorpusStore(store)
        log = PersistentRecencyLog(cs, agent_id="a1")
        log.note_fired("face.x", now=10.0)
        log.note_fired("face.x", now=20.0)
        log.note_fired("face.x", now=30.0)
        rows = cs.load_recency("a1")
        # Each note_fired upserts +1; 3 in-memory + 3 SQL = 3 in SQL too
        # (in-memory count tracked via super().note_fired which increments).
        assert rows["face.x"][1] == 3
        assert rows["face.x"][0] == pytest.approx(30.0)


# ---------------------------------------------------------------------------
# SoulPlugin — extra_corpus / replace_corpus / first-run seeding
# ---------------------------------------------------------------------------


class TestPluginCorpusWiring:
    def test_first_run_seeds_default_corpus(self, tmp_path):
        # Use a unique path so SoulStore.get singleton doesn't cross
        # tests with leaked state.
        with SoulPlugin(agent_id="p1", db_path=tmp_path / "p1.db") as plugin:
            faces = plugin.corpus.faces
            assert len(faces) == len(DEFAULT_FACES)
            ids = {f.id for f in faces}
            expected = {f.id for f in DEFAULT_FACES}
            assert ids == expected

    def test_extra_corpus_augments_default(self, tmp_path):
        extra = (_face("host.custom.a"), _face("host.custom.b"))
        with SoulPlugin(
            agent_id="p2",
            db_path=tmp_path / "p2.db",
            extra_corpus=extra,
        ) as plugin:
            ids = {f.id for f in plugin.corpus.faces}
            assert "host.custom.a" in ids
            assert "host.custom.b" in ids
            # Defaults still present.
            assert any(i.startswith("core.") for i in ids)
            # Total = defaults + 2 extras.
            assert len(ids) == len(DEFAULT_FACES) + 2

    def test_replace_corpus_wipes_defaults(self, tmp_path):
        extra = (_face("only.this"),)
        with SoulPlugin(
            agent_id="p3",
            db_path=tmp_path / "p3.db",
            extra_corpus=extra,
            replace_corpus=True,
        ) as plugin:
            ids = {f.id for f in plugin.corpus.faces}
            assert ids == {"only.this"}

    def test_second_open_preserves_default_seed(self, tmp_path):
        db = tmp_path / "p4.db"
        # First open seeds defaults.
        with SoulPlugin(agent_id="p4", db_path=db):
            pass
        # Reset the SoulStore singleton so the second open uses a fresh
        # connection (matches the cross-process restart scenario more
        # closely than reusing the same in-process connection).
        SoulStore._instances.pop(str(db), None)
        # Second open — count should still match defaults.
        with SoulPlugin(agent_id="p4", db_path=db) as plugin2:
            assert len(plugin2.corpus.faces) == len(DEFAULT_FACES)

    def test_retired_face_stays_retired_across_reopen(self, tmp_path):
        db = tmp_path / "p5.db"
        with SoulPlugin(agent_id="p5", db_path=db) as plugin:
            target_id = next(iter(plugin.corpus.faces)).id
            plugin.corpus_store.retire_face(target_id)
            plugin.reload_corpus()
            assert target_id not in {f.id for f in plugin.corpus.faces}
        SoulStore._instances.pop(str(db), None)
        with SoulPlugin(agent_id="p5", db_path=db) as plugin2:
            assert target_id not in {f.id for f in plugin2.corpus.faces}


# ---------------------------------------------------------------------------
# Cooldown survives restart
# ---------------------------------------------------------------------------


class _Host:
    """Minimal PulseHost for engine-level tests."""

    def __init__(self, snapshot: dict):
        self._snap = snapshot
        self.dispatched: list[PulseAction] = []

    def snapshot(self) -> dict:
        return dict(self._snap)

    def slow_drift_tick(self) -> None:
        return None

    def most_recent_target(self) -> PulseTarget | None:
        return PulseTarget(payload={"channel": "ch", "user": "u"})

    def due_reminders(self):
        return []

    def deliver_reminder(self, target, reminder):
        return None

    async def dispatch_action(self, action: PulseAction) -> ActionOutcome:
        self.dispatched.append(action)
        return ActionOutcome(delivered=True)

    def dispatch_pulse(self, target, trigger, prompt) -> bool:
        return True


def _distress_snap() -> dict:
    # Mood far below soul, V dropped, W dropped → distress trigger fires.
    return {
        "soul": dict(SOUL_DEFAULT),
        "mood": [80, 130, 110, 70, 100, 110, 100],
        "soul_distance": 70.0,
        "trauma_load": 5.0,
        "nourishment_load": 0.0,
    }


class TestCooldownSurvivesRestart:
    def test_recent_fire_survives_plugin_close(self, tmp_path):
        db = tmp_path / "cd.db"
        host = _Host(_distress_snap())
        with SoulPlugin(agent_id="cd1", db_path=db) as plugin:
            engine = PulseEngine(
                host,
                PulseConfig(min_quiet_seconds=0, startup_grace_s=0),
                event_log=plugin.event_log,
                agent_id="cd1",
                corpus=plugin.corpus,
                recency=plugin.recency,
                physics=plugin.physics,
            )
            engine.note_outbound()
            asyncio.run(engine.tick())

        # Pull face_id of whatever fired.
        assert host.dispatched, "engine should have fired"
        fired_face_id = host.dispatched[0].extra.get("face_id")
        assert fired_face_id is not None

        # Capture the fire timestamp from disk so the assertion uses an
        # actually-future "now."
        with SoulPlugin(agent_id="cd1", db_path=db) as plugin_inspect:
            recency_rows = plugin_inspect.corpus_store.load_recency("cd1")
            assert fired_face_id in recency_rows
            fire_ts = recency_rows[fired_face_id][0]

        # Reopen — recency log should preload the fire.
        SoulStore._instances.pop(str(db), None)
        with SoulPlugin(agent_id="cd1", db_path=db) as plugin2:
            elapsed = plugin2.recency.seconds_since(fired_face_id, now=fire_ts + 5.0)
            assert elapsed is not None
            # 5s after the recorded fire — preload restored the timestamp.
            assert elapsed == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# pulse_log.face_id stamping
# ---------------------------------------------------------------------------


class TestPulseLogFaceId:
    def test_dispatched_pulse_has_face_id(self, tmp_path):
        db = tmp_path / "fl.db"
        host = _Host(_distress_snap())
        with SoulPlugin(agent_id="fl1", db_path=db) as plugin:
            engine = PulseEngine(
                host,
                PulseConfig(min_quiet_seconds=0, startup_grace_s=0),
                event_log=plugin.event_log,
                agent_id="fl1",
                corpus=plugin.corpus,
                recency=plugin.recency,
                physics=plugin.physics,
            )
            engine.note_outbound()
            asyncio.run(engine.tick())
            # Read pulse_log row directly.
            with plugin.store.lock:
                rows = plugin.store.connection.execute(
                    "SELECT face_id, dispatched FROM pulse_log WHERE agent_id = ? ORDER BY id DESC",
                    ("fl1",),
                ).fetchall()
        assert rows, "no pulse_log row written"
        row = rows[0]
        assert row[1] == 1, "row should be dispatched"
        assert row[0] is not None, "face_id should be stamped on dispatched pulses"
        # Face id matches what landed in the action.
        assert host.dispatched[0].extra.get("face_id") == row[0]

    def test_legacy_no_corpus_path_face_id_none(self, tmp_path):
        db = tmp_path / "leg.db"
        host = _Host(_distress_snap())
        store = SoulStore(db)
        # Build engine WITHOUT a corpus to exercise the legacy fallback.
        from clanker_soul import EmotionalPhysics, SoulState, SqliteEventLog

        physics = EmotionalPhysics(
            soul=SoulState(),
            event_log=SqliteEventLog(store),
            agent_id="leg",
        )
        engine = PulseEngine(
            host,
            PulseConfig(min_quiet_seconds=0, startup_grace_s=0),
            event_log=SqliteEventLog(store),
            agent_id="leg",
            physics=physics,
        )
        engine.note_outbound()
        asyncio.run(engine.tick())
        with store.lock:
            rows = store.connection.execute(
                "SELECT face_id FROM pulse_log WHERE agent_id = ?",
                ("leg",),
            ).fetchall()
        assert rows
        # Without a corpus, no face_id should be recorded.
        assert all(r[0] is None for r in rows)
