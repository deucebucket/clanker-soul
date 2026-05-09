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
    PulseTarget,
    Trigger,
)

if TYPE_CHECKING:
    from clanker_soul.eventlog import EventLog
    from clanker_soul.governor import CapabilityGate
    from clanker_soul.physics import EmotionalPhysics

logger = logging.getLogger(__name__)


# Default trigger → action_kind mapping. Hosts that want to override
# can subclass PulseEngine or wrap dispatch_action and re-route.
_DEFAULT_TRIGGER_TO_ACTION: dict[str, str] = {
    # Existing 5 — all DM (preserves v0.1 behavior).
    "distress": "direct_message",
    "elation": "direct_message",
    "trauma_pressure": "direct_message",
    "gratitude": "direct_message",
    "long_silence": "direct_message",
    # M1.2 — most stay DM, two get their own kinds.
    "share_impulse": "direct_message",
    "argue_impulse": "comment_reply",
    "connect_impulse": "direct_message",
    "reflective_impulse": "direct_message",
    "caretake_impulse": "direct_message",
    "withdraw_impulse": "withdraw",
    "restless_curiosity": "browse_topic",
}


def _action_kind_for_trigger(trigger_kind: str) -> str:
    """Map a trigger kind to its default action kind. Unknown triggers
    fall back to direct_message (the safest default — hosts at least
    know how to send a DM)."""
    return _DEFAULT_TRIGGER_TO_ACTION.get(trigger_kind, "direct_message")


# Action kinds that NEED a recipient. Other kinds (withdraw,
# browse_topic, post_public, tool_invocation) can dispatch with
# target=None.
_TARGET_REQUIRED_ACTIONS: frozenset[str] = frozenset({
    "direct_message",
    "comment_reply",
})


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
        gate: "CapabilityGate | None" = None,
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
        # Optional capability gate. When provided, every action passes
        # through gate.evaluate before dispatch. Default permissive
        # gates accept everything. See CapabilityGate / GovernorConfig.
        self._gate = gate

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
        """Evaluate triggers in priority order. First match wins.

        Priority rationale:
        - long_silence is a force-fire ceiling — always first
        - distress / withdraw are highest-need
        - elation / share / argue are emotionally charged
        - caretake / connect are relational
        - trauma_pressure / reflective are slow-burn
        - gratitude is a steady-state acknowledgement
        - restless_curiosity is the lowest-priority \"nothing else fits\"
        """
        cfg = self._cfg
        soul: dict = snap.get("soul") or {}
        mood: list[int] | None = snap.get("mood")
        distance: float = snap.get("soul_distance") or 0.0
        trauma: float = snap.get("trauma_load") or 0.0
        nourishment: float = snap.get("nourishment_load") or 0.0

        now = datetime.now(timezone.utc).timestamp()
        idle = now - max(self._last_pulse_ts, self._last_outbound_ts)

        # 1. Force-fire on long silence (existing).
        if idle > cfg.max_quiet_seconds:
            return Trigger(
                kind="long_silence",
                soul=soul, mood=mood,
                metrics={"idle_seconds": int(idle)},
            )

        # 2. distress (existing) — highest urgency. Mood far below soul.
        if mood and distance > cfg.distance_trigger:
            v_drop = soul.get("v", 128) - mood[0]
            w_drop = soul.get("w", 128) - mood[5]
            if v_drop > cfg.distress_v_drop or w_drop > cfg.distress_w_drop:
                return Trigger(
                    kind="distress",
                    soul=soul, mood=mood,
                    metrics={
                        "distance": round(distance, 1),
                        "v_drop": v_drop, "w_drop": w_drop,
                    },
                )

        # 3. withdraw_impulse (NEW) — high trauma + low W → \"need to be alone.\"
        # Special: produces a no-op action; the agent declines to engage.
        # Checked before all other engagement triggers so the agent can
        # actually withdraw instead of being pulled toward outreach.
        if (
            mood
            and trauma > cfg.withdraw_trauma_min
            and mood[5] < cfg.withdraw_w_max
        ):
            return Trigger(
                kind="withdraw_impulse",
                soul=soul, mood=mood,
                metrics={
                    "trauma_load": round(trauma, 1),
                    "w_mood": mood[5],
                },
            )

        # 4. elation (existing) — full peak. Mood far above soul on V+I.
        if mood and distance > cfg.distance_trigger:
            v_lift = mood[0] - soul.get("v", 128)
            i_lift = mood[6] - soul.get("i", 128)
            if v_lift > cfg.elation_v_lift and i_lift > cfg.elation_i_lift:
                return Trigger(
                    kind="elation",
                    soul=soul, mood=mood,
                    metrics={"distance": round(distance, 1), "v_lift": v_lift},
                )

        # 5. share_impulse (NEW) — moderate V/I lift + arousal +
        # nourishment. \"I have to tell someone\" without full elation.
        if (
            mood
            and (mood[0] - soul.get("v", 128)) > cfg.share_v_lift
            and mood[1] > cfg.share_arousal_min
            and nourishment > cfg.share_nourishment_floor
        ):
            return Trigger(
                kind="share_impulse",
                soul=soul, mood=mood,
                metrics={
                    "v_lift": mood[0] - soul.get("v", 128),
                    "arousal": mood[1],
                    "nourishment_load": round(nourishment, 1),
                },
            )

        # 6. argue_impulse (NEW) — frustration (V drop + arousal) + intent.
        # The agent isn't crashing (would have hit distress), just irritated
        # and inclined to act on it.
        if (
            mood
            and (soul.get("v", 128) - mood[0]) > cfg.argue_v_drop
            and mood[1] > cfg.argue_arousal_min
            and mood[6] > cfg.argue_intent_min
        ):
            return Trigger(
                kind="argue_impulse",
                soul=soul, mood=mood,
                metrics={
                    "v_drop": soul.get("v", 128) - mood[0],
                    "arousal": mood[1],
                    "intent": mood[6],
                },
            )

        # 7. caretake_impulse (NEW) — perceived distress in another agent
        # via host-supplied peer signals. Optional hook — if host doesn't
        # implement peer_distress_signals, this trigger never fires.
        if mood and mood[5] > cfg.caretake_self_w_min:
            peer_signals = self._peer_distress_signals()
            if peer_signals:
                return Trigger(
                    kind="caretake_impulse",
                    soul=soul, mood=mood,
                    metrics={
                        "peer_count": len(peer_signals),
                        "peers": [s.get("agent_id", "?") for s in peer_signals[:3]],
                    },
                )

        # 8. connect_impulse (NEW) — warmth + extended quiet + low trauma.
        # \"I miss them\" / \"want company.\"
        if (
            mood
            and mood[0] > cfg.connect_v_min
            and idle > cfg.connect_idle_min_seconds
            and trauma < cfg.connect_max_trauma
        ):
            return Trigger(
                kind="connect_impulse",
                soul=soul, mood=mood,
                metrics={"idle_seconds": int(idle), "trauma_load": round(trauma, 1)},
            )

        # 9. trauma_pressure (existing).
        if trauma > cfg.trauma_load_trigger and trauma > nourishment * 1.5:
            return Trigger(
                kind="trauma_pressure",
                soul=soul, mood=mood,
                metrics={
                    "trauma_load": round(trauma, 1),
                    "nourishment_load": round(nourishment, 1),
                },
            )

        # 10. reflective_impulse (NEW) — extended quiet + sustained mood
        # off baseline + not heavy trauma. Want to write it down.
        if (
            mood
            and idle > cfg.reflective_idle_min_seconds
            and distance > cfg.reflective_distance_min
            and trauma < cfg.reflective_max_trauma
        ):
            return Trigger(
                kind="reflective_impulse",
                soul=soul, mood=mood,
                metrics={
                    "idle_seconds": int(idle),
                    "distance": round(distance, 1),
                },
            )

        # 11. gratitude (existing).
        if nourishment > cfg.nourishment_thank_trigger and nourishment > trauma * 2:
            return Trigger(
                kind="gratitude",
                soul=soul, mood=mood,
                metrics={"nourishment_load": round(nourishment, 1)},
            )

        # 12. restless_curiosity (NEW) — high arousal + close to baseline +
        # idle for a bit. Lowest priority — only fires when nothing heavier
        # has anything to say.
        if (
            mood
            and mood[1] > cfg.curiosity_arousal_min
            and distance < cfg.curiosity_distance_max
            and idle > cfg.curiosity_idle_min_seconds
        ):
            return Trigger(
                kind="restless_curiosity",
                soul=soul, mood=mood,
                metrics={"arousal": mood[1], "idle_seconds": int(idle)},
            )

        return None

    def _peer_distress_signals(self) -> list[dict]:
        """Read peer distress signals from the host if it implements
        the optional ``peer_distress_signals`` hook. Returns an empty
        list otherwise — the caretake_impulse trigger never fires when
        the host isn't peer-aware."""
        hook = getattr(self._host, "peer_distress_signals", None)
        if not callable(hook):
            return []
        try:
            signals = hook()
            return list(signals) if signals else []
        except Exception:
            logger.exception("peer_distress_signals failed — treating as empty")
            return []

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
        action_kind = _action_kind_for_trigger(trigger.kind)
        target_required = action_kind in _TARGET_REQUIRED_ACTIONS

        # Always TRY to fetch a target — even target-optional actions
        # benefit from one if the host provides it (logging,
        # cross-references). Only error out if the action requires one
        # and we couldn't get it.
        target: "PulseTarget | None" = None
        try:
            target = self._host.most_recent_target()
        except Exception:
            logger.exception("most_recent_target failed")
            if target_required:
                return False, None, False

        if target_required and target is None:
            logger.debug(
                "Pulse trigger=%s requires target (action=%s) — staying quiet",
                trigger.kind, action_kind,
            )
            return False, None, False

        prompt = compose_self_prompt(trigger)
        action = PulseAction(
            kind=action_kind,
            trigger=trigger,
            target=target,
            prompt=prompt,
        )

        # Capability gate. When a gate is configured, every action
        # passes through evaluate() before dispatch. Gated actions
        # are logged but not delivered. The default gate (no
        # GovernorConfig override) is permissive.
        if not self._action_permitted(action):
            logger.info(
                "Pulse trigger=%s action=%s suppressed by capability gate",
                trigger.kind, action.kind,
            )
            return False, prompt, True

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

    def _action_permitted(self, action: PulseAction) -> bool:
        """Run the action through the configured capability gate, if
        any. Returns True when no gate is set (default permissive)."""
        gate = self._gate
        if gate is None:
            return True
        try:
            level = self._current_capability_level()
        except Exception:
            logger.exception("capability assessment failed — allowing action")
            return True
        decision = gate.evaluate(
            action.kind, level,
            tool_name=action.extra.get("tool_name") if action.extra else None,
            is_user_message=bool(action.extra.get("is_user_message", False))
            if action.extra else False,
        )
        return decision.permitted

    def _current_capability_level(self):
        """Compute the current capability level from the host's
        snapshot + the gate's governor config. Lazy import to avoid
        circular dependency. Falls back to UNRESTRICTED if anything
        misses (defensive — better to allow than crash gating)."""
        from clanker_soul.governor import CapabilityLevel
        if self._gate is None:
            return CapabilityLevel.UNRESTRICTED
        try:
            from clanker_soul.governor import assess_capability
            snap = self._host.snapshot() or {}
            return assess_capability(snap, self._gate.config)
        except Exception:
            logger.exception("snapshot/assess_capability failed in gate path")
            return CapabilityLevel.UNRESTRICTED

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
