# Config API + descriptive labels for every knob

**Status:** design (2026-05-11)
**Companion issues:** to be filed from the "Implementation slices" section below.

## Problem

`clanker-soul` exposes a lot of knobs: `PhysicsConfig` (17 fields),
`GovernorConfig` (15 fields including capability profiles),
`PulseConfig` (30+ fields covering 12 trigger thresholds),
`PendingDeltaConfig` (4 status-keyed tuples + a scale + a mode),
`GateConfig` (6 fields, just shipped in #81), plus per-agent
`SoulState` (7 dims). Every one of them is a behavior dial.

Today operators have two paths to tweak them:

1. **Construction-time:** pass `PhysicsConfig(blend_alpha=0.7, ...)`
   to `EmotionalPhysics` / `SoulPlugin`. Requires a code change and a
   restart.
2. **Runtime via `ConfigOverrides`:** the v0.2 schema persists a
   partial-merge override bundle on a SQLite table. The bundled
   FastAPI UI (`clanker_soul/ui/app.py`) exposes a *browser-only*
   HTML form at `/config/override` for `PhysicsConfig` + `SoulState`
   fields.

Neither path lets an **AI agent (with permission)** tweak its own
knobs programmatically. The runtime path only covers two of the five
config classes. And — most importantly for the user-facing framing
that motivates this spec — every knob is named for the engineer who
wrote it, not the operator who will tune it. Fields like
`mood_decay_half_life_base`, `breach_delta`, `dim_resilience_max`,
`distance_trigger`, `level1_w_floor` are precise but opaque to anyone
who hasn't read the physics paper.

The user's framing:

> make sure the clanker-soul has api endpoints that if directed can
> be tweaked by an ai with permission. setting toggles and
> everything. also make sure what every knob button and switch is
> labeled for people who have no fuckin clue what they do, not vague
> naming of them, descript, and make it known

Two requirements bound together:

* **A.** A JSON HTTP API any AI agent with a credential can call to
  read current state, list available knobs, and tweak them — with
  permission scopes preventing accidental damage.
* **B.** Every knob has a human-friendly label and description
  surfaced through the API so a caller (AI or human) with no
  context can understand what it does, what units it's in, what
  the valid range is, and what flipping it actually changes.

This spec addresses both.

## Prior art in the codebase

* **`ConfigOverrides`** (`clanker_soul/overrides.py`) is the
  persistence + partial-merge engine for runtime knob writes. The
  API will write through it, not around it. Anything the API
  supports must already be representable as an `OverrideBundle`
  field (or the bundle gains a new field — see the *physics scope*
  discussion below).
* **`clanker_soul/ui/app.py`** already runs a FastAPI app behind
  `python -m clanker_soul ui --db ./soul.db`. It owns the routing
  surface this spec extends. New JSON routes live alongside the
  existing HTML routes under an `/api/v1/` prefix so the browser UI
  is untouched.
* **`Preset.apply(overrides, agent_id)`**
  (`clanker_soul/presets.py`) writes a full personality profile in
  one call via `ConfigOverrides.set`. The API exposes `Preset` as a
  first-class endpoint — quicker than tweaking 7 soul dims by hand.
* **`memory/feedback_everything_is_a_toggle.md`** is the load-bearing
  project rule: every gating/policy cell is operator-overridable.
  This spec extends that rule from "operator can override in code"
  to "operator can override at runtime via API call with a
  descriptive label explaining what they're doing."

## Architecture line: one registry, two consumers

```
              ┌──────────────────────────────────────────────┐
              │   KnobRegistry (single source of truth)      │
              │   - field path: "physics.blend_alpha"        │
              │   - human label: "Mood blend strength"       │
              │   - description: "How much each event …"     │
              │   - units / range / default / category       │
              └─────────────────┬────────────────────────────┘
                                │
              ┌─────────────────┴────────────────────────────┐
              │                                              │
              ▼                                              ▼
   ┌──────────────────────┐                  ┌──────────────────────────┐
   │  HTTP /api/v1/knobs  │                  │  HTML /config (existing) │
   │  JSON, auth-gated    │                  │  browser form, no auth   │
   │  AI- and CLI-friendly│                  │  unchanged by this spec  │
   └──────────────────────┘                  └──────────────────────────┘
              │
              ▼
   ┌──────────────────────┐
   │  ConfigOverrides     │
   │  (existing v0.2)     │
   └──────────────────────┘
              │
              ▼
   ┌──────────────────────┐
   │  EmotionalPhysics    │
   │  reload_overrides()  │
   └──────────────────────┘
```

The registry is the single source of truth: both the HTTP API and
(later) the HTML form pull labels and descriptions from it. Adding a
new knob requires registering it; if you don't, the API doesn't show
it and the HTML form gets a fallback "raw field name" rendering — a
mild but visible nag to register every new knob.

## Part B first: KnobLabel registry

The label registry is the foundational primitive — both the API and
the docs depend on it. Defining it first means the API and the
"make it known" docs follow naturally from a single inventory pass.

### `KnobLabel` dataclass

```python
@dataclass(frozen=True)
class KnobLabel:
    """Human-readable metadata for one tunable parameter."""

    # ── identity ────────────────────────────────────────────────
    path: str                       # "physics.blend_alpha"
    group: str                      # "physics" | "governor" | "pulse" | "pending" | "cascade.gate" | "soul"
    field_name: str                 # "blend_alpha" — the Python attribute name
    type: str                       # "float" | "int" | "bool" | "str" | "enum:level0,level1,…"

    # ── what the operator sees ──────────────────────────────────
    label: str                      # "Mood blend strength"
    description: str                # 1-3 sentences in plain language
    why_tweak: str                  # "Increase if mood snaps back to soul too fast;
                                    #  decrease if the agent feels like it's flailing."

    # ── bounds + units ──────────────────────────────────────────
    units: str | None = None        # "seconds", "0-255 VADUGWI", "fraction 0-1", None
    min_value: float | None = None  # inclusive
    max_value: float | None = None  # inclusive
    default: object = None          # the constructor default
    enum_values: tuple[str, ...] | None = None   # for enum-typed knobs

    # ── safety + provenance ─────────────────────────────────────
    sensitivity: str = "tune"       # "tune" | "structural" | "dangerous"
    requires_scope: str = "tweak_knobs"  # "read" | "tweak_knobs" | "dangerous"
    affects: tuple[str, ...] = ()   # what flipping this changes — names of
                                    # behaviors the operator might look for,
                                    # e.g. ("breach frequency", "long-term soul drift")
```

`label` and `description` are non-negotiable for any registered knob.
`why_tweak` is the "you have no fuckin clue what this does" answer.
`sensitivity` is the API's safety hint: `"tune"` knobs are normal
dials, `"structural"` knobs change emotional architecture (e.g.
`HEAVY_PATTERNS`), `"dangerous"` knobs can break the agent's
emotional invariants (e.g. disabling breach entirely).

### Three sensitivity tiers, three auth scopes

| Sensitivity   | Scope needed     | Example                                          |
|---------------|------------------|--------------------------------------------------|
| `tune`        | `tweak_knobs`    | `physics.blend_alpha`, `gate.base_probability`   |
| `structural`  | `dangerous`      | `governor.capability_profiles`, pattern sets     |
| `dangerous`   | `dangerous`      | `governor.enable_crisis_lockout`, soul rewrite   |

A token with only `read` scope can list and inspect every knob and
read current values. A token with `tweak_knobs` can write `tune`
knobs but is rejected on `structural` / `dangerous`. A token with
`dangerous` can write all three. Distinguishing the scopes early
keeps the door for "give the agent itself a tuning credential" open
without giving it permission to dismantle its own safety governor.

### Where labels live

Labels live next to the config classes they annotate, not in a
giant central file. Each module exports its labels as a tuple:

```python
# clanker_soul/physics/config.py
PHYSICS_LABELS: tuple[KnobLabel, ...] = (
    KnobLabel(
        path="physics.blend_alpha",
        group="physics",
        field_name="blend_alpha",
        type="float",
        label="Mood blend strength",
        description=(
            "How much each incoming event displaces the agent's mood. "
            "Higher = mood reacts strongly to each event; lower = mood "
            "is sluggish and only the largest events move it."
        ),
        why_tweak=(
            "Raise (e.g. 0.7) for a more reactive agent that feels each "
            "event acutely. Lower (e.g. 0.3) for a phlegmatic agent that "
            "shrugs off most things."
        ),
        units="fraction 0–1",
        min_value=0.0,
        max_value=1.0,
        default=0.55,
        sensitivity="tune",
        affects=("how strongly individual events move mood", "perceived reactivity"),
    ),
    # … one entry per PhysicsConfig field …
)
```

A central `clanker_soul/knobs/registry.py` collects them via
discovery:

```python
class KnobRegistry:
    def __init__(self) -> None: ...
    def register_group(self, labels: Iterable[KnobLabel]) -> None: ...
    def lookup(self, path: str) -> KnobLabel | None: ...
    def list_group(self, group: str) -> tuple[KnobLabel, ...]: ...
    def groups(self) -> tuple[str, ...]: ...

DEFAULT_REGISTRY: KnobRegistry  # populated at import time
```

### Forcing every new field to register a label

A pytest test walks every dataclass in the registered config classes
and asserts that every field has a corresponding `KnobLabel`:

```python
def test_every_physics_field_has_a_label() -> None:
    fields = {f.name for f in dataclasses.fields(PhysicsConfig)}
    labeled = {l.field_name for l in PHYSICS_LABELS}
    missing = fields - labeled
    assert not missing, f"PhysicsConfig fields without KnobLabel: {missing}"
```

One test per config class. Adding a knob without a label fails CI.
This is how the "every knob has a label" invariant is enforced; not
"please remember to add a label" but "you can't merge without one."

## Part A: HTTP API surface

All new routes are JSON-native, prefixed `/api/v1/`. They live in
`clanker_soul/ui/app.py` alongside the existing HTML routes — same
process, same `SoulStore`, same `ConfigOverrides`. The bundled
FastAPI app gains JSON cousins; the existing browser form is
untouched.

### Discovery + read

| Method | Path                                  | Auth scope | Purpose                                                          |
|--------|---------------------------------------|------------|------------------------------------------------------------------|
| GET    | `/api/v1/knobs`                       | `read`     | List every registered knob with full `KnobLabel` metadata.       |
| GET    | `/api/v1/knobs?group=physics`         | `read`     | Filter to one group.                                             |
| GET    | `/api/v1/knobs/{path}`                | `read`     | One specific knob's label + current effective value.             |
| GET    | `/api/v1/agents`                      | `read`     | List known agent_ids in the store.                               |
| GET    | `/api/v1/agents/{agent_id}/state`     | `read`     | Soul + mood + reservoirs + active overrides for one agent.       |
| GET    | `/api/v1/agents/{agent_id}/effective` | `read`     | Effective config after override merge — what physics is using.   |
| GET    | `/api/v1/presets`                     | `read`     | List built-in presets (CHILD / ADULT / BRITTLE / STOIC + custom).|

### Write

| Method | Path                                          | Auth scope        | Purpose                                                       |
|--------|-----------------------------------------------|-------------------|---------------------------------------------------------------|
| PATCH  | `/api/v1/agents/{agent_id}/knobs`             | `tweak_knobs`     | Apply a partial override bundle. Body: `{"physics": {"blend_alpha": 0.7}}`. |
| DELETE | `/api/v1/agents/{agent_id}/knobs/{path}`      | `tweak_knobs`     | Remove one overridden field — reverts to constructor value.   |
| POST   | `/api/v1/agents/{agent_id}/preset`            | `tweak_knobs`     | Apply a named preset.                                         |
| POST   | `/api/v1/agents/{agent_id}/knobs/dangerous`   | `dangerous`       | Apply overrides on `structural` / `dangerous` knobs.          |
| DELETE | `/api/v1/agents/{agent_id}/knobs`             | `dangerous`       | Clear ALL overrides — full revert to constructor values.      |

`PATCH` is the workhorse. The body shape mirrors `OverrideBundle`:

```json
{
  "physics": {
    "blend_alpha": 0.7,
    "breach_threshold": 40.0
  },
  "soul": {
    "w": 165
  }
}
```

Response shape:

```json
{
  "applied": [
    {"path": "physics.blend_alpha", "old": 0.55, "new": 0.7},
    {"path": "physics.breach_threshold", "old": 35.0, "new": 40.0},
    {"path": "soul.w", "old": 175, "new": 165}
  ],
  "rejected": [],
  "effective_now": { /* the merged-effective config after this call */ }
}
```

`rejected` carries `{path, value, reason}` entries when the caller
either tried to set a knob outside `[min_value, max_value]`, used a
knob requiring a scope they don't hold, or referenced an unknown
path. The whole call is **all-or-nothing** — if any field is
rejected, none are applied. This is intentional: a partial-apply
followed by a "you actually needed `dangerous`" rejection is the
worst kind of half-broken state.

### Live reload

After every write, the API calls `physics.reload_overrides()` so the
running agent picks up the change without process restart. This is
already idempotent and partial-merge-safe via `ConfigOverrides`
semantics — the existing v0.2 contract holds, no new schema needed.

### Out of scope for v1

* GraphQL or RPC surfaces — REST+JSON is enough.
* WebSocket push of state changes — clients poll
  `/api/v1/agents/{id}/state` (the browser UI already polls every 2s
  via HTMX).
* MCP server. Listed in *Follow-ons*.

## Auth model: Bearer tokens + scope file

Decisions locked from the scoping question (2026-05-11):

* **Bearer token in `Authorization: Bearer <token>` header.** No
  cookies, no signed payloads, no rotating-secret crypto — keep it
  boring.
* **Tokens live in a JSON file**, path from `CLANKER_SOUL_TOKENS`
  env var (default `~/.config/clanker-soul/tokens.json`):

  ```json
  {
    "tokens": [
      {"id": "carl-agent", "token_sha256": "…", "scopes": ["read", "tweak_knobs"]},
      {"id": "operator",   "token_sha256": "…", "scopes": ["read", "tweak_knobs", "dangerous"]}
    ]
  }
  ```

  Stored as SHA-256 hashes; raw tokens are only printed once by the
  `clanker_soul auth create-token` CLI (described below) at
  generation time.

* **Three scopes, no inheritance graph:** `read`, `tweak_knobs`,
  `dangerous`. Compose by listing all needed scopes on the token.
  Easier to audit than nested hierarchies; trivial to extend later.

* **No auth on HTML routes.** Browser UI keeps current behavior
  (localhost binding by default). The API requires a token always —
  even on localhost — because the loopback assumption can no longer
  be trusted in a world where AI agents on the same host call out
  to network services. The `clanker_soul ui` CLI prints the
  bootstrap token on first run if the token file is empty.

* **Token revocation** is "edit the JSON file." Good enough for a
  local-first learning tool; not building an OAuth grant server.

* **Rate limit** — none in v1. Re-evaluate when we see real
  multi-agent traffic.

### Token CLI (new)

`python -m clanker_soul auth create-token --scopes read,tweak_knobs --id carl-agent`
prints a token once, appends its hash + metadata to the tokens file.
`auth list-tokens` shows ids and scopes (never raw tokens).
`auth revoke-token <id>` removes one entry.

These commands live under a new subparser in `clanker_soul/__main__.py`.

## "Make it known"

The user's third requirement: surface this everywhere. A registered
knob with great labels is invisible if nobody knows the API exists.

### Documentation

* **New `docs/api.md`** — canonical API reference. Generated where
  possible from the registry so labels stay in sync. Hand-written
  intro + auth section + worked examples (curl + Python + how to
  hand a token to a Claude/MCP-ish agent).
* **README section** — new `## Tweaking the agent at runtime`
  section after the existing setup section. Three paragraphs:
  what the API is, how to start the server, how an AI agent can
  call it. Links to `docs/api.md` for full reference.
* **CLAUDE.md** — add a section to the *Reading order* list:
  `docs/api.md` for hosts that want runtime tuning. Add an
  invariant to *Design invariants worth knowing*:
  "Every config field has a `KnobLabel`. Adding a config field
  without one fails CI."
* **`CHANGELOG.md`** — each implementation slice lands its own
  changelog entry per the per-PR contract.

### Discoverability inside the running agent

* The `clanker_soul ui` CLI prints, on startup:

  ```
  clanker-soul UI on http://127.0.0.1:8765
    Browser:  http://127.0.0.1:8765/
    JSON API: http://127.0.0.1:8765/api/v1/knobs   (requires token — see `auth create-token`)
  ```

* The HTML config page (existing browser UI) gains a footer link
  to `/api/v1/knobs` so an operator poking around the form
  discovers the programmatic surface naturally.

* Every knob in the browser form renders its `label` + `description`
  + `why_tweak` as helper text under the input. Today the form
  renders raw field names; after this lands, the field labels match
  what an AI caller sees in the JSON API. One source of truth.

## Implementation slices

Each slice is a separate issue / PR. Order matters because slice 1
is the foundation everything else hangs off.

### Slice 1 — `KnobLabel` + registry + label tests (foundation)

* New package `clanker_soul/knobs/` with `KnobLabel`, `KnobRegistry`,
  `DEFAULT_REGISTRY`.
* `PHYSICS_LABELS`, `GOVERNOR_LABELS`, `PULSE_LABELS`,
  `PENDING_LABELS`, `GATE_LABELS`, `SOUL_LABELS` populated to cover
  every field in their respective dataclasses.
* Pytest test per group enforcing every dataclass field has a label.
* Zero behavior change at runtime — purely additive data.

### Slice 2 — Auth foundation + token CLI

* `clanker_soul/api/auth.py` with `TokenStore`, `verify_token`,
  scope check helpers.
* `auth` subcommand in `clanker_soul/__main__.py` (`create-token`,
  `list-tokens`, `revoke-token`).
* `docs/api.md` draft with auth section only (the read/write
  sections fill in as slices 3 and 4 land).

### Slice 3 — `/api/v1` read endpoints

* `/api/v1/knobs`, `/api/v1/knobs/{path}`, `/api/v1/agents`,
  `/api/v1/agents/{id}/state`, `/api/v1/agents/{id}/effective`,
  `/api/v1/presets` — all read-only, scope `read`.
* JSON responses match the spec above.
* Tests exercise auth (missing token, wrong scope) + each route.

### Slice 4 — `/api/v1` write endpoints

* PATCH / DELETE / preset POST routes.
* All-or-nothing semantics. `rejected` array carries `{path, value,
  reason}` on validation failures.
* Live `reload_overrides` after every successful write.
* Tests for happy path, out-of-range rejection, scope rejection,
  unknown-path rejection, all-or-nothing rollback.

### Slice 5 — Browser form + README + discoverability

* Existing `/config` HTML form pulls labels and descriptions from
  the registry instead of raw field names.
* CLI banner advertises the JSON API.
* README + CLAUDE.md updates land.

### Slice 6 — `dangerous` scope + capability-profile editing

* The `/api/v1/agents/{id}/knobs/dangerous` route lands here, not
  in slice 4. Justification: editing `capability_profiles` is a
  qualitatively different action ("change the safety governor")
  and warrants its own review.
* `STRICT_CAPABILITY_PROFILES` becomes assignable via the API as a
  preset-style atomic switch.

### Slice 7 — Migration polish

* `clanker_soul auth` bootstrap on first run (creates a token file
  with a single `read,tweak_knobs` token, prints it once).
* Token rotation helpers.
* Docs polish + curl snippets.

## Follow-on issues (out of scope)

* **MCP server** wrapping the same registry + auth. Lets MCP-aware
  hosts (Claude Desktop, Cursor) tweak knobs through native MCP
  tool calls without bespoke HTTP plumbing. Big surface, separate
  issue.
* **Audit log** of every API write — useful for forensics, but the
  existing event log already records every `ConfigOverride`
  application. We may simply add an `api_caller_token_id` column.
* **Webhook on state change** — for ops dashboards that want push
  rather than poll. Not needed for AI-agent self-tuning.
* **Live overrides UI** that an AI agent could navigate — likely
  belongs as an MCP resource, not an HTML page.

## Open questions

1. **Default bind address for the API.** Loopback only by default,
   or `0.0.0.0` so containerised hosts can expose without extra
   flags? Recommendation: loopback by default, `--bind 0.0.0.0`
   opt-in, refuse to start `0.0.0.0` if the tokens file is empty
   (no-auth + open binding is a footgun).
2. **Does `SoulPlugin` need a flag to opt the API out entirely?**
   For embedded uses where exposing a token-gated HTTP server is
   overkill. Recommendation: yes, `SoulPlugin(api=False)` would
   short-circuit the auto-registration if we ever auto-register.
   Today the UI is already explicit (`python -m clanker_soul ui`),
   so this may be moot.
3. **Should `PulseConfig` be exposed in v1 or deferred?** It has
   30+ fields and three of them are arousal floors that need
   coordinated tuning. Recommendation: expose in v1 with a
   `category: "pulse.thresholds.distress"` etc. grouping so a
   caller can find related knobs without reading the source.

## Acceptance criteria

* Every field in `PhysicsConfig`, `GovernorConfig`, `PulseConfig`,
  `PendingDeltaConfig`, `GateConfig`, and the personality fields of
  `SoulState` has a `KnobLabel` with non-empty `label`,
  `description`, and `why_tweak`.
* CI rejects PRs that add a config field without a corresponding
  label.
* An AI agent holding a `tweak_knobs` token can:
  - list every knob with its description in one GET call
  - read its agent's current state in one GET call
  - write a partial override in one PATCH call
  - confirm the live agent picked up the change
* A token with only `read` scope is rejected on writes; a token
  with `tweak_knobs` scope is rejected on `structural` /
  `dangerous` knobs.
* The README's setup section points at `docs/api.md`.
* `python -m clanker_soul ui` prints the JSON API URL on startup.
