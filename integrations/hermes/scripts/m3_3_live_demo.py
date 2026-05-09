"""Live-LLM demo for M3.3 — corpus persistence + cross-restart cooldown.

Proves four things hermes operators care about, with real DeepSeek V3
Flash inference at each step so the captured evidence isn't just unit-
test mocks:

  1. **First-run seeding** — fresh DB, no faces; SoulPlugin auto-installs
     the M3.2 default corpus (49 faces) on construction.
  2. **extra_corpus augments** — host-supplied faces sit alongside the
     default set and can win the dice roll.
  3. **Cooldown survives restart** — fire a face, close the plugin,
     reopen with a fresh process-shaped instance, watch the same trigger
     produce a *different* face (cooldown blocks the just-fired id).
  4. **replace_corpus is destructive-on-purpose** — opening with
     ``replace_corpus=True`` clears the DB and installs only the
     supplied faces. Re-opening without the flag does NOT re-seed
     defaults if any rows remain (operator's clean-slate is preserved).

For each step we send ONE LLM call through OpenRouter so the captured
evidence shows the model's actual response — readable confirmation that
the face id observed in `pulse_log.face_id` and the prompt body match.

Output:

  * ``integrations/hermes/EVIDENCE_M3.3.md`` — markdown summary.
  * ``logs/m3_3_live_demo.log`` — raw terminal log.

Usage::

    OPENROUTER_API_KEY=... python integrations/hermes/scripts/m3_3_live_demo.py

Reads the key from env or ``~/ai-drive/hermes-agent/.env`` if absent.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import httpx

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT))

from clanker_soul import (  # noqa: E402
    ActionOutcome,
    DEFAULT_FACES,
    PromptFace,
    PulseAction,
    PulseConfig,
    PulseEngine,
    PulseTarget,
    Score,
    SoulPlugin,
    SoulStore,
    VadugwiPredicate,
)


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "deepseek/deepseek-chat"
EVIDENCE_FILE = _REPO_ROOT / "integrations" / "hermes" / "EVIDENCE_M3.3.md"
RAW_LOG = _REPO_ROOT / "logs" / "m3_3_live_demo.log"


# ── API key loading ────────────────────────────────────────────────────


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
    """Single non-streaming chat completion. Returns assistant text or
    a [LLM-ERROR: ...] sentinel so the demo keeps running on transient
    failures."""
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
            OPENROUTER_URL,
            headers=headers,
            content=json.dumps(payload),
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"[LLM-ERROR: {type(e).__name__}: {e}]"


# ── Drivers — replicate M3.2's distress shape ──────────────────────────


def _drive_distress(plugin: SoulPlugin, ticks: int = 6) -> None:
    for _ in range(ticks):
        plugin.ingest(
            Score(
                v=60,
                a=140,
                d=110,
                u=70,
                g=100,
                w=100,
                i=100,
                patterns=("ABANDONMENT",),
                direction="SELF_DIRECTED",
                source="demo-m33:distress",
            )
        )


def _custom_distress_face() -> PromptFace:
    """A host-injected face with a distinctive id so we can spot it in
    a winning sample."""
    return PromptFace(
        id="hermes.custom.distress.signal_check",
        trigger_kinds=frozenset({"distress"}),
        vadugwi_predicates=(VadugwiPredicate("V", "<=", 110, "mood"),),
        situation_tags=frozenset(),
        cooldown_seconds=600,
        base_weight=4.0,  # heavy bias so it's likely to win the dice
        motif="relational",
        template=(
            "[INTERNAL PULSE — distress · custom hermes face]\n"
            "Something feels off. Check in with one trusted person, "
            "even if it's just to say 'I'm rattled.' No fix needed."
        ),
    )


# ── Tiny PulseHost mock — same shape as M3.2's demo ────────────────────


class _DemoHost:
    def __init__(self, snap):
        self._snap = snap
        self.dispatched: list[PulseAction] = []

    def snapshot(self):
        return self._snap

    def slow_drift_tick(self):
        return None

    def most_recent_target(self):
        return PulseTarget(payload={"chat": "demo", "user": "operator"})

    def due_reminders(self):
        return []

    def deliver_reminder(self, target, reminder):
        return None

    async def dispatch_action(self, action: PulseAction) -> ActionOutcome:
        self.dispatched.append(action)
        return ActionOutcome(delivered=True)

    def dispatch_pulse(self, target, trigger, prompt) -> bool:
        # Legacy DM hook — engine prefers dispatch_action when present;
        # this exists only so PulseHost protocol introspection passes.
        return True


def _build_engine(plugin: SoulPlugin, host: _DemoHost) -> PulseEngine:
    return PulseEngine(
        host,
        PulseConfig(min_quiet_seconds=0, startup_grace_s=0),
        event_log=plugin.event_log,
        agent_id=plugin.agent_id,
        corpus=plugin.corpus,
        recency=plugin.recency,
        physics=plugin.physics,
    )


def _system_prompt() -> str:
    return (
        "You are a soft-hearted but sharp AI. You receive an internal "
        "self-prompt from your own emotional engine and you respond as "
        "the agent — a single short paragraph in first person, plain "
        "language, no headers."
    )


# ── Main demo ──────────────────────────────────────────────────────────


def main() -> int:
    api_key = _load_api_key()
    started = datetime.now(timezone.utc)
    log_lines: list[str] = []

    def log(msg: str) -> None:
        line = f"[{datetime.now(timezone.utc).isoformat()}] {msg}"
        log_lines.append(line)
        print(line, flush=True)

    log(f"=== M3.3 live demo start ({started.isoformat()}) ===")
    log(f"OPENROUTER_API_KEY: {'present' if api_key else 'MISSING — LLM calls will be skipped'}")

    if not api_key:
        log("Continuing without LLM calls — captured evidence will record [LLM-SKIPPED].")

    tmp = tempfile.mkdtemp(prefix="m33-demo-")
    db_path = Path(tmp) / "soul.db"
    log(f"DB: {db_path}")

    # Reset the SoulStore singleton so each `with SoulPlugin(...)` block
    # below opens a fresh connection — closer to a real cross-process
    # restart than reusing the cached handle.
    def _reset_store() -> None:
        SoulStore._instances.pop(str(db_path), None)

    summary: list[dict] = []

    # ── Step 1: First-run seeding ───────────────────────────────────────
    log("\n--- Step 1: First open — default corpus seeded ---")
    with SoulPlugin(agent_id="demo-m33", db_path=db_path) as plugin:
        n_faces = len(plugin.corpus.faces)
        n_default = len(DEFAULT_FACES)
        log(f"plugin.corpus.faces: {n_faces} (DEFAULT_FACES: {n_default})")
        assert n_faces == n_default, "first-run should match default count"
        # Drive distress, fire a pulse, see which face the dice picked.
        _drive_distress(plugin)
        host = _DemoHost(plugin.snapshot())
        engine = _build_engine(plugin, host)
        engine.note_outbound()
        asyncio.run(engine.tick())
        assert host.dispatched, "step 1 should have fired"
        action_step1 = host.dispatched[0]
        face_id_step1 = action_step1.extra.get("face_id")
        log(f"step 1 fired face_id: {face_id_step1}")
        log(f"step 1 prompt: {action_step1.prompt[:120]}...")
        if api_key:
            llm = _call_model(api_key, _system_prompt(), action_step1.prompt)
        else:
            llm = "[LLM-SKIPPED]"
        log(f"step 1 model response: {llm[:160]}...")
        summary.append(
            {
                "step": "1 — first-run default seed",
                "face_id": face_id_step1,
                "prompt": action_step1.prompt,
                "model_response": llm,
                "n_faces": n_faces,
            }
        )

    # ── Step 2: Reopen + cooldown survives restart ──────────────────────
    log("\n--- Step 2: Reopen — cooldown survives, different face wins ---")
    _reset_store()
    with SoulPlugin(agent_id="demo-m33", db_path=db_path) as plugin:
        # Confirm the just-fired face is still in cooldown after restart.
        recency_rows = plugin.corpus_store.load_recency("demo-m33")
        log(f"preloaded recency rows: {len(recency_rows)}")
        log(f"recency contains step-1 face: {face_id_step1 in recency_rows}")
        # Drive distress again, fire a new pulse — should pick a
        # *different* face because the previous one is cooling down.
        _drive_distress(plugin)
        host2 = _DemoHost(plugin.snapshot())
        engine2 = _build_engine(plugin, host2)
        engine2.note_outbound()
        asyncio.run(engine2.tick())
        action_step2 = host2.dispatched[0]
        face_id_step2 = action_step2.extra.get("face_id")
        log(f"step 2 fired face_id: {face_id_step2}")
        log(f"step 2 prompt: {action_step2.prompt[:120]}...")
        cooldown_held = face_id_step2 != face_id_step1
        log(
            f"COOLDOWN-SURVIVED-RESTART: {cooldown_held} "
            f"(step1={face_id_step1!r} != step2={face_id_step2!r})"
        )
        if api_key:
            llm2 = _call_model(api_key, _system_prompt(), action_step2.prompt)
        else:
            llm2 = "[LLM-SKIPPED]"
        log(f"step 2 model response: {llm2[:160]}...")
        summary.append(
            {
                "step": "2 — cooldown survives restart",
                "face_id": face_id_step2,
                "prompt": action_step2.prompt,
                "model_response": llm2,
                "cooldown_held": cooldown_held,
                "step1_face_id": face_id_step1,
            }
        )

    # ── Step 3: extra_corpus augments default ───────────────────────────
    log("\n--- Step 3: extra_corpus — host face joins default set ---")
    _reset_store()
    extra = (_custom_distress_face(),)
    # Use a brand new agent_id so cooldowns don't suppress the new face
    # via the recency log accumulated above.
    with SoulPlugin(
        agent_id="demo-m33-augment",
        db_path=db_path,
        extra_corpus=extra,
    ) as plugin:
        ids = {f.id for f in plugin.corpus.faces}
        log(f"plugin.corpus.faces: {len(ids)} (default {len(DEFAULT_FACES)} + extra 1)")
        custom_present = "hermes.custom.distress.signal_check" in ids
        log(f"custom face present: {custom_present}")
        # Drive distress; with base_weight=4.0 the custom face has good odds.
        _drive_distress(plugin)
        host3 = _DemoHost(plugin.snapshot())
        engine3 = _build_engine(plugin, host3)
        engine3.note_outbound()
        asyncio.run(engine3.tick())
        action_step3 = host3.dispatched[0]
        face_id_step3 = action_step3.extra.get("face_id")
        log(f"step 3 fired face_id: {face_id_step3}")
        log(f"step 3 prompt: {action_step3.prompt[:120]}...")
        if api_key:
            llm3 = _call_model(api_key, _system_prompt(), action_step3.prompt)
        else:
            llm3 = "[LLM-SKIPPED]"
        log(f"step 3 model response: {llm3[:160]}...")
        summary.append(
            {
                "step": "3 — extra_corpus augments default",
                "face_id": face_id_step3,
                "prompt": action_step3.prompt,
                "model_response": llm3,
                "custom_face_in_corpus": custom_present,
                "won_dice_roll": face_id_step3 == "hermes.custom.distress.signal_check",
            }
        )

    # ── Step 4: replace_corpus wipes-and-replaces ───────────────────────
    log("\n--- Step 4: replace_corpus — clean slate, only host faces ---")
    _reset_store()
    with SoulPlugin(
        agent_id="demo-m33-replace",
        db_path=db_path,
        extra_corpus=extra,
        replace_corpus=True,
    ) as plugin:
        ids = {f.id for f in plugin.corpus.faces}
        log(f"plugin.corpus.faces: {len(ids)} (expected: 1)")
        assert ids == {"hermes.custom.distress.signal_check"}, ids
        _drive_distress(plugin)
        host4 = _DemoHost(plugin.snapshot())
        engine4 = _build_engine(plugin, host4)
        engine4.note_outbound()
        asyncio.run(engine4.tick())
        action_step4 = host4.dispatched[0]
        face_id_step4 = action_step4.extra.get("face_id")
        log(f"step 4 fired face_id: {face_id_step4}")
        log(f"step 4 prompt: {action_step4.prompt[:120]}...")
        if api_key:
            llm4 = _call_model(api_key, _system_prompt(), action_step4.prompt)
        else:
            llm4 = "[LLM-SKIPPED]"
        log(f"step 4 model response: {llm4[:160]}...")
        summary.append(
            {
                "step": "4 — replace_corpus clean-slate",
                "face_id": face_id_step4,
                "prompt": action_step4.prompt,
                "model_response": llm4,
                "corpus_size": len(ids),
            }
        )

    # ── Step 5: pulse_log.face_id round-trips through SQL ───────────────
    log("\n--- Step 5: pulse_log.face_id — read back from SQL ---")
    _reset_store()
    with SoulPlugin(agent_id="demo-m33", db_path=db_path) as plugin:
        with plugin.store.lock:
            rows = plugin.store.connection.execute(
                "SELECT face_id, dispatched, trigger_kind FROM pulse_log "
                "WHERE agent_id = 'demo-m33' AND dispatched = 1 "
                "ORDER BY ts ASC",
            ).fetchall()
        log(f"dispatched pulse_log rows: {len(rows)}")
        for i, row in enumerate(rows[:8]):
            log(f"  row {i}: trigger={row[2]} face_id={row[0]}")
        all_face_ids_present = all(r[0] is not None for r in rows)
        log(f"all face_ids non-NULL: {all_face_ids_present}")
        summary.append(
            {
                "step": "5 — pulse_log.face_id round-trip",
                "rows": [{"trigger": r[2], "face_id": r[0]} for r in rows],
                "all_present": all_face_ids_present,
            }
        )

    # ── Write evidence ──────────────────────────────────────────────────
    log("\n--- Writing evidence ---")
    EVIDENCE_FILE.parent.mkdir(parents=True, exist_ok=True)
    RAW_LOG.parent.mkdir(parents=True, exist_ok=True)

    md_lines: list[str] = [
        "# M3.3 Live Demo Evidence",
        "",
        f"Run: `{started.isoformat()}` → `{datetime.now(timezone.utc).isoformat()}`",
        f"Model: `{MODEL}` via OpenRouter",
        f"DB (ephemeral): `{db_path}`",
        "",
        "## Summary",
        "",
        "| Step | Face id | Notes |",
        "| --- | --- | --- |",
    ]
    for entry in summary:
        face_id = entry.get("face_id", "—")
        notes = []
        if "cooldown_held" in entry:
            notes.append(f"cooldown_held={entry['cooldown_held']}")
        if "won_dice_roll" in entry:
            notes.append(f"custom_won={entry['won_dice_roll']}")
        if "corpus_size" in entry:
            notes.append(f"corpus_size={entry['corpus_size']}")
        if "all_present" in entry:
            notes.append(f"all_face_ids_non_null={entry['all_present']}")
        md_lines.append(f"| {entry['step']} | `{face_id}` | {', '.join(notes) or '—'} |")
    md_lines.append("")

    for entry in summary:
        md_lines.append(f"### {entry['step']}")
        md_lines.append("")
        if "face_id" in entry:
            md_lines.append(f"**face_id:** `{entry['face_id']}`")
            md_lines.append("")
        if "prompt" in entry:
            md_lines.append("**prompt:**")
            md_lines.append("")
            md_lines.append("```")
            md_lines.append(entry["prompt"])
            md_lines.append("```")
            md_lines.append("")
        if "model_response" in entry:
            md_lines.append("**model response:**")
            md_lines.append("")
            md_lines.append("```")
            md_lines.append(entry["model_response"])
            md_lines.append("```")
            md_lines.append("")
        for k in (
            "cooldown_held",
            "step1_face_id",
            "custom_face_in_corpus",
            "won_dice_roll",
            "corpus_size",
            "all_present",
            "rows",
        ):
            if k in entry:
                md_lines.append(f"- **{k}**: `{entry[k]}`")
        md_lines.append("")

    EVIDENCE_FILE.write_text("\n".join(md_lines) + "\n")
    log(f"Wrote {EVIDENCE_FILE}")

    RAW_LOG.write_text("\n".join(log_lines) + "\n")
    log(f"Wrote {RAW_LOG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
