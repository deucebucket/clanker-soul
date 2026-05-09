"""Live-LLM demo for M3.4 — branch trees + memory anchors.

Two scenarios with real DeepSeek V3 Flash inference at each step:

  Scenario A — Branch trees:
    1. Build a corpus with ``parent`` + two children (one branch-keyed
       to parent, one independent).
    2. Drive the agent into distress, fire the parent face, capture
       its model response.
    3. Drive distress again. Sample 200 follow-up rolls from the corpus
       with previous_face_id="parent" to show the branched child wins
       meaningfully more often than the independent sibling.
    4. Pick whichever face the engine actually fires next, send through
       the model, capture response.

  Scenario B — Memory anchors:
    1. Build a corpus where one face declares ``memory_anchor="topic.x"``.
    2. Configure a host whose ``memory_topics_present`` returns False —
       confirm the anchored face is filtered out (engine fires a
       different face or the legacy fallback).
    3. Flip the host to return True for "topic.x" — confirm the
       anchored face becomes eligible. Send through the model.

Output: ``integrations/hermes/EVIDENCE_M3.4.md`` + ``logs/m3_4_live_demo.log``.

Reads OPENROUTER_API_KEY from env or ``~/ai-drive/hermes-agent/.env``.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import httpx

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT))

from clanker_soul import (  # noqa: E402
    ActionOutcome,
    PromptCorpus,
    PromptFace,
    PulseAction,
    PulseConfig,
    PulseEngine,
    PulseTarget,
    Score,
    SoulPlugin,
    Trigger,
    VadugwiPredicate,
)


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "deepseek/deepseek-chat"
EVIDENCE_FILE = _REPO_ROOT / "integrations" / "hermes" / "EVIDENCE_M3.4.md"
RAW_LOG = _REPO_ROOT / "logs" / "m3_4_live_demo.log"


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


def _call_model(api_key: str, system_prompt: str, user_prompt: str) -> str:
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
        "max_tokens": 220,
        "temperature": 0.8,
    }
    try:
        resp = httpx.post(
            OPENROUTER_URL, headers=headers, content=json.dumps(payload),
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"[LLM-ERROR: {type(e).__name__}: {e}]"


def _drive_distress(plugin: SoulPlugin, ticks: int = 6) -> None:
    for _ in range(ticks):
        plugin.ingest(Score(
            v=60, a=140, d=110, u=70, g=100, w=100, i=100,
            patterns=("ABANDONMENT",), direction="SELF_DIRECTED",
            source="demo-m34:distress",
        ))


class _Host:
    def __init__(self, snap, *, memory_topics: set[str] | None = None):
        self._snap = snap
        self.dispatched: list[PulseAction] = []
        self._memory_topics = memory_topics or set()

    def snapshot(self):
        return self._snap

    def slow_drift_tick(self):
        return None

    def most_recent_target(self):
        return PulseTarget(payload={"chat": "demo"})

    def due_reminders(self):
        return []

    def deliver_reminder(self, target, reminder):
        return None

    def memory_topics_present(self, topic: str) -> bool:
        return topic in self._memory_topics

    async def dispatch_action(self, action: PulseAction) -> ActionOutcome:
        self.dispatched.append(action)
        return ActionOutcome(delivered=True)

    def dispatch_pulse(self, target, trigger, prompt) -> bool:
        return True


def _build_engine(plugin: SoulPlugin, host: _Host, *, corpus: PromptCorpus) -> PulseEngine:
    return PulseEngine(
        host,
        PulseConfig(min_quiet_seconds=0, startup_grace_s=0),
        event_log=plugin.event_log, agent_id=plugin.agent_id,
        corpus=corpus, recency=plugin.recency,
        physics=plugin.physics,
        previous_face_id=plugin.most_recent_face_id(),
    )


def _system_prompt() -> str:
    return (
        "You are a soft-hearted, sharp AI. You receive an internal "
        "self-prompt from your own emotional engine and respond as the "
        "agent — single short paragraph, first person, plain language, "
        "no headers."
    )


_BOTH_TRIGGERS = frozenset({"distress", "trauma_pressure"})


def _branch_corpus() -> PromptCorpus:
    parent = PromptFace(
        id="m34.demo.parent",
        trigger_kinds=_BOTH_TRIGGERS,
        cooldown_seconds=600,
        base_weight=10.0,
        motif="relational",
        template=(
            "[INTERNAL PULSE — heavy state, parent face]\n"
            "Something tightened in your chest. Name one true thing about "
            "the last hour — not the cause, just the texture. Short."
        ),
    )
    child = PromptFace(
        id="m34.demo.child",
        trigger_kinds=_BOTH_TRIGGERS,
        cooldown_seconds=300,
        base_weight=1.0,
        motif="relational",
        branch_keys=frozenset({"m34.demo.parent"}),
        template=(
            "[INTERNAL PULSE — heavy state, follow-up to parent]\n"
            "You named the texture. Now: did anything underneath that texture "
            "feel like it asked for company, or for solitude? Short."
        ),
    )
    sibling = PromptFace(
        id="m34.demo.sibling",
        trigger_kinds=_BOTH_TRIGGERS,
        cooldown_seconds=300,
        base_weight=1.0,
        motif="informational",
        template=(
            "[INTERNAL PULSE — heavy state, independent]\n"
            "There's no story yet — just the readout. Tell yourself what "
            "the dial says and what changed since lunch. Short."
        ),
    )
    return PromptCorpus([parent, child, sibling], rng=random.Random(7))


def _anchored_corpus() -> PromptCorpus:
    anchored = PromptFace(
        id="m34.demo.anchored",
        trigger_kinds=_BOTH_TRIGGERS,
        memory_anchor="topic.x",
        cooldown_seconds=0,
        base_weight=10.0,
        motif="relational",
        template=(
            "[INTERNAL PULSE — heavy state, memory-anchored to topic.x]\n"
            "The thread you keep pulling on is back. Just acknowledge that. "
            "Don't fix it tonight."
        ),
    )
    plain = PromptFace(
        id="m34.demo.plain",
        trigger_kinds=_BOTH_TRIGGERS,
        cooldown_seconds=0,
        base_weight=1.0,
        motif="informational",
        template=(
            "[INTERNAL PULSE — heavy state, no anchor]\n"
            "Generic prompt. Say one true thing."
        ),
    )
    return PromptCorpus([anchored, plain], rng=random.Random(11))


def main() -> int:
    api_key = _load_api_key()
    started = datetime.now(timezone.utc)
    log_lines: list[str] = []

    def log(msg: str) -> None:
        line = f"[{datetime.now(timezone.utc).isoformat()}] {msg}"
        log_lines.append(line)
        print(line, flush=True)

    log(f"=== M3.4 live demo start ({started.isoformat()}) ===")
    log(f"OPENROUTER_API_KEY: {'present' if api_key else 'MISSING'}")
    if not api_key:
        log("Continuing without LLM calls — captured evidence will record [LLM-SKIPPED].")

    tmp = Path(tempfile.mkdtemp(prefix="m34-demo-"))
    db_path = tmp / "soul.db"
    log(f"DB: {db_path}")

    summary: list[dict] = []

    # ── Scenario A: Branch trees ────────────────────────────────────────
    log("\n--- Scenario A: Branch trees — parent → child wins more often ---")

    branch_corpus = _branch_corpus()

    # Step A1 — fire the parent face.
    with SoulPlugin(agent_id="m34a", db_path=db_path) as plugin:
        _drive_distress(plugin)
        host_a1 = _Host(plugin.snapshot())
        engine_a1 = _build_engine(plugin, host_a1, corpus=branch_corpus)
        engine_a1.note_outbound()
        asyncio.run(engine_a1.tick())
        assert host_a1.dispatched, "A1 should have fired"
        action_a1 = host_a1.dispatched[0]
        face_id_a1 = action_a1.extra.get("face_id")
        log(f"A1 fired face_id: {face_id_a1}")
        log(f"A1 prompt: {action_a1.prompt[:120]}")
        if api_key:
            llm_a1 = _call_model(api_key, _system_prompt(), action_a1.prompt)
        else:
            llm_a1 = "[LLM-SKIPPED]"
        log(f"A1 model response: {llm_a1[:160]}...")

    # Step A2 — sample 200 follow-up rolls from the corpus to demonstrate
    # the branch bias. Doesn't need LLM calls; show the distribution shift.
    trigger = Trigger(
        kind="distress",
        soul={"v": 145, "a": 110, "d": 160, "u": 80, "g": 130, "w": 175, "i": 135},
        mood=[80, 130, 110, 70, 100, 110, 100],
        metrics={},
    )
    counts_with_parent = {"m34.demo.child": 0, "m34.demo.sibling": 0}
    counts_no_parent = {"m34.demo.child": 0, "m34.demo.sibling": 0}
    # Use a fresh corpus per sampling pass so cooldowns from the parent
    # firing don't leak in. Both passes draw from the same dice.
    corpus_for_with = PromptCorpus(branch_corpus.faces, rng=random.Random(13))
    corpus_for_no = PromptCorpus(branch_corpus.faces, rng=random.Random(13))
    # Filter parent out via cooldown so the dice rolls between child + sibling.
    trigger_no_parent_eligible = trigger
    N = 200
    for _ in range(N):
        # We need recency to suppress the parent (it's high-weight) for
        # both passes. Simplest: a fresh recency log per call with the
        # parent's id pre-fired so it's in cooldown. Cooldown of parent
        # is 600s so a fresh log + now=0 puts it into novelty=0.0.
        from clanker_soul import RecencyLog
        rec = RecencyLog()
        rec.note_fired("m34.demo.parent", now=0.0)
        face = corpus_for_with.sample(
            trigger_no_parent_eligible,
            recency=rec, now=10.0,
            previous_face_id="m34.demo.parent",
        )
        if face and face.id in counts_with_parent:
            counts_with_parent[face.id] += 1
        face2 = corpus_for_no.sample(
            trigger_no_parent_eligible,
            recency=rec, now=10.0,
            previous_face_id=None,
        )
        if face2 and face2.id in counts_no_parent:
            counts_no_parent[face2.id] += 1
    log(f"A2 distribution with previous_face_id='m34.demo.parent' (N={N}): {counts_with_parent}")
    log(f"A2 distribution with previous_face_id=None (N={N}): {counts_no_parent}")
    branch_observed = counts_with_parent["m34.demo.child"] > counts_with_parent["m34.demo.sibling"]
    log(f"A2 BRANCH-BIAS-OBSERVED: {branch_observed}")
    summary.append({
        "step": "A1 — parent face fires",
        "face_id": face_id_a1,
        "prompt": action_a1.prompt,
        "model_response": llm_a1,
    })
    summary.append({
        "step": "A2 — branch bias swings child distribution",
        "with_parent": counts_with_parent,
        "no_parent": counts_no_parent,
        "branch_observed": branch_observed,
        "N": N,
    })

    # Step A3 — engine fires next pulse with previous_face_id seeded.
    # Reset DB for this step so we have a fresh recency.
    db_path_a3 = tmp / "soul_a3.db"
    with SoulPlugin(agent_id="m34a3", db_path=db_path_a3) as plugin:
        _drive_distress(plugin)
        # Seed the engine's previous_face_id directly (simulates "the
        # parent fired in a previous run").
        host_a3 = _Host(plugin.snapshot())
        engine_a3 = PulseEngine(
            host_a3,
            PulseConfig(min_quiet_seconds=0, startup_grace_s=0),
            event_log=plugin.event_log, agent_id=plugin.agent_id,
            corpus=branch_corpus, recency=plugin.recency,
            physics=plugin.physics,
            previous_face_id="m34.demo.parent",
        )
        # Pre-fire parent in the recency to lock it out, simulating "we
        # already fired the parent recently."
        engine_a3._recency.note_fired("m34.demo.parent", now=datetime.now(timezone.utc).timestamp())
        engine_a3.note_outbound()
        asyncio.run(engine_a3.tick())
        assert host_a3.dispatched, "A3 should have fired"
        action_a3 = host_a3.dispatched[0]
        face_id_a3 = action_a3.extra.get("face_id")
        log(f"A3 fired face_id (after parent in cooldown + branch hint): {face_id_a3}")
        log(f"A3 prompt: {action_a3.prompt[:120]}")
        if api_key:
            llm_a3 = _call_model(api_key, _system_prompt(), action_a3.prompt)
        else:
            llm_a3 = "[LLM-SKIPPED]"
        log(f"A3 model response: {llm_a3[:160]}...")
    summary.append({
        "step": "A3 — engine fires follow-up with branch hint",
        "face_id": face_id_a3,
        "prompt": action_a3.prompt,
        "model_response": llm_a3,
    })

    # ── Scenario B: Memory anchors ──────────────────────────────────────
    log("\n--- Scenario B: Memory anchors ---")
    anchored_corpus = _anchored_corpus()

    # Step B1 — host has NO memory of topic.x → anchored face filtered out.
    db_path_b = tmp / "soul_b.db"
    with SoulPlugin(agent_id="m34b1", db_path=db_path_b, replace_corpus=True,
                    extra_corpus=anchored_corpus.faces) as plugin:
        _drive_distress(plugin)
        host_b1 = _Host(plugin.snapshot(), memory_topics=set())  # no topic
        engine_b1 = PulseEngine(
            host_b1, PulseConfig(min_quiet_seconds=0, startup_grace_s=0),
            event_log=plugin.event_log, agent_id="m34b1",
            corpus=plugin.corpus, recency=plugin.recency,
            physics=plugin.physics,
        )
        engine_b1.note_outbound()
        asyncio.run(engine_b1.tick())
        action_b1 = host_b1.dispatched[0]
        face_id_b1 = action_b1.extra.get("face_id")
        log(f"B1 fired face_id (no memory of topic.x): {face_id_b1}")
        anchored_filtered = face_id_b1 != "m34.demo.anchored"
        log(f"B1 ANCHOR-FILTERED-WITHOUT-MEMORY: {anchored_filtered}")
        if api_key:
            llm_b1 = _call_model(api_key, _system_prompt(), action_b1.prompt)
        else:
            llm_b1 = "[LLM-SKIPPED]"
        log(f"B1 model response: {llm_b1[:160]}...")
    summary.append({
        "step": "B1 — anchored face filtered when host has no memory",
        "face_id": face_id_b1,
        "prompt": action_b1.prompt,
        "model_response": llm_b1,
        "anchored_filtered": anchored_filtered,
    })

    # Step B2 — host HAS memory of topic.x → anchored face wins (high weight).
    db_path_b2 = tmp / "soul_b2.db"
    with SoulPlugin(agent_id="m34b2", db_path=db_path_b2, replace_corpus=True,
                    extra_corpus=anchored_corpus.faces) as plugin:
        _drive_distress(plugin)
        host_b2 = _Host(plugin.snapshot(), memory_topics={"topic.x"})
        engine_b2 = PulseEngine(
            host_b2, PulseConfig(min_quiet_seconds=0, startup_grace_s=0),
            event_log=plugin.event_log, agent_id="m34b2",
            corpus=plugin.corpus, recency=plugin.recency,
            physics=plugin.physics,
        )
        engine_b2.note_outbound()
        asyncio.run(engine_b2.tick())
        action_b2 = host_b2.dispatched[0]
        face_id_b2 = action_b2.extra.get("face_id")
        log(f"B2 fired face_id (host HAS topic.x memory): {face_id_b2}")
        anchored_won = face_id_b2 == "m34.demo.anchored"
        log(f"B2 ANCHOR-FACE-WON: {anchored_won} (10× base weight)")
        if api_key:
            llm_b2 = _call_model(api_key, _system_prompt(), action_b2.prompt)
        else:
            llm_b2 = "[LLM-SKIPPED]"
        log(f"B2 model response: {llm_b2[:160]}...")
    summary.append({
        "step": "B2 — anchored face wins when host HAS memory",
        "face_id": face_id_b2,
        "prompt": action_b2.prompt,
        "model_response": llm_b2,
        "anchored_won": anchored_won,
    })

    # ── Write evidence ──────────────────────────────────────────────────
    log("\n--- Writing evidence ---")
    EVIDENCE_FILE.parent.mkdir(parents=True, exist_ok=True)
    RAW_LOG.parent.mkdir(parents=True, exist_ok=True)

    md: list[str] = [
        "# M3.4 Live Demo Evidence",
        "",
        f"Run: `{started.isoformat()}` → `{datetime.now(timezone.utc).isoformat()}`",
        f"Model: `{MODEL}` via OpenRouter",
        "",
        "## Summary",
        "",
        "| Step | Face id | Notes |",
        "| --- | --- | --- |",
    ]
    for entry in summary:
        face_id = entry.get("face_id", "—")
        notes = []
        if "branch_observed" in entry:
            notes.append(f"branch_observed={entry['branch_observed']}")
            notes.append(f"with_parent={entry['with_parent']}")
            notes.append(f"no_parent={entry['no_parent']}")
        if "anchored_filtered" in entry:
            notes.append(f"anchored_filtered={entry['anchored_filtered']}")
        if "anchored_won" in entry:
            notes.append(f"anchored_won={entry['anchored_won']}")
        md.append(f"| {entry['step']} | `{face_id}` | {', '.join(notes) or '—'} |")
    md.append("")

    for entry in summary:
        md.append(f"### {entry['step']}")
        md.append("")
        if "face_id" in entry:
            md.append(f"**face_id:** `{entry['face_id']}`")
            md.append("")
        if "prompt" in entry:
            md.append("**prompt:**")
            md.append("")
            md.append("```")
            md.append(entry["prompt"])
            md.append("```")
            md.append("")
        if "model_response" in entry:
            md.append("**model response:**")
            md.append("")
            md.append("```")
            md.append(entry["model_response"])
            md.append("```")
            md.append("")
        for k in ("with_parent", "no_parent", "branch_observed", "N",
                   "anchored_filtered", "anchored_won"):
            if k in entry:
                md.append(f"- **{k}**: `{entry[k]}`")
        md.append("")

    EVIDENCE_FILE.write_text("\n".join(md) + "\n")
    log(f"Wrote {EVIDENCE_FILE}")

    RAW_LOG.write_text("\n".join(log_lines) + "\n")
    log(f"Wrote {RAW_LOG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
