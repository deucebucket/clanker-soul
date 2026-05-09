# clanker-soul

The 3-layer VADUGWI runtime. Persistent emotional state for LLM agents — extracted from [CARL](https://github.com/deucebucket/carl) so any agent runtime can carry mood across actions instead of restarting from neutral every event.

> **VADUGWI** = Valence, Arousal, Dominance, Urgency, Gravity, self-Worth, Intent. Seven dimensions, each `0-255`, scored per event by whatever upstream system you trust (LLM, [clanker-lang](https://github.com/deucebucket/clanker), hand-written rules). clanker-soul is what *happens* to those scores after they arrive.

## Why

Most "AI mood" code holds a single vector and overwrites it on every new score. That works for one-off classification but produces incoherent agents over a session: every action lands on a fresh emotional baseline, every wound is re-experienced from neutral, and a string of small bad events doesn't accumulate the way it does in a real mind.

clanker-soul adds three layers between "raw score" and "what the model sees":

| Layer | Timescale | Purpose |
|---|---|---|
| **Conversational** (`Score`) | per-event | The score itself. Whatever produced it is the host's concern. |
| **Mood** (`EmotionalPhysics`) | minutes–hours | Working state. Blends new events, cushions small hits via *dim-resilience*, drifts back toward soul, and tints the next perception via *mood-prime*. |
| **Soul** (`SoulState`) | days–weeks | The persistent baseline. Mood drifts toward it. Heavy events during an unhealed wound bypass the slow filter and leak straight into Soul (the *breach* mechanic). Survives restarts via SQLite. |

Two sidecars run alongside Soul:

- **`TraumaReservoir`** / **`NourishmentReservoir`** — pattern-keyed, 14-day half-life. Lets "the same wound poked again" be detected differently from "many unrelated bad days."
- **`PulseEngine`** — host-agnostic asyncio loop that decides when the agent should *say something on its own* based on mood/soul distance, trauma load, sustained nourishment, or long silence.

## Install

```bash
pip install -e .
# or with dev deps for testing
pip install -e ".[dev]"
```

Python 3.10+. Zero runtime dependencies (stdlib only).

## Quick start (recommended)

`SoulPlugin` is the documented entry point. It wires physics + persistence + event log + live-tunable overrides into one class so you don't have to.

```python
from clanker_soul import Score, SoulPlugin

with SoulPlugin(agent_id="my-agent", db_path="./soul.db") as plugin:
    plugin.ingest(Score(v=80, w=50, patterns=("ABANDONMENT",)))
    plugin.tick()                     # reload UI overrides + drift soul
    print(plugin.snapshot())          # PulseHost-compatible dict
# auto-saves on context exit
```

Every `ingest()` is logged to SQLite with full mood-before/mood-after, soul-before/soul-after, weight/armor/breach math, and a human-readable `why` string. Every `tick()` evaluation by a `PulseEngine` (when wired up) is logged similarly. The UI in Phase 2 will read this log.

### Personality presets

Soul can start anywhere. The package ships four bundles you can apply with one call:

```python
from clanker_soul import CHILD, BRITTLE, STOIC, SoulPlugin

with SoulPlugin(agent_id="kid", db_path="./soul.db",
                default_soul=CHILD.soul) as plugin:
    CHILD.apply(plugin.overrides, "kid")
    plugin.tick()  # picks up the preset
    # plugin is now child-shaped: low W/D, high A/I, fast soul drift
```

| Preset    | Personality |
|-----------|-------------|
| `CHILD`   | Easily influenced, eager, ungrounded. Low W/D, high A/I, fast soul drift. |
| `ADULT`   | Package defaults. Competent, settled. |
| `BRITTLE` | Feels every event. Armor cap and dim-resilience low. |
| `STOIC`   | Slow to move. High armor cap, low blend, fast mood decay. |

Build your own — `Preset` is a tiny `(name, description, soul, config)` dataclass.

### What gets logged

When `event_log=True` (default), every ingest writes one row to the `events` table with these fields: `ts`, `agent_id`, `raw_score`, `primed_score` (nullable), `mood_before` (nullable for first event), `mood_after`, `soul_before`, `soul_after`, `weight_raw`, `armor`, `weight_effective`, `breached`, `breach_delta`, `patterns`, `classification`, `why`. The `why` field is a one-line human string like:

```
ABANDONMENT (weight=0.78); armor=0.55 → w_eff=0.42; mood was 52pt from soul → BREACH (Δ=0.071 to soul.v/w/g)
```

`PulseEngine` evaluations land in `pulse_log` with the snap, trigger kind (or `None`), suppressed reason (`cooldown` / `no_target` / `dispatch_failed` / `no_trigger`), target presence, dispatch outcome, and the synthetic prompt that was generated.

### Live-tunable knobs

The UI (Phase 2) writes to `config_overrides` rows; `plugin.tick()` calls `reload_overrides()` to pick them up without restart. Removing an override field reverts that field to the constructor value. Soul fields that were never overridden (and may have drifted) are left alone.

```python
from clanker_soul import ConfigOverrides

plugin.overrides.set("my-agent",
                     physics={"blend_alpha": 0.8},
                     soul={"w": 200})
plugin.tick()  # changes are now live
```

### CLI

`pip install` registers a `clanker-soul` binary:

```bash
clanker-soul info  --db ./soul.db
clanker-soul prune --db ./soul.db --before 2026-01-01 -y
clanker-soul prune --db ./soul.db --before 2026-01-01 --agent-id alice -y
clanker-soul ui    --db ./soul.db   # Phase 2 stub today
```

## Advanced usage

If you need to bypass persistence or compose your own event log, use `EmotionalPhysics` directly:

```python
from clanker_soul import EmotionalPhysics, PhysicsConfig, Score, SoulState

physics = EmotionalPhysics(soul=SoulState(), config=PhysicsConfig())
physics.ingest(Score(v=80, a=160, d=70, u=180, g=90, w=85, i=120,
                     patterns=("ABANDONMENT",)))
print(physics.mood)            # working state, post-blend, post-resilience
print(physics.soul)            # baseline (mostly unchanged unless a breach hit)
print(physics.trauma.load())   # decayed sum across all patterns
```

### Persistence (low-level)

```python
from pathlib import Path
from clanker_soul import SoulStore

store = SoulStore.get(Path("/var/lib/myagent/soul.db"))
soul, trauma, nourishment = store.load("agent-id")
# ...run physics...
store.save("agent-id", soul, trauma, nourishment)
```

`SoulStore.get(path)` returns a process-wide singleton per path. For tests, construct `SoulStore(tmp_path)` directly — no implicit defaults, no global state.

### PulseEngine

The pulse engine is host-agnostic. Implement `PulseHost`:

```python
from clanker_soul import PulseEngine, PulseHost, PulseTarget, Trigger

class MyHost:
    def snapshot(self) -> dict:
        # return {"soul": {...}, "mood": [...], "soul_distance": float,
        #         "trauma_load": float, "nourishment_load": float}
        ...

    def slow_drift_tick(self) -> None:
        # called every tick — run physics.tick() or equivalent
        ...

    def most_recent_target(self) -> PulseTarget | None:
        # return whoever the agent should reach out to, or None to stay quiet
        ...

    async def dispatch_pulse(self, target, trigger: Trigger, prompt: str) -> bool:
        # run `prompt` through your agent pipeline, send the response,
        # return True on success
        ...

    def due_reminders(self) -> list[dict]:
        return []

    def deliver_reminder(self, target, reminder: dict) -> None:
        ...

engine = PulseEngine(MyHost())
await engine.start()
# ...later...
await engine.stop()
```

The engine never invents recipients, never knows about your message dataclass, and never imports your channel layer. It just decides when and why to fire, and asks the host to do it.

## Design choices worth knowing about

**Soul defaults are not neutral 128.** A fresh agent biases mildly positive (V=145), in-control (D=160), with strong worth (W=175). Neutral input should not read as depression. Override per-agent at construction time.

**Failures are loud.** No swallowed exceptions in the physics or store layers. If the SQLite save fails the call returns and a warning logs; mood doesn't silently desync from disk.

**Mood-prime is the actual context-carrying piece.** Most "emotional state" code only ever pulls toward soul. `mood_prime_score(raw_event, current_mood, factor)` tints the *next* perception with the residue of the last one, which is what makes a sequence of events feel like a sequence rather than a series of independent observations.

**Dim-resilience is weight-gated.** Small events get cushioned toward soul; heavy events still land. Without the gate, a soul-armored agent becomes immune to genuinely big things.

## Hosts

CARL is the reference host. clanker-soul versions in CARL-master may temporarily diverge while changes are being upstreamed — `clanker_soul` here is the canonical module.

If you build something on top of this, open a PR adding it to the README.

## License

[AGPL-3.0](LICENSE). If you run a hosted service that exposes a clanker-soul-derived agent over a network, you must offer the source.

## Related

- **[clanker](https://github.com/deucebucket/clanker)** — the VADUGWI scoring engine (a separate concern from emotional dynamics).
- **[CARL](https://github.com/deucebucket/carl)** — the reference agent runtime that consumes this package.
