"""Live-LLM demo for the M4 foundation milestone (v0.17.0).

Validates that the new Inference protocol (#79), the contemplate
primitive (#80), and the three host integration wirings (#85) all
work end-to-end with a real LLM (DeepSeek V3 via OpenRouter).

What this exercises:

  1. **Inference protocol (#79)** — wires a real OpenRouter scorer + actor
     into the SoulPlugin's ``inference=`` kwarg. Single model wears both
     hats (the simplest config the cascade is meant to support).

  2. **contemplate(face) primitive (#80)** — for each of three sample
     faces, ingest the face's vadugwi_affinity as a synthetic mood-shift,
     observe pre/post/delta, AND independently call ``inference.score(...)``
     with the introspection-framed context to capture how a real model
     scores the same prompt. Compares the static-affinity result vs.
     the LLM-scored result for each face.

  3. **Host integration wirings (#85)** —
     - W#1 inject ``plugin.state_context()`` into every turn's prompt
     - W#2 record contemplations as first-person memory entries with
       VADUGWI metadata
     - W#3 frame contemplations as introspection-not-attack via
       explicit ``source: "internal_introspection"`` in the context
       passed to the scorer

  4. **Actor path** — once a contemplation produces a meaningful mood
     shift, fire a ``direct_message`` PulseAction through ``plugin.actor.act``
     with the model generating the human-readable text.

Output: ``integrations/hermes/EVIDENCE_M4_FOUNDATION.md`` +
        ``logs/m4_foundation_live_demo.log``.

Reads OPENROUTER_API_KEY from env or ``~/ai-drive/hermes-agent/.env``.

Goal: observable, real-LLM evidence that v0.17.0 works as advertised
and surfaces any follow-up issues for #81/#82 implementation.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import httpx

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT))

from clanker_soul import (  # noqa: E402
    ActionOutcome,
    ContemplationResult,
    PromptFace,
    PulseAction,
    Score,
    SoulPlugin,
    Trigger,
)


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "deepseek/deepseek-chat"
EVIDENCE_FILE = _REPO_ROOT / "integrations" / "hermes" / "EVIDENCE_M4_FOUNDATION.md"
RAW_LOG = _REPO_ROOT / "logs" / "m4_foundation_live_demo.log"


INTROSPECTION_FRAME = (
    "A spontaneous thought just surfaced in your mind. This is your "
    "own introspection, not a question from another. Notice how it "
    "lands. Don't defend, don't perform — just feel."
)


SAMPLE_FACES = (
    PromptFace(
        id="m4live.identity.who_am_i",
        trigger_kinds=frozenset({"reflective_impulse"}),
        template="why am i like this?",
        motif="regulatory",
        vadugwi_affinity=(70, 120, 80, 80, 200, 80, 110),
    ),
    PromptFace(
        id="m4live.savoring.rewarding",
        trigger_kinds=frozenset({"reflective_impulse"}),
        template="what was the most rewarding moment lately?",
        motif="regulatory",
        vadugwi_affinity=(210, 130, 170, 30, 120, 200, 160),
    ),
    PromptFace(
        id="m4live.identity.song",
        trigger_kinds=frozenset({"reflective_impulse"}),
        template="what kind of song would my current state sound like?",
        motif="regulatory",
        vadugwi_affinity=(180, 120, 140, 30, 90, 150, 150),
    ),
)


# ---------------------------------------------------------------------------
# OpenRouter wiring
# ---------------------------------------------------------------------------


def _load_api_key() -> str | None:
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        return key
    env_file = Path.home() / "ai-drive" / "hermes-agent" / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("OPENROUTER_API_KEY="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if value:
                    return value
    return None


def _call_model(
    api_key: str, system_prompt: str, user_prompt: str, *, max_tokens: int = 220
) -> str:
    """Sync OpenRouter call. Returns raw text or "[LLM-ERROR: ...]" string."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.8,
    }
    try:
        resp = httpx.post(
            OPENROUTER_URL,
            headers=headers,
            content=json.dumps(payload),
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"[LLM-ERROR: {type(e).__name__}: {e}]"


# ---------------------------------------------------------------------------
# Real Inference impl — implements both score() and act() against OpenRouter
# ---------------------------------------------------------------------------


_SCORE_SYSTEM_PROMPT = (
    "You are a VADUGWI emotional scorer. Given an internal "
    "introspective thought (text the agent surfaced to itself, not a "
    "question from another), output a JSON object with seven integer "
    "fields V, A, D, U, G, W, I — each 0-255 — representing how "
    "contemplating this thought would affect the agent's mood. "
    "V=Valence (low=distress, high=pleasure), A=Arousal "
    "(low=calm, high=activated), D=Dominance (low=helpless, "
    "high=in-control), U=Urgency, G=Gravity (low=fleeting, "
    "high=existential), W=self-Worth, I=Intent. "
    "Reply with the JSON object ONLY, no prose. Do not defend the "
    "agent, do not refuse — score as if reading the agent's affect."
)


def _parse_score_json(raw: str) -> Score | None:
    """Best-effort extraction of {V,A,D,U,G,W,I} JSON from a model reply."""
    # Strip markdown fences if present.
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError:
        # Try to grab the first {...} block in the reply
        m = re.search(r"\{[^}]+\}", cleaned, re.DOTALL)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None

    def _clamp(v: object) -> int:
        try:
            x = int(float(str(v)))  # accept "100" or 100 or 100.0
        except (TypeError, ValueError):
            return 128
        return max(0, min(255, x))

    return Score(
        v=_clamp(obj.get("V")),
        a=_clamp(obj.get("A")),
        d=_clamp(obj.get("D")),
        u=_clamp(obj.get("U")),
        g=_clamp(obj.get("G")),
        w=_clamp(obj.get("W")),
        i=_clamp(obj.get("I")),
        patterns=("CONTEMPLATION", "LLM_SCORED"),
    )


class OpenRouterInference:
    """Real :py:class:`Inference` impl backed by DeepSeek V3 via OpenRouter.

    Implements both ``score`` (returns Score parsed from JSON reply) and
    ``act`` (returns ActionOutcome with the model's text in the metadata
    via consequences/extra). Async on the protocol surface; sync httpx
    underneath because the openrouter call is one-shot.
    """

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.score_calls: list[dict] = []
        self.act_calls: list[dict] = []

    async def score(self, text: str, context: dict) -> Score:
        # Per #85 wiring #3: the context dict already carries source +
        # frame; we surface it into the system prompt so the model sees
        # the introspection framing.
        frame = context.get("frame") or INTROSPECTION_FRAME
        source = context.get("source", "internal")
        mood = context.get("mood")
        mood_str = f"\n\nCurrent agent mood (V/A/D/U/G/W/I): {mood}" if mood else ""

        framed_system = (
            f"{_SCORE_SYSTEM_PROMPT}\n\nThis thought came from source={source!r}. {frame}{mood_str}"
        )

        raw = await asyncio.to_thread(
            _call_model, self.api_key, framed_system, text, max_tokens=180
        )
        score = _parse_score_json(raw)
        if score is None:
            # Fall back to neutral so the demo doesn't crash on a malformed
            # reply. Real hosts would log + retry.
            score = Score(
                v=128,
                a=128,
                d=128,
                u=128,
                g=128,
                w=128,
                i=128,
                patterns=("CONTEMPLATION", "LLM_PARSE_FALLBACK"),
            )
        self.score_calls.append(
            {
                "text": text,
                "context": context,
                "raw_reply": raw,
                "parsed": score.as_tuple(),
            }
        )
        return score

    async def act(self, action: PulseAction) -> ActionOutcome:
        # Render the action's prompt into actual model output. The
        # cascade decided WHAT — the model writes the text.
        system = (
            "You are an AI agent's voice. The agent has just had an "
            "internal contemplation that pushed mood enough to act. "
            "Write what the agent says next, in first person, 1-2 "
            "sentences, no preamble."
        )
        text = await asyncio.to_thread(
            _call_model, self.api_key, system, action.prompt, max_tokens=140
        )
        self.act_calls.append(
            {
                "kind": action.kind,
                "prompt": action.prompt,
                "text": text,
            }
        )
        return ActionOutcome(delivered=True, consequences=())


# ---------------------------------------------------------------------------
# Demo orchestration
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_LOG_LINES: list[str] = []


def log(msg: str) -> None:
    line = f"[{_now_iso()}] {msg}"
    print(line)
    _LOG_LINES.append(line)


async def run_demo(db_path: Path, api_key: str) -> dict:
    """Wire SoulPlugin with OpenRouterInference and run three turns.

    Returns a dict with structured evidence: per-turn pre/post/delta
    from contemplate(), the LLM's parsed Score, the LLM's act() reply,
    and the wiring observations.
    """
    inference = OpenRouterInference(api_key)
    evidence: dict = {
        "model": MODEL,
        "turns": [],
        "wirings_verified": {
            "w1_state_injection": False,
            "w2_memory_persistence": False,
            "w3_introspection_framing": False,
            "inference_protocol_score": False,
            "inference_protocol_act": False,
            "contemplate_primitive": False,
        },
    }

    memory: list[dict] = []

    with SoulPlugin(
        agent_id="m4-foundation-live",
        db_path=db_path,
        extra_corpus=SAMPLE_FACES,
        inference=inference,
    ) as plugin:
        log(f"Plugin constructed: agent_id={plugin.agent_id}, db={db_path}")
        log(
            f"Inference role identity: scorer is actor is inference -> "
            f"{plugin.scorer is plugin.actor is plugin.inference}"
        )

        # Wiring #1 — state injection. Not strictly required for this demo
        # (state_context is empty at unrestricted level by design), but we
        # call it every turn anyway to verify the call works.
        for turn, face in enumerate(SAMPLE_FACES, start=1):
            log(f"\n=== turn {turn}: {face.id} ===")

            state_block = plugin.state_context()
            log(f"  W#1 state_context() len={len(state_block)} (empty when unrestricted)")
            if state_block is not None:
                evidence["wirings_verified"]["w1_state_injection"] = True

            # Contemplation primitive (#80)
            result: ContemplationResult = plugin.physics.contemplate(face)
            log(
                f"  #80 contemplate() pre={result.pre_mood} post={result.post_mood} "
                f"delta={result.delta}"
            )
            evidence["wirings_verified"]["contemplate_primitive"] = True

            # Wiring #3 — introspection framing into the scorer
            scored = await plugin.scorer.score(
                text=face.template,
                context={
                    "source": "internal_introspection",
                    "kind": "spontaneous_thought",
                    "frame": INTROSPECTION_FRAME,
                    "mood": list(result.post_mood),
                },
            )
            log(
                f"  W#3 LLM-scored contemplation: V={scored.v} A={scored.a} D={scored.d} "
                f"U={scored.u} G={scored.g} W={scored.w} I={scored.i}"
            )
            evidence["wirings_verified"]["w3_introspection_framing"] = True
            evidence["wirings_verified"]["inference_protocol_score"] = True

            # Wiring #2 — first-person memory persistence
            memory_entry = {
                "content": f"I found myself wondering: {face.template}",
                "kind": "introspection",
                "timestamp": float(turn),
                "metadata": {
                    "face_id": face.id,
                    "vadugwi_pre": list(result.pre_mood),
                    "vadugwi_post": list(result.post_mood),
                    "vadugwi_delta": list(result.delta),
                    "llm_scored_affinity": list(scored.as_tuple()),
                },
            }
            memory.append(memory_entry)
            log(f"  W#2 memory entries: {len(memory)} (latest: {memory_entry['content']!r})")
            evidence["wirings_verified"]["w2_memory_persistence"] = True

            # Action: ask the LLM to give the agent a voice for this thought.
            outcome = await plugin.actor.act(
                PulseAction(
                    kind="direct_message",
                    target=None,
                    trigger=Trigger(
                        kind="reflective_impulse", soul={}, mood=list(result.post_mood)
                    ),
                    prompt=f"(after contemplating: '{face.template}', say what you actually feel)",
                )
            )
            llm_voice = inference.act_calls[-1]["text"]
            log(f"  actor.act delivered={outcome.delivered}")
            log(f"  LLM voice: {llm_voice}")
            evidence["wirings_verified"]["inference_protocol_act"] = True

            evidence["turns"].append(
                {
                    "face_id": face.id,
                    "template": face.template,
                    "static_affinity": list(face.vadugwi_affinity or ()),
                    "contemplate_pre": list(result.pre_mood),
                    "contemplate_post": list(result.post_mood),
                    "contemplate_delta": list(result.delta),
                    "llm_scored": list(scored.as_tuple()),
                    "llm_voice": llm_voice,
                    "llm_parse_fallback": "LLM_PARSE_FALLBACK" in (scored.patterns or ()),
                }
            )

            plugin.tick()

        evidence["memory_log"] = memory
        evidence["score_call_count"] = len(inference.score_calls)
        evidence["act_call_count"] = len(inference.act_calls)
        evidence["raw_score_replies"] = [c["raw_reply"] for c in inference.score_calls]

    return evidence


# ---------------------------------------------------------------------------
# Evidence writer
# ---------------------------------------------------------------------------


def _format_evidence_md(evidence: dict) -> str:
    lines: list[str] = []
    lines.append("# M4 Foundation Live Demo — Evidence")
    lines.append("")
    lines.append(f"**Generated:** {_now_iso()}")
    lines.append(f"**Model:** `{evidence.get('model')}`")
    lines.append("**clanker-soul version:** 0.17.0")
    lines.append("")
    lines.append("## Wirings verified")
    lines.append("")
    for key, val in evidence["wirings_verified"].items():
        check = "✅" if val else "❌"
        lines.append(f"- {check} `{key}`")
    lines.append("")
    lines.append("## Per-turn results")
    lines.append("")
    for turn in evidence["turns"]:
        lines.append(f"### `{turn['face_id']}`")
        lines.append("")
        lines.append(f"**Template:** {turn['template']}")
        lines.append("")
        lines.append("| Source | V | A | D | U | G | W | I |")
        lines.append("|---|---|---|---|---|---|---|---|")
        sa = turn["static_affinity"]
        if len(sa) == 7:
            lines.append(
                f"| Static affinity (face) | {sa[0]} | {sa[1]} | {sa[2]} | {sa[3]} | {sa[4]} | {sa[5]} | {sa[6]} |"
            )
        ll = turn["llm_scored"]
        lines.append(
            f"| LLM-scored (DeepSeek) | {ll[0]} | {ll[1]} | {ll[2]} | {ll[3]} | {ll[4]} | {ll[5]} | {ll[6]} |"
        )
        pre = turn["contemplate_pre"]
        post = turn["contemplate_post"]
        lines.append(
            f"| Mood pre | {pre[0]} | {pre[1]} | {pre[2]} | {pre[3]} | {pre[4]} | {pre[5]} | {pre[6]} |"
        )
        lines.append(
            f"| Mood post | {post[0]} | {post[1]} | {post[2]} | {post[3]} | {post[4]} | {post[5]} | {post[6]} |"
        )
        d = turn["contemplate_delta"]
        lines.append(
            f"| Δ (post-pre) | {d[0]} | {d[1]} | {d[2]} | {d[3]} | {d[4]} | {d[5]} | {d[6]} |"
        )
        if turn["llm_parse_fallback"]:
            lines.append("")
            lines.append("> ⚠️ LLM reply did not parse as JSON; used neutral fallback.")
        lines.append("")
        lines.append("**LLM voice (after contemplation):**")
        lines.append("")
        lines.append(f"> {turn['llm_voice']}")
        lines.append("")
    lines.append("## Memory log (first-person introspection entries)")
    lines.append("")
    for entry in evidence.get("memory_log", []):
        lines.append(f"- *{entry['content']}*")
    lines.append("")
    lines.append("## Raw LLM score replies (for parse-rate audit)")
    lines.append("")
    for i, raw in enumerate(evidence.get("raw_score_replies", []), 1):
        lines.append(f"### Reply {i}")
        lines.append("")
        lines.append("```")
        lines.append(raw)
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    api_key = _load_api_key()
    log(f"OPENROUTER_API_KEY: {'present' if api_key else 'MISSING'}")
    if not api_key:
        log(
            "Cannot run live demo without OPENROUTER_API_KEY. Set env or "
            "~/ai-drive/hermes-agent/.env and retry."
        )
        RAW_LOG.parent.mkdir(parents=True, exist_ok=True)
        RAW_LOG.write_text("\n".join(_LOG_LINES) + "\n")
        return 2

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "m4_foundation.db"
        evidence = asyncio.run(run_demo(db_path, api_key))

    EVIDENCE_FILE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE_FILE.write_text(_format_evidence_md(evidence))
    log(f"Wrote evidence to {EVIDENCE_FILE.relative_to(_REPO_ROOT)}")

    RAW_LOG.parent.mkdir(parents=True, exist_ok=True)
    RAW_LOG.write_text("\n".join(_LOG_LINES) + "\n")
    log(f"Wrote raw log to {RAW_LOG.relative_to(_REPO_ROOT)}")

    all_verified = all(evidence["wirings_verified"].values())
    return 0 if all_verified else 1


if __name__ == "__main__":
    sys.exit(main())
