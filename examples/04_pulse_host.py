"""04 · Minimum PulseHost — the smallest thing PulseEngine can drive.

Run:
    python examples/04_pulse_host.py

What it does:
- defines StdoutPulseHost: prints to stdout instead of touching a real
  channel (no Slack, no Telegram, no XMPP — just stdout)
- spins up a SoulPlugin and a PulseEngine wired into the host
- ingests an event sequence designed to trigger 'distress'
  (sustained low V/W against a high-W soul = wide gap → distress fires)
- calls engine.tick() once and prints the dispatched pulse

What it shows:
- PulseEngine is host-agnostic. clanker-soul never invents recipients,
  never knows what your message dataclass is, never imports your
  channel layer. The host implements PulseHost; the engine calls into
  it.
- All five hooks (snapshot, slow_drift_tick, most_recent_target,
  dispatch_pulse, due_reminders, deliver_reminder) can be sync OR
  async. The engine uses asyncio.iscoroutine() rather than wrapping —
  return what's natural for your codebase.
- engine.tick() returns the Trigger that fired (or None). Useful for
  tests + host-driven schedulers. Production hosts call engine.start()
  / engine.stop() and let the internal asyncio loop drive ticks at
  PulseConfig.interval_s.
"""
from __future__ import annotations

import asyncio
import tempfile
from dataclasses import replace
from pathlib import Path

from clanker_soul import Score, SoulPlugin, SoulState
from clanker_soul.pulse import PulseConfig, PulseEngine, PulseTarget, Trigger


class StdoutPulseHost:
    """Smallest PulseHost: prints to stdout, never errors out."""

    def __init__(self, plugin: SoulPlugin) -> None:
        self._plugin = plugin

    # ---- core protocol hooks -------------------------------------------------

    def snapshot(self) -> dict:
        return self._plugin.snapshot()

    def slow_drift_tick(self) -> None:
        self._plugin.tick()

    def most_recent_target(self) -> PulseTarget | None:
        # In a real host this would be the freshest chat. Here we
        # always return a single canonical target so the engine has
        # somewhere to dispatch.
        return PulseTarget(payload={"channel": "stdout", "user": "demo"})

    def dispatch_pulse(self, target: PulseTarget, trigger: Trigger,
                      prompt: str) -> bool:
        # Sync return — the engine handles both sync and async.
        print(f"\n[PULSE FIRED] kind={trigger.kind!r} → target={target.payload}")
        print("synthetic self-prompt (the agent reads this and responds in its")
        print("own voice — clanker-soul does not generate the user-facing text):")
        print("-" * 60)
        print(prompt)
        print("-" * 60)
        return True  # successful delivery

    def due_reminders(self) -> list[dict]:
        return []  # no reminders in this minimal example

    def deliver_reminder(self, target: PulseTarget, reminder: dict) -> None:
        pass  # never called since due_reminders is empty


async def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="clanker-soul-ex04-"))
    db = tmp / "soul.db"
    print(f"db: {db}\n")

    # Use a strong-W default soul so the wide gap on distress is obvious.
    with SoulPlugin(
        agent_id="pulse-agent",
        db_path=db,
        default_soul=SoulState(v=145, a=110, d=160, u=80, g=130, w=200, i=135),
    ) as plugin:
        host = StdoutPulseHost(plugin)
        # Make distress and gratitude triggers easy to fire so the demo
        # is short. Production thresholds in PulseConfig() are looser.
        engine = PulseEngine(
            host,
            config=replace(
                PulseConfig(),
                min_quiet_seconds=0.0,         # disable cooldown for demo
                distress_v_drop=15,            # easier than default 30
                distress_w_drop=15,
            ),
            event_log=plugin.event_log,
            agent_id=plugin.agent_id,
        )

        # Drive mood far below soul on V and W → distress conditions.
        for _ in range(8):
            plugin.ingest(Score(
                v=40, w=40, u=200,
                patterns=("ABANDONMENT", "CRITICISM"),
                direction="SELF_DIRECTED",
            ))

        # Suppress the 'long_silence' trigger (which would fire first
        # since _last_outbound_ts defaults to 0 = "silent since epoch").
        # Real hosts call note_outbound() whenever ANY message ships.
        engine.note_outbound()

        # One tick is all we need to evaluate triggers + dispatch.
        trigger = await engine.tick()
        if trigger is None:
            print("no pulse fired — gap not wide enough or cooldown active")
        else:
            print(f"\nengine.tick() returned: {trigger.kind!r}")


if __name__ == "__main__":
    asyncio.run(main())
