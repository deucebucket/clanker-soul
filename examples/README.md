# clanker-soul examples

Runnable scripts that show how to wire clanker-soul into a real host. Each
file is self-contained (no shared imports across examples) and runs with a
plain `python examples/NN_*.py` after `pip install -e ".[ui]"`.

## What's here

| File | What it shows |
|---|---|
| [`01_minimal.py`](01_minimal.py) | The smallest possible integration: `SoulPlugin(agent_id, db_path)`, ingest a few `Score`s, print the state-context block the agent reads each turn. |
| [`02_async_host.py`](02_async_host.py) | Async ticker that calls `plugin.tick()` (drift + reload_overrides) on each iteration. Demonstrates that there's no separate async API surface — the same context manager + methods work in both sync and async. |
| [`03_custom_event_sink.py`](03_custom_event_sink.py) | Implementing the `EventLog` Protocol from scratch — an ndjson sink that ships ingest + pulse records to a file instead of (or in addition to) `SqliteEventLog`. Demonstrates the soft-fail invariant: logging failures must not raise into the engine. |
| [`04_pulse_host.py`](04_pulse_host.py) | Smallest possible `PulseHost`: a stdout-only host that satisfies the protocol's six hooks. Drives mood far below soul to fire a `distress` pulse and prints the synthetic self-prompt the agent would read. |

## How to run

```bash
pip install -e ".[ui]"          # one-time setup
python examples/01_minimal.py   # any of the four
```

Each script writes its `soul.db` (or ndjson log) to a fresh tmpdir and
prints the path. Inspect the resulting state with the dashboard if you
want a visual:

```bash
clanker-soul ui --db /tmp/clanker-soul-exNN-XXXXXX/soul.db
```

## Patterns worth copying

- **`with SoulPlugin(...) as plugin:`** is the recommended entry point.
  It wires `EmotionalPhysics` + `SoulStore` + `SqliteEventLog` +
  `ConfigOverrides` for you and auto-saves on exit. Direct
  `EmotionalPhysics(...)` construction is still supported (see
  `03_custom_event_sink.py`) but is "advanced usage" — most hosts want
  the plugin.
- **`plugin.tick()`** is the once-per-loop housekeeping call. It runs
  both `reload_overrides` (so live UI changes take effect) and
  `soul_drift` (so sustained mood reshapes Soul). Idempotent and cheap.
- **`EventLog` is a Protocol** (`runtime_checkable`). Anything with
  `.log_ingest()` and `.log_pulse()` satisfies it. No subclassing, no
  registration. Wrap multiple sinks in a fan-out class to log to N
  destinations at once.
- **`PulseHost` hooks may be sync OR async.** The engine uses
  `asyncio.iscoroutine()` discipline rather than wrapping. Return what's
  natural for your codebase.
- **Soft-fail invariant.** Storage and event-log failures must warn and
  continue, never raise into ingest. Custom event sinks should follow
  the pattern in `03_custom_event_sink.py`.
