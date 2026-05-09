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
└── clanker_soul_hermes/
    ├── __init__.py                       # ClankerSoulMemoryProvider + get_provider()
    └── scorer.py                         # KeywordScorer (the default scorer)
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
