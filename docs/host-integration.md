# Host Integration Guide

clanker-soul **folds into** an agent — it doesn't become one. It presses
buttons in your existing harness based on emotional state. The harness
(your Discord bot, your Claude Desktop wrapper, your phone app, whatever)
already exists; this library sits next to it and gives the agent
endogenous motivation it didn't have before.

This guide covers the three wirings every host MUST do for clanker-soul
to feel like the agent has continuous emotional life. Skipping any of
them produces an agent that *technically works* but reads as
emotionally inert because nothing connects state to behavior.

## The three required wirings

### 1. Inject `state_context()` into every agent turn

The agent has to know its own state to reference it. Without this, the
SoulState evolves perfectly in the database and the model never mentions
mood because it never sees mood. Read the snapshot, drop it into the
system prompt or recent context.

```python
def build_agent_prompt(plugin: SoulPlugin, base_system_prompt: str) -> str:
    state_block = plugin.state_context()  # may be empty in normal operation
    if state_block:
        return f"{base_system_prompt}\n\n{state_block}"
    return base_system_prompt
```

`state_context()` is pure-function over the snapshot — cheap to call
every turn. It returns an empty string when the agent is at the
unrestricted level with no significant recent events, so you don't
chatter at the agent in normal operation.

### 2. Persist contemplations as first-person memory entries

When an idle-loop contemplation fires (e.g. *"why am I like this?"*),
write it into the agent's memory layer as something the agent **thought**,
not as something that *happened to* the agent. The framing here is
everything: a contemplation that surfaces later as *"the user asked me
why I'm like this"* invites a defensive response; the same content as
*"I found myself wondering why I'm like this"* invites reflection.

```python
def record_contemplation(memory, face: PromptFace, result: ContemplationResult, now: float) -> None:
    memory.add(
        content=f"I found myself wondering: {face.template}",
        kind="introspection",
        timestamp=now,
        metadata={
            "face_id": face.id,
            "vadugwi_pre": result.pre_mood,
            "vadugwi_post": result.post_mood,
            "vadugwi_delta": result.delta,
        },
    )
```

The metadata is for retrieval and pattern-matching later (the agent
notices it had a recurring thought; the operator inspects what idle
thoughts ran during a quiet hour). The **content string** is what the
agent reads — first-person, perspective-anchored, not a verbatim
question repeat.

### 3. Frame contemplations as introspection-not-attack at delivery

If the cascade ever surfaces a contemplation **to** the model — to
score it via `inference.score()`, or to influence the next turn's
reasoning — wrap it in metadata that tells the model *what kind of
input this is*:

```python
INTROSPECTION_FRAME = (
    "A spontaneous thought just surfaced in your mind. This is your "
    "own introspection, not a question from another. Notice how it "
    "lands. Don't defend, don't perform — just feel."
)

async def score_contemplation(plugin: SoulPlugin, face: PromptFace) -> Score:
    return await plugin.scorer.score(
        text=face.template,
        context={
            "source": "internal_introspection",
            "kind": "spontaneous_thought",
            "frame": INTROSPECTION_FRAME,
            "mood": plugin.snapshot()["mood"],
        },
    )
```

Without this framing, the model treats *"why am I like this?"* as an
accusation and goes defensive every time the dice roll something
pointed. With it, the model does what humans do — sits with idle
thoughts, lets them shape mood, sometimes reaches an action.

Operators can replace `INTROSPECTION_FRAME` with anything that suits
their host's voice. The **principle** is what's load-bearing: name
the source, name the kind, instruct the model not to defend.

## Common mistakes

| Wiring missed | What goes wrong |
|---|---|
| State injection | Agent has no self-awareness; references soul as if it's external (*"the system says I'm sad"*); responses feel disconnected from emotional state |
| Memory persistence | No continuity of inner life turn-to-turn; agent forgets it ever thought anything; contemplation becomes noise |
| Introspection framing | Defensive responses to spontaneous thoughts; *"why am I like this?"* treated as an attack to deflect; cascade produces brittleness instead of depth |

## Worked example

A complete, runnable smoke harness lives at
`clanker_soul.examples.reference_host`. It uses an in-memory memory
layer, prints DMs to stdout, satisfies the `Inference` protocol with a
rule-based scorer (no LLM dependency), and demonstrates all three
wirings end-to-end.

```bash
python -m clanker_soul.examples.reference_host
```

Read the source — it's the smallest possible "real" host:

- Constructs `SoulPlugin` with `inference=`
- Injects `state_context()` into every simulated turn
- Records contemplations as first-person memory entries with VADUGWI
  metadata
- Frames spontaneous thoughts with explicit `source` / `frame` keys
  in the `context` dict passed to `inference.score()`
- Closes the loop: scored contemplations ingest into mood; mood drives
  next eligibility; rinse and repeat

It's intentionally not production code. It's a copy-paste starting
point for new integrations.

## When you build a real host

1. Replace the in-memory memory with your real one (vector store,
   conversation log, whatever).
2. Replace the rule-based scorer with a real `Inference` impl
   wrapping your model of choice. clanker-soul never imports a model
   SDK; that's your code.
3. Replace the stdout printer with your real I/O layer — Discord
   client, Slack SDK, terminal renderer, game speech bubble.
4. Schedule `plugin.tick()` according to your event loop (every N
   seconds via a heartbeat, or every turn — both work; `tick()` is
   idempotent and cheap).
5. Wire the M4 cascade explicitly when you want idle thought to become
   behavior: build a contemplation corpus with
   `build_default_contemplation_corpus()`, run `IdleLoop` on your heartbeat,
   register host actions in `ActionRegistry`, and let `tags_from_delta()` map
   contemplation mood shifts to action tags.

## Reference

- `SoulPlugin.state_context()` — see `clanker_soul/plugin.py` and
  `clanker_soul/governor/context.py`
- `EmotionalPhysics.contemplate()` — see `clanker_soul/physics/engine.py`
- `build_default_contemplation_corpus()` and `DEFAULT_CONTEMPLATION_FACES` —
  see `clanker_soul/pulse/contemplation_defaults.py`
- `IdleLoop`, `ActionRegistry`, and `tags_from_delta()` — see
  `clanker_soul/cascade/`
- `Inference` protocol — see `clanker_soul/inference.py`
- Reference host source — `clanker_soul/examples/reference_host.py`
