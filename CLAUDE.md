# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`clanker-soul` is the 3-layer VADUGWI emotional-state runtime extracted from the CARL agent. It is a **library** (no CLI, no service) that hosts integrate to give an LLM agent persistent mood that survives restarts and accumulates across events. VADUGWI = Valence, Arousal, Dominance, Urgency, Gravity, self-Worth, Intent — seven dimensions, each `0-255`.

Scoring events is **not this library's job**. Hosts produce `Score` objects however they like (LLM scorer, [clanker-lang](https://github.com/deucebucket/clanker), hand rules). clanker-soul is what *happens* to those scores afterward.

## Commands

```bash
pip install -e ".[dev]"                                  # dev install with pytest
pytest                                                   # full suite
pytest tests/test_physics.py                             # one file
pytest tests/test_pulse.py::test_distress_fires_when_v_and_w_drop  # one test
pytest -k breach                                         # by name pattern
```

`pyproject.toml` sets `asyncio_mode = "auto"` — async tests don't need `@pytest.mark.asyncio` to run, but existing tests use it for clarity. Ruff is configured (line-length 100, py310) but not wired into CI; `ruff check .` / `ruff format .` if you have it installed.

Python 3.10+. **Zero runtime dependencies** — stdlib only. Don't add any without a strong reason; this is a deliberate constraint.

## Recommended entry point

`SoulPlugin(agent_id, db_path)` (`clanker_soul/plugin.py`) is the documented one-call drop-in. It wires `EmotionalPhysics` + `SoulStore` + `SqliteEventLog` + `ConfigOverrides` together so plugin authors don't compose four modules by hand. Direct use of `EmotionalPhysics` is still supported and unchanged — that path is "advanced usage" now, not the default story.

The `with SoulPlugin(...) as plugin:` form auto-saves on exit. `plugin.tick()` does both `reload_overrides()` and `soul_drift()` — call it once per agent tick.

## Architecture

The three layers, in order of timescale:

```
Score (per event) ──► Mood (minutes-hours) ──► Soul (days-weeks, persisted)
   conversational      EmotionalPhysics            SoulState + SoulStore
                        + reservoirs
```

**`clanker_soul/score.py`** — `Score` is a frozen 7-int dataclass with optional `patterns` tuple. Tiny on purpose: host-specific telemetry (description, source IDs, latency) belongs in *wrappers* around `Score`, not in it. Physics only reads the 7 dims + patterns.

**`clanker_soul/soul/`** — package: `state.py` (`SoulState` baseline dataclass), `reservoirs.py` (`TraumaReservoir`, `NourishmentReservoir`, 14-day half-life, capped at `RESERVOIR_CAP=1000`), `store.py` (`SoulStore` with SQLite + the v0.2 schema setup). `SoulStore.get(path)` returns a process-wide singleton per path; tests construct `SoulStore(tmp_path)` directly to avoid sharing the connection. The package `__init__.py` re-exports everything so `from clanker_soul.soul import SoulState` keeps working.

**`clanker_soul/physics/`** — package: `config.py` (`PhysicsConfig`, `POSITIVE_PATTERNS`, `HEAVY_PATTERNS`), `math.py` (pure helpers: `event_weight`, `soul_armor`, `mood_prime_score`, `dim_resilience`, `soul_distance`, plus the internal `_why` reason generator), `tick.py` (`PhysicsTick` diagnostic record), `engine.py` (`EmotionalPhysics` stateful engine — one instance per agent, **not thread-safe**). Per-event pipeline in `ingest()`:

1. `event_weight` from raw VADUGWI (worth/valence distance from neutral, intensified by urgency/gravity)
2. `soul_armor` from current Soul (high W/D/G = resilient)
3. blend new event into Mood with α scaled by effective weight
4. `_apply_dim_resilience` — per-dim pull back toward Soul, **scaled DOWN by event weight** so heavy hits still punch through
5. **breach check** — if event is heavy AND mood already far from soul AND pattern is in `HEAVY_PATTERNS`, fraction of event leaks straight into Soul (`_apply_breach` mutates V/W/G only)
6. trauma/nourishment reservoirs updated by classification

`soul_drift()` is the slow bookkeeping pass — call periodically (e.g. from `PulseHost.slow_drift_tick`). Idempotent via `last_drift_ts`; skips work under 3min elapsed.

**`clanker_soul/eventlog/`** — package: `records.py` (`IngestRecord`, `PulseRecord` frozen dataclasses), `protocol.py` (`EventLog` runtime-checkable Protocol, `NullEventLog` noop default), `sqlite.py` (`SqliteEventLog` durable impl writing via the shared `SoulStore.connection` + `SoulStore.lock` — no second handle). **Soft-fail invariant:** writes that fail (DB locked, disk full, connection closed mid-tick) MUST log a warning and continue; they MUST NOT raise into the caller. Physics catches sink exceptions in addition to `SqliteEventLog`'s own catch — defense in depth for custom impls.

**`clanker_soul/overrides.py`** — `OverrideBundle` is a frozen `(physics: dict, soul: dict)` partial-fields dataclass. `ConfigOverrides(store)` reads/writes the v0.2 `config_overrides` table. `apply_overrides()` is a pure merge function. **Partial-merge semantics:** only fields explicitly present in the bundle are overridden. Removing a previously-overridden field reverts that field to its **constructor** value (not the dataclass default — the value the agent was constructed with). Soul fields that were *never* overridden are left alone, so drift accumulated since construction is preserved across `reload_overrides()` calls. Unknown override keys are logged at WARNING and ignored — forward-compat with future `PhysicsConfig` fields and survives a v0.2/v0.3 schema skew between agent and UI processes.

**`clanker_soul/presets.py`** — `Preset` is a frozen `(name, description, soul, config)` dataclass. The four built-ins (`CHILD`, `ADULT`, `BRITTLE`, `STOIC`) are **tuples, not subclasses** — anyone can construct their own. `Preset.apply(overrides, agent_id)` writes ALL physics fields and the personality soul fields (V/A/D/U/G/W/I) — **bookkeeping fields** (`last_drift_ts`, `last_save_ts`) **are intentionally excluded** since they're runtime state, not personality. Switching presets uses `ConfigOverrides.set` (replace), not `update` (merge), so stale knobs from the previous preset don't linger.

**`clanker_soul/__main__.py`** — CLI subcommands `info` / `prune` / `ui`. The `ui` subcommand uses `try: from clanker_soul.ui import launch` to dispatch — Phase 2 just adds the module, no CLI changes needed.

**`clanker_soul/ui/`** — package gated behind the `[ui]` optional dependency group: `app.py` (`create_app(db_path, *, default_agent_id, governor_config) -> FastAPI` factory — testable without uvicorn), `__init__.py` (`launch(db_path, *, agent_id, port, host, log_level)` — blocking uvicorn launcher), `live.py` (`build_live_view(store, agent_id)` — pure-ish view builder with precomputed SVG radar geometry), `templates/{base,index,_live_panel}.html` (Jinja2; Tailwind + HTMX via CDN — no Node toolchain), `static/`. The CLI's `ui` subcommand uses `try: from clanker_soul.ui import launch` so this module's existence is the install-detector. Tests use `pytest.importorskip("fastapi")` to skip cleanly without the extra. The live panel polls `/snapshot` every 2s via HTMX `hx-trigger="every 2s"` and swaps the fragment into a stable div. Subsequent route work (#27-#29) extends `app.py` and adds templates.

**`clanker_soul/governor/`** — package: `levels.py` (`CapabilityLevel` IntEnum + `GovernorConfig` thresholds), `assessment.py` (`assess_capability` pure function), `crisis.py` (`crisis_signal` + `CrisisDiagnosis` — discriminates emotional spike from real emergency using `Score.direction` + `Score.source`), `context.py` (`compose_state_context` — produces the human-readable string the agent reads to know its own state). Wires into `SoulPlugin` via `plugin.capability_level()`, `plugin.crisis_signal()`, `plugin.state_context()`. **User communication channel is always preserved** at levels 0-3 (the user's framing: "rage all you want, use your words, no destruction in anger"). Level 4 (`crisis_lockout`) is opt-in only via `GovernorConfig.enable_crisis_lockout=True`.

**`clanker_soul/pulse/`** — package: `config.py` (`PulseConfig`), `triggers.py` (`Trigger`, `PulseTarget`), `host.py` (`PulseHost` Protocol), `prompt.py` (`compose_self_prompt`), `engine.py` (`PulseEngine`). `PulseEngine` is **host-agnostic** — it never invents recipients, never knows about your message dataclass, never imports your channel layer. Hosts implement the `PulseHost` protocol (`snapshot`, `slow_drift_tick`, `most_recent_target`, `dispatch_pulse`, `due_reminders`, `deliver_reminder`). Each host hook may be sync or async; the engine uses `asyncio.iscoroutine()` rather than wrapping. Triggers: `distress`, `elation`, `trauma_pressure`, `gratitude`, `long_silence`. Cooldown is `min_quiet_seconds` since *any* outbound (call `note_outbound()` on reactive replies, not just pulses).

## Design invariants worth knowing

These are deliberate and easy to break by accident:

- **Soul defaults are NOT neutral 128.** A fresh `SoulState` is `(V=145, A=110, D=160, U=80, G=130, W=175, I=135)` — mildly positive, in-control, strong-worth. Neutral input should not read as depression. Per-agent overrides go in the `SoulState(...)` constructor, not in defaults.
- **Mood anchors to Soul, not 128.** First-ever event in `ingest()` blends against `_mood_anchor()` which returns the Soul vector. The agent wakes up *as itself*. `mood` decay also pulls toward Soul, not toward 128.
- **`mood_prime_score` is the actual context-carrying piece.** Most "emotional state" code only pulls toward soul. Mood-prime tints the *next* perception with the residue of the last one, so a sequence of events feels like a sequence. Hosts that want session-coherent perception must call this on raw scores before `ingest()` — physics does not auto-prime.
- **Dim-resilience is weight-gated.** `_apply_dim_resilience` multiplies the per-dim pull by `(1 - event_weight)`. Without that gate, a high-W soul becomes immune to genuinely big things. Don't remove the scale.
- **Failures are loud in physics, soft in storage.** Physics raises on bad input. `SoulStore.save` catches and logs a warning so a transient SQLite hiccup doesn't desync mood from disk forever — but corruption on `load` falls back to defaults rather than crashing the agent. Don't add silent excepts to physics; don't add hard raises to the store.
- **`HEAVY_PATTERNS` and `POSITIVE_PATTERNS` are frozensets defined in `physics.py`.** Hosts using a different scoring engine extend these by **replacing the constant** before constructing `EmotionalPhysics`, or by subclassing. Pattern matching is upper-cased.
- **The breach mechanic only mutates V, W, G.** Other dims drift through `soul_drift` only. Keep it that way — wholesale soul rewrite from a single event is a bug, not a feature.
- **Event log writes are soft-fail.** A logging failure (DB locked, disk full, connection closed mid-tick) MUST warn and continue, never raise into physics. `SqliteEventLog` catches; `EmotionalPhysics` and `PulseEngine` also catch as defense-in-depth for custom sinks. Don't add `raise` into a logging path.
- **`config_overrides` are partial-merge, not full-replace.** `reload_overrides()` only touches fields explicitly in the bundle. Soul fields that were never overridden are left alone — drift is preserved across reloads. The `_active_*_overrides` sets on `EmotionalPhysics` track which fields are *currently* overridden so removing one cleanly reverts it to the constructor value without clobbering everything else.
- **`SoulPlugin` is the recommended entry point but not the only one.** Direct `EmotionalPhysics(...)` construction works exactly as it did in v0.1 — opt-in `event_log=` and `overrides=` kwargs are the only additions. Don't deprecate the low-level path; advanced hosts (test rigs, custom persistence) need it.
- **Presets are tuples, not subclasses.** `Preset(name, description, soul, config)` is a frozen dataclass. Custom presets are constructed, not inherited from. `Preset.apply` excludes `last_drift_ts` and `last_save_ts` because those are runtime state — overriding them would reset drift cadence on every preset switch.
- **The user-message channel is never gated by the governor at levels 0-3.** Rage, distress, anger, despair — all can be expressed verbally. What's gated is *destructive action*. Don't add a level that blocks messaging the user without an explicit opt-in flag (level 4 is the only such level and it's `enable_crisis_lockout=False` by default).
- **`Score.direction` + `Score.source` are the crisis-vs-spike key.** Without them, the governor can only flag "spike, unclear" with low confidence. Hosts that want emergency escalation must populate at least one of those fields. `SELF_DIRECTED` from one source = personal spike; `EXTERNAL_REPORT` from diverse sources = world emergency.
- **State-context generation is pure-function over snapshot + recent_events + crisis.** The host can call it cheaply each turn, or once per N turns and cache. Don't add side effects (file writes, network calls) to the context generator.

## Release process

The repo follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) + [SemVer](https://semver.org/).

**Per-PR contract:** every PR MUST update `CHANGELOG.md` `[Unreleased]` with a bullet describing the user-visible change (under `### Added`/`### Changed`/`### Fixed`/`### Removed` as appropriate). PR template enforces a checkbox. Skip only for purely internal changes (CI tweaks, comment-only edits) — and even then, prefer adding a line.

**Cutting a release:**
1. Branch `release/X.Y.Z`
2. Bump `version` in `pyproject.toml` AND `__version__` in `clanker_soul/__init__.py` (both must match)
3. In `CHANGELOG.md`: rename `[Unreleased]` heading to `[X.Y.Z] — YYYY-MM-DD`, add a fresh empty `[Unreleased]` section above it
4. Update the link footer: bump the `[Unreleased]` compare to `vX.Y.Z...HEAD`, add a `[X.Y.Z]` entry comparing to the previous tag
5. Open PR, merge, then `git tag vX.Y.Z` on the merge commit and push the tag
6. `gh release create vX.Y.Z` with the changelog section as the body

**Versioning rules** (pre-1.0 these are looser but still SemVer-shaped):
- MAJOR (X) — breaking public API change. Reserved for 1.0.
- MINOR (Y) — new feature, backward-compatible. Most Phase 2/3 PRs land as a minor bump (or accumulate under `[Unreleased]` until a milestone, depending on size).
- PATCH (Z) — bug fixes only.

Don't bump the version on every PR mechanically — bump when you cut a release and the changes warrant publishing. Most PRs just update `[Unreleased]`.

## Hosts

CARL (https://github.com/deucebucket/carl) is the reference host and the source this was extracted from. The module here is canonical; CARL's bundled copy may temporarily diverge while changes are upstreamed.

## License

AGPL-3.0. Network-exposed derivatives must offer source.
