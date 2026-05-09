# PendingAction Live Demo Evidence

Run: `2026-05-09T19:35:24.305959+00:00` → `2026-05-09T19:35:29.116950+00:00`
Classifier: `LLMOutcomeClassifier` (model `deepseek/deepseek-chat`)

## Summary

| Scenario | Classified | Resolved status | ΔV | ΔW |
| --- | --- | --- | ---: | ---: |
| A — acknowledged fast | `acknowledged` | `acknowledged` | +13 | +0 |
| B — ignored | `ignored` | `ignored` | -32 | -32 |
| C — expired | `expired` | `expired` | -11 | -17 |

### A — acknowledged fast

**agent_message:** `Hey, you've been quiet today — wanted to check in. How are things?`

**inbound:** `Hey! Yeah I'm doing okay, just busy. Thanks for checking in.`

**classified:** `acknowledged`
**resolved_status:** `acknowledged`
**mood delta:** ΔV=+13, ΔW=+0

### B — ignored

**agent_message:** `I noticed something heavy earlier today and wanted to share — got a minute?`

**inbound:** `Did you see the latest patch notes for the game? They nerfed the warlock again.`

**classified:** `ignored`
**resolved_status:** `ignored`
**mood delta:** ΔV=-32, ΔW=-32

### C — expired

**agent_message:** `Just thinking about you. No need to reply, just wanted to send the thought.`

**inbound:** `(none — TTL elapsed)`

**classified:** `expired`
**resolved_status:** `expired`
**mood delta:** ΔV=-11, ΔW=-17

