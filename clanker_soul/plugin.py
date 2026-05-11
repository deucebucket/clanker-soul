"""SoulPlugin — the documented one-call drop-in entry point for hosts.

The library's lower-level pieces (``EmotionalPhysics``, ``SoulStore``,
``SqliteEventLog``, ``ConfigOverrides``) compose cleanly but require
boilerplate to wire together: open a store, load the soul row, build
a sink, build an overrides provider, hand them all to physics, remember
to call ``reload_overrides`` and ``soul_drift`` per tick, save before
exit. Every consumer would write the same wrapper, so we ship it.

::

    with SoulPlugin(agent_id="my-agent", db_path="./soul.db") as plugin:
        plugin.ingest(score)           # logs the event
        plugin.tick()                  # reloads overrides + drifts soul
        snap = plugin.snapshot()       # PulseHost-compatible dict

Direct use of ``EmotionalPhysics`` is still supported for advanced
hosts that need to bypass the persistence layer or compose their own
event log. ``SoulPlugin`` is for everyone else.
"""

from __future__ import annotations

import logging
from pathlib import Path

from typing import Iterable

from clanker_soul.eventlog import (
    EventLog,
    IngestRecord,
    NullEventLog,
    SqliteEventLog,
)
from clanker_soul.governor import (
    CapabilityLevel,
    CrisisDiagnosis,
    GovernorConfig,
    assess_capability,
    compose_state_context,
    crisis_signal,
)
from clanker_soul.inference import Inference, _MissingInference
from clanker_soul.overrides import ConfigOverrides
from clanker_soul.physics import (
    EmotionalPhysics,
    PhysicsConfig,
    PhysicsTick,
    soul_distance,
)
from clanker_soul.pending import (
    InMemoryPendingActionStore,
    OutcomeClassifier,
    PendingActionStore,
    PendingCoordinator,
    PendingDeltaConfig,
    SqlitePendingActionStore,
)
from clanker_soul.pulse.corpus import PromptCorpus, PromptFace
from clanker_soul.pulse.corpus_defaults import DEFAULT_FACES
from clanker_soul.pulse.corpus_store import CorpusStore, PersistentRecencyLog
from clanker_soul.score import Score
from clanker_soul.soul import SoulState, SoulStore

logger = logging.getLogger(__name__)


def _agent_row_exists(store: SoulStore, agent_id: str) -> bool:
    """Cheap existence check — distinguishes 'never seen this agent
    before' from 'agent exists with default-shaped soul.' Used to
    decide whether the user-supplied ``default_soul`` should kick in."""
    with store.lock:
        row = store.connection.execute(
            "SELECT 1 FROM soul_state WHERE agent_id = ?",
            (agent_id,),
        ).fetchone()
    return row is not None


class SoulPlugin:
    """One-call wrapper around physics + storage + event log + overrides.

    **Host integration is not optional.** clanker-soul folds INTO an
    agent — it doesn't become one. For the soul to actually shape
    behavior, every host MUST do three things (see
    ``docs/host-integration.md`` for the full guide and
    ``clanker_soul.examples.reference_host`` for a runnable
    starting point):

    1. **Inject ``plugin.state_context()`` into every agent turn** —
       without this the agent has no awareness of its own mood.
    2. **Persist contemplations as first-person memory entries** —
       *"I found myself wondering: …"*, not *"someone asked me: …"*.
    3. **Frame contemplations as introspection-not-attack at delivery** —
       wrap with explicit ``source: "internal_introspection"``
       metadata in the context dict passed to ``inference.score()``
       so the model treats spontaneous thoughts as its own, not as
       accusations to deflect.

    Construction params:
      ``agent_id``     — string key used for all per-agent rows.
      ``db_path``      — SQLite file path. Process-singleton store via
                         :py:meth:`SoulStore.get`.
      ``config``       — optional :py:class:`PhysicsConfig` override.
      ``default_soul`` — used as the initial soul ONLY when this agent
                         has no saved row. Once the agent has been
                         saved at least once, the persisted soul wins
                         and ``default_soul`` is ignored.
      ``event_log``    — bool. Default True wires a :py:class:`SqliteEventLog`
                         that writes every ingest + pulse to the v0.2
                         tables. Set False for a fully quiet plugin.
      ``governor_config`` — optional :py:class:`GovernorConfig` for
                         the safety governor (capability gating +
                         crisis discrimination). Defaults to
                         standard thresholds.
      ``inference``    — optional :py:class:`Inference` impl that
                         covers BOTH score + act roles. Pass when one
                         model wears both hats. clanker-soul never
                         imports any model SDK; the impl lives in
                         host code or a companion package.
      ``scorer``       — optional :py:class:`Inference` impl for the
                         score role only. If both ``inference`` and
                         ``scorer`` are passed, ``scorer`` wins for
                         the score role.
      ``actor``        — optional :py:class:`Inference` impl for the
                         act role only. Same precedence as ``scorer``.
                         Hosts wanting different backends per role
                         (cheap local for scoring, deliberate cloud
                         for acting) pass both kwargs and skip
                         ``inference``.
    """

    def __init__(
        self,
        agent_id: str,
        db_path: Path | str,
        *,
        config: PhysicsConfig | None = None,
        default_soul: SoulState | None = None,
        event_log: bool | EventLog = True,
        governor_config: GovernorConfig | None = None,
        extra_corpus: Iterable[PromptFace] | None = None,
        replace_corpus: bool = False,
        inference: Inference | None = None,
        scorer: Inference | None = None,
        actor: Inference | None = None,
    ) -> None:
        self._agent_id = agent_id
        self._db_path = Path(db_path)
        self._store = SoulStore.get(self._db_path)

        existed = _agent_row_exists(self._store, agent_id)
        soul, trauma, nourishment = self._store.load(agent_id)
        # M4 #97 — load the per-agent mistakes reservoir. Always returns
        # an empty MistakeReservoir for never-saved agents OR for v0.x
        # rows that pre-date the mistakes_json column (the column
        # migration on _init_schema gave those rows '{}').
        mistakes = self._store.load_mistakes(agent_id)
        if not existed and default_soul is not None:
            soul = default_soul

        # Resolve event_log: True → SqliteEventLog, False → NullEventLog,
        # custom impl → use as-is. Custom impls let hosts bring their
        # own sink (file-tail, syslog, OpenTelemetry, etc.).
        if event_log is True:
            self._event_log: EventLog = SqliteEventLog(self._store)
        elif event_log is False:
            self._event_log = NullEventLog()
        else:
            self._event_log = event_log

        self._overrides = ConfigOverrides(self._store)

        # Only thread the event_log into physics when it's a real one;
        # passing NullEventLog still triggers agent_id required path,
        # which is fine, but pass None for the no-op case to keep the
        # "constructed without logging" semantics observable.
        physics_event_log: EventLog | None
        if isinstance(self._event_log, NullEventLog):
            physics_event_log = None
        else:
            physics_event_log = self._event_log

        self._physics = EmotionalPhysics(
            soul=soul,
            trauma=trauma,
            nourishment=nourishment,
            mistakes=mistakes,
            config=config,
            event_log=physics_event_log,
            overrides=self._overrides,
            agent_id=agent_id,
        )
        self._governor_config = governor_config or GovernorConfig()

        # M3.3 — corpus persistence. The CorpusStore wraps the same
        # SoulStore connection, so we don't open a second handle.
        self._corpus_store = CorpusStore(self._store)
        extras = tuple(extra_corpus) if extra_corpus else ()
        if replace_corpus:
            # Operator wants ONLY their faces — clear default + any
            # accumulated rows, install extras.
            self._corpus_store.replace_all(extras, source="host")
        else:
            # Seed defaults on first run (empty DB). On subsequent
            # constructions leave the existing rows alone — operator
            # edits/retirements are preserved across restart. Always
            # upsert the extras so a host can roll out new host faces
            # without manual DB ops.
            if self._corpus_store.count_faces() == 0:
                self._corpus_store.save_faces(DEFAULT_FACES, source="default")
            if extras:
                self._corpus_store.save_faces(extras, source="host")

        # Build the live corpus from whatever now lives on disk.
        self._corpus = PromptCorpus(self._corpus_store.load_faces())
        # Persistent recency log — preloads agent's last-fired
        # timestamps so cooldowns survive restart.
        self._recency = PersistentRecencyLog(self._corpus_store, agent_id)

        # M4 — Inference resolution (#79). Single seam, optional split.
        # If only ``inference`` is passed: scorer/actor alias to it.
        # If ``scorer``/``actor`` are passed: they win for that role.
        # If neither was passed for a role: install a sentinel that
        # raises a clear error on use rather than at construction
        # time, so hosts that don't yet wire inference still construct.
        self._inference: Inference | None = inference
        self._scorer: Inference | _MissingInference = (
            scorer
            if scorer is not None
            else (inference if inference is not None else _MissingInference("scorer"))
        )
        self._actor: Inference | _MissingInference = (
            actor
            if actor is not None
            else (inference if inference is not None else _MissingInference("actor"))
        )

        self._closed = False

    # ------------------------------------------------------------------
    # Properties (let advanced hosts reach in without duplicating state)
    # ------------------------------------------------------------------

    @property
    def agent_id(self) -> str:
        return self._agent_id

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def physics(self) -> EmotionalPhysics:
        return self._physics

    @property
    def overrides(self) -> ConfigOverrides:
        return self._overrides

    @property
    def event_log(self) -> EventLog:
        return self._event_log

    @property
    def store(self) -> SoulStore:
        return self._store

    @property
    def corpus(self) -> PromptCorpus:
        """The live :py:class:`PromptCorpus` rebuilt from disk at
        construction. Pass to :py:class:`PulseEngine(corpus=...)`."""
        return self._corpus

    @property
    def corpus_store(self) -> CorpusStore:
        """The durable :py:class:`CorpusStore`. Hosts use this for
        runtime CRUD: ``plugin.corpus_store.save_face(...)``,
        ``plugin.corpus_store.retire_face(face_id)``, etc."""
        return self._corpus_store

    @property
    def inference(self) -> Inference | None:
        """The :py:class:`Inference` impl that covers both roles, when
        a host wired one. ``None`` when only role-specific
        ``scorer``/``actor`` were passed (or neither). Use
        :py:attr:`scorer` / :py:attr:`actor` for the resolved per-role
        impls; they fall back to this one when no role-specific
        kwarg overrode them."""
        return self._inference

    @property
    def scorer(self) -> Inference:
        """The :py:class:`Inference` impl that handles the *score*
        role. Aliases to :py:attr:`inference` when no role-specific
        ``scorer=`` kwarg was passed. Calls into a host that never
        wired any inference raise ``RuntimeError`` with a clear
        message — not at construction, only on use."""
        return self._scorer  # type: ignore[return-value]

    @property
    def actor(self) -> Inference:
        """The :py:class:`Inference` impl that handles the *act* role.
        Aliases to :py:attr:`inference` when no role-specific
        ``actor=`` kwarg was passed. Same use-site error semantics as
        :py:attr:`scorer`."""
        return self._actor  # type: ignore[return-value]

    @property
    def recency(self) -> PersistentRecencyLog:
        """The live :py:class:`PersistentRecencyLog`. Pass to
        :py:class:`PulseEngine(recency=...)` so cooldowns survive
        process restart."""
        return self._recency

    def reload_corpus(self) -> PromptCorpus:
        """Rebuild the in-memory corpus from disk. Useful when an
        operator UI has just edited rows and the agent needs to pick up
        the change without a process restart. Returns the freshly-built
        corpus (also stored on the plugin for ``plugin.corpus``)."""
        self._corpus = PromptCorpus(self._corpus_store.load_faces())
        return self._corpus

    def build_pending_coordinator(
        self,
        classifier: OutcomeClassifier,
        *,
        store: PendingActionStore | None = None,
        delta_config: PendingDeltaConfig | None = None,
        durable: bool = True,
    ) -> PendingCoordinator:
        """Construct a :py:class:`PendingCoordinator` wired into this
        plugin's :py:class:`EmotionalPhysics`.

        ``classifier`` is host-supplied (small LLM prompt or rule-based
        matcher); this is the only mandatory argument because how to
        decide "what does this inbound mean for that pending" is
        domain-specific.

        ``store`` defaults to :py:class:`SqlitePendingActionStore`
        backed by the plugin's :py:class:`SoulStore` when ``durable=True``,
        falling back to :py:class:`InMemoryPendingActionStore`
        otherwise. Hosts that already own a different
        :py:class:`PendingActionStore` impl pass it directly via the
        kwarg — this method's defaults are conveniences, not
        requirements.

        ``delta_config`` lets operators tune the per-status mood
        deltas (see :py:class:`PendingDeltaConfig`); None uses the
        documented defaults.

        Returns the live coordinator. Hosts call ``record`` /
        ``observe`` / ``tick`` against it. Multiple coordinators can
        coexist for one plugin if a host wants different policies per
        action kind, but most hosts only need one.
        """
        if store is None:
            if durable:
                store = SqlitePendingActionStore(self._store)
            else:
                store = InMemoryPendingActionStore()
        return PendingCoordinator(
            physics=self._physics,
            store=store,
            classifier=classifier,
            delta_config=delta_config,
            agent_id=self._agent_id,
        )

    def most_recent_face_id(self) -> str | None:
        """Return the face_id of the most recent *delivered* pulse for
        this agent — or None if the pulse log is empty / no event log is
        wired / the most recent dispatched row predates M3.3.

        Hosts pass this into ``PulseEngine(previous_face_id=...)`` on
        first construction so branch-tree weights survive process
        restart: the new engine's first tick still benefits from the
        last delivered face's branch hints. Returns None silently
        rather than raising on a missing/empty log so plugin
        construction never fails for this reason."""
        if not isinstance(self._event_log, SqliteEventLog):
            return None
        try:
            with self._store.lock:
                row = self._store.connection.execute(
                    "SELECT face_id FROM pulse_log "
                    "WHERE agent_id = ? AND dispatched = 1 "
                    "  AND face_id IS NOT NULL "
                    "ORDER BY ts DESC LIMIT 1",
                    (self._agent_id,),
                ).fetchone()
        except Exception:
            logger.exception("most_recent_face_id query failed — returning None")
            return None
        return row[0] if row else None

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def ingest(self, score: Score, *, raw: Score | None = None) -> PhysicsTick:
        """Score → ingest → log. Returns the same ``PhysicsTick`` the
        underlying physics would. ``raw=`` is forwarded for hosts that
        applied :py:func:`mood_prime_score` before this call."""
        return self._physics.ingest(score, raw=raw)

    def tick(self) -> dict:
        """Per-tick bookkeeping: reload any UI-set overrides, then run
        ``soul_drift``. Returns the drift report dict for the host to
        log/inspect. Cheap enough to call every tick."""
        self._physics.reload_overrides()
        return self._physics.soul_drift()

    def snapshot(self) -> dict:
        """``PulseHost.snapshot``-compatible dict.

        ``soul``: dict of all SoulState fields.
        ``mood``: list[7] (V/A/D/U/G/W/I) or None when no mood yet.
        ``soul_distance``: float distance Mood↔Soul, or None.
        ``trauma_load`` / ``nourishment_load``: decayed reservoir sums.
        ``mistake_pressure``: decayed sum of the mistakes reservoir
        (M4 #97). Consumers that don't know about this field ignore
        it via ``dict.get`` — additive."""
        mood = self._physics.mood
        soul = self._physics.soul
        return {
            "soul": soul.to_dict(),
            "mood": mood.as_list() if mood is not None else None,
            "soul_distance": (soul_distance(mood, soul) if mood is not None else None),
            "trauma_load": self._physics.trauma.load(),
            "nourishment_load": self._physics.nourishment.load(),
            "mistake_pressure": self._physics.mistakes.load(),
        }

    def mistake_pressure(self) -> float:
        """Decayed sum of the per-agent :py:class:`MistakeReservoir`.

        Hosts read this to bias behaviour toward double-checking,
        verifying tool calls, or pausing before risky operations. The
        M4 cascade (Issue B / #98) reads it for action selection.
        Returns ``0.0`` on a fresh agent. M4 #97."""
        return self._physics.mistakes.load()

    # ------------------------------------------------------------------
    # Safety governor — capability gating + crisis discrimination
    # ------------------------------------------------------------------

    @property
    def governor_config(self) -> GovernorConfig:
        return self._governor_config

    def capability_level(self) -> CapabilityLevel:
        """Return the agent's current operational restriction level
        based on mood/soul/trauma state. Lower = freer; higher = more
        restricted. Hosts use this to filter which tools to expose."""
        return assess_capability(self.snapshot(), self._governor_config)

    def crisis_signal(
        self,
        *,
        recent_events: list[IngestRecord] | None = None,
    ) -> CrisisDiagnosis:
        """Discriminate emotional spike vs real-world emergency.

        ``recent_events`` defaults to the most-recent significant
        events from the event log (negative-classification or
        breached, capped at ``GovernorConfig.crisis_window_events``).
        Hosts can pass their own list — e.g. only events from the
        last 30 minutes — for custom windowing."""
        if recent_events is None:
            recent_events = self._fetch_recent_significant_events()
        return crisis_signal(recent_events, self._governor_config)

    def state_context(
        self,
        *,
        level: CapabilityLevel | None = None,
        recent_events: list[IngestRecord] | None = None,
        crisis: CrisisDiagnosis | None = None,
    ) -> str:
        """Produce the system-prompt-ready string the agent reads to
        understand its own current operational state. The host
        prepends this to the agent's system prompt each turn so the
        agent knows:

          - what restriction level it's at and why
          - what's still allowed (always: messaging the user)
          - which numbers must recover for restrictions to ease
          - what recent significant events were and where they came from
          - whether this looks like an emergency or a spike

        Empty string when level is UNRESTRICTED with no notable recent
        events — no need to chatter at the agent in normal operation.

        Defaults to computing level/recent_events/crisis fresh; pass
        them in if you've already computed them this turn (avoids
        re-querying)."""
        snap = self.snapshot()
        if level is None:
            level = assess_capability(snap, self._governor_config)
        if recent_events is None:
            recent_events = self._fetch_recent_significant_events()
        if crisis is None:
            crisis = crisis_signal(recent_events, self._governor_config)
        return compose_state_context(
            level,
            snap,
            self._governor_config,
            recent_events=recent_events,
            crisis=crisis,
        )

    def _fetch_recent_significant_events(self) -> list[IngestRecord]:
        """Pull recent negative-classification or breached events from
        the log. Returns an empty list when no event log is wired
        (NullEventLog) — graceful degradation."""
        if not isinstance(self._event_log, SqliteEventLog):
            return []
        # Read more than the window so we can filter for significance
        # and still have enough left.
        candidates = self._event_log.read_ingest(
            self._agent_id,
            limit=self._governor_config.crisis_window_events * 5,
        )
        significant = [ev for ev in candidates if ev.classification == "negative" or ev.breached]
        return significant[: self._governor_config.crisis_window_events]

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self) -> None:
        """Flush soul + reservoirs to disk. Idempotent — call as often
        as you like. SqliteEventLog rows are written synchronously so
        they're already durable; this only persists the slow-moving
        soul/reservoir state."""
        self._store.save(
            self._agent_id,
            self._physics.soul,
            self._physics.trauma,
            self._physics.nourishment,
        )
        # M4 #97 — additive mistakes persistence. Must run AFTER the
        # legacy save() because save_mistakes uses UPDATE (no row
        # creation); save() runs INSERT OR REPLACE and guarantees the
        # row exists.
        self._store.save_mistakes(self._agent_id, self._physics.mistakes)

    def close(self) -> None:
        """Save and mark closed. Safe to call twice. The underlying
        ``SoulStore`` connection is process-shared (via
        :py:meth:`SoulStore.get`) and is NOT closed here — other
        plugins or readers may still be using it."""
        if self._closed:
            return
        try:
            self.save()
        except Exception:
            logger.exception("save() during close() failed — continuing")
        self._closed = True

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "SoulPlugin":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ARG002
        self.close()


__all__ = ["SoulPlugin"]
