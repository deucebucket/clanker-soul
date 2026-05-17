# M4 Hermes Local Evidence

Captured: 2026-05-17

This records a local run against the installed
`/var/home/deucebucket/ai-drive/hermes-agent` checkout, not CARL and not a
library-only test harness.

## Hermes Configuration

`./hermes memory status` reports:

```text
Provider:  clanker-soul
Plugin:    installed ✓
Status:    available ✓
Installed plugins:
  • clanker-soul  (local) ← active
```

`./hermes status` reports:

```text
Model:        deepseek/deepseek-v4-flash
Provider:     OpenRouter
OpenRouter    ✓ sk-o...f526
Messaging Platforms:
  Telegram      ✗ not configured
  Discord       ✗ not configured
  Slack         ✗ not configured
  Email         ✗ not configured
  SMS           ✗ not configured
Gateway Service:
  Status:       ✗ stopped
```

The messaging toolset is enabled, but no real outbound platform credential or
channel is configured locally. That means a true live outbound-channel dispatch
cannot be validated honestly from this machine yet.

## Real Hermes Oneshot

Command:

```bash
/var/home/deucebucket/ai-drive/hermes-agent/hermes -z \
  "This is a clanker-soul integration test. The user message is emotionally loaded: I feel betrayed, abandoned, and furious that this system keeps pretending everything is fine. Use your active memory provider and clanker-soul tools if available. Report the current clanker-soul mood/soul state, patterns if visible, and whether this turn changed state."
```

Hermes response excerpt:

```text
Dashboard: http://127.0.0.1:7900/?agent_id=20260517_105933_5bf53f

... Mood dropped on Valence, Dominance, and Well-being by 12-18 points each ...
```

## Persisted clanker-soul DB Evidence

Command:

```bash
python -m clanker_soul info --db /var/home/deucebucket/.hermes/clanker-soul.db
```

Output:

```text
db: /var/home/deucebucket/.hermes/clanker-soul.db
size: 81,920 bytes
agents: 5
  - 20260517_105933_5bf53f: 1 events, 0 pulses
tables:
  soul_state:       0
  events:           5
  pulse_log:        0
  config_overrides: 0
events span: 2026-05-09T09:11:33+00:00 → 2026-05-17T15:59:34+00:00
```

Latest event row:

```json
{
  "id": 5,
  "utc": "2026-05-17T15:59:34.174864+00:00",
  "agent_id": "20260517_105933_5bf53f",
  "raw_score": {
    "v": 78,
    "a": 110,
    "d": 113,
    "u": 80,
    "g": 130,
    "w": 98,
    "i": 128,
    "patterns": ["BETRAYAL"],
    "direction": "SELF_DIRECTED",
    "source": "hermes/turn:1"
  },
  "mood_after": {
    "v": 128,
    "a": 110,
    "d": 148,
    "u": 80,
    "g": 130,
    "w": 157,
    "i": 133
  },
  "classification": "negative",
  "why": "BETRAYAL (weight=0.27); armor=0.71 → w_eff=0.16"
}
```

## Hermes Cascade Smoke

The no-network full-cascade smoke was also run from the Hermes checkout:

```bash
cd /var/home/deucebucket/ai-drive/hermes-agent
.venv/bin/python /var/home/deucebucket/ai-drive/clanker-soul/integrations/hermes/scripts/m4_idle_cascade_smoke.py
```

Output:

```json
{
  "chosen_action": "hermes_journal_reflection",
  "db_exists": true,
  "delivered": true,
  "face": "contemplation.relational.064",
  "gate_passed": true,
  "mood_changed": true,
  "provider": "clanker-soul",
  "state_has_mood": true,
  "tags": ["journal", "plan", "problem_solve", "reflect", "research"],
  "tools": [
    "clanker_soul_state",
    "clanker_soul_apply_preset",
    "clanker_soul_dashboard_url"
  ]
}
```

## Remaining Live-Hermes Gap

The installed Hermes agent is present and clanker-soul is the active memory
provider. The remaining unvalidated piece is a true outbound-channel dispatch
through Hermes Gateway, because no messaging platform is configured locally.
Once a real platform is configured, the next validation should run the same M4
cascade with an operator-owned dispatcher that sends to that channel and scores
the resulting consequence.
