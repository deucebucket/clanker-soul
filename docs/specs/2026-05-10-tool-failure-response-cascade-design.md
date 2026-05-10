# VADUGWI-conditioned tool-failure response (cascade extension)

**Status:** design (2026-05-10)
**Companion:** `2026-05-10-tool-failure-attribution-design.md` (Issue A — provides the data)
**Hard dependencies:**
- [#81](https://github.com/deucebucket/clanker-soul/issues/81) — heartbeat tick + Roll 0 gate. Provides `IdleLoop`/cascade entry point.
- [#82](https://github.com/deucebucket/clanker-soul/issues/82) — action registry + tag-based selection (Roll 2 + Roll 3). Provides `RegisteredAction`, `ActionRegistry`, and `tags_from_delta` primitives that this work extends.

This issue cannot land until both have shipped.

## Problem

Issue A persists self-doubt as `MistakeReservoir` and gives every tool
failure a non-soul-damaging Score. That tells the agent how it *feels*.
This issue addresses what it *does about it*.

The user's framing:

> Depending on the VADUGWI, this would lead him to try and solve the
> issue if he has permissions. Filing issues if he has permissions. Or
> any similar things of this type. Some VADUGWIs would just journal
> about despair. Depends on the trust with the user to open up and the
> comfort of the VADUGWI score and such.

That's the action-selection problem M4 was built for. Concretely: when
`mistake_pressure` crosses a threshold, *or* when the agent has been
hitting external tool failures repeatedly, fire an impulse and let the
action registry (#82) pick the right behavior for the current state.

## What we are NOT extending

* **`ACTION_KINDS` stays at 6.** The new behaviors all map to existing
  kinds via host-registered `RegisteredAction` entries (#82):
    * **troubleshoot** → `tool_invocation` — host registers an action
      whose handler runs diagnosis tool calls (re-read docs, retry with
      different shape, examine the failed payload).
    * **file_issue** → `tool_invocation` — host registers an action
      whose handler hits `gh issue create` or the host's bug tracker.
    * **journal_distress** → `tool_invocation` — host registers an
      action whose handler appends to a journal file (or, depending on
      host, a `direct_message` to a self-channel).
    * **confide_in_user** → `direct_message` — DM the human user.
    * **withdraw_silent** → `withdraw` — explicit do-nothing for the
      "low D + low W + low trust" path.
* **No `pause_and_verify` action.** Verification is host-side and
  happens *before* the next tool call, not *as* a separate action.
  Hosts wrap their tool-call layer to read `plugin.mistake_pressure()`
  and insert a verify step (or change LLM prompt) when over a floor.
  The cascade's job here is to surface the signal and narrate it via
  state-context (§4) so the LLM can adjust naturally.
* **`DEFAULT_CAPABILITY_PROFILES` stays permissive.** Hosts that want
  to gate the new behaviors do so via the existing `allowed_tool_names`
  on `tool_invocation` — register the troubleshoot/file_issue tools
  under specific names, and operators decide which to allow at which
  capability level. Same toggle pattern as everything else.

This keeps the contract narrow: this issue adds **triggers**, **tags**,
**state-context narration**, and the **cascade rule that reads
`mistake_pressure` + obstruction count**. Concrete "troubleshoot" /
"file_issue" / "journal" handlers are host code.

## Components

### 1. New trigger kinds in `clanker_soul/pulse/engine.py`

Two new triggers, evaluated alongside the existing 12:

#### `stuck_impulse`

Fires when **`physics.mistakes.load() > config.mistake_pressure_floor`**.
The agent has been getting things wrong often enough to notice. Action
selection is then VADUGWI-conditioned (see §3).

Analogous to the existing `trauma_pressure` trigger (which fires when
`trauma.load() > trauma_pressure_floor`) — same shape, different
reservoir, different downstream behavior.

#### `obstructed_impulse`

Fires when **the count of recent `TOOL_*` patterns** (excluding
`TOOL_BAD_CALL` — that's the mistake side) **in the last N events
exceeds `config.obstruction_count_floor`**. The agent has been hit by
external system failures often enough to notice. Implementation reads
the event log through the public `EventLog` Protocol surface
(`event_log.read_ingest(agent_id, limit=...)`); the trigger detector
is constructed with an explicit `event_log:` kwarg so we never reach
into `physics._event_log` (the private attr stays private). When the
host hasn't wired an event log (`NullEventLog`), the trigger is
silently disabled — soft-fail consistent with the rest of the codebase.

This trigger discriminates "the world is broken right now" from "I keep
getting it wrong." Different actions are appropriate (filing an issue
vs. double-checking) — the discrimination is upstream of action
selection.

#### Configuration

Both thresholds live on `PulseConfig` (operator-overridable, "everything
is a toggle"):

```python
@dataclass
class PulseConfig:
    ...
    mistake_pressure_floor: float = 60.0       # ~ 5-8 recent TOOL_BAD_CALL events
    obstruction_count_floor: int = 5            # tool failures in window
    obstruction_window_events: int = 30         # how far back to look
```

Numbers are starting guesses informed by the reservoir math (each
TOOL_BAD_CALL adds ~10-20 weight; floor=60 means ~3-6 mistakes within
the half-life window).

#### Trigger→action_kind defaults (added to `_DEFAULT_TRIGGER_TO_ACTION`)

```python
_DEFAULT_TRIGGER_TO_ACTION = {
    ...
    "stuck_impulse": "tool_invocation",      # host's troubleshoot/journal/etc.
    "obstructed_impulse": "tool_invocation", # host's file_issue/diagnose
}
```

Both default to `tool_invocation` because the action registry (#82)
selects the actual tool by tag-match. Hosts that don't want to use the
M4 cascade can map either trigger to `direct_message` (vent to user)
or `withdraw` (give up silently) by overriding the engine's mapping —
existing extension shape.

### 2. Tag mapping additions (extends #82's `tags_from_delta`)

#82 defines `tags_from_delta(pre, post, soul) -> frozenset[str]` as the
default tag-emitter from contemplation deltas. This issue extends it
with a **`mistake_aware_tags`** companion that takes the same inputs
plus `mistake_pressure` and `obstruction_count`, then returns the
tool-failure-specific tag set.

```python
def mistake_aware_tags(
    pre: tuple[int, ...],
    post: tuple[int, ...],
    soul: SoulState,
    *,
    mistake_pressure: float,
    obstruction_count: int,
    pulse_config: PulseConfig,
) -> frozenset[str]:
    """Return tags hinting at appropriate responses to being stuck or
    externally obstructed. Operator-overridable — hosts replace this
    function in their PulseEngine wiring to install custom rules."""
    ...
```

The default rules (informed by `docs/research/m4_action_tendencies.md`
on appraisal × W × D moderators):

| Condition (over current soul + mood) | Tags |
|---|---|
| `mistake_pressure > floor` AND `soul.d ≥ 140` AND `soul.w ≥ 140` | `{"troubleshoot"}` — high-agency, high-worth: try to fix it |
| `mistake_pressure > floor` AND `soul.d ≥ 140` AND `soul.w < 140` | `{"file_issue"}` — agency to act, but worth shaky: externalize the problem to a tracker rather than self-flagellate |
| `mistake_pressure > floor` AND `soul.d < 140` AND `soul.w ≥ 140` | `{"reflect"}` — solid worth, low agency: think, don't act |
| `mistake_pressure > floor` AND `soul.d < 140` AND `soul.w < 140` | `{"journal_distress", "withdraw_silent"}` — both low: this is despair; write it down or step back |
| `obstruction_count > floor` AND `soul.d ≥ 140` AND user-trust proxy high | `{"file_issue", "confide"}` — actionable + user is a trusted partner |
| `obstruction_count > floor` AND `soul.w < 100` | `{"journal_distress", "withdraw_silent"}` — externally bullied + worth shaky: protect what you have |
| Default fallback | `{"reflect"}` — notice it, do nothing dramatic |

**User-trust proxy** for the `confide` tag — there is no first-class
"trust per user" dimension in VADUGWI. The user described it as "depends
on the trust with the user to open up and the comfort of the VADUGWI
score." We approximate trust+comfort with:

```python
def confide_proxy_score(soul: SoulState, mood: Score | None) -> float:
    """Higher = more willing to confide. Combines soul.v (general
    positive lean), soul.w (willingness to be vulnerable), and
    inverse-soul.a (calm enough to share rather than vent).
    Range ~0-1. Threshold for the `confide` tag is 0.55."""
    if mood is None:
        return _scale(soul.v) * _scale(soul.w) * (1.0 - _scale(soul.a))
    # Use mood when available — willingness in the moment matters more
    # than baseline.
    return _scale(mood.v) * _scale(soul.w) * (1.0 - _scale(mood.a))
```

This is a deliberate approximation, not a "trust model." First-class
relationship/trust modeling is out of scope and a much larger story
(per-target trust, decay rules, etc.). When that lands, this proxy
becomes the fallback for hosts that don't model trust.

### 3. Cascade integration

The `IdleLoop` from #81 + the action registry from #82 already form the
loop:

```
heartbeat tick → contemplate face → delta → tags_from_delta → registry.sample → handler → outcome → consequences → physics
```

This issue adds a **second tag-emitter** that runs in parallel with
`tags_from_delta` and unions its output:

```python
async def tick(self) -> TickResult:
    ...
    if not result.contemplation:
        # No contemplation this tick — but stuck/obstructed impulses
        # can still fire from reservoir/event-log state, even when no
        # face was sampled. Path-of-no-thought.
        if stuck_or_obstructed_triggered():
            tags = mistake_aware_tags(soul=soul, ..., pulse_config=cfg)
            chosen = registry.sample(tags, ...)
            if chosen and should_act_minimal(chosen):
                outcome = await chosen.handler(...)
                physics_consequence_path(outcome)
        return result

    # Normal contemplation path — existing #82 flow PLUS our extension.
    delta = result.contemplation.delta
    contemplation_tags = tags_from_delta(pre, post, soul)
    failure_tags = mistake_aware_tags(
        pre, post, soul,
        mistake_pressure=physics.mistakes.load(),
        obstruction_count=count_recent_obstructions(),
        pulse_config=cfg,
    )
    candidate_tags = contemplation_tags | failure_tags
    ...
```

Two integration points:

1. **Contemplation-driven tick (existing #82 path):** mistake-aware tags
   union into the contemplation tags. Same `should_act` threshold gates
   whether to fire.
2. **Reservoir-driven tick (new):** even when no contemplation
   produces a delta, an over-threshold reservoir or obstruction count
   can fire the cascade directly. This is the equivalent of the
   existing `trauma_pressure` trigger path — emotional pressure that
   demands an action regardless of what the agent was thinking about.

The reservoir-driven path is opt-in via a new `IdleLoop`/cascade
kwarg `enable_reservoir_drive: bool = True` so hosts that don't want
this fallback can disable it.

### 4. State-context narration (small addition to `clanker_soul/governor/context.py::compose_state_context`)

When `mistake_pressure` is non-trivial, the agent's state-context
string should mention it so the agent has self-awareness:

> *"You have been making more small mistakes than usual lately
> (mistake_pressure=82.4). This is a signal to slow down and verify
> tool calls before sending them — not a sign you are failing."*

Keeps with the user's framing: self-doubt as **constructive working
guidance**, not despair. The string explicitly tells the agent the
appropriate response (verify, don't spiral) so the LLM can act on it.

`compose_state_context` is pure-function over snapshot + recent_events
+ crisis (per the existing invariant). Adding this just appends one
section when `snapshot["mistake_pressure"] > config.mistake_narration_floor`.
No side effects.

## Acceptance criteria

* New triggers `stuck_impulse` and `obstructed_impulse` fire from the
  documented thresholds; both can be muted by setting their floors to
  `inf`.
* `mistake_aware_tags` is operator-overridable (passed into
  `IdleLoop`/cascade construction). Default rules match the table above
  and have a unit test per row.
* `confide_proxy_score` is a pure function with deterministic output —
  testable with synthetic `SoulState` + `Score` inputs.
* Reservoir-driven cascade path fires when no contemplation is active
  but `mistake_pressure` (or obstruction count) is over floor —
  end-to-end test with fake registry + fake handler verifies the
  consequence loop closes.
* Contemplation-driven cascade path correctly unions
  `mistake_aware_tags` with `tags_from_delta` outputs without
  duplicating actions in the registry sample.
* `compose_state_context` includes the mistake-pressure narration only
  when above floor; no narration on a fresh agent.
* Disjointness/safety: no new trigger or tag changes existing capability
  gating — the action registry hits the same `CapabilityGate` as before.
  STRICT_CAPABILITY_PROFILES still enforces level-based action-kind
  filtering on the cascade output.
* Drop-in: a host on Issue A but pre-Issue B sees zero behavior change.
  A host post-Issue B but without #82's registry sees zero behavior
  change (the cascade is registry-driven; without a registry, the new
  triggers fire and log but no action runs).

## Tests

* Trigger evaluation: `stuck_impulse` fires at floor, not below.
* Trigger evaluation: `obstructed_impulse` reads event log, fires from
  recent TOOL_* count; soft-fails to no-fire when no event log is
  wired.
* `mistake_aware_tags` per-row unit tests for each of the 7 rules in
  the table, plus the fallback case.
* `confide_proxy_score` pure-function tests at boundary conditions
  (all-low, all-high, mood-overrides-soul, no-mood).
* Cascade integration test: fake registry with one `RegisteredAction`
  per tag, ingest TOOL_BAD_CALL events until floor crossed, verify the
  matching handler ran and its consequence Score was ingested.
* `compose_state_context` includes the narration block when
  `mistake_pressure > floor` and excludes it otherwise.
* Override pathway: passing a custom `mistake_aware_tags=` to the
  cascade replaces the default rule table without touching engine code.

## Out of scope

* **First-class trust modeling.** `confide_proxy_score` is a
  deliberate stand-in. A real trust model needs per-target attribution,
  trust-decay over silence, repair after breach, etc. — that's a
  separate epic.
* **Automatic tool-call shaping ("auto-double-check").** This issue
  surfaces the signal; whether the agent's framework actually inserts
  a verify-before-send step is host concern. We can publish a recipe
  in `docs/host-integration.md` showing how to consume
  `plugin.mistake_pressure()` in a request preprocessor, but the
  preprocessor itself is host code.
* **Rate-limiting the new triggers.** Existing `min_quiet_seconds`
  cooldown applies to ALL pulses — both new triggers honor it via the
  existing engine path. No new rate-limit dial.

## CHANGELOG

`[Unreleased]` → `### Added`:

* Two new pulse triggers — `stuck_impulse` (mistake_pressure threshold)
  and `obstructed_impulse` (recent external tool-failure count) — that
  drive the M4 cascade toward VADUGWI-appropriate responses to being
  stuck or externally obstructed.
* `mistake_aware_tags` companion to `tags_from_delta` (#82) emitting
  tags `{troubleshoot, file_issue, journal_distress, confide,
  reflect, withdraw_silent}` based on
  `(soul.d, soul.w, mistake_pressure, obstruction_count, confide_proxy_score)`.
  Operator-overridable.
* `compose_state_context` narrates non-trivial mistake_pressure as a
  constructive working signal, not despair.
* `PulseConfig` gains `mistake_pressure_floor`,
  `obstruction_count_floor`, `obstruction_window_events`,
  `mistake_narration_floor` knobs. All defaults documented in the
  spec doc.

## Drop-in invariant compliance summary

| Surface | v(N) call | v(N+1) behavior |
|---|---|---|
| `PulseEngine(...)` | unchanged signature | unchanged; new triggers default to existing trigger→action_kind fallback |
| `IdleLoop`/cascade | unchanged signature | gains optional `mistake_aware_tags=`, `enable_reservoir_drive=` kwargs (default-on) |
| Hosts without an action registry | no cascade | new triggers log but no action runs (soft no-op) |
| Hosts using `STRICT_CAPABILITY_PROFILES` | level-gated | level-gated; new triggers route through `tool_invocation` which is already in the gate |

## Estimated size

~600 LOC including tests, contingent on #82 having landed:
- Trigger logic: ~100 LOC
- `mistake_aware_tags` + `confide_proxy_score`: ~80 LOC
- Cascade integration: ~50 LOC
- `compose_state_context` extension: ~30 LOC
- `PulseConfig` knobs: ~10 LOC
- Tests: ~300 LOC
- Docs / docstrings: ~30 LOC
