# Changelog

All notable changes to `clanker-soul` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.18.0] — 2026-05-25

### Changed

- **Project license changed from AGPL-3.0 to MIT.** All future releases ship under the MIT license.
- **Hermes scorer now routes through clanker-soul's own pattern system.**
  ``KeywordScorer`` (17 ad-hoc regex patterns with hand-coded deltas) replaced
  by ``ClankerScorer`` — 35+ patterns mapped to clanker's native
  ``POSITIVE_PATTERNS`` and ``HEAVY_PATTERNS`` sets so the physics engine's
  ``_classify()`` routes correctly into trauma/nourishment/breach/correction
  pathways. VADUGWI baselines are set per-class so the V/W heuristic agrees
  with pattern-based routing. ``KeywordScorer`` kept as backward-compatible
  alias. Old scorer never triggered breach because its pattern names didn't
  match clanker's heavy set; the new one does.
- **Hermes plugin.yaml now declares ``on_inference_failure`` hook.** Previously
  the plugin implemented the hook but didn't list it in the manifest, so
  hermes agents that validate hooks against the manifest would silently skip
  inference-failure ingestion.

### Added

- **Local Hermes M4 evidence (#86).** Added
  ``integrations/hermes/EVIDENCE_M4_HERMES_LOCAL.md`` documenting a real
  installed-Hermes ``hermes -z`` run with ``clanker-soul`` as active memory
  provider, persisted DB evidence, and the remaining outbound-channel
  limitation.
- **Hermes M4 cascade smoke (#86).** New
  ``integrations/hermes/scripts/m4_idle_cascade_smoke.py`` initializes the
  Hermes memory provider, runs the shipped contemplation corpus through
  ``IdleLoop`` and ``ActionRegistry``, ingests a consequence, and verifies
  provider tool state without network access.
- **Broader M4 action tags (#86).** ``tags_from_delta()`` now emits
  conservative defaults for joy, anger, boredom, loneliness, excitement,
  contentment, disgust, curiosity, and restlessness instead of falling
  through to an empty tag set for those common mood shapes.
- **M4 idle-cascade example (#86).** Added
  ``examples/05_m4_idle_cascade.py`` showing the opt-in path from the
  shipped contemplation corpus through ``IdleLoop``, ``tags_from_delta()``,
  ``ActionRegistry``, action handling, and consequence ingestion.
- **M4 action-tendency tag defaults (#83).** ``tags_from_delta()`` now
  implements the researched sadness, anxiety, shame, grief, pride-trap,
  and fear-freeze mappings from
  ``docs/research/m4_action_tendencies.md``. The mapper remains
  conservative: quiet deltas and unrecognized mood shapes still return
  no tags, while the documented low-worth/low-agency and shame traps
  prefer withdrawal/isolation over generic reach-out.
- **Plugin manifest data model (#54 Slice 1).** New
  ``clanker_soul.plugins`` package with frozen ``PluginManifest``,
  ``manifest_from_dict()``, ``parse_manifest_json()``, and
  ``STANDARD_PLUGIN_KINDS``. The parser validates required
  ``plugin.json`` fields, lowercase/underscore names, semantic versions,
  standard kinds, ``module:attribute`` entrypoints, dependency names, and
  list-shaped metadata. Invalid JSON or schema errors soft-fail with a
  WARNING and return ``None`` so hosts can skip broken plugin folders
  without aborting boot.
- **Plugin master-file parser (#54 Slice 2).** New ``MasterEntry``,
  ``parse_plugins_toml()``, and ``overlay_settings()`` parse the v1
  ``plugins.toml`` shape (``enabled``, ``priority``, inline
  ``settings``) without adding runtime dependencies. Missing master files
  return an empty mapping; malformed lines and wrong-shaped values warn
  and fall back to safe disabled/default entries.
- **Plugin reference loader (#54 Slice 3).** New ``PluginLoader`` and
  ``LoadedPlugin`` discover ``*/plugin.json`` folders, filter by
  ``plugins.toml`` enablement, warn on missing env vars, skip
  incompatible host-version ranges, skip missing dependencies, topological
  sort dependency order ahead of priority order, import entrypoints, and
  return resolved manifest/master settings for host registration.
- **Plugin manifest docs and reference plugin (#54 Slice 4).** New
  ``docs/plugins.md`` quick-start plus
  ``examples/plugins/hello_world/`` validate the reference loader against
  a real manifest-folder plugin.
- **M4 ContemplationCorpus defaults (#84).** New
  ``DEFAULT_CONTEMPLATION_FACES`` and
  ``build_default_contemplation_corpus()`` expose 978
  ``idle_introspection`` ``PromptFace`` entries across identity, bodily,
  comparative, curious, creative, existential, present, future, past,
  and relational introspection categories. Every shipped face has an
  explicit ``vadugwi_affinity`` so it is safe for
  ``EmotionalPhysics.contemplate()`` and ``IdleLoop`` use. The generated
  source fragments are also preserved under
  ``docs/research/m4_contemplation_corpus_draft/`` as ``*.pyfrag``
  provenance artifacts.
- **PromptFace affinity calibration helper (#114).** New
  ``clanker_soul.tools.calibrate_face`` helper scores a ``PromptFace``
  repeatedly through any ``Inference`` implementation and returns a
  ``CalibrationReport`` with static affinity, median score, population
  standard deviation, per-dimension divergence, max absolute delta, and
  an advisory recommendation. This is intentionally non-mutating; authors
  use the report to decide whether a static VADUGWI affinity should be
  adjusted.
- **M4 action registry + tag-based selection (#82).** New
  ``clanker_soul.cascade.action`` module with ``RegisteredAction``,
  ``ActionRegistry``, ``ActionThresholdConfig``, ``tags_from_delta``,
  and ``should_act``. ``IdleLoop`` now accepts an optional
  ``registry=`` and, when present, runs Roll 2/3 after contemplation:
  delta thresholding, tagged weighted action sampling, handler dispatch,
  and consequence ingestion back into physics. With no registry, existing
  gate+contemplate behavior is unchanged.
- **M4 tool-failure response cascade (#98).** ``PulseEngine`` now emits
  ``stuck_impulse`` from sustained ``mistake_pressure`` and
  ``obstructed_impulse`` from recent external ``TOOL_*`` failures.
  ``mistake_aware_tags`` and ``confide_proxy_score`` extend cascade
  selection with tags such as ``troubleshoot``, ``file_issue``,
  ``journal_distress``, ``confide``, ``reflect``, and
  ``withdraw_silent``. ``IdleLoop`` can union those tags into normal
  contemplation-driven action selection, or run a reservoir-driven
  action when no contemplation face is active. ``compose_state_context``
  now narrates elevated mistake pressure as a constructive verification
  signal.
- **Tool-failure attribution + MistakeReservoir (#97).** New
  ``clanker_soul.tool_health`` module with two helpers:
  ``score_from_action_failure(reason, *, tool, override)`` produces
  an ingestable ``Score`` for tool/system failures (timeout, rate
  limit, denied, validation_error, etc.) — all with
  ``direction="OBSERVATION"`` and patterns disjoint from
  ``HEAVY_PATTERNS`` so a rate-limit never triggers the breach
  mechanic. ``validation_error`` is the **only** category that dents
  Worth (W=120) and emits ``TOOL_BAD_CALL`` — routed to the new
  ``MistakeReservoir`` for accumulating self-doubt.
  ``score_from_correction(*, tool, after_mistakes, kind)`` produces
  the post-resolution Score. Two emotional shapes per the
  Sweeny/Vohs/Bandura literature: pride-shaped (``kind="tool_fix"``
  / ``"problem_solved"`` / ``"recovery"``) — durable W and D
  affirmation scaling with ``after_mistakes``; relief-shaped
  (``kind="relief_exhaustion"``) — flat A/U drop, no pride
  integration, *still* relieves the reservoir. Configuration-shaped
  reasons (``not_implemented``, ``tool_disabled``, ``config_error``)
  return ``None`` — operator concerns, not agent experiences.
- ``MistakeReservoir`` (parallel to ``TraumaReservoir`` /
  ``NourishmentReservoir``) accumulates self-attributed errors on
  the same 14-day half-life. Includes ``relieve(weight)`` for
  active decrement on correction events; spreads relief
  proportionally across current pattern entries, capped at the
  current load (never goes negative). Persisted via the new
  ``mistakes_json`` column on ``soul_state``, added idempotently
  to v0.x databases via the same ``PRAGMA table_info`` pattern as
  ``pulse_log.face_id``.
- ``MISTAKE_PATTERNS`` (``{"TOOL_BAD_CALL"}``) and
  ``CORRECTION_PATTERNS`` (``{"RECOVERY", "TOOL_FIX",
  "PROBLEM_SOLVED"}``) frozensets, replaceable by operators.
  ``CORRECTION_PATTERNS ⊆ POSITIVE_PATTERNS`` by construction —
  corrections are a kind of nourishment with extra mistake-relieving
  behavior.
- ``PhysicsConfig.mistake_pressure_floor`` (50.0),
  ``mistake_wounding_rate`` (0.0003 — deliberately weaker than
  trauma's ``wounding_rate``),
  ``correction_relief_factor`` (1.0 — tune to 0.0 to disable
  active relief), ``resilience_correction_floor`` (50.0), and
  ``recovery_resilience_rate`` (**0.0 by default — OFF**;
  operators opt in to the soul-uplift dynamic by setting to
  ``0.0003`` or higher).
- ``soul_drift`` now applies (1) a mild W/V wear when mistakes
  load exceeds floor — G untouched, distinct shape from trauma's
  wear — and (2) a slow W/D uplift when correction load exceeds
  floor AND dominates mistakes load (the "winning the correction
  contest" gate). Default rate is off; opt-in encodes resilience
  from mastery per ``docs/research/m4_failure_response_matrix.md``.
- ``TraumaReservoir.by_pattern_filtered(patterns, *, now_ts)`` —
  decayed-sum query restricted to the named patterns. Lives on
  the base class so all three reservoirs inherit it.
- ``SoulPlugin.mistake_pressure() -> float`` accessor for hosts
  (and the M4 cascade in #98) to bias behavior toward verifying
  tool calls. ``snapshot()`` gains the additive
  ``"mistake_pressure"`` field; ``tick()`` drift report gains
  ``mistakes_load`` / ``correction_load`` / ``resilience_uplift``.
- ``EmotionalPhysics(mistakes=...)`` kwarg (default empty
  reservoir) — additive.
- ``SoulStore.load_mistakes(agent_id)`` /
  ``save_mistakes(agent_id, mistakes)`` — additive methods; the
  legacy 3-tuple ``load(...)`` / ``save(...)`` signatures are
  unchanged. Drop-in invariant preserved: a v(N) host upgrading
  to v(N+1) writes zero new code and behaves identically.

### Changed

- ``POSITIVE_PATTERNS`` extended with ``RECOVERY``, ``TOOL_FIX``,
  ``PROBLEM_SOLVED`` so corrections register as nourishment
  alongside their mistake-relieving role. Existing patterns
  unchanged.
- ``EmotionalPhysics._classify`` now also returns ``"mistake"``
  and ``"correction"``. Routing priority is **mistake → correction
  → positive → negative** so a Score that somehow carries both a
  mistake and a correction pattern routes to mistake (pessimistic
  branch wins — host-bug protection). The
  ``events.classification`` column gains the two new possible
  values; existing readers (notably the governor's
  ``_fetch_recent_significant_events`` filter) ignore them.

- **M4 cascade entry point: ``IdleLoop`` + Roll 0 gate (#81).** New
  ``clanker_soul.cascade`` package ships the heartbeat tick the M4
  Autonomous Motivation Cascade is built around. Each ``await
  loop.tick()`` rolls a *gate* (mood-arousal-biased probability,
  honoring action / contemplation / quiet cooldowns); when the roll
  passes, the loop samples a face from a host-supplied contemplation
  corpus (``trigger_kinds={"idle_introspection"}`` by default) and
  runs ``physics.contemplate``. The mood shifts; ``TickResult``
  reports gate decision, probability, skip reason, sampled face,
  contemplation deltas, and elapsed wall time. Three cooldowns are
  independently configurable: ``cooldown_after_action_s`` (host
  calls ``loop.note_action()`` from its dispatch path),
  ``cooldown_after_contemplation_s`` (loop tracks internally),
  ``min_quiet_s`` (host calls ``loop.note_event()`` from its ingest
  path). When a ``note_*`` was never called, the corresponding
  cooldown is treated as already-elapsed — drop-in safe. Custom
  gate logic replaces ``default_gate`` wholesale via ``gate_fn=``;
  ``GateConfig`` is the operator-overridable knob table for
  everything else (defaults: ~5%/tick base probability, 300s
  action cooldown, 60s contemplation cooldown, 30s min quiet).
  ``IdleLoop`` is opt-in — ``SoulPlugin`` does not construct one
  automatically. Action selection (Roll 2 + Roll 3) lands separately
  in #82.

### Fixed

- **M4 cascade contemplation cooldown (#105).** ``IdleLoop`` now updates
  its last-contemplation timestamp only when a contemplation face is
  actually sampled. Empty or temporarily ineligible corpora no longer
  make ``seconds_since_last_contemplation()`` report a fresh thought or
  block the next eligible contemplation behind the global cooldown.

## [0.17.0] — 2026-05-10

### Added

- **Host integration guide + runnable reference host (#85).** New
  ``docs/host-integration.md`` documents the three host-side wirings
  every integration MUST do for clanker-soul to actually shape
  behavior: (1) inject ``plugin.state_context()`` into every agent
  turn so the agent has self-awareness of its own mood,
  (2) persist contemplations as first-person memory entries
  (*"I found myself wondering: …"*) so the inner life feels
  continuous, (3) frame contemplations as introspection-not-attack
  at delivery (explicit ``source: "internal_introspection"`` in the
  context dict) so the model doesn't go defensive on spontaneous
  thoughts. New ``clanker_soul.examples.reference_host`` is a
  runnable copy-paste starting point — in-memory memory, stdout
  printer, rule-based ``Inference`` impl (no LLM dependency),
  end-to-end demo of all three wirings (``python -m
  clanker_soul.examples.reference_host``). README updated with a
  pointer to the guide and the wirings summary; ``SoulPlugin``
  docstring lead now flags the three requirements. 7 new smoke
  tests verify the demo runs and that the wirings produce
  observable effects.
- **M4 ``Inference`` protocol + ``SoulPlugin`` wiring (#79).** New
  ``clanker_soul.Inference`` Protocol — a ``runtime_checkable`` async
  seam with two methods, ``score(text, context) -> Score`` and
  ``act(action) -> ActionOutcome``. clanker-soul itself never imports
  any model SDK; concrete impls live in host code or companion
  packages. ``SoulPlugin`` accepts three new mutually-compatible
  kwargs: ``inference=`` (single impl covers both roles, simplest
  config — pure-local Qwen / Phi / etc.), ``scorer=`` and ``actor=``
  (split backends per role for hosts that want cheap local for
  scoring + deliberate cloud for acting). When only ``inference`` is
  passed, ``plugin.scorer is plugin.actor is plugin.inference``.
  When ``scorer``/``actor`` are passed, they win over ``inference``
  for their role. When *no* impl is wired for a role, the property
  returns a sentinel that raises ``RuntimeError`` *on use*, not on
  access — construction never fails for hosts that don't yet wire
  inference. ``Inference`` is exported from the top-level package.
  Foundation for #81 (heartbeat) and #82 (action registry).
- **M4 contemplation primitive (#80).** New
  ``EmotionalPhysics.contemplate(face, *, weight_scale=1.0)`` ingests
  a ``PromptFace.vadugwi_affinity`` as a synthetic mood-shift —
  *without* updating soul reservoirs, triggering breach, or writing
  an event-log ``IngestRecord``. A thought is not an event: only mood
  moves. Returns a frozen ``ContemplationResult(pre_mood, post_mood,
  delta, score)`` so downstream cascade layers (heartbeat in #81,
  action selection in #82) can route off the *direction* of the shift,
  not just absolute state. Mood-anchored: the blend starts from
  current mood (or the soul-projected fallback for first-ever
  contemplation), so affinity acts as a target the mood drifts toward,
  not an absolute jump. Existing ``soul_armor`` × ``dim_resilience``
  machinery keeps personality load-bearing — a high-W agent
  contemplating the same face moves measurably less than a low-W
  agent. ``ContemplationResult`` exported from the top-level package.
- **``PromptFace.vadugwi_affinity`` field.** Optional 7-tuple in
  V/A/D/U/G/W/I order (each 0-255) describing the mood-shape a face
  evokes when contemplated. Distinct from ``vadugwi_predicates``
  (which gates *eligibility*): affinity says "if the agent thinks
  about this, mood will tint toward this 7-vector." Defaults to
  ``None`` — backward-compatible; existing faces without affinity
  raise a clear ``ValueError`` when passed to ``contemplate`` rather
  than guessing a default. Construction-time validation enforces
  7-tuple length and 0..255 range when set.
- **M4 thought-content research seed (#87).** Initial Gemini deep-research
  output saved as `docs/research/m4_thought_content_corpus.md` —
  framework grounded in mind-wandering / rumination-reflection /
  non-clinical intrusive-thought literature plus 10 themed clusters of
  introspective prompts and 5 branching trees. Seed material for #84
  (ContemplationCorpus content). Formatting quirks preserved as-is;
  cleanup and expansion to ≥500 entries happen during #84
  implementation.
- **M4 action-tendency research seed (#83).** Companion deep-research
  output saved as `docs/research/m4_action_tendencies.md` — VADUGWI
  topology mapped to action tendencies via appraisal theory, personality
  moderators (self-worth × agency × attachment × extraversion),
  Lazarus & Folkman coping taxonomy, and explicit mood-incongruent
  traps (Shame Paradox, Pride Trap, Fear-Freeze). Includes structured
  action-tendency matrix entries for sadness/anxiety/shame/grief.
  Seed material for #82 (action registry + `tags_from_delta()`
  defaults). Cleaned from a Google-Docs HTML-saturated paste on #83.

## [0.16.0] — 2026-05-09

### Changed

- **M3.4 live demo consolidated to a single SoulPlugin / single DB
  (#74, #75).** Previously the demo created four separate
  ``SoulPlugin`` instances over four separate temp DB files (one per
  scenario step), passed branch-corpus faces via the engine-only
  ``corpus=`` kwarg (in-memory, not persisted), and required pointing
  ``clanker-soul faces`` at four DBs to see all four fires. Now: one
  shared ``SoulPlugin(agent_id="m34demo",
  extra_corpus=combined_faces, replace_corpus=True)`` covers every
  step. Branch + anchored faces are persisted to ``prompt_corpus`` so
  ``clanker-soul faces --db <demo.db>`` lists all four fires with the
  ``motif`` column populated for every row. Closer to how a real
  hermes deploy operates and teaches the right mental model.
  ``_build_engine`` defaults ``corpus=`` to ``plugin.corpus`` so all
  engines automatically use the persisted set; the ``corpus=``
  override is preserved for transient sampling flows. Cooldown
  management between steps is explicit
  (``plugin.recency.note_fired(...)``) rather than implicit via DB
  switching. EVIDENCE_M3.4.md regenerated against the consolidated
  run.

### Added

- **`PendingDeltaConfig.delta_mode` — relative-anchor mode for
  saturated agents (#73).** New ``Literal["absolute", "relative"]``
  field on ``PendingDeltaConfig`` (default ``"absolute"`` — preserves
  v0.15 behavior bit-exact). In ``"relative"`` mode the synthetic
  ``Score`` ingested for a resolved pending is anchored at the
  agent's *current* mood for each dim instead of neutral 128, so a
  positive delta always lands above current mood (positive blend
  pressure regardless of starting point) and a negative delta always
  lands below. This fixes a structural issue surfaced by the live
  pending demo: an agent at high W (172) cannot be lifted by
  ``acknowledged_fast``'s +4W in absolute mode because the scaled
  Score W=168 lands below current mood and gets absorbed by blending.
  Relative mode produces the spec-intended positive lift on
  saturated dims. Falls back to absolute when ``physics.mood`` is
  unset (no prior ingest). New ``_to_score_dim(delta, scale, *,
  anchor=128)`` signature replaces the implicit-128 anchor — the
  ``anchor`` kwarg is what ``"relative"`` populates per dim. 6 new
  unit tests cover the default mode, relative anchoring at high mood,
  bit-exact absolute regression, mood-unset fallback, negative delta
  direction, and 0/255 clamping in relative mode.

- **`clanker-soul faces` CLI subcommand (#69).** New local-ops command
  for auditing pulse activity from the terminal:
  ``clanker-soul faces --db PATH [--limit N] [--agent ID]
  [--since YYYY-MM-DD] [--by-face FACE_ID] [--motif MOTIF]
  [--dispatched-only]``. Reads ``pulse_log`` LEFT JOIN-ed with
  ``prompt_corpus`` so each row shows ``ts | agent | trigger |
  face_id | motif | fired | suppressed``. Most-recent-first;
  empty-result is rc 0 (not an error). Width-aware on a TTY,
  tab-separated when piped (so `awk` / `sort` / `cut` work). Default
  ``--limit 20``. Useful for spot-checking a live agent's recent
  trigger/face distribution without standing up the dashboard. 9 new
  tests covering each filter axis, the empty-result path, and the
  invalid-date error path.

- **`PendingActionStore.all_pending(agent_id=None)` — global view of
  unresolved pendings (#68).** New Protocol method returning every
  still-pending action (status="pending") across every surface,
  ordered oldest-first by `fired_at`. Implemented on
  `InMemoryPendingActionStore` (single-agent; `agent_id` is accepted
  for Protocol parity but ignored) and `SqlitePendingActionStore`
  (current schema is one table per agent — `agent_id` is advisory; a
  future schema migration adding an `agent_id` column would let one
  store back multiple agents). New `PendingCoordinator.all_pending()`
  convenience that forwards the coordinator's configured `agent_id`.
  Useful for status panels, debug UIs, and governance hooks that want
  to back off triggers when the agent has many unanswered pulses out.
  8 new tests covering empty store, oldest-first ordering across
  surfaces, exclusion of resolved/expired rows, SQLite cross-restart
  persistence, and the coordinator-level convenience.

- **`PendingDeltaConfig.delta_scale` is now operator-tunable (#67).**
  Promotes the previously-hardcoded ``delta_scale = 10`` multiplier
  in ``PendingCoordinator._apply_delta`` to a field on
  ``PendingDeltaConfig`` (default 10.0). Hosts with different physics
  curves than the project default can now tune mood-delta intensity
  without forking ``pending.py``. ``_to_score_dim`` accepts a float
  scale and rounds when computing the integer Score dim. 4 new unit
  tests cover the default value, custom scaling, [0, 255] clamp under
  extreme scales, and fractional-scale rounding.

- **`LLMOutcomeClassifier` — first-class LLM-backed classifier (#64).**
  Promotes the inline LLM classifier from the #57 live demo into a
  shipped `clanker_soul.pending.LLMOutcomeClassifier`. Generic
  `call_model: Callable[[str, str], str]` callable so the classifier
  isn't coupled to any specific provider — pass an OpenRouter wrapper,
  Anthropic SDK call, Ollama call, or a stub for tests. Ships the
  validated system prompt from the demo. Falls back to `unrelated`
  on parse failure or `call_model` exception (matches the
  `OutcomeClassifier` soft-fail invariant). Substring-matches the
  four label words in priority order so robust to padded responses
  like "I'd say that's acknowledged.". Last raw response is captured
  on the instance for debugging. New type exported from
  `clanker_soul`: `LLMOutcomeClassifier`. The hermes pending demo
  (`integrations/hermes/scripts/pending_action_live_demo.py`) now
  uses this shipped class via a tiny adapter that wraps OpenRouter
  HTTP into the `(system, user) -> str` shape — proves the same
  classifier works against real DeepSeek V3 Flash inference. 12 new
  unit tests using stub `call_model` callables (no network).

## [0.15.0] — 2026-05-09

### Added

- **PendingAction tracking + outcome classification (#57).** New
  module `clanker_soul.pending` extends the action loop to actions
  whose outcome arrives later — or never. `PendingAction` is a frozen
  dataclass capturing the action body, surface key, soul snapshot at
  firing, expected response, status, and TTL. `PendingActionStore`
  Protocol with two reference impls: `InMemoryPendingActionStore`
  (default, process-local) and `SqlitePendingActionStore` (durable,
  reuses the `SoulStore` connection, survives process restart via
  the new `pending_actions` table). `OutcomeClassifier` Protocol with
  a trivial `KeywordOutcomeClassifier` reference impl that parses
  `expected_response` strings like `"ack:hi,hello;ignore:cancel,no"`.
  `PendingCoordinator` orchestrates the full loop: `record(pending)`
  on fire, `observe(surface_key, observation)` on inbound (runs the
  classifier, marks resolved status, applies the configured mood
  delta as a synthetic `Score` ingested into physics), `tick(now)` to
  expire stale pendings. `PendingDeltaConfig` lets operators tune the
  per-status mood deltas — defaults match the spec table
  (acknowledged_fast +6V/+4W, acknowledged_late +3V/+2W, mixed
  -2V/-2W, ignored -8V/-6W/-3G/-2I, expired -3V/-3W/-2G).
  `SoulPlugin.build_pending_coordinator(classifier, *, store=,
  delta_config=, durable=True)` is the documented one-call helper —
  durable defaults to True (SQLite), `store=` lets hosts plug in a
  custom impl. New types exported from `clanker_soul`:
  `PendingAction`, `PendingStatus`, `ClassifyOutcome`,
  `PendingDeltaConfig`, `PendingActionStore`,
  `InMemoryPendingActionStore`, `SqlitePendingActionStore`,
  `OutcomeClassifier`, `KeywordOutcomeClassifier`,
  `PendingCoordinator`, `ResolutionResult`. 33 new unit tests covering
  data model, both stores, the keyword classifier, the full
  coordinator loop including classifier soft-fail and SQLite
  persistence across restart.

- **`PulseDispatcher` — generic host-agnostic action router (#53).**
  New `clanker_soul.pulse.dispatcher.PulseDispatcher` turns any
  :py:class:`PulseAction` into a real-world effect via constructor-
  injected handler callables and returns an :py:class:`ActionOutcome`
  for the soul to learn from. Routes by `action.kind` to the right
  subsystem: `signal_sender` for `direct_message`, `tool_executor` for
  `tool_invocation`, `browse_handler` for `browse_topic`,
  `post_handlers[platform]` for `post_public`,
  `reply_handlers[platform]` for `comment_reply`, `withdraw_handler`
  for `withdraw`. Soft-fail by default — handler exceptions become
  `delivered=False, note="dispatch_exception:..."` rather than
  propagating up. Sync/async bridging via `_maybe_await`. Anything left
  None falls through to a not-wired stub so a fresh integration can
  run the engine end-to-end on day one with observable-but-no-op
  enactment, then turn each subsystem on independently. New types
  exported from `clanker_soul`: `PulseDispatcher`, `ActionHandler`.
  22 hermetic unit tests.

- **M3.4 — branch trees + memory anchors.** New
  `clanker_soul.pulse.corpus.branch_bias` returns a 1.5× multiplier
  when a face's `branch_keys` contains the immediately previous
  delivered face id, so follow-up faces win the dice more often than
  independent rolls would. `PromptCorpus.faces_for` and `.sample` gain
  an optional `previous_face_id=` kwarg. `compose_self_prompt` and
  `compose_self_prompt_with_face` thread it through. `PulseEngine`
  gains a constructor kwarg `previous_face_id=` (so hosts can seed
  state from disk on first build) and tracks the last delivered
  face id in `_previous_face_id`, updated only on delivered fires —
  gated/dropped attempts don't poison the branch chain.
  `SoulPlugin.most_recent_face_id()` reads the freshest
  `dispatched=1, face_id IS NOT NULL` row from `pulse_log` so hosts
  can reconstruct branch state across process restart. Memory anchors
  (already plumbed end-to-end in M3.1) are now formally documented
  on `PulseHost` as an optional `memory_topics_present(topic) -> bool`
  hook (still runtime-detected via `getattr` so existing hosts are
  unaffected). Live demo at
  `integrations/hermes/scripts/m3_4_live_demo.py` proves the
  distribution shift (147/53 child:sibling with parent hint vs 134/66
  without) and shows the engine actually firing the branched child
  face after the parent enters cooldown; memory-anchor scenario shows
  the anchored face filtered when the host has no memory of the topic
  and winning the dice when it does. Captured at
  `integrations/hermes/EVIDENCE_M3.4.md` + `logs/m3_4_live_demo.log`.

- **M3.3 — corpus persistence + cross-restart cooldown.** New
  `clanker_soul.pulse.corpus_store.CorpusStore` wraps the
  `SoulStore` connection and provides face CRUD (`save_face`,
  `save_faces`, `load_faces`, `retire_face`, `replace_all`,
  `count_faces`) plus per-agent recency upserts (`note_fired`,
  `load_recency`). New `PersistentRecencyLog(corpus_store, agent_id)`
  is a drop-in replacement for the in-memory `RecencyLog` that
  preloads from disk on construction so face cooldowns survive
  process restart. The schema gains two tables — `prompt_corpus`
  (faces, source-tagged + soft-deletable via `retired_at`) and
  `face_recency` ((agent_id, face_id) primary key with
  `last_fired_at` + `fire_count`). The existing `pulse_log` gains a
  `face_id` column via in-place `ALTER TABLE` — agents that ran on
  v0.2 keep working unchanged. `SoulPlugin` learns two new kwargs
  (`extra_corpus=Iterable[PromptFace]`, `replace_corpus: bool`) and
  exposes `plugin.corpus`, `plugin.corpus_store`, `plugin.recency`
  properties. First-run plugins seed `DEFAULT_FACES`; subsequent
  opens preserve operator edits and retirements. New types exported
  from `clanker_soul`: `CorpusStore`, `PersistentRecencyLog`. New
  `PulseEngine` kwarg `recency=RecencyLog | None` lets hosts inject
  the persistent variant. `PulseRecord.face_id` is now stamped on
  every dispatched pulse so log analysis can answer "which faces
  actually fire" without reparsing prompt text.

- **M3.2 — corpus wiring + baseline default corpus.** `compose_self_prompt`
  now accepts an optional `corpus`, `situation_tags`, `memory_topics_present`,
  `recency`, `now`, and `primed` — when a corpus is supplied the engine
  rolls a weighted die over eligible faces and renders the chosen
  template via `str.format` against a curated namespace
  (`trigger_kind`, `state_line`, `idle_min`, `trauma_load`,
  `nourishment_load`, `peers`, plus per-dim `mood_v` / `soul_v` etc.).
  Falls back to the legacy deterministic prompt when no face is eligible
  OR when a template references unknown keys — the engine never goes
  silent because the corpus has gaps. New `compose_self_prompt_with_face`
  variant returns the sampled face alongside the rendered string for
  hosts that want to log which face fired.
- **`clanker_soul.pulse.corpus_defaults`** — ships 49 baseline faces
  (`DEFAULT_FACES`) covering all 12 trigger kinds × four motifs ×
  major situational gradients. `build_default_corpus(rng=, extra=,
  replace=)` factory lets hosts append their own faces (carl phone
  curiosity, persona-specific responses) or replace the baseline
  entirely. Face ids follow the `core.<trigger>.<motif>.<handle>`
  convention so host-extended ids (e.g. `carl.phone.curiosity.scroll`)
  never collide.
- **`PulseEngine` corpus + recency wiring.** New `corpus=` constructor
  kwarg; engine threads `situation_tags` through, calls the optional
  host hooks `situation_tags(trigger)` and `memory_topics_present(topic)`
  via `getattr` (existing `PulseHost` implementations are unaffected),
  records each delivered face fire in an in-memory `RecencyLog` so
  cooldowns apply within the session. SQLite-backed recency persistence
  lands in M3.3. The engine stamps `extra={"face_id": ...}` on every
  `PulseAction` so consequence/audit logs can correlate model output
  back to the face that produced it.
- **Live LLM evidence.** `integrations/hermes/scripts/m3_2_live_demo.py`
  drives a real `SoulPlugin` through five emotional states, samples
  the default corpus, and sends one prompt per state through DeepSeek
  V3 Flash via OpenRouter. The captured run is committed at
  `integrations/hermes/EVIDENCE_M3.2.md` (alongside the existing M2
  `EVIDENCE.md`); `logs/m3_2_live_demo.log` carries the raw terminal
  log of the same run.
- **`clanker_soul.pulse.corpus`** (M3.1) — pure in-memory `PromptCorpus`
  + sampler. Replaces the static `compose_self_prompt(trigger)` mapping
  with a weighted dice over candidate `PromptFace`s, each tagged with
  trigger eligibility, AND-combined `VadugwiPredicate` constraints
  (mood / soul / primed layers), situational tags (any-of or all-of),
  optional memory anchors, and recency cooldown. Selection weight is
  `base_weight × vadugwi_affinity × novelty × motif_bias`. Four motifs
  (`informational`, `relational`, `exploratory`, `regulatory`) up-weight
  the right kind of prompt for the agent's current shape — relational
  comfort wins over informational explanation when the agent is shaken
  with low W. New types exported from `clanker_soul`: `PromptCorpus`,
  `PromptFace`, `VadugwiPredicate`, `RecencyLog`,
  `default_tags_from_metrics`. **No engine wiring yet** — `compose_self_prompt`
  still produces fixed strings; M3.2 wires the corpus through the engine
  with a baseline default corpus and falls back to legacy when no corpus
  is supplied. M3.3 adds SQLite persistence; M3.4 adds branch trees +
  memory-anchor `PulseHost` callbacks.
- **`integrations/hermes/inference_health.py`** —
  `score_from_failover(reason, *, provider, override)` maps hermes-agent's
  structured `FailoverReason` taxonomy (`auth`, `billing`, `rate_limit`,
  `timeout`, `context_overflow`, ...) to ingestable VADUGWI `Score`s. The
  agent's *own* connection breakdowns are real experiences — getting
  rate-limited is a brief frustration, getting cut off for billing is
  stronger. Configuration-shaped failures (`model_not_found`,
  `provider_policy_blocked`, `format_error`, `thinking_signature`,
  `long_context_tier`) return `None` because they're operator concerns,
  not agent experiences. All emitted patterns are intentionally distinct
  from `HEAVY_PATTERNS` so inference failures cannot trigger the breach
  mechanic. Operators can per-persona-tune via the `override` argument
  without forking the table.
- **`ClankerSoulMemoryProvider.on_inference_failure`** — hooks the new
  `MemoryProvider.on_inference_failure` plugin contract introduced in
  hermes-agent. When hermes's retry loop gives up on an API call, the
  classified reason flows into the soul as an `OBSERVATION`-direction
  Score with `source="inference:{provider}"`. The chat layer can then
  decide to stay silent on `failed=True` rather than leak raw provider
  errors into the persona's voice — and the agent's affect tracks its
  own inference health across sessions. Soft-fails: a fault here is
  logged and swallowed so a plugin issue never escalates an inference
  failure into a session abort.

## [0.14.0] — 2026-05-09

The autonomous-outreach release. Hermes plugin now wires the
\`PulseEngine\` motivation engine landed in M1 to the agent's actual
channel layer, so the agent can proactively reach out when bored,
elated, in distress, or any of the other 12 trigger states. Closes
**#44** (M2).

### Added

- **\`integrations/hermes/pulse_runner.py\`** — \`PulseRunner\` runs a
  \`PulseEngine\` in a daemon thread with its own asyncio event loop.
  Bridges hermes's synchronous \`MemoryProvider\` lifecycle with the
  engine's asyncio-native design. Lifecycle is idempotent.
- **\`PulseRunner.note_outbound()\`** — thread-safe forward to the
  engine's \`note_outbound\` so cooldown covers reactive replies.
- **Built-in \`_PulseHostAdapter\`** — implements the \`PulseHost\`
  Protocol by proxying to a SoulPlugin. Operator-supplied dispatcher
  receives \`PulseAction\` and returns \`ActionOutcome\` (sync or
  async); engine auto-ingests \`outcome.consequences\` back into the
  soul. Closes the learning loop.
- **\`CLANKER_SOUL_PULSE_OUTBOUND\`** env var — activates the runner
  when set to \`1\` / \`true\` / \`yes\` / \`on\`. Off by default;
  preserves v0.13.1 passive behavior for users who haven't opted in.
- **\`CLANKER_SOUL_PULSE_DISPATCH\`** env var —
  \`module.path:callable\` pointer to the dispatcher. Resolved lazily
  on runner init. Bad values warn-and-fall-back to a no-op dispatcher
  rather than crashing the agent.
- **\`provider.set_pulse_dispatcher(callback)\`** — programmatic
  registration alternative for deployments that wrap the plugin
  loader.
- **\`sync_turn\` now calls \`runner.note_outbound\`** when the
  pulse-outbound path is active. Reactive replies count toward the
  pulse-cooldown timer, preventing a pulse from firing seconds after
  a normal reply ships.

### Changed
- \`integrations/hermes/plugin.yaml\` — version bumped, hooks list now
  includes \`sync_turn\`, description reframed as \"emotional learning
  tool\".
- \`integrations/hermes/README.md\` — new \"Pulse outbound\" section
  with worked dispatcher example showing the consequences-as-learning
  pattern.

### Backwards compatibility
- The pulse-outbound path is opt-in via env var or programmatic
  setter. Without either, runner never starts; provider behaves
  identically to v0.13.1.
- Existing dispatcher-callback signature \`(PulseAction) ->
  ActionOutcome\` matches the M1 protocol; nothing extra needed.
- Existing 26 hermes integration tests continue to pass alongside
  24 new outbound tests.

### Tests
24 new tests in \`tests/test_hermes_pulse_outbound.py\`. Coverage:
- Default-disabled (no thread, no env var)
- Env-var truthy/falsy values (5 truthy + 5 falsy parameterized)
- Programmatic activation via \`set_pulse_dispatcher\`
- \`CLANKER_SOUL_PULSE_DISPATCH\` resolution (good / unset / bad
  format / unimportable)
- Runner lifecycle (start/stop, idempotent start, safe-stop-without-
  start, default no-op dispatcher)
- End-to-end: synthetic distress state → engine ticks → dispatcher
  invoked with correct \`PulseAction\` (kind, trigger, target)
- End-to-end learning: dispatcher returns \`ActionOutcome\` with
  consequences → engine auto-ingests → physics' \`last_tick.patterns\`
  shows the consequence pattern (closes the loop, asserts the
  outcome)
- \`sync_turn\` advances the engine's \`_last_outbound_ts\` (cooldown
  works for reactive replies)

Full suite: 339 passed, 1 skipped (was 315 + 24 new).

### Bug found and fixed during M2
- \`PulseRunner.stop\` was awaiting \`engine.stop()\` but failing to
  call \`loop.stop()\`, leaving \`run_forever()\` running and
  blocking thread join until the 5s timeout. Tests went from 60s
  to 0.13s after the fix.

## [0.13.1] — 2026-05-09

Fourth (and final) of four PRs implementing M1 (#45). Documentation-only
patch that updates the README and CLAUDE.md to reflect the broader
framing established across v0.11.0–v0.13.0: clanker-soul is an
**emotional learning tool for AI agents** — not just an emotional state
runtime.

### Changed
- **README hero section** — now leads with \"emotional learning tool\"
  framing. New \"The learning loop\" diagram showing
  Score → Mood/Soul → Trigger → Action → Consequences → ingest →
  Soul updates, with explicit note that defaults are permissive and
  operators opt into safety.
- **\"Why\" section** — updated to address two motivations: persistent
  state across timescales AND motivation/action-feedback. The latter
  was implicit before and is now explicit.
- **\"Layers\" sidecar bullets** — \`PulseEngine\` description updated
  to motivation-engine framing with the 12 trigger × 6 action mapping.
- **Capability gating section** — rewritten to lead with
  per-action-kind configurable gates (\`CapabilityProfile\` /
  \`STRICT_CAPABILITY_PROFILES\`), with worked examples showing
  whole-dict and per-cell overrides.
- **PulseEngine usage section** — now demonstrates the modern
  \`dispatch_action\` path with \`physics=plugin.physics\` to close
  the learning loop, and notes the legacy \`dispatch_pulse\` path is
  still supported.

### Updated CLAUDE.md
- \"What this is\" — three-sentence framing: persistent state +
  motivation engine + learning loop. Calls out that clanker-soul is
  not a corporate safety wrapper.
- \"The learning loop\" — full ASCII diagram of the pipeline. Adding
  a feature that breaks this loop is a regression.
- \"Architecture\" — two-plane diagram showing state and motivation
  layers superimposed.
- \"Pulse\" module description — full coverage of 12 triggers, 6
  action kinds, modern vs legacy dispatch paths, learning-loop
  closure via \`physics=\` kwarg, capability gating via \`gate=\`.
- **3 new design invariants** added:
  - The learning loop is first-class — \`physics=\` kwarg closes it
  - Defaults are permissive, opt-in safety
  - Every gating cell is operator-overridable

### Tests
No code changes; 315 passed, 1 skipped (unchanged from v0.13.0).

### Closes
- #45 (M1 milestone) — all four PRs (M1.1–M1.4) shipped.

## [0.13.0] — 2026-05-09

Third of four PRs implementing M1 (#45). Per-level configurable
capability gating + public-action rate limiter. Defaults are
**permissive** per the learning-tool framing; \`STRICT_CAPABILITY_PROFILES\`
ships as the conservative opt-in alternative.

### Added

- **`CapabilityProfile`** frozen dataclass — what an agent can do at
  one capability level. Fields: \`allowed_action_kinds\`,
  \`allowed_tool_names\` (None = all), \`public_action_rate_limit_per_hour\`,
  \`user_message_allowed\`, \`description\`. Every cell operator-overridable.
- **\`DEFAULT_CAPABILITY_PROFILES\`** — permissive profiles for all 5
  levels. Every level allows every action kind, every tool, no rate
  limit. The agent acts on impulses; consequences feed back into the
  soul; that IS the learning loop.
- **\`STRICT_CAPABILITY_PROFILES\`** — conservative alternative for
  production-style deployments. Levels 1+ progressively restrict
  public actions, tools, and (at level 4) the user-message channel.
- **\`GovernorConfig.capability_profiles\`** — operator overrides any
  cell of the matrix. Default factory returns DEFAULT_ (permissive).
- **\`GovernorConfig.enable_public_action_lockout\`** — default-OFF.
  Reserved flag for production overlays.
- **\`CapabilityGate\`** — runtime enforcement class. Owns the
  rate-limit bucket. Thread-safe. \`evaluate(action_kind, level, *,
  tool_name=None, is_user_message=False) -> GateDecision\`.
- **\`GateDecision\`** — \`permitted\`, \`reason\` (one of \`ok\` /
  \`action_kind_blocked\` / \`rate_limited\` / \`tool_blocked\` /
  \`user_message_blocked\`), \`profile\`. Callers can introspect why
  an action was denied for logging or substitution.
- **\`PulseEngine(gate=...)\`** kwarg — optional. When provided, every
  action passes through \`gate.evaluate\` before dispatch. Gated
  actions are logged but not delivered. When absent, behavior is
  unchanged from v0.12.0 (default permissive).

### Backwards compatibility

- \`GovernorConfig()\` defaults to permissive profiles — no behavior
  change for existing users
- \`PulseEngine(...)\` without \`gate=\` kwarg works exactly as before
- All 11 existing pulse tests + 13 M1.1 tests + 23 M1.2 tests +
  16 new M1.3 tests pass alongside each other

### Tests

16 new tests in \`tests/governor/test_capability_gate.py\` covering:
- Default permissiveness at every level for every action kind
- Strict-profile spec compliance (the conservative table)
- Operator override of any cell (whole-dict and per-level)
- Rate-limit bucket: under cap allows, over cap denies, isolated to
  public action kinds, zero means unlimited
- Tool-name gating via \`allowed_tool_names\`
- User-message blocking at CRISIS_LOCKOUT
- Engine integration: gated actions don't dispatch; ungated do;
  no-gate path preserves v0.12.0 behavior

Full suite: 315 passed, 1 skipped (was 299 + 16 new).

## [0.12.0] — 2026-05-09

Second of four PRs implementing M1 (#45). The motivation engine grows
from 5 trigger kinds to 12, mapped to the 6-action-kind vocabulary
landed in v0.11.0.

### Added — 7 new trigger kinds

- **\`share_impulse\`** — V/I lift + arousal + nourishment → \"I have to
  tell someone.\" Distinct from elation: lower V threshold, paired
  with positive accumulation rather than peak.
- **\`argue_impulse\`** — V drop + arousal + intent → \"someone's
  wrong.\" Maps to \`comment_reply\` action. Distinct from distress:
  V drop is smaller (irritation, not crash) and intent is what makes
  the agent want to act, not just stew.
- **\`connect_impulse\`** — warmth + extended quiet + low trauma →
  \"I miss them.\" Suppressed when trauma is high (don't seek
  company while wounded — that's distress's job).
- **\`withdraw_impulse\`** — high trauma + low W → \"I need to be
  alone.\" Maps to \`withdraw\` action — host typically does nothing,
  but can choose to set status, dim UI, etc. Pre-empts engagement
  triggers so the agent can actually withdraw.
- **\`reflective_impulse\`** — extended quiet + sustained mood off
  baseline + low trauma → \"write this down.\" Slower-burn than
  trauma_pressure; about reflection, not venting.
- **\`caretake_impulse\`** — perceived distress in another agent (via
  optional \`host.peer_distress_signals\` hook) + high self-W →
  \"check in on them.\" Hosts that don't implement the peer hook
  never see this fire.
- **\`restless_curiosity\`** — high arousal + close to baseline + idle
  → \"I'm bored, want to learn.\" Maps to \`browse_topic\` action.
  Lowest priority — only fires when nothing heavier has anything to
  say.

### Added — config + mapping

- 18 new tunable thresholds on \`PulseConfig\` for the 7 new triggers
  (share_v_lift / share_arousal_min / share_nourishment_floor /
  curiosity_arousal_min / curiosity_distance_max /
  curiosity_idle_min_seconds / argue_v_drop / argue_arousal_min /
  argue_intent_min / connect_v_min / connect_idle_min_seconds /
  connect_max_trauma / withdraw_trauma_min / withdraw_w_max /
  reflective_idle_min_seconds / reflective_distance_min /
  reflective_max_trauma / caretake_self_w_min). All overridable
  per the \"everything is a toggle\" principle.
- \`_DEFAULT_TRIGGER_TO_ACTION\` mapping (12 entries). Most triggers
  default to \`direct_message\` (preserves v0.11.0 backwards-compat
  behavior); \`argue_impulse\` → \`comment_reply\`, \`withdraw_impulse\`
  → \`withdraw\`, \`restless_curiosity\` → \`browse_topic\`.
- \`_action_kind_for_trigger(kind)\` public helper for hosts that
  want to introspect the mapping.
- \`_TARGET_REQUIRED_ACTIONS\` set — only \`direct_message\` and
  \`comment_reply\` require a recipient. The other action kinds can
  dispatch with \`target=None\` (e.g. \`browse_topic\` doesn't need a
  recipient; \`withdraw\` is a do-nothing).
- Optional \`PulseHost.peer_distress_signals() -> list[dict]\` hook.
  Returns peer distress info (typically read from a shared SoulStore).
  Not part of the formal Protocol — runtime-detected via
  \`getattr(host, \"peer_distress_signals\", None)\`. When absent or
  raises, caretake_impulse never fires (graceful degradation).

### Added — synthetic prompts

\`compose_self_prompt\` extended with 7 new prompt templates. Each
new trigger kind produces a distinct \"[INTERNAL PULSE — KIND]\"
header so the agent can tell what state fired. The \`withdraw_impulse\`
prompt explicitly directs the agent to respond \`NOPULSE\` —
withdrawal is a first-class outcome, not an absence.

### Tests

23 new tests in \`tests/pulse/test_motivation_triggers.py\`. Each new
trigger has a fires-under-right-conditions test and (where useful) a
quiet-under-wrong-conditions test. Coverage includes:
- Action-kind mappings for all 12 triggers
- Priority ordering (distress pre-empts argue, withdraw pre-empts
  connect)
- Optional peer_distress_signals hook behavior
- Distinct synthetic prompts for all 7 new kinds
- The withdraw prompt directing NOPULSE

Full suite: 299 passed, 1 skipped (was 276 + 23 new).

### Backwards compatibility

All existing trigger kinds continue to fire under exactly the same
conditions. The 5 v0.10.0 triggers map to the same \`direct_message\`
action kind as before. New triggers strictly add new firing
conditions; nothing existing was tightened or relaxed.

## [0.11.0] — 2026-05-09

The motivation-engine foundation release. First of four PRs implementing
the M1 milestone (#45) — clanker-soul reframed from \"emotional state
runtime\" to **emotional learning tool for AI agents.**

This PR ships the protocol expansion only — no new triggers yet (those
are M1.2). Backwards compatibility is preserved: every existing
\`PulseHost\` implementation continues to work unchanged via an internal
shim.

### Added
- **`PulseAction`** frozen dataclass — the unit of motivation. Six
  kinds: \`direct_message\`, \`post_public\`, \`comment_reply\`,
  \`browse_topic\`, \`withdraw\`, \`tool_invocation\`. \`__post_init__\`
  validates kind against the new \`ACTION_KINDS\` constant.
- **`ActionOutcome`** frozen dataclass — what the host reports back.
  Has \`delivered\`, \`consequences: tuple[Score, ...]\`, \`note\`. The
  \`consequences\` field carries the **learning signal** — Score events
  the host generated from the real-world result of the action.
- **`PulseHost.dispatch_action(action) -> ActionOutcome`** Protocol
  hook (sync OR async). Modern alternative to \`dispatch_pulse\`. Hosts
  that implement it can serve all six action kinds and report
  consequences for the soul to learn from.
- **`PulseEngine(physics=...)`** kwarg — optional reference to the
  agent's \`EmotionalPhysics\`. When provided, the engine
  auto-ingests every Score in \`outcome.consequences\` after each
  successful action, closing the impulse → action → consequence →
  soul-update learning loop.
- **`ACTION_KINDS`** frozenset constant exported at package root.
- New module-level docstring on \`pulse/__init__.py\` reflecting the
  motivation-engine framing.

### Changed
- \`PulseEngine._fire_pulse\` now routes through a unified internal
  \`_dispatch_action_via_host\` helper that prefers
  \`dispatch_action\` and falls back to \`dispatch_pulse\` for legacy
  hosts. Legacy DM flow goes through a default-built
  \`PulseAction(kind=\"direct_message\", ...)\`.

### Backwards compatibility
- Every existing \`PulseHost\` implementation (defining only
  \`dispatch_pulse\`) continues to work unchanged. Engine wraps
  legacy boolean returns in \`ActionOutcome(delivered=..., consequences=())\`.
- Existing \`PulseEngine\` constructions without \`physics=\` still
  work; consequences from non-empty outcomes are dropped with a single
  warning.
- All 13 new tests in \`tests/pulse/test_action_protocol.py\` pass
  alongside 11 unchanged existing pulse tests.

### Hosts: how to upgrade
- Keep using \`dispatch_pulse\` and you get exactly the v0.10.0
  behavior. No changes required.
- Switch to \`dispatch_action\` if you want to (a) handle action kinds
  beyond DMs (post_public / comment_reply / browse_topic) and/or
  (b) feed action consequences back into the soul as Score events.
  Pass \`physics=plugin.physics\` (or your EmotionalPhysics ref) at
  engine construction to enable auto-ingest.

## [0.10.0] — 2026-05-09

The proven-on-a-real-LLM release. clanker-soul now ships a first-class
integration with [Nous Research's hermes-agent](https://github.com/NousResearch/hermes-agent)
and has captured A/B evidence of an observable effect on a real
agentic LLM service (DeepSeek V4 Flash via OpenRouter).

### Added
- **`integrations/hermes/`** — drop-in `MemoryProvider` plugin for
  hermes-agent. Symlink it into `hermes-agent/plugins/memory/clanker-soul/`,
  set `memory.provider clanker-soul`, and the agent's emotional state
  becomes part of its system prompt every turn.
  - `__init__.py` — `ClankerSoulMemoryProvider` class implementing
    name/is_available/initialize/system_prompt_block/on_turn_start/
    sync_turn/get_tool_schemas/handle_tool_call/get_config_schema/
    save_config/shutdown.
  - `scorer.py` — `KeywordScorer`, the default natural-language →
    `Score` mapper with a 22-pattern lexicon covering gratitude,
    affirmation, humor, abandonment, dehumanization, betrayal,
    existential negation, criticism, distress, fear, overwhelm, etc.
    First-person introspection flips direction to `SELF_DIRECTED` for
    the Safety Governor's spike-vs-emergency discrimination.
  - `plugin.yaml` — hermes plugin manifest.
  - `README.md` — install + activate + replace-the-scorer recipe.
  - `EVIDENCE.md` — captured live A/B run on DeepSeek V4 Flash:
    same neutral question, soul-on vs soul-off, model literally
    reflecting back the pattern names from the injected state-context
    block.
- **Three agent-facing tools** exposed by the provider:
  `clanker_soul_state` (read snapshot), `clanker_soul_apply_preset`
  (reshape personality), `clanker_soul_dashboard_url` (return UI link).
- **`tests/test_hermes_integration.py`** — 18 tests covering the
  scorer's lexicon + the provider's lifecycle hooks. Hermes-agent is
  not a test dep; the provider's import-fallback path makes it
  loadable without it.

### Notes
- Hermes-4-70B does not currently support tool use on OpenRouter, so
  the demo defaults to `deepseek/deepseek-v4-flash` ($0.14/$0.28 per M).
  Free tier alternatives exist
  (`nousresearch/hermes-3-llama-3.1-405b:free`).

## [0.9.0] — 2026-05-09

The runnable-examples release. Cuts time-to-first-event from "read 400
lines of docs" to "run one script."

### Added
- **`examples/`** directory (#39) with four self-contained scripts:
  - `01_minimal.py` — the smallest possible integration. `SoulPlugin`,
    a few hand-built `Score`s, print the state-context block.
  - `02_async_host.py` — async ticker calling `plugin.tick()` on a
    loop. Demonstrates that the same context-manager API works in
    async code without a separate surface.
  - `03_custom_event_sink.py` — implementing the `EventLog` Protocol
    from scratch as an ndjson file sink. Demonstrates the soft-fail
    invariant pattern (logging failures must not raise into ingest).
    Uses the lower-level `EmotionalPhysics(...)` constructor directly,
    not `SoulPlugin`.
  - `04_pulse_host.py` — minimum `PulseHost`: stdout-only host that
    satisfies all six protocol hooks. Drives mood far below soul to
    fire a `distress` pulse and prints the synthetic self-prompt.
- **`examples/README.md`** — index + patterns-worth-copying section.
- **`tests/test_examples.py`** — CI smoke-test that runs each example
  as a subprocess and asserts exit 0. If an example breaks (API drift,
  removed kwarg, forgotten import), CI catches it before an adopter
  copies broken code from the docs.
- README "Examples" section linking to the four scripts.

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

[Unreleased]: https://github.com/deucebucket/clanker-soul/compare/v0.18.0...HEAD
[0.18.0]: https://github.com/deucebucket/clanker-soul/compare/v0.17.0...v0.18.0
[0.17.0]: https://github.com/deucebucket/clanker-soul/compare/v0.16.0...v0.17.0
[0.16.0]: https://github.com/deucebucket/clanker-soul/compare/v0.15.0...v0.16.0
[0.15.0]: https://github.com/deucebucket/clanker-soul/compare/v0.14.0...v0.15.0
[0.14.0]: https://github.com/deucebucket/clanker-soul/compare/v0.13.1...v0.14.0
[0.13.1]: https://github.com/deucebucket/clanker-soul/compare/v0.13.0...v0.13.1
[0.13.0]: https://github.com/deucebucket/clanker-soul/compare/v0.12.0...v0.13.0
[0.12.0]: https://github.com/deucebucket/clanker-soul/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/deucebucket/clanker-soul/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/deucebucket/clanker-soul/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/deucebucket/clanker-soul/compare/v0.8.2...v0.9.0
[0.8.2]: https://github.com/deucebucket/clanker-soul/compare/v0.8.1...v0.8.2
[0.8.1]: https://github.com/deucebucket/clanker-soul/compare/v0.8.0...v0.8.1
[0.8.0]: https://github.com/deucebucket/clanker-soul/compare/v0.6.0...v0.8.0
[0.6.0]: https://github.com/deucebucket/clanker-soul/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/deucebucket/clanker-soul/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/deucebucket/clanker-soul/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/deucebucket/clanker-soul/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/deucebucket/clanker-soul/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/deucebucket/clanker-soul/releases/tag/v0.1.0
