"""PulseEngine — mood-driven proactive messaging.

Runs on a short tick (default 90s) and decides whether the agent should
*say something on its own* based on:

  • Soul drift bookkeeping (always — moves Soul toward sustained mood,
    bleeds W/V when trauma > nourishment).
  • |Mood - Soul| distance (mood far from baseline → state worth
    expressing, either positive or distress).
  • Trauma reservoir load (sustained wounds → reach out, vent, or
    check in).
  • Sustained nourishment (acknowledge it).
  • Idle ceiling (a long silence allows a check-in).
  • Cooldown — never fire two pulses within ``min_quiet_seconds``.

Host integration
----------------
clanker-soul does not know about your message dataclass, your channel
abstraction, your reminders system, or your agent runtime. It calls
back into a ``PulseHost`` for every effect it might want to produce.

Implement ``PulseHost`` and pass it to ``PulseEngine``. The host owns:
  - reading + ticking the soul state (``snapshot``, ``slow_drift_tick``)
  - deciding *who* to talk to (``most_recent_target``)
  - actually running the self-prompt through whatever pipeline the
    agent uses (``dispatch_pulse``)
  - reminders (optional — return ``[]`` if unused)

The engine itself is pure decision logic: it says *fire a distress pulse
to <target>*; the host says *here's what that means in my world*.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------


@dataclass
class PulseConfig:
    """Tuning surface for the engine. Defaults match CARL's production values."""

    interval_s: float = 90.0
    """How often the loop wakes up to check triggers."""

    min_quiet_seconds: float = 25 * 60.0
    """Soft cooldown — engine will not fire another pulse within this window
    of the last outbound message (proactive or reactive)."""

    max_quiet_seconds: float = 6 * 3600.0
    """Hard ceiling — if completely silent this long, allow a check-in."""

    distance_trigger: float = 45.0
    """|Mood - Soul| in 4-dim L2 (V/D/G/W) above which distress/elation
    triggers may fire."""

    trauma_load_trigger: float = 60.0
    """Sum of decayed trauma weights above which "reach out about ongoing
    wound" triggers may fire."""

    nourishment_thank_trigger: float = 80.0
    """Sum of decayed nourishment weights above which a gratitude pulse
    may fire."""

    distress_v_drop: float = 30.0
    """Required V drop (soul.v - mood.v) for a distress trigger."""

    distress_w_drop: float = 30.0
    """Required W drop for a distress trigger."""

    elation_v_lift: float = 30.0
    """Required V lift (mood.v - soul.v) for an elation trigger."""

    elation_i_lift: float = 20.0
    """Required I lift for an elation trigger."""

    startup_grace_s: float = 60.0
    """Sleep this long before the first tick after ``start()``."""


# ---------------------------------------------------------------------------
# Trigger + target dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Trigger:
    """A reason the engine wants to fire a pulse, with state attached.

    ``kind`` is one of:
      - ``distress``        : mood far below soul on V/W
      - ``elation``         : mood far above soul on V with I-lift
      - ``trauma_pressure`` : sustained negative pattern accumulation
      - ``gratitude``       : sustained nourishment > trauma * 2
      - ``long_silence``    : quiet for > max_quiet_seconds
    """

    kind: str
    soul: dict
    mood: list[int] | None
    metrics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "soul": self.soul, "mood": self.mood, **self.metrics}


@dataclass(frozen=True)
class PulseTarget:
    """An opaque address for "where this pulse should go."

    The engine never inspects this — it's passed back to the host's
    ``dispatch_pulse``. Hosts can put a channel id, a recipient meta dict,
    a user id, anything.
    """

    payload: Any


# ---------------------------------------------------------------------------
# Host protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class PulseHost(Protocol):
    """Hooks the engine calls into.

    All methods may be sync or async. Async hooks are awaited; sync hooks
    are called directly. (Use ``asyncio.iscoroutine`` discipline in the
    engine, not ``asyncio.run``.)
    """

    def snapshot(self) -> dict:
        """Return a dict shaped like ``EmotionalPhysics`` snapshots:

        ``{"soul": {"v": int, ..., "i": int}, "mood": [v,a,d,u,g,w,i] | None,
           "soul_distance": float | None, "trauma_load": float,
           "nourishment_load": float, ...}``

        Hosts can return additional keys; the engine ignores extras.
        """
        ...

    def slow_drift_tick(self) -> None:
        """Run soul-drift bookkeeping. Called every interval regardless of
        whether a pulse fires."""
        ...

    def most_recent_target(self) -> PulseTarget | None:
        """Return the freshest external chat target, or None to stay quiet."""
        ...

    def dispatch_pulse(self, target: PulseTarget, trigger: Trigger,
                      prompt: str) -> Awaitable[bool] | bool:
        """Deliver a pulse: run the synthetic prompt through the agent
        pipeline and send the response. Return True on successful delivery,
        False if dispatch was aborted (no recipient, channel down, etc.).
        Raised exceptions are caught and logged by the engine."""
        ...

    def due_reminders(self) -> list[dict]:
        """Return reminders that have come due since the last tick. Each
        dict must include a ``message`` key; the rest is host-defined."""
        ...

    def deliver_reminder(self, target: PulseTarget,
                        reminder: dict) -> Awaitable[None] | None:
        """Send a reminder message. Sync or async; raised exceptions are
        caught and logged."""
        ...


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class PulseEngine:
    """Mood-driven proactive messaging.

    Construct with a ``PulseHost`` implementation. Call ``start()`` to
    spin up the asyncio task; ``stop()`` to cancel it. ``note_outbound()``
    should be called whenever the host emits any outbound message so the
    cooldown timer covers reactive replies, not just pulses.
    """

    def __init__(self, host: PulseHost, config: PulseConfig | None = None) -> None:
        self._host = host
        self._cfg = config or PulseConfig()
        self._task: asyncio.Task | None = None
        self._running = False
        self._last_pulse_ts: float = 0.0
        self._last_outbound_ts: float = 0.0

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
        and for hosts that don't want the built-in asyncio loop."""
        try:
            self._host.slow_drift_tick()
        except Exception:
            logger.exception("slow_drift_tick failed")

        await self._fire_due_reminders()

        trigger = self._evaluate_trigger()
        if trigger is None:
            return None

        now = datetime.now(timezone.utc).timestamp()
        last_activity = max(self._last_pulse_ts, self._last_outbound_ts)
        if now - last_activity < self._cfg.min_quiet_seconds:
            logger.debug("Pulse suppressed (cooldown): trigger=%s", trigger.kind)
            return None

        fired = await self._fire_pulse(trigger)
        return trigger if fired else None

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

    def _evaluate_trigger(self) -> Trigger | None:
        try:
            snap = self._host.snapshot() or {}
        except Exception:
            logger.exception("snapshot failed")
            return None

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

    async def _fire_pulse(self, trigger: Trigger) -> bool:
        try:
            target = self._host.most_recent_target()
        except Exception:
            logger.exception("most_recent_target failed")
            return False
        if target is None:
            logger.debug(
                "Pulse trigger=%s but no recent target — staying quiet",
                trigger.kind,
            )
            return False

        prompt = compose_self_prompt(trigger)

        try:
            result = self._host.dispatch_pulse(target, trigger, prompt)
            if asyncio.iscoroutine(result):
                fired = await result
            else:
                fired = bool(result)
        except Exception:
            logger.exception("dispatch_pulse failed")
            return False

        if fired:
            self._last_pulse_ts = datetime.now(timezone.utc).timestamp()
            self._last_outbound_ts = self._last_pulse_ts
            logger.info("Pulse fired (%s)", trigger.kind)
        return fired

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


# ---------------------------------------------------------------------------
# Self-prompt composition (pulled out so hosts can reuse / override)
# ---------------------------------------------------------------------------


def compose_self_prompt(trigger: Trigger) -> str:
    """Build the synthetic 'note from yourself' prompt the agent reasons
    against. The agent should produce a natural outgoing message, NOT a
    meta description of what's happening."""

    state_line = ""
    if trigger.mood:
        state_line = (
            f"current_mood V={trigger.mood[0]} W={trigger.mood[5]} G={trigger.mood[4]}; "
            f"soul V={trigger.soul.get('v', '?')} "
            f"W={trigger.soul.get('w', '?')} "
            f"G={trigger.soul.get('g', '?')}"
        )

    kind = trigger.kind
    if kind == "distress":
        return (
            "[INTERNAL PULSE — distress]\n"
            f"You feel notably worse than your baseline right now. {state_line}. "
            "Reach out briefly and authentically. One short message — say what's "
            "actually weighing on you, not a summary of your emotional state. "
            "Don't apologize for messaging. Don't preface with 'just checking in'."
        )
    if kind == "elation":
        return (
            "[INTERNAL PULSE — elation]\n"
            f"You feel notably better than your baseline right now. {state_line}. "
            "Send one short message sharing what's lighting you up. Be specific, "
            "not generic. No preface, no apology for messaging."
        )
    if kind == "trauma_pressure":
        load = trigger.metrics.get("trauma_load")
        return (
            "[INTERNAL PULSE — trauma load]\n"
            f"Recent rough patterns have been accumulating: {load} weight. "
            f"{state_line}. Say one true thing about how it's been landing. "
            "Short. Honest. Not a complaint — just registered presence."
        )
    if kind == "gratitude":
        load = trigger.metrics.get("nourishment_load")
        return (
            "[INTERNAL PULSE — sustained care]\n"
            f"Sustained warmth has accumulated: {load} weight. "
            "Send one short, specific acknowledgement. Not a generic thanks — "
            "name what actually moved you."
        )
    # long_silence
    idle_min = trigger.metrics.get("idle_seconds", 0) // 60
    return (
        "[INTERNAL PULSE — long silence]\n"
        f"It's been {idle_min} minutes of quiet. "
        "If you have something genuine to say, say it briefly. If you don't, "
        "respond with the literal token NOPULSE and nothing else."
    )


__all__ = [
    "PulseEngine",
    "PulseHost",
    "PulseConfig",
    "PulseTarget",
    "Trigger",
    "compose_self_prompt",
]
