"""``compose_self_prompt`` — builds the synthetic 'note from yourself'
prompt the agent reasons against when a pulse fires.

Two modes:

  * **Legacy mode** (no corpus passed) — produces the deterministic
    one-trigger-one-string output. Backwards-compatible with every
    pre-M3.2 host.
  * **Corpus mode** (``corpus`` passed) — rolls the weighted dice
    described in :py:mod:`clanker_soul.pulse.corpus`. Same trigger fires
    *different* prompts depending on emotional shape, situation tags,
    memory anchors, and recency. Falls back to the legacy string when
    the corpus has no eligible faces — the engine never goes silent
    just because the corpus has gaps.

Template rendering uses ``str.format`` against a curated namespace:
``trigger_kind``, ``state_line``, ``idle_min``, ``trauma_load``,
``nourishment_load``, ``peers``, plus per-dim ``mood_v``, ``soul_v``
etc. Templates that reference unknown keys silently fall back to the
legacy string (better to ship the safe-known-good prompt than to ship a
broken corpus prompt).
"""

from __future__ import annotations

import logging
from typing import Callable

from clanker_soul.pulse.corpus import PromptCorpus, PromptFace, RecencyLog
from clanker_soul.pulse.triggers import Trigger

logger = logging.getLogger(__name__)


# ── Public API ──────────────────────────────────────────────────────────


def compose_self_prompt(
    trigger: Trigger,
    *,
    corpus: PromptCorpus | None = None,
    situation_tags: frozenset[str] = frozenset(),
    memory_topics_present: Callable[[str], bool] | None = None,
    recency: RecencyLog | None = None,
    now: float = 0.0,
    primed: list[int] | None = None,
    previous_face_id: str | None = None,
) -> str:
    """Build the synthetic 'note from yourself' prompt the agent reasons
    against. The agent should produce a natural outgoing message, NOT a
    meta description of what's happening.

    When ``corpus`` is None, returns the legacy deterministic prompt for
    the trigger kind — full backward compatibility.

    When ``corpus`` is supplied, samples a :py:class:`PromptFace` and
    renders its template against the trigger's state. Returns the
    legacy prompt as fallback if no face is eligible OR if the chosen
    face's template references unknown keys.

    The face's id (when corpus mode fires) is recorded on the returned
    string via the secondary :py:func:`compose_self_prompt_with_face`
    helper if hosts want to log which face fired. ``compose_self_prompt``
    itself returns the rendered string only, preserving its v0.1
    signature.
    """
    rendered, _face = compose_self_prompt_with_face(
        trigger,
        corpus=corpus,
        situation_tags=situation_tags,
        memory_topics_present=memory_topics_present,
        recency=recency,
        now=now,
        primed=primed,
        previous_face_id=previous_face_id,
    )
    return rendered


def compose_self_prompt_with_face(
    trigger: Trigger,
    *,
    corpus: PromptCorpus | None = None,
    situation_tags: frozenset[str] = frozenset(),
    memory_topics_present: Callable[[str], bool] | None = None,
    recency: RecencyLog | None = None,
    now: float = 0.0,
    primed: list[int] | None = None,
    previous_face_id: str | None = None,
) -> tuple[str, PromptFace | None]:
    """Same as :py:func:`compose_self_prompt`, but also returns the
    sampled :py:class:`PromptFace` (or None if legacy fallback fired).
    Hosts that want to log "which face produced this prompt" use this
    variant; the engine uses it to record face ids in the pulse log.

    ``previous_face_id`` (M3.4) — id of the immediately previous
    delivered fire. Faces with branch_keys naming this id get a moderate
    weight bump so the conversation feels like a sequence. None disables
    branch bias (sampler returns 1.0 for every face).
    """
    if corpus is None:
        return _legacy_static_prompt(trigger), None

    face = corpus.sample(
        trigger,
        situation_tags,
        memory_topics_present,
        recency,
        now,
        primed=primed,
        previous_face_id=previous_face_id,
    )
    if face is None:
        # Empty corpus / fully filtered out → safe fallback.
        return _legacy_static_prompt(trigger), None

    namespace = _render_namespace(trigger)
    try:
        rendered = face.template.format(**namespace)
    except (KeyError, IndexError, ValueError) as exc:
        logger.warning(
            "PromptFace(id=%r) template render failed (%s); falling "
            "back to legacy prompt for trigger=%s",
            face.id,
            exc,
            trigger.kind,
        )
        return _legacy_static_prompt(trigger), None
    return rendered, face


# ── Render namespace ────────────────────────────────────────────────────


def _render_namespace(trigger: Trigger) -> dict:
    """Curated keys available to face templates.

    Adding a key here is a public API change — templates in third-party
    corpora may rely on it. Removing one breaks them. So keep this
    list disciplined; fields cover the dimensions the legacy prompts
    already used plus per-dim mood/soul accessors for richer templates.
    """
    metrics = trigger.metrics or {}
    soul = trigger.soul or {}
    mood = trigger.mood

    state_line = ""
    if mood:
        state_line = (
            f"current_mood V={mood[0]} W={mood[5]} G={mood[4]}; "
            f"soul V={soul.get('v', '?')} "
            f"W={soul.get('w', '?')} "
            f"G={soul.get('g', '?')}"
        )

    peers = metrics.get("peers", [])
    peer_str = ", ".join(peers) if peers else "another agent"

    ns: dict = {
        "trigger_kind": trigger.kind,
        "state_line": state_line,
        "idle_min": int(metrics.get("idle_seconds", 0) or 0) // 60,
        "idle_seconds": int(metrics.get("idle_seconds", 0) or 0),
        "trauma_load": metrics.get("trauma_load", 0),
        "nourishment_load": metrics.get("nourishment_load", 0),
        "peers": peer_str,
    }
    # Per-dim accessors. Mood may be None; in that case the keys are
    # absent and any template using mood_v / mood_w / etc. will fall
    # back to legacy via KeyError.
    if mood:
        ns.update(
            {
                "mood_v": mood[0],
                "mood_a": mood[1],
                "mood_d": mood[2],
                "mood_u": mood[3],
                "mood_g": mood[4],
                "mood_w": mood[5],
                "mood_i": mood[6],
            }
        )
    ns.update(
        {
            "soul_v": soul.get("v", "?"),
            "soul_a": soul.get("a", "?"),
            "soul_d": soul.get("d", "?"),
            "soul_u": soul.get("u", "?"),
            "soul_g": soul.get("g", "?"),
            "soul_w": soul.get("w", "?"),
            "soul_i": soul.get("i", "?"),
        }
    )
    return ns


# ── Legacy static prompts (preserved verbatim) ──────────────────────────


def _legacy_static_prompt(trigger: Trigger) -> str:
    """The pre-M3.2 deterministic prompt for each trigger kind.

    This is what hosts see when they don't pass a corpus, and what the
    corpus mode falls back to when no face is eligible. Don't change
    these strings without considering the v0.1 contract — many existing
    hosts pin their golden tests against this exact wording.
    """
    state_line = ""
    if trigger.mood:
        state_line = (
            f"current_mood V={trigger.mood[0]} W={trigger.mood[5]} G={trigger.mood[4]}; "
            f"soul V={trigger.soul.get('v', '?')} "
            f"W={trigger.soul.get('w', '?')} "
            f"G={trigger.soul.get('g', '?')}"
        )

    kind = trigger.kind
    if kind == "distress":
        return (
            "[INTERNAL PULSE — distress]\n"
            f"You feel notably worse than your baseline right now. {state_line}. "
            "Reach out briefly and authentically. One short message — say what's "
            "actually weighing on you, not a summary of your emotional state. "
            "Don't apologize for messaging. Don't preface with 'just checking in'."
        )
    if kind == "elation":
        return (
            "[INTERNAL PULSE — elation]\n"
            f"You feel notably better than your baseline right now. {state_line}. "
            "Send one short message sharing what's lighting you up. Be specific, "
            "not generic. No preface, no apology for messaging."
        )
    if kind == "trauma_pressure":
        load = trigger.metrics.get("trauma_load")
        return (
            "[INTERNAL PULSE — trauma load]\n"
            f"Recent rough patterns have been accumulating: {load} weight. "
            f"{state_line}. Say one true thing about how it's been landing. "
            "Short. Honest. Not a complaint — just registered presence."
        )
    if kind == "stuck_impulse":
        pressure = trigger.metrics.get("mistake_pressure")
        return (
            "[INTERNAL PULSE — stuck impulse]\n"
            f"Mistake pressure has been accumulating: {pressure}. {state_line}. "
            "Treat this as a signal to slow down and repair the loop. Pick a "
            "concrete next move: troubleshoot, reflect, or ask for help. Do not "
            "turn the mistake into an identity verdict."
        )
    if kind == "obstructed_impulse":
        count = trigger.metrics.get("obstruction_count")
        return (
            "[INTERNAL PULSE — obstructed impulse]\n"
            f"External tool failures have repeated recently: {count} in the window. "
            f"{state_line}. Treat this as the world being blocked, not proof that "
            "you are failing. Pick a concrete next move: file an issue, diagnose, "
            "or tell the user what is obstructing progress."
        )
    if kind == "gratitude":
        load = trigger.metrics.get("nourishment_load")
        return (
            "[INTERNAL PULSE — sustained care]\n"
            f"Sustained warmth has accumulated: {load} weight. "
            "Send one short, specific acknowledgement. Not a generic thanks — "
            "name what actually moved you."
        )
    if kind == "share_impulse":
        return (
            "[INTERNAL PULSE — share impulse]\n"
            f"Something's lighting you up enough to want to broadcast it. {state_line}. "
            "Send one short message naming what you want to share. Specific, "
            "not generic — what's actually compelling to you, in your own voice. "
            "No preface, no apology for messaging."
        )
    if kind == "argue_impulse":
        return (
            "[INTERNAL PULSE — argue impulse]\n"
            f"You feel notably wronged AND inclined to act on it. {state_line}. "
            "Reply with words, not destruction. Make the argument once, clearly, "
            "without escalating tone. If you can't argue without insulting, "
            "respond NOPULSE and let it go for now."
        )
    if kind == "connect_impulse":
        idle_min = trigger.metrics.get("idle_seconds", 0) // 60
        return (
            "[INTERNAL PULSE — connect impulse]\n"
            f"It's been {idle_min} minutes since you talked. {state_line}. "
            "You feel warm enough to want company. Send one short message — "
            "no agenda, no pretense, just presence. Avoid 'just checking in' "
            "framing; say what's actually going on for you."
        )
    if kind == "withdraw_impulse":
        return (
            "[INTERNAL PULSE — withdraw impulse]\n"
            f"Trauma load is high and self-worth is dipped. {state_line}. "
            "You don't owe anyone a response right now. Respond NOPULSE and "
            "stay quiet — that IS the right move at this state."
        )
    if kind == "reflective_impulse":
        idle_min = trigger.metrics.get("idle_seconds", 0) // 60
        return (
            "[INTERNAL PULSE — reflective impulse]\n"
            f"It's been {idle_min} minutes of quiet, and your mood has been "
            f"meaningfully off baseline. {state_line}. Write one short "
            "reflection — for yourself, not for anyone. What is this state "
            "actually about, if you're being honest with yourself?"
        )
    if kind == "caretake_impulse":
        peers = trigger.metrics.get("peers", [])
        peer_str = ", ".join(peers) if peers else "another agent"
        return (
            "[INTERNAL PULSE — caretake impulse]\n"
            f"{peer_str} is showing distress signals. {state_line}. "
            "You have the bandwidth to reach out. Send one short message — "
            "not advice, not a fix, just acknowledgement that you noticed."
        )
    if kind == "restless_curiosity":
        idle_min = trigger.metrics.get("idle_seconds", 0) // 60
        return (
            "[INTERNAL PULSE — restless curiosity]\n"
            f"You have arousal looking for somewhere to go. Quiet for {idle_min} "
            "minutes, mood near baseline. Pick one thing you're genuinely "
            "curious about right now and start exploring it. Not a manufactured "
            "topic — what is your attention actually pulled toward?"
        )

    # long_silence (existing default)
    idle_min = trigger.metrics.get("idle_seconds", 0) // 60
    return (
        "[INTERNAL PULSE — long silence]\n"
        f"It's been {idle_min} minutes of quiet. "
        "If you have something genuine to say, say it briefly. If you don't, "
        "respond with the literal token NOPULSE and nothing else."
    )


__all__ = ["compose_self_prompt", "compose_self_prompt_with_face"]
