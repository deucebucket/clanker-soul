# Changelog

All notable changes to `clanker-soul` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] — 2026-05-09

The drop-in plugin release. `pip install clanker-soul` + six lines of code now gets any
agent framework a fully-wired VADUGWI runtime with persistent soul, durable event log,
live-tunable knobs, and personality presets.

### Changed
- **Refactor: split monolithic modules into focused subpackages** (#21). `physics.py`,
  `pulse.py`, `soul.py`, and `eventlog.py` are now subpackages with one concept per file:
  - `physics/{config,math,tick,engine}.py`
  - `pulse/{config,triggers,host,prompt,engine}.py`
  - `soul/{state,reservoirs,store}.py`
  - `eventlog/{records,protocol,sqlite}.py`
  Public API preserved exactly through re-export `__init__.py` files — `from
  clanker_soul.physics import EmotionalPhysics` and `from clanker_soul.soul import SoulState`
  keep working unchanged. No behavior changes; pure file reorganization. All 124 existing
  tests pass without modification.

### Added
- `CLAUDE.md` — guidance for Claude Code agents working in this repo.
- `CHANGELOG.md` — this file.
- `.github/` issue + PR templates.
- **Schema v0.2** (#1): `SoulStore` now creates three additional tables alongside `soul_state` —
  `events` (full `PhysicsTick` history), `config_overrides` (live-tunable knobs), and
  `pulse_log` (every `PulseEngine` evaluation). Composite `(agent_id, ts DESC)` indexes on
  `events` and `pulse_log` for fast UI queries. Schema is created idempotently and upgrades
  v0.1 databases (which only had `soul_state`) in place without data loss.
- `SoulStore.connection` and `SoulStore.lock` properties so sibling modules can share the
  same SQLite connection and write lock (avoids second-handle contention).
- **`clanker-soul` CLI** (#8): minimal local-ops surface for v0.2 soul.db files.
  - `clanker-soul info --db PATH` — db size, table row counts, agent ids, oldest/newest event timestamps
  - `clanker-soul prune --db PATH --before YYYY-MM-DD [--agent-id X] [-y]` — deletes events + pulses older than the date; refuses without `-y`; supports per-agent scoping
  - `clanker-soul ui --db PATH [--agent-id X] [--port 7900]` — Phase-2 stub today; auto-dispatches to `clanker_soul.ui.launch` once that subpackage exists
  - Wired through `[project.scripts]` in `pyproject.toml` so `pip install -e .` registers the binary.
- **Phase 1 integration test suite** (#7): `tests/test_phase1_integration.py` exercises
  the full drop-in promise end-to-end — full lifecycle (construct, preset apply, ingest
  warm and harsh events, verify event log, switch personality at runtime, persist,
  reopen, verify state and log survive); multi-agent isolation against a shared DB;
  PulseEngine driven by a SoulPlugin's snapshot with shared event log captures pulse
  decisions; package-level imports cover all Phase 1 names. If this file fails, Phase 1
  is broken.
- **`SoulPlugin` — the documented one-call drop-in entry point** (#6). Wraps physics +
  storage + event log + overrides into a single class. `pip install clanker-soul` and
  six lines of code now gets a host a fully-loaded VADUGWI runtime: construct, ingest,
  tick, snapshot, save, close. Context-manager form auto-saves on exit. `event_log=`
  accepts `True` (SqliteEventLog), `False` (NullEventLog), or any custom EventLog
  implementation. `default_soul=` is used only when the agent has no saved row.
  Direct `EmotionalPhysics` usage is still supported for advanced hosts.
- **`clanker_soul.presets` module** (#5): four built-in `Preset` bundles bundling a
  `SoulState` + `PhysicsConfig` for distinct agent personalities.
  - `CHILD` — easily influenced; low W/D, high A/I, ungrounded G; faster soul drift
  - `ADULT` — package defaults; competent and settled
  - `BRITTLE` — feels every event; armor cap turned WAY down, low breach threshold
  - `STOIC` — slow to move; high armor cap, low blend, fast mood decay
  - `Preset.apply(overrides, agent_id)` writes the full physics + personality-soul
    bundle (excluding bookkeeping fields) so switching presets is a clean replacement,
    not a merge.
  - `clanker_soul.PRESETS` exposes all four by name for UI dropdowns.
- **`clanker_soul.overrides` module** (#4): live-tunable `PhysicsConfig` + `SoulState`
  surface for the UI. `OverrideBundle` is a frozen partial-fields dataclass; `ConfigOverrides`
  reads/writes the v0.2 `config_overrides` table; `apply_overrides()` is a pure merge
  function. `EmotionalPhysics` accepts an optional `overrides=` kwarg and gains a
  `reload_overrides()` method that applies bundle deltas in-place. Field-level reversion:
  removing a previously-overridden field restores it to its constructor value, while
  fields that were never overridden (and may have drifted) are left alone — drift is
  preserved across reload calls. Unknown override keys are logged at WARNING and ignored
  for forward-compat.
- **EventLog wiring** (#3): `EmotionalPhysics` and `PulseEngine` now accept optional
  `event_log` + `agent_id` constructor kwargs. When provided, every `ingest()` call emits
  one `IngestRecord` (with `mood_before`/`mood_after`, `soul_before`/`soul_after`,
  weight/armor/breach math, and a pre-baked human-readable `why` string), and every
  `tick()` evaluation emits one `PulseRecord` (fired, suppressed by `cooldown`,
  `no_target`, `dispatch_failed`, or `no_trigger`). Defaults preserve existing behavior:
  `event_log=None` means no logging, no agent_id required, and no API change observable
  to existing callers. Defense-in-depth: physics catches sink exceptions even though
  `SqliteEventLog` already does — custom sinks must not be able to crash physics.
- `EmotionalPhysics.ingest(event, *, raw=...)` keyword arg lets hosts that apply
  `mood_prime_score` themselves record both the pre-prime `raw` and the primed `event`
  in the log. Omitting `raw` records the score as raw with `primed=None`.
- **`clanker_soul.eventlog` module** (#2): durable per-event sink for the UI to read.
  Frozen `IngestRecord` and `PulseRecord` dataclasses, an `EventLog` runtime-checkable
  Protocol, a `NullEventLog` noop default, and a `SqliteEventLog` impl that writes via
  the shared `SoulStore` connection + lock. **Soft-fail invariant:** logging errors warn
  and continue, never raise into physics. Read helpers (`read_ingest`, `read_pulse`,
  `count_ingest`, `count_pulse`) return records most-recent-first with optional limit.

## [0.1.0] — 2026-05-08

### Added
- Initial extraction from CARL.
- Three-layer VADUGWI runtime: `Score` (conversational), `EmotionalPhysics` (mood),
  `SoulState` + `SoulStore` (persistent baseline).
- `TraumaReservoir` and `NourishmentReservoir` with 14-day half-life.
- Host-agnostic `PulseEngine` driven by a `PulseHost` protocol.
- Test suite covering physics, soul, score, and pulse triggers.

[Unreleased]: https://github.com/deucebucket/clanker-soul/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/deucebucket/clanker-soul/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/deucebucket/clanker-soul/releases/tag/v0.1.0
