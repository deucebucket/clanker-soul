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
from clanker_soul.overrides import ConfigOverrides
from clanker_soul.physics import (
    EmotionalPhysics,
    PhysicsConfig,
    PhysicsTick,
    soul_distance,
)
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
    ) -> None:
        self._agent_id = agent_id
        self._db_path = Path(db_path)
        self._store = SoulStore.get(self._db_path)

        existed = _agent_row_exists(self._store, agent_id)
        soul, trauma, nourishment = self._store.load(agent_id)
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
            soul=soul, trauma=trauma, nourishment=nourishment,
            config=config,
            event_log=physics_event_log,
            overrides=self._overrides,
            agent_id=agent_id,
        )
        self._governor_config = governor_config or GovernorConfig()
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
        ``trauma_load`` / ``nourishment_load``: decayed reservoir sums."""
        mood = self._physics.mood
        soul = self._physics.soul
        return {
            "soul": soul.to_dict(),
            "mood": mood.as_list() if mood is not None else None,
            "soul_distance": (
                soul_distance(mood, soul) if mood is not None else None
            ),
            "trauma_load": self._physics.trauma.load(),
            "nourishment_load": self._physics.nourishment.load(),
        }

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
        self, *, recent_events: list[IngestRecord] | None = None,
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
        self, *,
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
            level, snap, self._governor_config,
            recent_events=recent_events, crisis=crisis,
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
        significant = [
            ev for ev in candidates
            if ev.classification == "negative" or ev.breached
        ]
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
