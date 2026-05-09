"""``PulseEngine`` — mood-driven proactive messaging.

Runs on a short tick (default 90s) and decides whether the agent
should *say something on its own* based on:

  • Soul drift bookkeeping (always — moves Soul toward sustained mood,
    bleeds W/V when trauma > nourishment).
  • |Mood - Soul| distance (mood far from baseline → state worth
    expressing, either positive or distress).
  • Trauma reservoir load (sustained wounds → reach out, vent, or
    check in).
  • Sustained nourishment (acknowledge it).
  • Idle ceiling (a long silence allows a check-in).
  • Cooldown — never fire two pulses within ``min_quiet_seconds``.

The engine is host-agnostic: see :py:class:`PulseHost` for the
interface hosts implement.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from clanker_soul.pulse.config import PulseConfig
from clanker_soul.pulse.host import PulseHost
from clanker_soul.pulse.prompt import compose_self_prompt
from clanker_soul.pulse.triggers import (
    ActionOutcome,
    PulseAction,
    Trigger,
)

if TYPE_CHECKING:
    from clanker_soul.eventlog import EventLog
    from clanker_soul.physics import EmotionalPhysics

logger = logging.getLogger(__name__)


class PulseEngine:
    """Mood-driven proactive messaging.

    Construct with a :py:class:`PulseHost` implementation. Call
    :py:meth:`start` to spin up the asyncio task; :py:meth:`stop` to
    cancel it. :py:meth:`note_outbound` should be called whenever the
    host emits any outbound message so the cooldown timer covers
    reactive replies, not just pulses.

    When ``event_log`` is provided, every evaluation produces one
    :py:class:`PulseRecord` (fired, suppressed by cooldown, suppressed
    by missing target, dispatch failed, or no trigger at all)."""

    def __init__(
        self, host: PulseHost, config: PulseConfig | None = None,
        *,
        event_log: "EventLog | None" = None,
        agent_id: str | None = None,
        physics: "EmotionalPhysics | None" = None,
    ) -> None:
        if event_log is not None and not agent_id:
            raise ValueError(
                "agent_id is required when event_log is provided "
                "(log rows are scoped per-agent)"
            )
        self._host = host
        self._cfg = config or PulseConfig()
        self._task: asyncio.Task | None = None
        self._running = False
        self._last_pulse_ts: float = 0.0
        self._last_outbound_ts: float = 0.0
        self._event_log = event_log
        self._agent_id = agent_id
        # Optional physics ref for the action-outcome learning loop.
        # When provided, ActionOutcome.consequences are auto-ingested
        # back into the soul. When None, consequences are warned-and-
        # dropped — the engine still works but the loop is open.
        self._physics = physics
        self._consequences_warned: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="clanker-soul-pulse")
        logger.info("PulseEngine started (interval=%.0fs)", self._cfg.interval_s)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("PulseEngine stopped")

    def note_outbound(self) -> None:
        """Host calls this whenever any outbound message ships, so the
        cooldown timer covers reactive replies in addition to pulses."""
        self._last_outbound_ts = datetime.now(timezone.utc).timestamp()

    # ------------------------------------------------------------------
    # Public single-tick (handy for tests + host-driven schedulers)
    # ------------------------------------------------------------------

    async def tick(self) -> Trigger | None:
        """Run one full tick — drift bookkeeping, reminders, and a pulse
        if warranted. Returns the fired trigger or None. Useful for tests
        and for hosts that don't want the built-in asyncio loop.

        When an ``event_log`` was provided at construction, every
        evaluation produces one ``PulseRecord``."""
        try:
            self._host.slow_drift_tick()
        except Exception:
            logger.exception("slow_drift_tick failed")

        await self._fire_due_reminders()

        snap = self._take_snapshot()
        trigger = self._evaluate_trigger(snap) if snap is not None else None
        log_snap = snap or {}

        if trigger is None:
            self._log_pulse_outcome(
                snap=log_snap, trigger_kind=None,
                suppressed_reason="no_trigger",
                target_present=False, dispatched=False, prompt=None,
            )
            return None

        now = datetime.now(timezone.utc).timestamp()
        last_activity = max(self._last_pulse_ts, self._last_outbound_ts)
        if now - last_activity < self._cfg.min_quiet_seconds:
            logger.debug("Pulse suppressed (cooldown): trigger=%s", trigger.kind)
            self._log_pulse_outcome(
                snap=log_snap, trigger_kind=trigger.kind,
                suppressed_reason="cooldown",
                target_present=False, dispatched=False, prompt=None,
            )
            return None

        fired, prompt, target_present = await self._fire_pulse(trigger)
        if not fired:
            reason = "no_target" if not target_present else "dispatch_failed"
            self._log_pulse_outcome(
                snap=log_snap, trigger_kind=trigger.kind,
                suppressed_reason=reason,
                target_present=target_present, dispatched=False, prompt=prompt,
            )
            return None

        self._log_pulse_outcome(
            snap=log_snap, trigger_kind=trigger.kind,
            suppressed_reason=None,
            target_present=True, dispatched=True, prompt=prompt,
        )
        return trigger

    # ------------------------------------------------------------------
    # Loop
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        await asyncio.sleep(self._cfg.startup_grace_s)
        while self._running:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("PulseEngine tick failed")
            await asyncio.sleep(self._cfg.interval_s)

    # ------------------------------------------------------------------
    # Decision
    # ------------------------------------------------------------------

    def _take_snapshot(self) -> dict | None:
        """Pull a fresh snapshot from the host. Returns None on failure
        so the caller can log a 'snapshot_failed' outcome instead of
        silently returning."""
        try:
            return self._host.snapshot() or {}
        except Exception:
            logger.exception("snapshot failed")
            return None

    def _evaluate_trigger(self, snap: dict) -> Trigger | None:
        soul: dict = snap.get("soul") or {}
        mood: list[int] | None = snap.get("mood")
        distance: float = snap.get("soul_distance") or 0.0
        trauma: float = snap.get("trauma_load") or 0.0
        nourishment: float = snap.get("nourishment_load") or 0.0

        # Force-fire on long silence
        now = datetime.now(timezone.utc).timestamp()
        idle = now - max(self._last_pulse_ts, self._last_outbound_ts)
        if idle > self._cfg.max_quiet_seconds:
            return Trigger(
                kind="long_silence",
                soul=soul, mood=mood,
                metrics={"idle_seconds": int(idle)},
            )

        if mood and distance > self._cfg.distance_trigger:
            v_drop = soul.get("v", 128) - mood[0]
            w_drop = soul.get("w", 128) - mood[5]
            if v_drop > self._cfg.distress_v_drop or w_drop > self._cfg.distress_w_drop:
                return Trigger(
                    kind="distress",
                    soul=soul, mood=mood,
                    metrics={
                        "distance": round(distance, 1),
                        "v_drop": v_drop, "w_drop": w_drop,
                    },
                )

            v_lift = mood[0] - soul.get("v", 128)
            i_lift = mood[6] - soul.get("i", 128)
            if v_lift > self._cfg.elation_v_lift and i_lift > self._cfg.elation_i_lift:
                return Trigger(
                    kind="elation",
                    soul=soul, mood=mood,
                    metrics={"distance": round(distance, 1), "v_lift": v_lift},
                )

        if trauma > self._cfg.trauma_load_trigger and trauma > nourishment * 1.5:
            return Trigger(
                kind="trauma_pressure",
                soul=soul, mood=mood,
                metrics={
                    "trauma_load": round(trauma, 1),
                    "nourishment_load": round(nourishment, 1),
                },
            )

        if nourishment > self._cfg.nourishment_thank_trigger and nourishment > trauma * 2:
            return Trigger(
                kind="gratitude",
                soul=soul, mood=mood,
                metrics={"nourishment_load": round(nourishment, 1)},
            )

        return None

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def _fire_pulse(
        self, trigger: Trigger,
    ) -> tuple[bool, str | None, bool]:
        """Returns ``(fired, prompt, target_present)`` so the caller
        can log a complete outcome regardless of which path we took.

        Wraps the legacy DM dispatch in the new action-based flow:
        builds a ``direct_message`` :py:class:`PulseAction`, calls
        :py:meth:`_dispatch_action_via_host` (which prefers
        ``dispatch_action`` and falls back to ``dispatch_pulse`` for
        legacy hosts), then auto-ingests outcome consequences.
        """
        try:
            target = self._host.most_recent_target()
        except Exception:
            logger.exception("most_recent_target failed")
            return False, None, False
        if target is None:
            logger.debug(
                "Pulse trigger=%s but no recent target — staying quiet",
                trigger.kind,
            )
            return False, None, False

        prompt = compose_self_prompt(trigger)
        action = PulseAction(
            kind="direct_message",
            trigger=trigger,
            target=target,
            prompt=prompt,
        )

        outcome = await self._dispatch_action_via_host(action)
        if outcome is None:
            return False, prompt, True

        self._absorb_consequences(outcome)

        if outcome.delivered:
            self._last_pulse_ts = datetime.now(timezone.utc).timestamp()
            self._last_outbound_ts = self._last_pulse_ts
            logger.info("Pulse fired (%s)", trigger.kind)
        return outcome.delivered, prompt, True

    async def _dispatch_action_via_host(
        self, action: PulseAction,
    ) -> ActionOutcome | None:
        """Try ``host.dispatch_action`` first; fall back to
        ``host.dispatch_pulse`` for legacy hosts.

        Returns ``None`` only when both paths raised — every successful
        return (even ``ActionOutcome(delivered=False)``) yields a real
        outcome the caller can log + absorb.
        """
        # Prefer the modern hook if the host implements it.
        modern = getattr(self._host, "dispatch_action", None)
        if callable(modern):
            try:
                result = modern(action)
                if asyncio.iscoroutine(result):
                    outcome = await result
                else:
                    outcome = result
                if not isinstance(outcome, ActionOutcome):
                    logger.warning(
                        "dispatch_action(%s) returned %r, expected ActionOutcome",
                        action.kind, type(outcome).__name__,
                    )
                    return None
                return outcome
            except NotImplementedError:
                # Host explicitly declared dispatch_action unimplemented.
                # Fall through to the legacy path.
                pass
            except Exception:
                logger.exception("dispatch_action failed")
                return None

        # Legacy path. Only direct_message actions can be served here.
        if action.kind != "direct_message":
            logger.warning(
                "host has no dispatch_action and action.kind=%r is not "
                "direct_message — cannot dispatch",
                action.kind,
            )
            return ActionOutcome(delivered=False, note="legacy_host_no_action_support")

        target = action.target
        if target is None:
            return ActionOutcome(delivered=False, note="no_target")

        try:
            result = self._host.dispatch_pulse(target, action.trigger, action.prompt)
            if asyncio.iscoroutine(result):
                fired = await result
            else:
                fired = bool(result)
            return ActionOutcome(delivered=fired)
        except Exception:
            logger.exception("dispatch_pulse failed")
            return None

    def _absorb_consequences(self, outcome: ActionOutcome) -> None:
        """Feed :py:attr:`ActionOutcome.consequences` back into physics.

        This is the learning loop. Without an injected ``physics`` ref
        at engine construction, consequences are warned-and-dropped
        (once) and the loop stays open."""
        if not outcome.consequences:
            return
        if self._physics is None:
            if not self._consequences_warned:
                logger.warning(
                    "ActionOutcome had %d consequences but PulseEngine was "
                    "constructed without physics= — dropping. Pass physics=plugin.physics "
                    "(or your EmotionalPhysics instance) to close the learning loop.",
                    len(outcome.consequences),
                )
                self._consequences_warned = True
            return
        for score in outcome.consequences:
            try:
                self._physics.ingest(score)
            except Exception:
                # Soft-fail: a single bad consequence must not crash the engine.
                logger.exception(
                    "Failed to ingest consequence Score (patterns=%s) — skipping",
                    score.patterns,
                )

    def _log_pulse_outcome(
        self, *, snap: dict, trigger_kind: str | None,
        suppressed_reason: str | None,
        target_present: bool, dispatched: bool, prompt: str | None,
    ) -> None:
        """Build a PulseRecord and ship it to the configured sink.

        Soft-fail: a logger that raises must not propagate into the
        engine. The :py:class:`SqliteEventLog` impl already catches;
        this is defense in depth for custom sinks."""
        if self._event_log is None:
            return
        try:
            from clanker_soul.eventlog import PulseRecord
            rec = PulseRecord(
                ts=datetime.now(timezone.utc).timestamp(),
                agent_id=self._agent_id or "",
                snap=snap,
                trigger_kind=trigger_kind,
                suppressed_reason=suppressed_reason,
                target_present=target_present,
                dispatched=dispatched,
                prompt=prompt,
            )
            self._event_log.log_pulse(rec)
        except Exception:
            logger.exception("event_log.log_pulse raised — engine continuing")

    async def _fire_due_reminders(self) -> None:
        try:
            due = self._host.due_reminders() or []
        except Exception:
            logger.exception("due_reminders failed")
            return

        if not due:
            return

        for reminder in due:
            try:
                target = self._host.most_recent_target()
            except Exception:
                logger.exception("most_recent_target failed (reminder)")
                continue
            if target is None:
                logger.warning(
                    "Reminder %r fired but no target available",
                    reminder.get("message"),
                )
                continue
            try:
                result = self._host.deliver_reminder(target, reminder)
                if asyncio.iscoroutine(result):
                    await result
                self._last_outbound_ts = datetime.now(timezone.utc).timestamp()
            except Exception:
                logger.exception("deliver_reminder failed")


__all__ = ["PulseEngine"]
