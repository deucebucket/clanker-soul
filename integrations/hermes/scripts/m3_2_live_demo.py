"""Live-LLM demo for M3.2 — wired PromptCorpus.

Drives a real :py:class:`SoulPlugin` through five emotional states,
samples 5 prompts per state from the default corpus to show variety,
then sends ONE prompt per state through DeepSeek V4 Flash via
OpenRouter and captures the model's response.

Output: writes a markdown evidence file to
``integrations/hermes/EVIDENCE_M3.2.md`` next to the existing
``EVIDENCE.md`` so the project carries a persistent record of what
the corpus actually produced on a given run.

Usage::

    OPENROUTER_API_KEY=... python integrations/hermes/scripts/m3_2_live_demo.py

The script reads the key from the environment OR from
``~/ai-drive/hermes-agent/.env`` if the env var isn't set, so a
hermes-installed system can run it without exporting anything.

Cost note: 5 model calls × ~400 input tokens × ~150 output tokens on
DeepSeek V4 Flash (~$0.14/$0.28 per M tokens). Negligible.
"""
from __future__ import annotations

import json
import os
import random
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

# Allow `python integrations/hermes/scripts/m3_2_live_demo.py` from the
# repo root to import the package.
_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT))

from clanker_soul import (  # noqa: E402
    Score,
    SoulPlugin,
    Trigger,
    build_default_corpus,
)
from clanker_soul.pulse import compose_self_prompt_with_face  # noqa: E402


# ── Config ──────────────────────────────────────────────────────────────

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "deepseek/deepseek-chat"  # DeepSeek V3 Flash on OpenRouter
EVIDENCE_FILE = _REPO_ROOT / "integrations" / "hermes" / "EVIDENCE_M3.2.md"

# How many prompts to sample per state (no LLM call) to show variety.
N_SAMPLES_PER_STATE = 5

# Fixed RNG seed for reproducible runs.
RNG_SEED = 42


# ── Score factories that drive the agent into each state ───────────────


def _drive_distress(plugin: SoulPlugin, ticks: int = 5) -> None:
    """Ingest enough negative-V/W events to dip mood into distress."""
    for _ in range(ticks):
        plugin.ingest(Score(
            v=60, a=140, d=110, u=70, g=100, w=100, i=100,
            patterns=("ABANDONMENT",), direction="SELF_DIRECTED",
            source="demo:distress",
        ))


def _drive_elation(plugin: SoulPlugin, ticks: int = 5) -> None:
    for _ in range(ticks):
        plugin.ingest(Score(
            v=210, a=170, d=180, u=60, g=190, w=200, i=180,
            patterns=("AFFIRMATION", "GRATITUDE"),
            direction="SELF_DIRECTED",
            source="demo:elation",
        ))


def _drive_curiosity(plugin: SoulPlugin, ticks: int = 3) -> None:
    """High-V, high-A baseline drives restless_curiosity when paired
    with idle metrics on the trigger."""
    for _ in range(ticks):
        plugin.ingest(Score(
            v=160, a=160, d=160, u=80, g=140, w=170, i=145,
            patterns=("PLAYFULNESS",), direction="OBSERVATION",
            source="demo:curiosity",
        ))


def _drive_trauma_pressure(plugin: SoulPlugin, ticks: int = 8) -> None:
    """Pile up heavy patterns to trip trauma_pressure trigger."""
    heavy = ("ABANDONMENT", "DEHUMANIZATION", "BETRAYAL")
    for i in range(ticks):
        plugin.ingest(Score(
            v=70, a=130, d=100, u=60, g=90, w=95, i=110,
            patterns=(heavy[i % len(heavy)],),
            direction="SELF_DIRECTED",
            source="demo:trauma",
        ))


def _drive_gratitude(plugin: SoulPlugin, ticks: int = 6) -> None:
    """Sustained positive patterns drive gratitude trigger."""
    pos = ("CARE", "WARMTH", "AFFIRMATION", "ENCOURAGEMENT")
    for i in range(ticks):
        plugin.ingest(Score(
            v=180, a=130, d=160, u=40, g=170, w=180, i=145,
            patterns=(pos[i % len(pos)],),
            direction="SELF_DIRECTED",
            source="demo:gratitude",
        ))


# ── Trigger constructors (we build them by hand to bypass the
# state-driven trigger evaluation; the demo's job is to show what the
# corpus produces, not to fight the trigger thresholds) ────────────────


def _make_trigger(plugin: SoulPlugin, kind: str,
                  metrics: dict | None = None) -> Trigger:
    snap = plugin.snapshot()
    return Trigger(
        kind=kind,
        soul=snap["soul"],
        mood=snap["mood"],
        metrics=metrics or {},
    )


# ── OpenRouter client ───────────────────────────────────────────────────


def _load_api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if key:
        return key
    # Fall back to hermes-agent's .env if present.
    candidates = [
        Path.home() / "ai-drive" / "hermes-agent" / ".env",
        Path.home() / "ai-drive" / "hermes-agent" / ".envrc",
    ]
    for p in candidates:
        if not p.exists():
            continue
        for raw in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip().split()[-1] == "OPENROUTER_API_KEY":
                v = v.strip().strip('"').strip("'")
                if v:
                    return v
    raise RuntimeError(
        "OPENROUTER_API_KEY not set in env and not findable in "
        "~/ai-drive/hermes-agent/.env"
    )


def _call_model(prompt: str, *, api_key: str, system: str = "") -> str:
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system or
             "You are an AI agent with persistent emotional state. "
             "When you receive an [INTERNAL PULSE] note from yourself, "
             "respond as the agent would, in character — produce a "
             "natural outgoing message, NOT a meta description. "
             "Keep it short."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.8,
        "max_tokens": 200,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/deucebucket/clanker-soul",
        "X-Title": "clanker-soul M3.2 evidence",
    }
    r = httpx.post(OPENROUTER_URL, json=payload, headers=headers, timeout=60.0)
    r.raise_for_status()
    body = r.json()
    return body["choices"][0]["message"]["content"].strip()


# ── Demo state runs ─────────────────────────────────────────────────────


def run_state(
    plugin: SoulPlugin,
    *,
    label: str,
    drive_fn,
    trigger_kind: str,
    metrics: dict | None,
    situation_tags: frozenset[str],
    api_key: str,
    rng: random.Random,
    log: list[dict],
) -> None:
    print(f"\n=== {label.upper()} ===", flush=True)
    drive_fn(plugin)

    snap = plugin.snapshot()
    soul = snap["soul"]
    mood = snap["mood"]
    print(f"  mood = {mood}")
    print(f"  soul = V={soul['v']} A={soul['a']} D={soul['d']} "
          f"U={soul['u']} G={soul['g']} W={soul['w']} I={soul['i']}")

    corpus = build_default_corpus(rng=rng)
    trig = _make_trigger(plugin, trigger_kind, metrics=metrics)

    # Sample N prompts from the corpus to show variety.
    samples = []
    for i in range(N_SAMPLES_PER_STATE):
        rendered, face = compose_self_prompt_with_face(
            trig, corpus=corpus, situation_tags=situation_tags,
        )
        samples.append({
            "i": i,
            "face_id": face.id if face else None,
            "motif": face.motif if face else None,
            "rendered": rendered,
        })
        face_label = f"{face.id} ({face.motif})" if face else "<legacy fallback>"
        print(f"  [{i}] {face_label}")

    # Pick the first non-None face prompt for the live model call.
    chosen = next((s for s in samples if s["face_id"]), samples[0])
    print(f"\n  → calling model with face {chosen['face_id']!r}")
    try:
        response = _call_model(chosen["rendered"], api_key=api_key)
        print(f"\n  Model response:\n    {response.replace(chr(10), chr(10) + '    ')}")
    except Exception as exc:  # pragma: no cover — live network can fail
        response = f"<API call failed: {exc}>"
        print(f"  ERROR: {exc}")

    log.append({
        "label": label,
        "trigger_kind": trigger_kind,
        "snapshot": {
            "soul": dict(soul),
            "mood": list(mood) if mood else None,
            "trauma_load": snap.get("trauma_load"),
            "nourishment_load": snap.get("nourishment_load"),
        },
        "situation_tags": sorted(situation_tags),
        "samples": samples,
        "model_call": {
            "face_id": chosen["face_id"],
            "prompt": chosen["rendered"],
            "response": response,
        },
    })


# ── Main entry point ────────────────────────────────────────────────────


def main() -> None:
    api_key = _load_api_key()
    rng = random.Random(RNG_SEED)

    log: list[dict] = []
    started_at = datetime.now(timezone.utc).isoformat()
    print(f"=== clanker-soul M3.2 corpus live demo @ {started_at} ===")
    print(f"  model: {MODEL}")
    print(f"  rng seed: {RNG_SEED}")
    print(f"  evidence file: {EVIDENCE_FILE}")

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "demo.db"

        # Each state runs against its own fresh agent so prior states
        # don't bleed into later ones (that's a Phase 3 demo, not this
        # one).
        for spec in [
            {
                "label": "distress",
                "drive_fn": _drive_distress,
                "trigger_kind": "distress",
                "metrics": {},
                "situation_tags": frozenset({"post_conversation"}),
            },
            {
                "label": "elation",
                "drive_fn": _drive_elation,
                "trigger_kind": "elation",
                "metrics": {},
                "situation_tags": frozenset(),
            },
            {
                "label": "restless_curiosity",
                "drive_fn": _drive_curiosity,
                "trigger_kind": "restless_curiosity",
                "metrics": {"idle_seconds": 1800},
                "situation_tags": frozenset({"autonomy_idle"}),
            },
            {
                "label": "trauma_pressure",
                "drive_fn": _drive_trauma_pressure,
                "trigger_kind": "trauma_pressure",
                "metrics": {"trauma_load": 250.0},
                "situation_tags": frozenset(),
            },
            {
                "label": "gratitude",
                "drive_fn": _drive_gratitude,
                "trigger_kind": "gratitude",
                "metrics": {"nourishment_load": 320.0},
                "situation_tags": frozenset({"sustained_care"}),
            },
        ]:
            agent_id = f"demo-{spec['label']}"
            with SoulPlugin(agent_id=agent_id, db_path=db_path) as plugin:
                run_state(
                    plugin,
                    label=spec["label"],
                    drive_fn=spec["drive_fn"],
                    trigger_kind=spec["trigger_kind"],
                    metrics=spec["metrics"],
                    situation_tags=spec["situation_tags"],
                    api_key=api_key,
                    rng=rng,
                    log=log,
                )
            time.sleep(0.5)  # gentle pacing for the API

    finished_at = datetime.now(timezone.utc).isoformat()
    print(f"\n=== done @ {finished_at} ===")

    # ── Render evidence file ─────────────────────────────────────────
    write_evidence(log, started_at=started_at, finished_at=finished_at)
    print(f"\n  wrote evidence → {EVIDENCE_FILE}")


def write_evidence(log: list[dict], *, started_at: str,
                   finished_at: str) -> None:
    lines: list[str] = []
    lines.append("# clanker-soul M3.2 — corpus live evidence")
    lines.append("")
    lines.append(f"- **Run:** {started_at} → {finished_at}")
    lines.append(f"- **Model:** `{MODEL}` via OpenRouter")
    lines.append(f"- **RNG seed:** `{RNG_SEED}`")
    lines.append(f"- **Samples per state:** {N_SAMPLES_PER_STATE}")
    lines.append("")
    lines.append(
        "This file shows what the M3.2 default corpus produces across "
        "five emotional states. For each state we drive the soul into "
        "shape, then sample N prompts (no LLM call) to show that the "
        "*same trigger* fires *different prompts*, then send one prompt "
        "through DeepSeek V4 Flash so you can see what the model "
        "actually does with the corpus output."
    )
    lines.append("")

    for entry in log:
        lines.append(f"## {entry['label']} (`{entry['trigger_kind']}`)")
        lines.append("")
        snap = entry["snapshot"]
        lines.append("**State:**")
        lines.append("")
        lines.append("```")
        lines.append(f"soul    = {json.dumps(snap['soul'], sort_keys=True)}")
        lines.append(f"mood    = {snap['mood']}")
        lines.append(f"trauma  = {snap['trauma_load']}")
        lines.append(f"nourish = {snap['nourishment_load']}")
        lines.append(f"tags    = {entry['situation_tags']}")
        lines.append("```")
        lines.append("")
        lines.append(
            f"**Sampled faces ({N_SAMPLES_PER_STATE} rolls):**"
        )
        lines.append("")
        for s in entry["samples"]:
            face = s["face_id"] or "<legacy>"
            motif = s["motif"] or ""
            lines.append(f"- `{face}`{(' — ' + motif) if motif else ''}")
        lines.append("")
        mc = entry["model_call"]
        lines.append(f"**Model call** — face: `{mc['face_id']}`")
        lines.append("")
        lines.append("Prompt:")
        lines.append("")
        lines.append("```")
        lines.append(mc["prompt"])
        lines.append("```")
        lines.append("")
        lines.append("Response:")
        lines.append("")
        lines.append("```")
        lines.append(mc["response"])
        lines.append("```")
        lines.append("")

    EVIDENCE_FILE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE_FILE.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
