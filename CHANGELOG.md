# Changelog

All notable changes to `clanker-soul` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.8.2] — 2026-05-09

CI hotfix release. The `[ui]` extra was silently relying on a transitive
`python-multipart` install — local dev environments had it pulled in by
some other dep, but a clean `pip install clanker-soul[ui]` on a fresh
machine would fail at runtime as soon as any `Form(...)`-using route
(every config + simulate POST) was hit. Caught by the new CI workflow's
first run on a clean Ubuntu image. Fixed by adding `python-multipart`
to the `[ui]` extra explicitly.

### Fixed
- `[ui]` extra now declares `python-multipart>=0.0.7` so FastAPI's
  `Form(...)` parsing works on a clean install. Without this, every
  POST handler under `/config/*` and `/simulate/*` would 500 with
  `RuntimeError: Form data requires "python-multipart" to be installed`.

## [0.8.1] — 2026-05-09

Infrastructure patch. CI now runs on every push and PR, the package
ships PEP 561 type info, and a quietly-broken wheel (missing UI
templates) is fixed.

### Added
- **GitHub Actions CI** (#37) — `.github/workflows/ci.yml` runs `pytest`
  on Python 3.10/3.11/3.12/3.13 (matrix, fail-fast off) on every push to
  main and every PR. Concurrency cancels superseded runs on the same
  ref. Separate non-blocking ruff job surfaces lint/format issues
  without gating merges (will be promoted to required once we adopt
  ruff fully). README CI badge added.
- **PEP 561 `py.typed` marker** (#38) — `clanker_soul/py.typed` empty
  marker file ships in the wheel + sdist. Downstream type checkers
  (mypy, pyright) now consume clanker-soul's annotations directly
  instead of treating them as `Any`.

### Fixed
- Wheels were silently missing `clanker_soul/ui/templates/*.html` and
  the static dir — `pip install clanker-soul[ui]` would have failed at
  runtime when FastAPI tried to load templates. Added explicit
  `[tool.setuptools.package-data]` entry covering `py.typed`,
  templates, and static. Verified by inspecting the built wheel.

## [0.8.0] — 2026-05-09

The simulator release. The "what if I had tuned this differently?" tool.
Replay the agent's recent event history through a hypothetical
`SoulState` + `PhysicsConfig` and see the resulting trajectory side-by-
side with reality. Operators can then one-click apply the simulated
config to the live agent.

### Added
- **`clanker_soul.ui.simulator`** module (#29): `replay_events(records,
  soul, config)` returns a `SimResult` with paired real-vs-sim mood per
  step, end-state soul deviations per dim, and elapsed-ms timing. Pure
  function; no I/O. Engine sandboxed — no `event_log`, no `overrides`
  provider — guaranteeing the simulator can never write to the live DB.
- **`SimStep`**, **`SimResult`**, **`DimDeviation`** dataclasses for the
  paired trajectory output.
- **`parse_soul`** / **`parse_config`** form-parsing helpers with strict
  range validation (delegates to the same `PHYSICS_FIELDS` metadata as
  the config panel).
- **`GET /simulate`** route — operator form: agent picker, hypothetical
  starting `SoulState` sliders (V/A/D/U/G/W/I), hypothetical
  `PhysicsConfig` sliders (all 13 fields), event count input (1–1000).
  Form pre-fills with the agent's *current* live config so operators
  tweak from where they are, not from defaults.
- **`POST /simulate/run`** route — runs replay, returns the result
  fragment (HTMX-swapped into the page, no full reload).
- **`POST /simulate/apply`** route — explicit "apply this config to live
  agent" button. Writes only fields that *differ from defaults* to the
  override bundle, then 303-redirects to `/config` so the operator can
  see what landed.
- **`templates/{simulate,_simulate_result}.html`** — full page + result
  fragment. Result includes per-dim SVG sparklines (real polyline in
  violet, simulated in cyan, soul-baseline as a dashed gray line),
  end-state soul comparison table with colored deltas, and the apply
  button with a confirm guard.
- Decay-timing fidelity: replay backdates `_mood_time` between events
  using the real recorded `ts` gaps so mood-decay sees the wall-clock
  delta the agent actually experienced — not the back-to-back replay
  speed. Soul drift is replayed deterministically via the existing
  `soul_drift(now_ts=)` injected-clock parameter.
- Determinism guarantee: `replay_events` normalizes the starting soul's
  `last_drift_ts` to the first record's `ts` so two runs of the same
  input produce byte-identical output regardless of when they run.
- Nav in `base.html` enables the `simulate` link.

## [0.7.0] — 2026-05-09

The config panel release. The dashboard now lets operators tune every
`PhysicsConfig` field and every `SoulState` personality dim live, with
preset bundles for one-click personality reshapes. Every slider writes
immediately through the existing `ConfigOverrides` from #4 — this is
just the operator-facing surface for that engine.

### Added
- **`clanker_soul.ui.config`** module (#28): `FieldMeta` (per-slider
  range/step/description), `FieldRow`, `ConfigView` view dataclasses,
  plus `build_config_view(overrides, agent_id)`,
  `apply_field_override(...)`, `clear_field_override(...)`, and
  `coerce_value(meta, raw)` with strict range validation.
- **`PHYSICS_FIELDS`** (13 entries) and **`SOUL_FIELDS`** (V/A/D/U/G/W/I)
  field-metadata tuples. New physics fields auto-render once added to
  this tuple — no template churn.
- **`GET /config`** route — full operator page: agent picker, presets
  bar (`child` / `adult` / `brittle` / `stoic`), physics section with
  13 sliders, soul section with 7 sliders, override badges, per-field
  reset, and a `reset all` confirm-protected wipe.
- **`POST /config/override`** (HTMX, fires on slider `change`) — updates
  one field, validates range, returns the freshly rendered panel.
- **`POST /config/clear`** — drops one field if `section`+`field` given,
  or wipes the whole bundle if not.
- **`POST /config/preset`** — applies a named preset bundle.
- **`templates/{config,_config_panel}.html`** — full page + panel
  partial. Sliders show the current value, default value, override
  state, and (on hover) the field description.
- Nav in `base.html` enables the `config` link.

## [0.6.0] — 2026-05-09

The events log release. Forensic view of every ingest event the agent
has processed: sortable, filterable, paginated, with per-row drill-down
showing the full `IngestRecord`. This is the answer to "why did the
agent do that?"

### Added
- **`clanker_soul.ui.events`** module (#27): `query_events(store, agent_id, *,
  sort, classification, breach, pattern_q, ts_after, ts_before, page,
  page_size)` returns an `EventQueryResult` with rows + total count + pagination
  metadata. Pure read-only query against the `events` table.
- **`GET /events`** route — full forensic page: agent picker, filter form
  (classification, breach, pattern substring, ts range), sort dropdown
  (ts_desc/asc, weight_desc/asc, breach_first), paginated table, per-row
  `<details>` drill-down showing raw + primed score, mood-before/after,
  soul-before/after, source + direction, full weight/armor/breach math.
  HTMX-driven filter/sort/paginate via `partial=1` query param.
- **`templates/{events,_events_table}.html`** — full page + table partial
  for HTMX swaps. Pagination links preserve filter state.
- Nav in `base.html` enables the `events` link.

## [0.5.0] — 2026-05-09

The live panel release. Dashboard now shows the agent's actual current state:
SVG mood/soul radar, capability badge, crisis-emergency badge, trauma + nourishment
bars, last pulse decision (with prompt expansion), recent events with source
attribution, and the state-context string the agent reads each turn. Auto-refreshes
every 2s via HTMX polling.

### Added
- **`clanker_soul.ui.live`** module (#26): `build_live_view(store, agent_id)` reads
  on-disk state and assembles a `LiveView` dataclass with everything the template
  needs — including precomputed SVG radar geometry (`RadarPoint` / `RadarPolygon`
  / `RadarRing`).
- **`GET /snapshot?agent_id=X`** route — returns the live-panel HTML fragment.
  HTMX polls this every 2s with `hx-trigger="every 2s"` and swaps it into a div.
  Page chrome stays static; only the data-bearing region re-renders.
- **`templates/_live_panel.html`** — Jinja2 partial rendering: governor capability
  badge (color-coded by level), emergency badge if crisis_signal flags it,
  mood/soul SVG radar (cyan over violet polygons), 7-dim numeric breakdown,
  trauma/nourishment top-10-by-pattern bars, last pulse card with collapsible
  prompt, recent events list with source + direction tags + the `why` string,
  and the full state-context block the agent reads.
- **`create_app(governor_config=...)`** kwarg — dashboard reads under custom
  governor thresholds if the host wants stricter or laxer gating in the UI than
  the agent uses.

### Changed
- `templates/index.html` rewritten: agent picker stays at top, live panel polls
  via HTMX into a stable div. Initial server render embeds the snapshot inline
  so there's no flash-of-empty-content while HTMX warms up.

## [0.4.0] — 2026-05-09

The dashboard scaffold release. `pip install 'clanker-soul[ui]'` and
`clanker-soul ui --db ./soul.db` now opens a real FastAPI server with
a working landing page. Subsequent releases (0.5.x) add the live
panel, events log, config panel, and simulator routes on top.

### Added
- **`clanker_soul.ui` subpackage** (#25), gated behind a new `[ui]`
  optional dependency group (`fastapi`, `uvicorn[standard]`, `jinja2`,
  `httpx` for the test client).
  - `create_app(db_path, *, default_agent_id) -> FastAPI` — testable
    factory; can be mounted under any ASGI server.
  - `launch(db_path, *, agent_id, port, host, log_level)` —
    blocking uvicorn launcher; binds to `127.0.0.1:7900` by default
    (not network-exposed).
  - `templates/base.html` + `templates/index.html` — Jinja2 with
    Tailwind + HTMX via CDN; no Node toolchain.
  - Routes: `GET /` (landing page with agent picker), `GET /health`
    (JSON liveness probe).
- The `clanker-soul ui --db PATH` CLI subcommand now actually
  launches when the `[ui]` extra is installed (was a stub before).

### Changed
- `tests/` mirrors source: new `tests/ui/` directory.
- `tests/test_cli.py::test_ui_emits_install_hint_*` skips when
  `[ui]` is installed; the post-install behavior is covered by
  `tests/ui/test_scaffold.py`.

## [0.3.0] — 2026-05-09

The safety governor release. Emotional state now translates into operational restrictions
on what tools the agent can use — but the user-communication channel is never gated.
Plus cross-context emotional persistence with source attribution: the agent knows *why*
it feels what it feels.

### Added
- **`clanker_soul.governor` subpackage** (#30): VADUGWI Safety Governor.
  - `CapabilityLevel` IntEnum: `UNRESTRICTED` / `NON_DESTRUCTIVE` / `READ_ONLY` /
    `VOICE_ONLY` / `CRISIS_LOCKOUT` — gradient gating from "all tools" down to
    "template message only," with user communication preserved at levels 0-3.
  - `GovernorConfig` — tunable thresholds for each gate. `enable_crisis_lockout=False`
    by default (opt-in only per user direction).
  - `assess_capability(snap, config) → CapabilityLevel` — pure function, deterministic,
    no latched state, restrictions ease automatically as mood recovers.
  - `crisis_signal(recent_events, config) → CrisisDiagnosis` — discriminates emotional
    spike from real-world emergency using `Score.direction` + `Score.source`. Diverse
    `EXTERNAL_REPORT` sources flag emergency; `SELF_DIRECTED` stream flags spike.
  - `compose_state_context(level, snap, config, *, recent_events, crisis) → str` —
    produces the human-readable string the agent reads to know its own state, with
    explicit recovery thresholds and source-attributed event history.
- **`SoulPlugin` governor methods**: `plugin.capability_level()`,
  `plugin.crisis_signal()`, `plugin.state_context()`. `governor_config=` kwarg on
  construction. Recent-significant-events fetched automatically from the event log.
- **`Score.direction` field** (optional, validated): `SELF_DIRECTED` /
  `EXTERNAL_REPORT` / `ATMOSPHERIC` / `OBSERVATION` / None. Tells the governor what
  the score is *about* so emotional-spike vs world-emergency can be distinguished.
- **`Score.source` field** (optional, free-form): provenance string. URL, channel id,
  or category. Used by the governor's state-context to answer "why do I feel this
  way" with concrete attribution like "x.com/post/ai-banned".
- Round-trip: `direction` and `source` persisted through `SqliteEventLog` JSON.

### Changed
- Test folder reorganized to mirror source structure: `tests/{eventlog,governor,physics,
  pulse,soul}/test_*.py` instead of a flat dump.
- Phase 3 (CARL/Hermes adapters) issue closed — user is handling CARL separately, and
  the unified-plugin direction makes per-framework adapters unnecessary; future
  integrations can be opened fresh as needed.

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

[Unreleased]: https://github.com/deucebucket/clanker-soul/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/deucebucket/clanker-soul/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/deucebucket/clanker-soul/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/deucebucket/clanker-soul/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/deucebucket/clanker-soul/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/deucebucket/clanker-soul/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/deucebucket/clanker-soul/releases/tag/v0.1.0
