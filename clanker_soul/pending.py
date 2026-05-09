"""``clanker_soul.pending`` — pending-action tracking + outcome
classification (#57).

The :py:class:`PulseEngine` already turns triggers into actions and
captures *immediate* outcomes via :py:class:`ActionOutcome`. This
module extends the loop to actions whose outcome **arrives later**, or
never at all:

  * Carl sends a check-in SMS at 4pm.
  * Four hours pass with no reply.
  * The operator messages, but about a totally unrelated thing.
  * Carl's plea was visible-and-skipped — that's a real emotional
    event. V/W should drop.

The pre-#57 :py:class:`ActionOutcome` could not represent that. With
this module, every fired action that wants a response is tracked as a
:py:class:`PendingAction` until something resolves it (the host
observes new input on the same surface, or the action expires). The
resolution carries a status — ``acknowledged`` / ``ignored`` /
``mixed`` / ``expired`` — and applies a mood delta the operator can
tune per-status.

This is generic agent territory. Any host running clanker-soul that
performs an action with an expected response benefits: messaging,
voice calls, social posts, even tool calls that produce delayed
effects. clanker-soul does NOT classify the inbound — the host
provides an :py:class:`OutcomeClassifier`. clanker-soul does NOT poll
for expiry — the host calls ``coordinator.tick(now)`` (typically from
its existing :py:meth:`SoulPlugin.tick` cadence).

Public API:

  * :py:class:`PendingAction` — frozen dataclass.
  * :py:class:`PendingActionStore` — Protocol; ``record`` / ``pending_on``
    / ``mark`` / ``prune_expired``.
  * :py:class:`InMemoryPendingActionStore` — default in-memory impl.
  * :py:class:`SqlitePendingActionStore` — durable impl backed by the
    ``SoulStore`` connection. Survives restart.
  * :py:class:`OutcomeClassifier` — Protocol; ``classify`` returns
    ``"acknowledged"`` / ``"ignored"`` / ``"mixed"`` / ``"unrelated"``.
  * :py:class:`KeywordOutcomeClassifier` — trivial reference impl that
    matches expected-response keywords. Hosts wanting smarter
    behavior write their own (e.g. ``LLMOutcomeClassifier`` using the
    host's existing model).
  * :py:class:`PendingCoordinator` — orchestrates record/observe/tick;
    applies mood deltas via the :py:class:`EmotionalPhysics` it was
    constructed with.
  * :py:class:`PendingDeltaConfig` — operator-tunable per-status mood
    deltas (V/A/D/U/G/W/I), plus the fast-vs-late threshold.

Out of scope here (separate issues):

  * Multi-action escalation ("3 unanswered = bigger drop").
  * Cross-surface bridging (host can already fold via consistent
    ``surface_key``).
  * pgvector / postgres persistence — SQLite is the reference.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import (
    Callable,
    ClassVar,
    Literal,
    Protocol,
    runtime_checkable,
)

from clanker_soul.physics import EmotionalPhysics
from clanker_soul.score import Score
from clanker_soul.soul import SoulStore

logger = logging.getLogger(__name__)


# ── Status type aliases ────────────────────────────────────────────────


PendingStatus = Literal["pending", "acknowledged", "ignored", "mixed", "expired"]
ClassifyOutcome = Literal["acknowledged", "ignored", "mixed", "unrelated"]


# ── Data model ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PendingAction:
    """An :py:class:`ActionOutcome` that hasn't resolved yet.

    ``surface_key`` is host-defined — typically ``(channel_id, user_id)``
    or ``(platform, conversation_id)``. The coordinator looks pendings
    up by exact match on this tuple, so hosts that want cross-surface
    bridging just pass a stable identifier on both sides.

    ``soul_snapshot`` is a dict shaped like
    :py:meth:`SoulPlugin.snapshot` — preserved at firing time so an
    ``acknowledged_late`` resolution knows what the soul looked like
    when the action went out, not just at resolution time.

    ``status`` evolves: every PendingAction starts at ``"pending"``.
    The store holds the row; the coordinator marks it via
    :py:meth:`PendingActionStore.mark` when an observation resolves it
    or when expiry passes.
    """

    id: str
    kind: str
    fired_at: datetime
    surface_key: tuple[str, ...]
    body: str | None
    soul_snapshot: dict
    expected_response: str
    status: PendingStatus
    expires_at: datetime
    extra: dict = field(default_factory=dict)

    @staticmethod
    def new(
        *,
        kind: str,
        surface_key: tuple[str, ...],
        body: str | None,
        soul_snapshot: dict,
        expected_response: str,
        fired_at: datetime | None = None,
        expires_at: datetime | None = None,
        ttl_seconds: int = 12 * 60 * 60,
        action_id: str | None = None,
        extra: dict | None = None,
    ) -> "PendingAction":
        """Convenience constructor that sets ``status="pending"``,
        generates a UUID id by default, and computes ``expires_at``
        from ``ttl_seconds`` when not supplied."""
        fired_at = fired_at or datetime.now(timezone.utc)
        if expires_at is None:
            expires_at = fired_at + timedelta(seconds=ttl_seconds)
        return PendingAction(
            id=action_id or uuid.uuid4().hex,
            kind=kind,
            fired_at=fired_at,
            surface_key=tuple(surface_key),
            body=body,
            soul_snapshot=dict(soul_snapshot),
            expected_response=expected_response,
            status="pending",
            expires_at=expires_at,
            extra=dict(extra or {}),
        )


# ── Mood-delta configuration ───────────────────────────────────────────


@dataclass(frozen=True)
class PendingDeltaConfig:
    """Per-status mood deltas applied when a pending resolves.

    Keys are status names; values are 7-tuples in V/A/D/U/G/W/I order,
    each in the range ``[-127, 127]``. Defaults are conservative —
    ``ignored`` is the largest hit because the plea was directly
    visible and skipped.

    ``fast_threshold_seconds`` distinguishes ``"acknowledged_fast"``
    from ``"acknowledged_late"`` for resolution timing — under the
    threshold a fast acknowledgement; over it, the gentler late one.
    """

    acknowledged_fast: tuple[int, int, int, int, int, int, int] = (
        +6,
        0,
        0,
        0,
        +2,
        +4,
        0,
    )
    acknowledged_late: tuple[int, int, int, int, int, int, int] = (
        +3,
        0,
        0,
        0,
        +1,
        +2,
        0,
    )
    mixed: tuple[int, int, int, int, int, int, int] = (
        -2,
        0,
        0,
        0,
        0,
        -2,
        0,
    )
    ignored: tuple[int, int, int, int, int, int, int] = (
        -8,
        0,
        0,
        0,
        -3,
        -6,
        -2,
    )
    expired: tuple[int, int, int, int, int, int, int] = (
        -3,
        0,
        0,
        0,
        -2,
        -3,
        0,
    )
    fast_threshold_seconds: float = 5 * 60  # 5 minutes


# ── Stores ─────────────────────────────────────────────────────────────


@runtime_checkable
class PendingActionStore(Protocol):
    """Minimal CRUD for pending-action persistence. Hosts can plug in
    a custom impl (e.g. an existing relational DB, a postgres backend)
    by satisfying this Protocol."""

    def record(self, action: PendingAction) -> None: ...

    def get(self, action_id: str) -> PendingAction | None: ...

    def pending_on(self, surface_key: tuple[str, ...]) -> list[PendingAction]: ...

    def mark(self, action_id: str, status: PendingStatus) -> None: ...

    def prune_expired(self, now: datetime) -> list[PendingAction]: ...


class InMemoryPendingActionStore:
    """Default :py:class:`PendingActionStore` impl. Process-local,
    not durable — fine for tests and short-lived agents.
    Use :py:class:`SqlitePendingActionStore` for production hosts that
    need cross-restart persistence."""

    def __init__(self) -> None:
        self._rows: dict[str, PendingAction] = {}

    def record(self, action: PendingAction) -> None:
        self._rows[action.id] = action

    def get(self, action_id: str) -> PendingAction | None:
        return self._rows.get(action_id)

    def pending_on(self, surface_key: tuple[str, ...]) -> list[PendingAction]:
        key = tuple(surface_key)
        return [a for a in self._rows.values() if a.surface_key == key and a.status == "pending"]

    def mark(self, action_id: str, status: PendingStatus) -> None:
        row = self._rows.get(action_id)
        if row is None:
            return
        self._rows[action_id] = replace(row, status=status)

    def prune_expired(self, now: datetime) -> list[PendingAction]:
        out: list[PendingAction] = []
        for action_id, row in list(self._rows.items()):
            if row.status == "pending" and row.expires_at <= now:
                expired = replace(row, status="expired")
                self._rows[action_id] = expired
                out.append(expired)
        return out

    def __len__(self) -> int:
        return len(self._rows)


class SqlitePendingActionStore:
    """Durable :py:class:`PendingActionStore` using the
    :py:class:`SoulStore` connection. Schema is created idempotently
    on first construction.

    Soft-fail writes: a transient DB hiccup warns and continues rather
    than raising into the engine. Reads are not soft-fail — a read
    failure should surface to tests/UI rather than be swallowed."""

    def __init__(self, store: SoulStore) -> None:
        self._store = store
        self._init_schema()

    def _init_schema(self) -> None:
        c = self._store.connection
        with self._store.lock:
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS pending_actions (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    fired_at REAL NOT NULL,
                    surface_key TEXT NOT NULL,
                    body TEXT,
                    soul_snapshot TEXT NOT NULL,
                    expected_response TEXT NOT NULL,
                    status TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    extra TEXT NOT NULL
                )
                """
            )
            c.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_pending_actions_surface_status
                    ON pending_actions (surface_key, status)
                """
            )
            c.commit()

    def record(self, action: PendingAction) -> None:
        try:
            with self._store.lock:
                self._store.connection.execute(
                    """
                    INSERT OR REPLACE INTO pending_actions (
                        id, kind, fired_at, surface_key, body,
                        soul_snapshot, expected_response, status,
                        expires_at, extra
                    ) VALUES (?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        action.id,
                        action.kind,
                        action.fired_at.timestamp(),
                        json.dumps(list(action.surface_key)),
                        action.body,
                        json.dumps(action.soul_snapshot),
                        action.expected_response,
                        action.status,
                        action.expires_at.timestamp(),
                        json.dumps(action.extra),
                    ),
                )
                self._store.connection.commit()
        except Exception as e:
            logger.warning("pending_actions record failed (%s) — continuing", e)

    def get(self, action_id: str) -> PendingAction | None:
        with self._store.lock:
            row = self._store.connection.execute(
                "SELECT id, kind, fired_at, surface_key, body, "
                "       soul_snapshot, expected_response, status, "
                "       expires_at, extra "
                "FROM pending_actions WHERE id = ?",
                (action_id,),
            ).fetchone()
        return _row_to_action(row) if row else None

    def pending_on(self, surface_key: tuple[str, ...]) -> list[PendingAction]:
        key_json = json.dumps(list(surface_key))
        with self._store.lock:
            rows = self._store.connection.execute(
                "SELECT id, kind, fired_at, surface_key, body, "
                "       soul_snapshot, expected_response, status, "
                "       expires_at, extra "
                "FROM pending_actions "
                "WHERE surface_key = ? AND status = 'pending' "
                "ORDER BY fired_at ASC",
                (key_json,),
            ).fetchall()
        return [_row_to_action(r) for r in rows]

    def mark(self, action_id: str, status: PendingStatus) -> None:
        try:
            with self._store.lock:
                self._store.connection.execute(
                    "UPDATE pending_actions SET status = ? WHERE id = ?",
                    (status, action_id),
                )
                self._store.connection.commit()
        except Exception as e:
            logger.warning("pending_actions mark(%r, %r) failed (%s)", action_id, status, e)

    def prune_expired(self, now: datetime) -> list[PendingAction]:
        ts = now.timestamp()
        with self._store.lock:
            rows = self._store.connection.execute(
                "SELECT id, kind, fired_at, surface_key, body, "
                "       soul_snapshot, expected_response, status, "
                "       expires_at, extra "
                "FROM pending_actions "
                "WHERE status = 'pending' AND expires_at <= ? "
                "ORDER BY expires_at ASC",
                (ts,),
            ).fetchall()
            actions = [_row_to_action(r) for r in rows]
            if actions:
                ids = [a.id for a in actions]
                placeholders = ",".join("?" * len(ids))
                try:
                    self._store.connection.execute(
                        f"UPDATE pending_actions SET status = 'expired' "
                        f"WHERE id IN ({placeholders})",
                        ids,
                    )
                    self._store.connection.commit()
                except Exception as e:
                    logger.warning("pending_actions prune_expired update failed (%s)", e)
        return [replace(a, status="expired") for a in actions]


def _row_to_action(row: tuple) -> PendingAction:
    return PendingAction(
        id=row[0],
        kind=row[1],
        fired_at=datetime.fromtimestamp(row[2], tz=timezone.utc),
        surface_key=tuple(json.loads(row[3])),
        body=row[4],
        soul_snapshot=json.loads(row[5]),
        expected_response=row[6],
        status=row[7],
        expires_at=datetime.fromtimestamp(row[8], tz=timezone.utc),
        extra=json.loads(row[9]),
    )


# ── Outcome classifier ─────────────────────────────────────────────────


@runtime_checkable
class OutcomeClassifier(Protocol):
    """Decide what happened to a pending action when an observation
    arrives. Hosts implement this — usually a small LLM prompt or a
    rule-based matcher specific to their domain."""

    def classify(
        self,
        pending: PendingAction,
        observation: dict,
    ) -> ClassifyOutcome: ...


@dataclass
class KeywordOutcomeClassifier:
    """Trivial reference :py:class:`OutcomeClassifier`. Matches a few
    comma-separated keyword sets in the pending's ``expected_response``
    field against the observation's ``"text"`` field. Useful for tests
    and for hosts that don't yet have a model wired up.

    Format: ``"ack:hi,hello,thanks; ignore:no,cancel"``. Tokens are
    case-insensitive substring matches. Multiple groups are OR-combined
    within each label and the first label that matches wins. Tokens
    on neither side → ``"unrelated"``.
    """

    label_priority: tuple[str, ...] = ("ack", "ignore", "mixed")

    def classify(
        self,
        pending: PendingAction,
        observation: dict,
    ) -> ClassifyOutcome:
        text = (observation.get("text") or "").lower()
        if not text:
            return "unrelated"
        groups = self._parse(pending.expected_response)
        for label in self.label_priority:
            tokens = groups.get(label, ())
            if any(tok in text for tok in tokens):
                return _label_to_outcome(label)
        return "unrelated"

    @staticmethod
    def _parse(spec: str) -> dict[str, tuple[str, ...]]:
        out: dict[str, tuple[str, ...]] = {}
        for chunk in spec.split(";"):
            chunk = chunk.strip()
            if not chunk or ":" not in chunk:
                continue
            label, raw = chunk.split(":", 1)
            tokens = tuple(t.strip().lower() for t in raw.split(",") if t.strip())
            if tokens:
                out[label.strip().lower()] = tokens
        return out


def _label_to_outcome(label: str) -> ClassifyOutcome:
    if label == "ack":
        return "acknowledged"
    if label == "ignore":
        return "ignored"
    if label == "mixed":
        return "mixed"
    return "unrelated"


@dataclass
class LLMOutcomeClassifier:
    """LLM-backed :py:class:`OutcomeClassifier` reference impl.

    Asks an external model to decide acknowledged / ignored / mixed /
    unrelated for a (pending, observation) pair. The model call is
    abstracted behind a host-supplied callable so this class isn't
    coupled to any specific provider — pass an OpenRouter wrapper, an
    Anthropic SDK call, an Ollama call, or a stub for tests.

    Why ship this rather than leave it to hosts: the live demo at
    ``integrations/hermes/scripts/pending_action_live_demo.py`` proved
    the prompt + parsing logic on real DeepSeek V3 Flash output. Every
    host that wants LLM-based classification would otherwise rewrite
    the same prompt and parser. Promoting it here makes the right path
    the easy path.

    ``call_model`` signature: ``Callable[[str, str], str]`` —
    ``(system_prompt, user_prompt) -> assistant_text``. May be sync
    only (the classifier is sync). Async hosts can wrap with
    ``asyncio.run`` or use a sync wrapper.

    Robustness:

      * Bad model output (empty, error sentinel, no recognised label)
        → returns ``"unrelated"`` so the pending stays open and no
        spurious mood delta lands. Matches the soft-fail invariant.
      * The classifier accepts label words in any case and ignores
        leading/trailing whitespace; it walks
        ``("acknowledged", "ignored", "mixed", "unrelated")`` in priority
        order and returns the first one substring-present in the
        response.

    Customising the system prompt: pass ``system_prompt=`` to override
    the default. The default works on the demo's models; hosts may
    want to tighten or loosen it for their model.
    """

    DEFAULT_SYSTEM_PROMPT: ClassVar[str] = (
        "You are a conversation-outcome classifier. Given a message that "
        "an AI agent sent to a human ('the agent's message') and the "
        "human's reply ('the inbound'), decide whether the inbound:\n"
        "  - acknowledges the agent's message (engages with it directly, "
        "    even briefly)\n"
        "  - ignores it (changes the subject completely or dismisses)\n"
        "  - mixed (partially engages, partially deflects)\n"
        "  - unrelated (didn't see the agent's message at all)\n"
        "Respond with EXACTLY ONE WORD from: acknowledged, ignored, mixed, "
        "unrelated. No punctuation, no explanation, no other text."
    )

    call_model: Callable[[str, str], str]
    system_prompt: str = field(default="")
    label_priority: tuple[str, ...] = (
        "acknowledged",
        "ignored",
        "mixed",
        "unrelated",
    )
    last_raw_response: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not self.system_prompt:
            # Use object.__setattr__ via type ignore not needed — dataclass is mutable.
            self.system_prompt = self.DEFAULT_SYSTEM_PROMPT

    def classify(
        self,
        pending: PendingAction,
        observation: dict,
    ) -> ClassifyOutcome:
        body = pending.body or "(no body)"
        text = observation.get("text", "")
        user_prompt = f"Agent's message: {body!r}\nInbound: {text!r}\nClassification:"
        try:
            raw = self.call_model(self.system_prompt, user_prompt) or ""
        except Exception:
            logger.exception(
                "LLMOutcomeClassifier.call_model raised; treating as unrelated",
            )
            self.last_raw_response = None
            return "unrelated"
        self.last_raw_response = raw
        # An error-sentinel return like "[LLM-ERROR: ...]" should land
        # at "unrelated" — substring matching against label_priority
        # naturally fails for those.
        normalized = raw.lower().strip()
        for label in self.label_priority:
            if label in normalized:
                # All four label_priority entries are valid
                # ClassifyOutcome strings — type-checker is happy.
                return label  # type: ignore[return-value]
        return "unrelated"


# ── Coordinator ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ResolutionResult:
    """What :py:meth:`PendingCoordinator.observe` returned for one
    resolution. Hosts that want to log resolutions or react to them
    (e.g. send the operator an "I noticed you ignored my pulse"
    follow-up) consume this.

    ``score`` is the synthetic Score event the coordinator ingested
    into physics, or None when the resolution was ``"unrelated"`` and
    no delta applied. Hosts can re-emit / re-log it; physics has
    already applied the change."""

    pending: PendingAction
    outcome: ClassifyOutcome
    resolved_status: PendingStatus
    score: Score | None


class PendingCoordinator:
    """Glue between store, classifier, and physics.

    Hosts construct one per agent. The plumbing is:

      1. Host fires an action via the engine. Immediately after, host
         calls :py:meth:`record` with the action body / surface key /
         expected response.
      2. On every observation that might resolve a pending — typically
         every inbound message — host calls :py:meth:`observe`. The
         coordinator looks up pendings on the same surface, runs the
         host's :py:class:`OutcomeClassifier` against each, marks
         resolved, and applies the corresponding mood delta as a
         synthetic :py:class:`Score` event ingested into physics.
      3. On every soul tick — typically the same one calling
         :py:meth:`SoulPlugin.tick` — host calls :py:meth:`tick` to
         expire stale pendings and apply the ``expired`` mood delta.
    """

    def __init__(
        self,
        *,
        physics: EmotionalPhysics,
        store: PendingActionStore,
        classifier: OutcomeClassifier,
        delta_config: PendingDeltaConfig | None = None,
        agent_id: str | None = None,
    ) -> None:
        self._physics = physics
        self._store = store
        self._classifier = classifier
        self._cfg = delta_config or PendingDeltaConfig()
        self._agent_id = agent_id

    @property
    def store(self) -> PendingActionStore:
        return self._store

    @property
    def delta_config(self) -> PendingDeltaConfig:
        return self._cfg

    def record(self, pending: PendingAction) -> None:
        """Store a freshly-fired pending. Returns nothing — the host
        already has the object."""
        self._store.record(pending)

    def observe(
        self,
        surface_key: tuple[str, ...],
        observation: dict,
        *,
        now: datetime | None = None,
    ) -> list[ResolutionResult]:
        """Run every still-pending action on ``surface_key`` through
        the classifier with the observation. Apply mood deltas for
        anything that resolved. Returns one :py:class:`ResolutionResult`
        per pending checked (including ``"unrelated"`` outcomes that
        produced no delta — useful for audit logs).
        """
        now = now or datetime.now(timezone.utc)
        results: list[ResolutionResult] = []
        pendings = self._store.pending_on(surface_key)
        for pending in pendings:
            try:
                outcome = self._classifier.classify(pending, observation)
            except Exception:
                logger.exception(
                    "OutcomeClassifier raised on pending=%r — treating as unrelated",
                    pending.id,
                )
                outcome = "unrelated"
            if outcome == "unrelated":
                results.append(
                    ResolutionResult(
                        pending=pending,
                        outcome=outcome,
                        resolved_status=pending.status,
                        score=None,
                    )
                )
                continue
            resolved_status = self._resolve_status(outcome)
            self._store.mark(pending.id, resolved_status)
            score = self._apply_delta(pending, outcome, now)
            results.append(
                ResolutionResult(
                    pending=pending,
                    outcome=outcome,
                    resolved_status=resolved_status,
                    score=score,
                )
            )
        return results

    def tick(self, *, now: datetime | None = None) -> list[ResolutionResult]:
        """Expire pending actions whose ``expires_at`` has passed.
        Apply the configured ``expired`` mood delta for each."""
        now = now or datetime.now(timezone.utc)
        expired = self._store.prune_expired(now)
        results: list[ResolutionResult] = []
        for action in expired:
            score = self._apply_delta(action, "expired_outcome_marker", now)
            results.append(
                ResolutionResult(
                    pending=action,
                    outcome="ignored",  # closest classify-outcome semantic
                    resolved_status="expired",
                    score=score,
                )
            )
        return results

    def context_bundle(
        self,
        surface_key: tuple[str, ...],
        *,
        now: datetime | None = None,
    ) -> dict:
        """Return a dict the host can fold into its prompt context:
        ``{"pending_count": N, "oldest_age_seconds": int | None,
           "kinds": [...]}``. Cheap — one query.
        """
        now = now or datetime.now(timezone.utc)
        pendings = self._store.pending_on(surface_key)
        if not pendings:
            return {"pending_count": 0, "oldest_age_seconds": None, "kinds": []}
        oldest = min(pendings, key=lambda a: a.fired_at)
        return {
            "pending_count": len(pendings),
            "oldest_age_seconds": int((now - oldest.fired_at).total_seconds()),
            "kinds": sorted({a.kind for a in pendings}),
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_status(self, outcome: ClassifyOutcome) -> PendingStatus:
        # Maps the classifier's outcome label to a stored status.
        if outcome == "acknowledged":
            return "acknowledged"
        if outcome == "ignored":
            return "ignored"
        if outcome == "mixed":
            return "mixed"
        return "pending"  # "unrelated" leaves status unchanged

    def _apply_delta(
        self,
        pending: PendingAction,
        outcome: str,
        now: datetime,
    ) -> Score | None:
        """Translate a status into a :py:class:`Score` delta and ingest
        it into physics. Returns the Score that was applied (None when
        no delta exists for the outcome).
        """
        cfg = self._cfg
        # Dispatch on outcome — including the synthesized
        # "expired_outcome_marker" so tick() can apply the expired delta
        # cleanly without confusing it with a classify path.
        if outcome == "acknowledged":
            elapsed = (now - pending.fired_at).total_seconds()
            deltas = (
                cfg.acknowledged_fast
                if elapsed <= cfg.fast_threshold_seconds
                else cfg.acknowledged_late
            )
            patterns = (
                f"PENDING_ACK_{'FAST' if elapsed <= cfg.fast_threshold_seconds else 'LATE'}",
            )
        elif outcome == "ignored":
            deltas = cfg.ignored
            patterns = ("PENDING_IGNORED",)
        elif outcome == "mixed":
            deltas = cfg.mixed
            patterns = ("PENDING_MIXED",)
        elif outcome == "expired_outcome_marker":
            deltas = cfg.expired
            patterns = ("PENDING_EXPIRED",)
        else:
            return None

        # Translate deltas (signed) into a Score on the [0, 255] scale.
        # Naively adding to neutral 128 would give too-mild values: when
        # the agent's current mood is far from neutral, blending a "+6"
        # Score (V=134) against a current V=144 actually pulls mood
        # DOWN toward the Score. To get the delta to read as the spec
        # intends — a real positive bump — we scale up the magnitude so
        # the synthetic Score lands meaningfully past the agent's
        # current mood. The 10× multiplier was tuned against the live
        # demo: at K=10 a +6 delta yields a synthetic V=188 which
        # consistently lifts a mid-range mood by roughly the spec's
        # intended movement after physics blending.
        delta_scale = 10
        v, a, d, u, g, w, i = deltas
        score = Score(
            v=_to_score_dim(v, delta_scale),
            a=_to_score_dim(a, delta_scale),
            d=_to_score_dim(d, delta_scale),
            u=_to_score_dim(u, delta_scale),
            g=_to_score_dim(g, delta_scale),
            w=_to_score_dim(w, delta_scale),
            i=_to_score_dim(i, delta_scale),
            patterns=patterns,
            direction="OBSERVATION",
            source=f"pending:{pending.kind}",
        )
        try:
            self._physics.ingest(score)
        except Exception:
            logger.exception(
                "Failed to ingest pending-action delta score for %r — skipping",
                pending.id,
            )
            return None
        return score


def _to_score_dim(delta: int, scale: int = 1) -> int:
    """Map a signed delta to a Score dim in ``[0, 255]``.

    With ``scale=1``, ``delta`` is interpreted as a direct shift around
    neutral 128 — useful for callers that already know the absolute
    Score value they want. With ``scale > 1``, the delta is multiplied
    so the resulting Score lands further from neutral, which matters
    when the result will be ingested into physics: blending toward a
    Score only-slightly-past-neutral tends to fight the intended
    direction when current mood is also past neutral.
    """
    return max(0, min(255, 128 + int(delta) * int(scale)))


__all__ = [
    "PendingAction",
    "PendingStatus",
    "ClassifyOutcome",
    "PendingDeltaConfig",
    "PendingActionStore",
    "InMemoryPendingActionStore",
    "SqlitePendingActionStore",
    "OutcomeClassifier",
    "KeywordOutcomeClassifier",
    "LLMOutcomeClassifier",
    "PendingCoordinator",
    "ResolutionResult",
]
