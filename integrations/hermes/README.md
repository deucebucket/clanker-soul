# clanker-soul plugin for hermes-agent

A `MemoryProvider` plugin that gives Nous Research's [hermes-agent](https://github.com/NousResearch/hermes-agent) persistent VADUGWI emotional state via [clanker-soul](https://github.com/deucebucket/clanker-soul).

## What it does

- **Injects soul state into every turn's system prompt** so the model reads
  its current mood, capability level, and recent emotional events before
  responding. The agent can color tone, push back, or ask clarifying
  questions based on its state.
- **Scores each user message** and ingests it into the soul on
  `on_turn_start`. The default scorer is keyword-based (see
  `scorer.py`); subclass and override `KeywordScorer.score` to wire in
  an LLM-as-scorer.
- **Exposes three tools** the agent can call:
  - `clanker_soul_state` — read the current snapshot (mood, soul,
    capability, reservoirs, state-context block)
  - `clanker_soul_apply_preset` — reshape personality (`child` /
    `adult` / `brittle` / `stoic`)
  - `clanker_soul_dashboard_url` — return the URL where the user can
    inspect the soul live
- **Persists across sessions.** Same `agent_id` → same `soul.db`.
  Mood survives restarts. Soul drifts toward sustained mood over days.

## Install

From a hermes-agent checkout:

```bash
# in your hermes venv
pip install clanker-soul

# symlink (or copy) this dir into hermes's plugin path:
ln -s /path/to/clanker-soul/integrations/hermes \
      /path/to/hermes-agent/plugins/memory/clanker-soul
```

Or, if you cloned both repos side-by-side:

```bash
cd hermes-agent
ln -s ../clanker-soul/integrations/hermes plugins/memory/clanker-soul
```

## Activate

```bash
hermes config set memory.provider clanker-soul
```

Optional environment overrides:

| var | default | meaning |
|---|---|---|
| `CLANKER_SOUL_DB_PATH` | `~/.hermes/clanker-soul.db` | where to store the soul |
| `CLANKER_SOUL_AGENT_ID` | (per-session) | share one soul across all hermes sessions |
| `CLANKER_SOUL_UI_PORT` | `7900` | port for the dashboard URL the agent reports |
| `CLANKER_SOUL_PULSE_OUTBOUND` | unset | set to `1` / `true` / `yes` to enable autonomous outreach (see below) |
| `CLANKER_SOUL_PULSE_DISPATCH` | unset | `module.path:callable` — operator-supplied dispatcher invoked when triggers fire |

## Pulse outbound — autonomous outreach (M2)

By default the plugin is *passive* — it scores user messages and injects state into the system prompt, but never initiates contact. Setting `CLANKER_SOUL_PULSE_OUTBOUND=1` activates a background `PulseEngine` that fires triggers based on the agent's emotional state and dispatches actions through an operator-supplied callback.

```bash
# Enable outbound mode
export CLANKER_SOUL_PULSE_OUTBOUND=1

# Point at your dispatcher
export CLANKER_SOUL_PULSE_DISPATCH=mybot.channels:send_to_telegram
```

The dispatcher receives a `PulseAction` and returns an `ActionOutcome`:

```python
# mybot/channels.py
from clanker_soul import ActionOutcome, PulseAction, Score

def send_to_telegram(action: PulseAction) -> ActionOutcome:
    # 1. Run action.prompt through your model
    response_text = my_model.complete(action.prompt)
    # 2. Send it via your channel
    msg = my_telegram_bot.send_message(chat_id=..., text=response_text)
    # 3. Score the consequence — this is the LEARNING SIGNAL
    consequence = score_engagement(msg)  # e.g. Score(...) from reactions
    return ActionOutcome(
        delivered=True,
        consequences=(consequence,) if consequence else (),
        note=f"telegram:{msg.message_id}",
    )
```

The engine auto-ingests every Score in `outcome.consequences` back into the soul, closing the impulse → action → consequence → soul-update loop. Without consequences, the agent acts but doesn't learn.

For programmatic activation (skip the env vars), call `provider.set_pulse_dispatcher(callback)` BEFORE `provider.initialize` runs. The plugin loader doesn't surface the provider instance — this path is for deployments wrapping the plugin with their own loader.

## Inference health — the agent feels its own broken brain

When hermes-agent's retry loop gives up on an API call (auth rejected, billing exhausted, rate-limited beyond retries, stream connection keeps dying), it surfaces the failure through the `MemoryProvider.on_inference_failure(reason, *, provider, model, retryable)` plugin hook. The provider maps the structured reason into a Score and ingests it as an `OBSERVATION`-direction event with `source="inference:{provider}"` — so the agent's mood actually reflects whether its inference layer is healthy.

What this enables:

- **No persona leaks.** The chat layer (e.g. Gwen on Telegram) can treat `failed=True` as "don't post anything" instead of leaking `OpenRouter 429: rate_limit_exceeded` into a persona's voice. The persona stays silent / shows a connection indicator at the channel level instead.
- **Self-aware affect.** A long stretch of rate limits genuinely tints the agent's mood — the dashboard's recent-events list shows `inference:openrouter` entries alongside human-interaction events.
- **No soul damage.** Inference patterns (`INFERENCE_RATE_LIMITED`, `INFERENCE_CUT_OFF`, etc.) are intentionally NOT in `HEAVY_PATTERNS`, so getting throttled won't leave a permanent dent in self-worth the way human contempt would.

Configuration-shaped failures (`model_not_found`, `provider_policy_blocked`, `format_error`, `thinking_signature`, `long_context_tier`) are deliberately **not** scored — those are operator concerns, not agent experiences.

To tune the affect response per persona without forking the mapping table:

```python
from clanker_soul_hermes.inference_health import score_from_failover

# Make this persona barely react to rate limits
override = {"rate_limit": None}
score = score_from_failover("rate_limit", provider="openrouter", override=override)
```

This integration relies on hermes-agent's `on_inference_failure` plugin hook (deucebucket/hermes-agent PR #2). It's an additive optional method on the existing `MemoryProvider` ABC — the same upstream-stable plugin contract clanker-soul already uses for `on_turn_start` and `sync_turn`. No fork-divergence beyond the one hook itself.

## Inspect the soul live

While hermes is running, in another terminal:

```bash
clanker-soul ui --db ~/.hermes/clanker-soul.db
# → http://127.0.0.1:7900
```

The dashboard shows the same state the model is reading via the system
prompt block — useful for debugging "why did the agent do that?" answers.

## How it differs from hermes's built-in memory

- **Built-in memory** (`MEMORY.md` / `USER.md`) is *facts*. Things the
  agent should remember across turns.
- **clanker-soul** is *feelings*. The agent's emotional state in
  response to those facts. Both run together — they don't conflict.

## File map

```
integrations/hermes/
├── plugin.yaml                           # hermes plugin manifest
├── README.md                             # this file
├── __init__.py                           # ClankerSoulMemoryProvider + get_provider()
├── scorer.py                             # KeywordScorer (the default scorer)
├── pulse_runner.py                       # daemon-thread PulseEngine bridge
└── inference_health.py                   # FailoverReason → Score mapping
```

## Replacing the scorer

The keyword scorer is intentionally crude — its job is to produce
*something* sensible from raw text so the soul can react. To plug in
an LLM-as-scorer:

```python
from clanker_soul_hermes.scorer import KeywordScorer
from clanker_soul import Score

class LLMScorer(KeywordScorer):
    def score(self, message: str, *, source: str | None = None) -> Score | None:
        # call your model with a "score this into V/A/D/U/G/W/I" prompt
        # parse the response into Score(...)
        return parsed_score
```

Then swap `self._scorer = KeywordScorer()` in
`ClankerSoulMemoryProvider.__init__` to your subclass — or fork the
provider and override `__init__`.
