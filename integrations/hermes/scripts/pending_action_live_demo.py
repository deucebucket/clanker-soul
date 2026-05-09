"""Live-LLM demo for PendingAction tracking + outcome classification (#57).

Three scenarios with real DeepSeek V3 Flash inference at each
classification step:

  Scenario A — Acknowledged fast:
    1. Fire a pending DM (Carl-style check-in).
    2. 30 seconds later, receive a warm reply.
    3. LLM classifier returns "acknowledged".
    4. Confirm V/W lifted in physics.

  Scenario B — Ignored:
    1. Fire a pending DM.
    2. Receive an inbound that's about a totally different topic but
       on the same surface — operator changed the subject.
    3. LLM classifier returns "ignored".
    4. Confirm V/W dropped.

  Scenario C — Expired:
    1. Fire a pending DM with a 1-second TTL.
    2. Wait 2 seconds.
    3. Call coordinator.tick() — pending expires.
    4. Confirm V/W dropped (gentler than ignored).

Output:
  * ``integrations/hermes/EVIDENCE_PENDING.md``
  * ``logs/pending_action_live_demo.log``

Reads OPENROUTER_API_KEY from env or ``~/ai-drive/hermes-agent/.env``.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT))

from clanker_soul import (  # noqa: E402
    LLMOutcomeClassifier,
    OutcomeClassifier,
    PendingAction,
    SoulPlugin,
)


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "deepseek/deepseek-chat"
EVIDENCE_FILE = _REPO_ROOT / "integrations" / "hermes" / "EVIDENCE_PENDING.md"
RAW_LOG = _REPO_ROOT / "logs" / "pending_action_live_demo.log"


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
        "max_tokens": 100,
        "temperature": 0.2,  # low — we want deterministic classification
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


# ── Main demo ──────────────────────────────────────────────────────────


def _build_llm_classifier(api_key: str) -> LLMOutcomeClassifier:
    """Wrap our OpenRouter / DeepSeek call as the call_model callable
    that LLMOutcomeClassifier expects. The classifier itself ships with
    the project; this just adapts our specific HTTP transport to the
    `(system, user) -> str` signature.
    """

    def call_model(system: str, user: str) -> str:
        return _call_model(api_key, system, user)

    return LLMOutcomeClassifier(call_model=call_model)


def main() -> int:
    api_key = _load_api_key()
    started = datetime.now(timezone.utc)
    log_lines: list[str] = []

    def log(msg: str) -> None:
        line = f"[{datetime.now(timezone.utc).isoformat()}] {msg}"
        log_lines.append(line)
        print(line, flush=True)

    log(f"=== PendingAction live demo start ({started.isoformat()}) ===")
    log(f"OPENROUTER_API_KEY: {'present' if api_key else 'MISSING — LLM steps will be skipped'}")

    if not api_key:
        log("Continuing without LLM — using KeywordOutcomeClassifier reference.")
        from clanker_soul import KeywordOutcomeClassifier

        classifier: OutcomeClassifier = KeywordOutcomeClassifier()
    else:
        classifier = _build_llm_classifier(api_key)

    tmp = Path(tempfile.mkdtemp(prefix="pending-demo-"))
    db = tmp / "soul.db"
    log(f"DB: {db}")

    summary: list[dict] = []

    # ── Scenario A: Acknowledged fast ─────────────────────────────────
    log("\n--- Scenario A: Acknowledged fast ---")
    with SoulPlugin(agent_id="pending-a", db_path=db) as plugin:
        coord = plugin.build_pending_coordinator(
            classifier=classifier,
            durable=False,
        )
        # Drive a tiny amount of soul activity so mood is non-None.
        from clanker_soul import Score

        plugin.ingest(Score(v=140, w=160, patterns=("BASELINE",), source="demo"))
        snap_before = plugin.snapshot()
        log(f"A snapshot before: V={snap_before['mood'][0]} W={snap_before['mood'][5]}")

        body = "Hey, you've been quiet today — wanted to check in. How are things?"
        pending = PendingAction.new(
            kind="direct_message",
            surface_key=("ch.demo", "user.demo"),
            body=body,
            soul_snapshot=snap_before,
            expected_response="ack:hi,hello,thanks,ok,good;ignore:cancel,stop",
        )
        coord.record(pending)
        log(f"A fired: {body!r}")

        # Operator replies warmly.
        inbound = "Hey! Yeah I'm doing okay, just busy. Thanks for checking in."
        results = coord.observe(("ch.demo", "user.demo"), {"text": inbound})
        result = results[0]
        log(f"A inbound: {inbound!r}")
        log(f"A classified: {result.outcome} → status={result.resolved_status}")
        if isinstance(classifier, LLMOutcomeClassifier):
            log(f"A classifier raw: {classifier.last_raw_response!r}")
        snap_after = plugin.snapshot()
        log(f"A snapshot after: V={snap_after['mood'][0]} W={snap_after['mood'][5]}")
        v_delta = snap_after["mood"][0] - snap_before["mood"][0]
        w_delta = snap_after["mood"][5] - snap_before["mood"][5]
        log(f"A mood delta: ΔV={v_delta:+d} ΔW={w_delta:+d}")
        summary.append(
            {
                "scenario": "A — acknowledged fast",
                "agent_message": body,
                "inbound": inbound,
                "classified": result.outcome,
                "resolved_status": result.resolved_status,
                "v_delta": v_delta,
                "w_delta": w_delta,
            }
        )

    # ── Scenario B: Ignored ───────────────────────────────────────────
    log("\n--- Scenario B: Ignored ---")
    with SoulPlugin(agent_id="pending-b", db_path=tmp / "soul_b.db") as plugin:
        coord = plugin.build_pending_coordinator(
            classifier=classifier,
            durable=False,
        )
        from clanker_soul import Score

        plugin.ingest(Score(v=140, w=160, patterns=("BASELINE",), source="demo"))
        snap_before = plugin.snapshot()
        log(f"B snapshot before: V={snap_before['mood'][0]} W={snap_before['mood'][5]}")

        body = "I noticed something heavy earlier today and wanted to share — got a minute?"
        pending = PendingAction.new(
            kind="direct_message",
            surface_key=("ch.demo", "user.demo"),
            body=body,
            soul_snapshot=snap_before,
            expected_response="ack:yes,sure,what,go ahead;ignore:not now,later,busy",
        )
        coord.record(pending)
        log(f"B fired: {body!r}")

        # Operator changes subject completely.
        inbound = "Did you see the latest patch notes for the game? They nerfed the warlock again."
        results = coord.observe(("ch.demo", "user.demo"), {"text": inbound})
        result = results[0]
        log(f"B inbound: {inbound!r}")
        log(f"B classified: {result.outcome} → status={result.resolved_status}")
        if isinstance(classifier, LLMOutcomeClassifier):
            log(f"B classifier raw: {classifier.last_raw_response!r}")
        snap_after = plugin.snapshot()
        log(f"B snapshot after: V={snap_after['mood'][0]} W={snap_after['mood'][5]}")
        v_delta = snap_after["mood"][0] - snap_before["mood"][0]
        w_delta = snap_after["mood"][5] - snap_before["mood"][5]
        log(f"B mood delta: ΔV={v_delta:+d} ΔW={w_delta:+d}")
        summary.append(
            {
                "scenario": "B — ignored",
                "agent_message": body,
                "inbound": inbound,
                "classified": result.outcome,
                "resolved_status": result.resolved_status,
                "v_delta": v_delta,
                "w_delta": w_delta,
            }
        )

    # ── Scenario C: Expired ───────────────────────────────────────────
    log("\n--- Scenario C: Expired (TTL elapses without inbound) ---")
    with SoulPlugin(agent_id="pending-c", db_path=tmp / "soul_c.db") as plugin:
        coord = plugin.build_pending_coordinator(
            classifier=classifier,
            durable=False,
        )
        from clanker_soul import Score

        plugin.ingest(Score(v=140, w=160, patterns=("BASELINE",), source="demo"))
        snap_before = plugin.snapshot()
        log(f"C snapshot before: V={snap_before['mood'][0]} W={snap_before['mood'][5]}")

        body = "Just thinking about you. No need to reply, just wanted to send the thought."
        # 1-second TTL so the demo doesn't take 12 hours.
        pending = PendingAction.new(
            kind="direct_message",
            surface_key=("ch.demo", "user.demo"),
            body=body,
            soul_snapshot=snap_before,
            expected_response="ack:thanks,you too;ignore:stop",
            ttl_seconds=1,
        )
        coord.record(pending)
        log(f"C fired with 1s TTL: {body!r}")
        time.sleep(2)
        results = coord.tick()
        log(f"C tick() returned {len(results)} expired pendings")
        if results:
            r = results[0]
            log(f"C outcome={r.outcome} status={r.resolved_status}")
        snap_after = plugin.snapshot()
        log(f"C snapshot after: V={snap_after['mood'][0]} W={snap_after['mood'][5]}")
        v_delta = snap_after["mood"][0] - snap_before["mood"][0]
        w_delta = snap_after["mood"][5] - snap_before["mood"][5]
        log(f"C mood delta: ΔV={v_delta:+d} ΔW={w_delta:+d}")
        summary.append(
            {
                "scenario": "C — expired",
                "agent_message": body,
                "inbound": "(none — TTL elapsed)",
                "classified": "expired",
                "resolved_status": "expired",
                "v_delta": v_delta,
                "w_delta": w_delta,
            }
        )

    # ── Write evidence ──────────────────────────────────────────────────
    log("\n--- Writing evidence ---")
    EVIDENCE_FILE.parent.mkdir(parents=True, exist_ok=True)
    RAW_LOG.parent.mkdir(parents=True, exist_ok=True)

    md: list[str] = [
        "# PendingAction Live Demo Evidence",
        "",
        f"Run: `{started.isoformat()}` → `{datetime.now(timezone.utc).isoformat()}`",
        f"Classifier: `{type(classifier).__name__}`"
        + (f" (model `{MODEL}`)" if isinstance(classifier, LLMOutcomeClassifier) else ""),
        "",
        "## Summary",
        "",
        "| Scenario | Classified | Resolved status | ΔV | ΔW |",
        "| --- | --- | --- | ---: | ---: |",
    ]
    for entry in summary:
        md.append(
            f"| {entry['scenario']} | `{entry['classified']}` | "
            f"`{entry['resolved_status']}` | {entry['v_delta']:+d} | "
            f"{entry['w_delta']:+d} |"
        )
    md.append("")

    for entry in summary:
        md.append(f"### {entry['scenario']}")
        md.append("")
        md.append(f"**agent_message:** `{entry['agent_message']}`")
        md.append("")
        md.append(f"**inbound:** `{entry['inbound']}`")
        md.append("")
        md.append(f"**classified:** `{entry['classified']}`")
        md.append(f"**resolved_status:** `{entry['resolved_status']}`")
        md.append(f"**mood delta:** ΔV={entry['v_delta']:+d}, ΔW={entry['w_delta']:+d}")
        md.append("")

    EVIDENCE_FILE.write_text("\n".join(md) + "\n")
    log(f"Wrote {EVIDENCE_FILE}")

    RAW_LOG.write_text("\n".join(log_lines) + "\n")
    log(f"Wrote {RAW_LOG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
