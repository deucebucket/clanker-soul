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

## Architecture

The three layers, in order of timescale:

```
Score (per event) ──► Mood (minutes-hours) ──► Soul (days-weeks, persisted)
   conversational      EmotionalPhysics            SoulState + SoulStore
                        + reservoirs
```

**`clanker_soul/score.py`** — `Score` is a frozen 7-int dataclass with optional `patterns` tuple. Tiny on purpose: host-specific telemetry (description, source IDs, latency) belongs in *wrappers* around `Score`, not in it. Physics only reads the 7 dims + patterns.

**`clanker_soul/soul.py`** — `SoulState` (the persistent baseline), `TraumaReservoir` / `NourishmentReservoir` (pattern-keyed, 14-day half-life, capped at `RESERVOIR_CAP=1000`), and `SoulStore` (SQLite). `SoulStore.get(path)` returns a process-wide singleton per path; tests should construct `SoulStore(tmp_path)` directly to avoid sharing the connection.

**`clanker_soul/physics.py`** — `EmotionalPhysics` is the engine. One instance per agent, **not thread-safe**. Per-event pipeline in `ingest()`:

1. `event_weight` from raw VADUGWI (worth/valence distance from neutral, intensified by urgency/gravity)
2. `soul_armor` from current Soul (high W/D/G = resilient)
3. blend new event into Mood with α scaled by effective weight
4. `_apply_dim_resilience` — per-dim pull back toward Soul, **scaled DOWN by event weight** so heavy hits still punch through
5. **breach check** — if event is heavy AND mood already far from soul AND pattern is in `HEAVY_PATTERNS`, fraction of event leaks straight into Soul (`_apply_breach` mutates V/W/G only)
6. trauma/nourishment reservoirs updated by classification

`soul_drift()` is the slow bookkeeping pass — call periodically (e.g. from `PulseHost.slow_drift_tick`). Idempotent via `last_drift_ts`; skips work under 3min elapsed.

**`clanker_soul/pulse.py`** — `PulseEngine` is **host-agnostic**. It never invents recipients, never knows about your message dataclass, and never imports your channel layer. Hosts implement the `PulseHost` protocol (`snapshot`, `slow_drift_tick`, `most_recent_target`, `dispatch_pulse`, `due_reminders`, `deliver_reminder`). Each host hook may be sync or async; the engine uses `asyncio.iscoroutine()` rather than wrapping. Triggers: `distress`, `elation`, `trauma_pressure`, `gratitude`, `long_silence`. Cooldown is `min_quiet_seconds` since *any* outbound (call `note_outbound()` on reactive replies, not just pulses).

## Design invariants worth knowing

These are deliberate and easy to break by accident:

- **Soul defaults are NOT neutral 128.** A fresh `SoulState` is `(V=145, A=110, D=160, U=80, G=130, W=175, I=135)` — mildly positive, in-control, strong-worth. Neutral input should not read as depression. Per-agent overrides go in the `SoulState(...)` constructor, not in defaults.
- **Mood anchors to Soul, not 128.** First-ever event in `ingest()` blends against `_mood_anchor()` which returns the Soul vector. The agent wakes up *as itself*. `mood` decay also pulls toward Soul, not toward 128.
- **`mood_prime_score` is the actual context-carrying piece.** Most "emotional state" code only pulls toward soul. Mood-prime tints the *next* perception with the residue of the last one, so a sequence of events feels like a sequence. Hosts that want session-coherent perception must call this on raw scores before `ingest()` — physics does not auto-prime.
- **Dim-resilience is weight-gated.** `_apply_dim_resilience` multiplies the per-dim pull by `(1 - event_weight)`. Without that gate, a high-W soul becomes immune to genuinely big things. Don't remove the scale.
- **Failures are loud in physics, soft in storage.** Physics raises on bad input. `SoulStore.save` catches and logs a warning so a transient SQLite hiccup doesn't desync mood from disk forever — but corruption on `load` falls back to defaults rather than crashing the agent. Don't add silent excepts to physics; don't add hard raises to the store.
- **`HEAVY_PATTERNS` and `POSITIVE_PATTERNS` are frozensets defined in `physics.py`.** Hosts using a different scoring engine extend these by **replacing the constant** before constructing `EmotionalPhysics`, or by subclassing. Pattern matching is upper-cased.
- **The breach mechanic only mutates V, W, G.** Other dims drift through `soul_drift` only. Keep it that way — wholesale soul rewrite from a single event is a bug, not a feature.

## Hosts

CARL (https://github.com/deucebucket/carl) is the reference host and the source this was extracted from. The module here is canonical; CARL's bundled copy may temporarily diverge while changes are upstreamed.

## License

AGPL-3.0. Network-exposed derivatives must offer source.
