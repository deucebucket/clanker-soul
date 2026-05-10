# Tool-failure attribution + mistakes reservoir

**Status:** design (2026-05-10)
**Companion:** `2026-05-10-tool-failure-response-cascade-design.md` (Issue B, gated on #82)

## Problem

When the agent's tools or the systems it depends on break — a file write
fails, an MCP server times out, a git push gets rejected, the browser
crashes mid-navigation, an OS permission denies a call — the resulting
emotional impact today goes through the same path as a person being
contemptuous to the agent. That's wrong.

A tool breaking is **not the agent's fault.** The agent should:

* feel **annoyance** (A↑, V↓) and a small dent in **Dominance** (D↓)
  when external systems obstruct it
* **not** lose self-Worth (W) — its sense of self is unrelated to whether
  OpenRouter is rate-limiting today
* **not** trigger the breach mechanic (no `HEAVY_PATTERNS` membership) — a
  rate-limit isn't human contempt and shouldn't permanently scar

The exception is **validation errors** — the tool rejected the call shape.
That's partly the agent's fault: a bad call IS a small mistake. The user's
guidance: "if it turns out it was the agent's fault because the tool calls
were used wrong, then it should affect it, and be a memory. There should
be a level of self-doubt too, where the agent learns over time that it
makes mistakes, and to double-check when something is wrong."

A single W dent on one bad call gets absorbed into mood within minutes
and forgotten. That's not memory. To deliver "self-doubt that builds
over time," we need persistence — the same shape `TraumaReservoir` and
`NourishmentReservoir` already provide for the other accumulating
emotional currents.

## Prior art in the codebase

* **`integrations/hermes/inference_health.py::score_from_failover`** is the
  hermes-specific bridge that maps `FailoverReason` enum values to light
  Scores with `direction="OBSERVATION"`, `source="inference:{provider}"`,
  patterns disjoint from `HEAVY_PATTERNS`. This design generalises that
  pattern to the agent's whole tool surface — the inference layer was just
  the first concrete instance.
* **`TraumaReservoir`/`NourishmentReservoir`** in `clanker_soul/soul/reservoirs.py`
  give us the persistence shape: pattern-keyed, 14-day half-life, capped
  at `RESERVOIR_CAP=1000`, JSON-serialised onto the `soul_state` row.
* **`PulseEngine._absorb_consequences`** auto-ingests
  `ActionOutcome.consequences` Scores back into physics when the engine
  is constructed with `physics=`. So a host whose tool failed mid-action
  can already attach a tool-failure Score to its `ActionOutcome` and the
  loop closes.
* **`Score.direction = "OBSERVATION"`** is the existing slot for
  "agent-observing-its-own-state" — what `inference_health.py` uses, and
  what `tool_health.py` will reuse. Not `SELF_DIRECTED` (no one is
  acting *on* the agent), not `EXTERNAL_REPORT` (this isn't a description
  of a third-party state).

## Architecture line: trauma damages, mistakes are observable

`TraumaReservoir` is the soul-wounding mechanism: when trauma load
crosses `trauma_pressure_floor` and exceeds nourishment, `soul_drift`
actively bleeds W/V/G via `wounding_rate` (engine.py L332-L336). It
encodes *being wronged* — patterns are things like `BETRAYAL`,
`CONTEMPT`, `ABANDONMENT`.

The new `MistakeReservoir` encodes *being wrong* — a different valence.
Self-doubt is a **constructive** signal the agent reads to decide
"should I double-check this time?" — it is not depression, and it must
not produce the same long-term soul shape that sustained contempt does.

**Therefore: `MistakeReservoir` does NOT feed `wounding_rate`.** It
accumulates and decays on the same 14-day half-life as the others, but
the soul never drifts because of it. It is purely **observable** to the
host and to Issue B's cascade. This architectural line is enforced by a
test.

## Drop-in invariant: zero-refactor upgrade

CLAUDE.md is unambiguous: "every clanker-soul change must let hosts upgrade
with zero code changes; new APIs go alongside, never inside." The
constraints this places on the design:

1. **`SoulStore.load(agent_id) -> tuple[SoulState, TraumaReservoir, NourishmentReservoir]`
   signature is frozen.** Any host that does
   `soul, trauma, nourishment = store.load(...)` must continue to work.
   Therefore `MistakeReservoir` is loaded via a **new method**
   `SoulStore.load_mistakes(agent_id) -> MistakeReservoir`, not by
   extending the tuple.
2. **`SoulStore.save(agent_id, soul, trauma, nourishment)` signature is
   frozen.** Same reason. New method `SoulStore.save_mistakes(agent_id,
   mistakes)`.
3. **`EmotionalPhysics.__init__` adds `mistakes: MistakeReservoir | None = None`
   as a kwarg with `None` default.** Old callers don't pass it; physics
   constructs an empty `MistakeReservoir` for them. The reservoir lives at
   `physics.mistakes` (additive attribute).
4. **`SoulPlugin.__init__` adds no new kwargs.** It loads/saves mistakes
   internally via the new `SoulStore` methods. Hosts using the
   recommended entry point get the feature for free without changing
   construction.
5. **`SoulStore` schema migration** uses the existing idempotent
   `PRAGMA table_info` pattern that `pulse_log.face_id` used (store.py
   L141-L147): if `mistakes_json` column is absent, `ALTER TABLE
   soul_state ADD COLUMN mistakes_json TEXT NOT NULL DEFAULT '{}'`.
   Existing rows get `'{}'`, decoded as an empty `MistakeReservoir`. v0.x
   databases upgrade in place; rollback to v0.x ignores the unknown
   column.

This is "additive everywhere." A v(N) host upgrading to v(N+1) writes
zero new code and behaves identically — empty mistakes reservoir, no
extra Scores produced unless the host opts in by calling
`score_from_action_failure`.

## Components

### 1. `clanker_soul/tool_health.py` (new module)

Mirrors the shape of `integrations/hermes/inference_health.py`. Lives in
core because tool failures are agent-agnostic — every host with any tool
surface (filesystem, browser, MCP, git, OS commands, custom) hits this.
The hermes module stays as the inference-layer specialisation; it does
not call into `tool_health.py` (different reason taxonomy).

```python
def score_from_action_failure(
    reason: str | Any,         # category key, or anything with a .value attr
    *,
    tool: str = "",            # tool/action identifier — drives Score.source
    override: Mapping[str, Mapping[str, Any] | None] | None = None,
) -> Score | None:
    ...
```

Returns a `Score` with `direction="OBSERVATION"` and
`source=f"tool:{tool}"` (or just `"tool"` when `tool=""`), or `None` for
configuration-shaped reasons and unknown/empty inputs. Uses the same
`_normalise_reason` helper shape as `inference_health.py` (accepts an
enum, a string, or anything with `.value`).

#### Default category map

| `reason` | A | V | D | U | G | W | I | pattern |
|---|---|---|---|---|---|---|---|---|
| `timeout` | 138 | 118 | 115 | 60 | 122 | 128 | 115 | `TOOL_TIMEOUT` |
| `unreachable` | 132 | 115 | 105 | 65 | 118 | 128 | 110 | `TOOL_UNREACHABLE` |
| `rate_limit` | 140 | 120 | 115 | 70 | 120 | 128 | 115 | `TOOL_RATE_LIMIT` |
| `resource_exhausted` | 135 | 118 | 110 | 70 | 115 | 128 | 110 | `TOOL_RESOURCE_EXHAUSTED` |
| `denied` *(operator/OS perm denial)* | 130 | 110 | 95 | 60 | 115 | 128 | 100 | `TOOL_DENIED` |
| `cancelled` | 115 | 122 | 115 | 40 | 122 | 128 | 115 | `TOOL_CANCELLED` |
| `validation_error` *("I made a mistake")* | 125 | 120 | 110 | 55 | 120 | **120** | 110 | **`TOOL_BAD_CALL`** |
| `unknown` | 125 | 118 | 110 | 60 | 118 | 128 | 110 | `TOOL_UNKNOWN_FAIL` |

* All non-`validation_error` rows have **W=128** — Worth untouched. The
  user's "not his fault" rule made literal.
* `validation_error` is the **only** Worth-touching category, with a
  small dent (W=120, V=120). The pattern `TOOL_BAD_CALL` is the input to
  the mistakes reservoir.
* All patterns are **upper-cased** (matches existing convention) and
  **disjoint from `HEAVY_PATTERNS`** — never triggers the breach
  mechanic. Asserted in tests.

#### Configuration-shaped reasons (return `None`)

`not_implemented`, `tool_disabled`, `config_error` — these are operator
concerns, not agent experiences. Tools can be turned off; the agent
doesn't feel the absence of an unconfigured capability. Same treatment
as `inference_health.py` gives `model_not_found` /
`provider_policy_blocked` / etc.

#### Per-host tuning

`override=` follows the `inference_health.py` precedent exactly: a
partial mapping that takes precedence over the defaults. Pass
`{"timeout": None}` to disable a category for one persona; pass full
kwargs to remap one category without forking the table. Hosts that want
host-specific patterns (`MY_HOST_BROWSER_TIMEOUT` vs the default
`TOOL_TIMEOUT`) replace via `override`.

### 2. `clanker_soul/physics/config.py` — `MISTAKE_PATTERNS`

```python
MISTAKE_PATTERNS = frozenset({"TOOL_BAD_CALL"})
```

Parallel to `HEAVY_PATTERNS` and `POSITIVE_PATTERNS`. Operators add
their own self-attribution patterns by replacing the constant before
constructing physics, or by subclassing — same extension shape as the
existing two sets.

**Disjointness invariants** (asserted in tests):
- `MISTAKE_PATTERNS ∩ HEAVY_PATTERNS == ∅`
- `MISTAKE_PATTERNS ∩ POSITIVE_PATTERNS == ∅`

### 3. `clanker_soul/soul/reservoirs.py` — `MistakeReservoir`

Subclass of `TraumaReservoir` (same shape as `NourishmentReservoir`
already does — engine.py L95-L112). Math identical, type structurally
distinct so `isinstance` checks branch correctly.

```python
class MistakeReservoir(TraumaReservoir):
    """Per-pattern accumulator for self-attributed errors.

    Same mechanics as TraumaReservoir (14d half-life, RESERVOIR_CAP).
    Semantically distinct: this reservoir is OBSERVABLE — hosts read
    it to decide "should I double-check?" — but it does NOT feed
    soul_drift's wounding_rate the way TraumaReservoir does. Self-doubt
    is a working signal, not personality damage."""

    @classmethod
    def from_dict(cls, data: dict, half_life_s: float = RESERVOIR_HALF_LIFE_S) -> "MistakeReservoir":
        ...  # mirrors NourishmentReservoir.from_dict
```

Re-exported from `clanker_soul/soul/__init__.py` and the package
`__init__.py` (`from clanker_soul import MistakeReservoir`).

### 4. `clanker_soul/soul/store.py` — additive persistence

**`_init_schema` adds an idempotent column migration** in the same
shape as `pulse_log.face_id` (L141-L147):

```python
existing_soul_cols = {row[1] for row in c.execute("PRAGMA table_info(soul_state)").fetchall()}
if "mistakes_json" not in existing_soul_cols:
    c.execute("ALTER TABLE soul_state ADD COLUMN mistakes_json TEXT NOT NULL DEFAULT '{}'")
```

**Two new methods** (load and save mistakes don't touch the legacy
3-tuple shape):

```python
def load_mistakes(self, agent_id: str) -> MistakeReservoir:
    with self._db_lock:
        row = self._db.execute(
            "SELECT mistakes_json FROM soul_state WHERE agent_id = ?",
            (agent_id,),
        ).fetchone()
    if row is None or not row[0]:
        return MistakeReservoir()
    try:
        return MistakeReservoir.from_dict(json.loads(row[0]))
    except Exception as e:
        logger.warning("mistakes reservoir corrupt for %s (%s) — resetting", agent_id, e)
        return MistakeReservoir()

def save_mistakes(self, agent_id: str, mistakes: MistakeReservoir) -> None:
    try:
        with self._db_lock:
            self._db.execute(
                "UPDATE soul_state SET mistakes_json = ? WHERE agent_id = ?",
                (json.dumps(mistakes.to_dict()), agent_id),
            )
            self._db.commit()
    except Exception as e:
        logger.warning("mistakes save failed for %s (%s) — continuing", agent_id, e)
```

**Soft-fail on save** (matches the existing `save()` pattern, store.py
L237-L238). **Reset-on-corruption on load** (matches existing `load`
fallback at L211-L213).

**Edge case:** `save_mistakes` runs UPDATE, not INSERT OR REPLACE — it
expects the row to exist (created by the legacy `save()`). The plugin
calls `save()` before `save_mistakes()` to guarantee that order. If a
host opts to call `save_mistakes` standalone on a never-saved agent_id,
the UPDATE is a no-op and a warning fires — soft-fail behaviour
preserved.

### 5. `clanker_soul/physics/engine.py` — routing

**Constructor change** (additive):

```python
def __init__(
    self,
    soul: SoulState,
    trauma: TraumaReservoir | None = None,
    nourishment: NourishmentReservoir | None = None,
    config: PhysicsConfig | None = None,
    *,
    event_log: "EventLog | None" = None,
    overrides: "ConfigOverrides | None" = None,
    agent_id: str | None = None,
    mistakes: "MistakeReservoir | None" = None,    # NEW
) -> None:
    ...
    self.mistakes = mistakes if mistakes is not None else MistakeReservoir()
```

**`_classify` gains a 4th return value** (`'mistake'`), checked **first**
so it wins over the V/W heuristic:

```python
@staticmethod
def _classify(event: Score) -> str | None:
    """Return 'positive', 'negative', 'mistake', or None (ambiguous)."""
    patterns_upper = [p.upper() for p in (event.patterns or ())]

    # Pattern routing wins over V/W heuristic — explicit beats implicit.
    if any(p in MISTAKE_PATTERNS for p in patterns_upper):
        return "mistake"
    if any(p in POSITIVE_PATTERNS for p in patterns_upper):
        return "positive"
    if any(p in HEAVY_PATTERNS for p in patterns_upper):
        return "negative"

    # V/W fallback (unchanged for backward compat).
    if event.v >= 155 and event.w >= 145:
        return "positive"
    if event.v <= 90 and event.w <= 100:
        return "negative"

    return None
```

**Why pattern wins over V/W:** a `validation_error` (V=120, W=120) is
ambiguous-by-V/W and would otherwise route nowhere. Even a host that
produced a more dramatic Score (V=80, W=85) with `TOOL_BAD_CALL` should
still go to mistakes, not trauma — the pattern is more specific than the
heuristic. Test asserts this with both shapes.

**`_update_reservoirs` adds a third branch:**

```python
def _update_reservoirs(self, event: Score, weight: float) -> None:
    if weight <= 0.05:
        return
    bucket = self._classify(event)
    if bucket is None:
        return
    if bucket == "mistake":
        target = self.mistakes
    elif bucket == "positive":
        target = self.nourishment
    else:  # negative
        target = self.trauma
    now = datetime.now(timezone.utc).timestamp()
    patterns = (
        list(event.patterns)
        if event.patterns
        else ["WARMTH" if bucket == "positive" else "GENERIC_NEGATIVE"]
    )
    per_pattern = weight * 100.0 / max(1, len(patterns))
    for p in patterns:
        target.add(p, per_pattern, now_ts=now)
```

The `if bucket is None / WARMTH / GENERIC_NEGATIVE` fallback path is
unchanged — only triggers when classification came from V/W heuristic
(no patterns provided), which never produces 'mistake' (mistakes are
pattern-keyed by definition).

**`soul_drift` is unmodified.** No mistakes-driven W/V drift. The
architectural line.

### 6. `clanker_soul/plugin.py` — wiring + accessor

**`__init__` loads mistakes alongside the legacy load:**

```python
soul, trauma, nourishment = self._store.load(agent_id)
mistakes = self._store.load_mistakes(agent_id)
if not existed and default_soul is not None:
    soul = default_soul

# ... event_log resolution unchanged ...

self._physics = EmotionalPhysics(
    soul=soul,
    trauma=trauma,
    nourishment=nourishment,
    mistakes=mistakes,                       # NEW
    config=config,
    event_log=physics_event_log,
    overrides=self._overrides,
    agent_id=agent_id,
)
```

**`save()` writes both:**

```python
def save(self) -> None:
    self._store.save(
        self._agent_id,
        self._physics.soul,
        self._physics.trauma,
        self._physics.nourishment,
    )
    self._store.save_mistakes(self._agent_id, self._physics.mistakes)
```

Order matters: legacy `save()` runs `INSERT OR REPLACE` on the row;
`save_mistakes()` runs `UPDATE`. The latter is a no-op if the row
doesn't exist, so always run `save()` first. (See Edge case under §4.)

**New accessor for hosts and Issue B:**

```python
def mistake_pressure(self) -> float:
    """Decayed sum of the mistakes reservoir. Hosts read this to bias
    behavior toward double-checking, asking for clarification, or
    pausing before risky tool calls. Issue B's cascade reads it for
    action selection. Returns 0.0 on a fresh agent."""
    return self._physics.mistakes.load()
```

**`snapshot()` adds the new field** (additive, doesn't break old
consumers):

```python
def snapshot(self) -> dict:
    ...
    return {
        "soul": soul.to_dict(),
        "mood": mood.as_list() if mood is not None else None,
        "soul_distance": (soul_distance(mood, soul) if mood is not None else None),
        "trauma_load": self._physics.trauma.load(),
        "nourishment_load": self._physics.nourishment.load(),
        "mistake_pressure": self._physics.mistakes.load(),  # NEW
    }
```

`PulseHost.snapshot` consumers that don't know about the field ignore
it — `dict.get` default-friendly. M4 #82's `IdleLoop` and the cascade
will read it.

### 7. `clanker_soul/__init__.py` — re-exports

Add to imports and `__all__`:

```python
from clanker_soul.physics import ..., MISTAKE_PATTERNS
from clanker_soul.soul import ..., MistakeReservoir
from clanker_soul.tool_health import score_from_action_failure
```

Also add `MISTAKE_PATTERNS` to the `clanker_soul.physics` package
`__init__` (where `HEAVY_PATTERNS`/`POSITIVE_PATTERNS` are exported
from `physics.config`).

## Significant-events filtering (governor untouched)

`SoulPlugin._fetch_recent_significant_events` (plugin.py L494-L507)
filters `classification == "negative" or breached`. Mistakes do **not**
match this filter — the new `'mistake'` classification is invisible to
the crisis-discrimination path. Intentional: the governor's job is
spike-vs-emergency discrimination, not "agent has been making mistakes
lately." The cascade (Issue B) is where mistake pressure influences
behaviour.

This means `events.classification` will gain a fourth possible value
(`'mistake'`) but no existing reader breaks — the filter just doesn't
match it, the UI events table renders the string verbatim.

## Tests

### `tests/test_tool_health.py` (new, ~10 tests)

Mirrors `tests/test_hermes_inference_failure.py` shape:

1. each default category produces a Score with the documented dims
2. `tool=` populates `Score.source` as `"tool:{tool}"`; empty `tool` →
   `"tool"`
3. `direction == "OBSERVATION"` for every default
4. enum-with-`.value` accepted
5. empty/None reason returns `None`
6. unknown reason string returns `None`
7. configuration-shaped reasons (`not_implemented`, `tool_disabled`,
   `config_error`) return `None`
8. `override={"timeout": None}` disables that category
9. `override={"timeout": {...full kwargs...}}` replaces the default
10. **invariants:** every default pattern not in `HEAVY_PATTERNS`;
    `validation_error` is the only category with W < 128;
    `validation_error.patterns == ("TOOL_BAD_CALL",)` and
    `"TOOL_BAD_CALL" in MISTAKE_PATTERNS`

### `tests/test_mistakes_reservoir.py` (new, ~6 tests)

1. `MistakeReservoir()` is empty; `.load() == 0.0`
2. `add("TOOL_BAD_CALL", weight=50)` updates the reservoir; `.load() > 0`
3. repeated adds accumulate; cap at `RESERVOIR_CAP`
4. decay over time toward 0 (advance `now_ts` by 14 days, expect
   ~halving)
5. `to_dict` / `from_dict` roundtrip preserves entries
6. `isinstance(MistakeReservoir(), TraumaReservoir)` (subclass) **but**
   `not isinstance(TraumaReservoir(), MistakeReservoir)` — type
   branching works

### `tests/test_physics.py` extensions (~5 tests)

1. ingesting `Score(patterns=("TOOL_BAD_CALL",), w=120, ...)` increments
   `physics.mistakes` and **does not** change `physics.trauma`
2. ingesting a Score with `TOOL_BAD_CALL` AND a `HEAVY_PATTERNS` member
   (e.g. `("TOOL_BAD_CALL", "BETRAYAL")`) routes to **mistakes**, not
   trauma — pattern check is by `MISTAKE_PATTERNS` first (this also
   serves as documentation that hosts shouldn't co-tag, but the
   behaviour is defined)
3. ingesting many `TOOL_BAD_CALL` events does **not** drift the soul
   via `soul_drift` (run drift after 100 events; soul.w unchanged
   absent trauma load)
4. ingesting a `TOOL_TIMEOUT` Score (W=128, no MISTAKE pattern) does
   **not** touch the mistakes reservoir
5. `_classify` returns `"mistake"` for pattern-tagged events even when
   V/W would otherwise classify as 'negative' (V=80, W=85, patterns =
   `("TOOL_BAD_CALL",)` → 'mistake')

### `tests/test_plugin.py` extensions (~3 tests)

1. `plugin.mistake_pressure()` is `0.0` on fresh agent; non-zero after
   ingesting a `TOOL_BAD_CALL` Score
2. **persistence roundtrip:** ingest TOOL_BAD_CALL → `plugin.save()` →
   construct a second `SoulPlugin` against the same db_path → second
   plugin's `mistake_pressure()` matches the first's (within decay
   tolerance for elapsed wall-clock)
3. **drop-in column migration:** open a v0.x-shaped DB (manually
   create `soul_state` without `mistakes_json` column), construct
   `SoulPlugin`, verify column was added and `mistake_pressure() ==
   0.0`

### Disjointness invariant tests

In `tests/test_physics_config.py` (or wherever pattern-set tests live —
add if absent):

```python
def test_mistake_patterns_disjoint():
    assert MISTAKE_PATTERNS.isdisjoint(HEAVY_PATTERNS)
    assert MISTAKE_PATTERNS.isdisjoint(POSITIVE_PATTERNS)
```

## Out of scope (in Issue B or beyond)

- **The "should the agent double-check?" decision logic.** That's a
  cascade concern — Issue B reads `plugin.mistake_pressure()` and biases
  action selection.
- **Generic tool-failure detection / wrapping.** `score_from_action_failure`
  is a *helper* — the host decides when to call it (e.g. inside a
  try/except around a tool call, or when an `ActionOutcome` reports
  failure). clanker-soul does not provide a tool-failure interceptor.
- **State-context narration of mistake pressure.** Adding "I've been
  making more mistakes than usual" to `compose_state_context` is a UX
  add that fits naturally in Issue B once the cascade decides to
  surface it. Not in Issue A.
- **Per-tool reservoirs.** The reservoir is pattern-keyed — an entry per
  pattern (`TOOL_BAD_CALL`, future `TOOL_RETRY_LOOP`, etc.). Per-tool
  attribution lives in `Score.source` on individual events. A future
  feature could introspect the events table for "which tool has been
  failing"; not needed now.

## CHANGELOG

`[Unreleased]` → `### Added`:

- `score_from_action_failure(reason, *, tool, override)` for attributing
  tool/action failures without dent to self-Worth (except
  `validation_error`, which carries a small W=120 hit). Patterns disjoint
  from `HEAVY_PATTERNS`. Companion to the hermes-only
  `score_from_failover` helper, generalised to the agent's full tool
  surface.
- `MistakeReservoir` (parallel to `TraumaReservoir` /
  `NourishmentReservoir`) accumulating self-attributed-error pressure
  with the same 14-day half-life and `RESERVOIR_CAP=1000`. Persisted
  via the new `mistakes_json` column on `soul_state`, idempotently
  added to v0.x databases.
- `MISTAKE_PATTERNS` frozenset (`{"TOOL_BAD_CALL"}` initially; extensible
  by replacing the constant). Disjoint from `HEAVY_PATTERNS` and
  `POSITIVE_PATTERNS`.
- `SoulPlugin.mistake_pressure() -> float` accessor; `snapshot()` gains
  `"mistake_pressure"` field.
- `EmotionalPhysics(mistakes=...)` kwarg (default empty reservoir).
- `SoulStore.load_mistakes(agent_id)` / `save_mistakes(agent_id,
  mistakes)` — additive methods; the legacy 3-tuple `load()`/`save()`
  signatures are unchanged.

## Drop-in invariant compliance summary

| Surface | v(N) call | v(N+1) behavior |
|---|---|---|
| `SoulStore.load(agent_id)` | unchanged 3-tuple | unchanged 3-tuple |
| `SoulStore.save(agent_id, soul, trauma, nourishment)` | unchanged signature | unchanged signature |
| `EmotionalPhysics(soul, trauma, nourishment, config)` | unchanged positional | unchanged; mistakes defaults to empty |
| `SoulPlugin(agent_id, db_path)` | unchanged | unchanged; loads/saves mistakes internally |
| `plugin.snapshot()` | dict with old keys | dict with old keys + `mistake_pressure` (additive) |
| `plugin.tick()` | drift report dict | unchanged shape; mistakes don't drift soul |
| v0.x DB file | only legacy tables | column added in place; existing rows get `'{}'` |

The check before merging this PR: *"Can a v(N) host upgrade to v(N+1)
without touching their code?"* ✅

## Estimated size

~500 LOC including tests:
- `tool_health.py`: ~150 LOC (mostly the mapping + docstrings)
- `MistakeReservoir`: ~30 LOC
- `SoulStore` migration + load/save methods: ~40 LOC
- `EmotionalPhysics` routing changes: ~15 LOC
- `SoulPlugin` wiring: ~15 LOC
- `__init__` exports + config additions: ~10 LOC
- Tests: ~250 LOC
- CHANGELOG + docstrings: ~30 LOC

One PR, one CHANGELOG entry, no host refactor required, no v0.x
migration scripts, fully covered by the existing release process.
