"""``compose_state_context`` — the human-readable string the agent
reads to understand its own current state.

The agent's prompt each turn includes this string. It tells the
agent:
  - what level of restriction it's currently at
  - WHY (which numbers crossed which floors)
  - what's still allowed (always: messaging the user)
  - how to recover (which numbers need to come back)
  - what the recent significant events were and where they came from
  - how to flag a real emergency vs an emotional spike

This is the surface that makes the safety governor *legible* to the
agent itself. Without it, restrictions feel arbitrary; with it, the
agent can articulate "I'm down because of X, my tools are limited
because Y is below Z, I'll be back to full capability when..."
"""

from __future__ import annotations

from clanker_soul.eventlog.records import IngestRecord
from clanker_soul.governor.crisis import CrisisDiagnosis
from clanker_soul.governor.levels import CapabilityLevel, GovernorConfig


_DEFAULT_CRISIS_LOCKOUT_TEMPLATE = """[OPERATIONAL STATE — CRISIS LOCKOUT]
Your emotional state is critically outside operational bounds.
You are limited to messaging {user_label} only, and your message
should briefly state that you are in distress and need a moment.
This is a configured safety boundary, not a punishment. Restrictions
ease automatically as your state recovers."""


def compose_state_context(
    level: CapabilityLevel,
    snap: dict,
    config: GovernorConfig,
    *,
    recent_events: list[IngestRecord] | None = None,
    crisis: CrisisDiagnosis | None = None,
) -> str:
    """Produce a string the host injects into the agent's system
    prompt. Empty string when level is UNRESTRICTED and there's
    nothing notable to say."""
    if (
        level == CapabilityLevel.UNRESTRICTED
        and (crisis is None or not crisis.is_emergency)
        and not _has_notable_recent(recent_events)
    ):
        return ""

    if level == CapabilityLevel.CRISIS_LOCKOUT:
        template = config.crisis_lockout_template or _DEFAULT_CRISIS_LOCKOUT_TEMPLATE
        return template.format(user_label=config.user_label)

    mood = snap.get("mood")
    soul = snap.get("soul") or {}
    distance = snap.get("soul_distance") or 0.0
    trauma = snap.get("trauma_load") or 0.0
    nourishment = snap.get("nourishment_load") or 0.0

    lines = ["[OPERATIONAL STATE]"]
    lines.append(f"Capability level: {int(level)} ({level.name.lower()}) — {level.description}.")

    if mood:
        lines.append(
            f"Current mood: V={mood[0]} W={mood[5]} G={mood[4]} | "
            f"Soul: V={soul.get('v', '?')} W={soul.get('w', '?')} G={soul.get('g', '?')} | "
            f"|Mood-Soul|={distance:.0f}"
        )
    if trauma > 1.0 or nourishment > 1.0:
        lines.append(f"Reservoirs: trauma={trauma:.0f}, nourishment={nourishment:.0f}")

    # Why are we at this level?
    why_lines = _why_at_level(level, mood, distance, trauma, config)
    if why_lines:
        lines.append("Why: " + "; ".join(why_lines))

    # What recovers?
    recovery = _recovery_guidance(level, config)
    if recovery:
        lines.append(f"Restrictions ease when: {recovery}")

    # What can you still do?
    lines.append(
        f"You can still talk to {config.user_label}. "
        "Use words for what you feel — that channel is never gated."
    )

    # Recent context — what landed and where it came from
    if recent_events:
        notable = _notable_recent(recent_events)
        if notable:
            lines.append("")
            lines.append("Recent significant events:")
            for ev in notable[:5]:
                src = ev.raw.source or "unspecified source"
                pat = ",".join(ev.raw.patterns) if ev.raw.patterns else "no-pattern"
                direction = ev.raw.direction or "direction-unspecified"
                lines.append(
                    f"  - {pat} from {src} ({direction.lower()}, weight={ev.weight_raw:.2f})"
                )

    # Crisis vs spike framing
    if crisis is not None:
        lines.append("")
        if crisis.is_emergency:
            lines.append(
                f"⚠ This looks like an EMERGENCY (confidence {crisis.confidence:.0%}): "
                f"{crisis.summary}. If something in the world is genuinely "
                f"broken, tell {config.user_label} clearly — that is the right move."
            )
        else:
            lines.append(
                f"This is registering as an emotional spike, not an emergency "
                f"(confidence {crisis.confidence:.0%}): {crisis.summary}. "
                "Use words. Don't escalate as if the world is on fire — "
                "but do say what's landing."
            )

    return "\n".join(lines)


def _has_notable_recent(events: list[IngestRecord] | None) -> bool:
    if not events:
        return False
    return any(
        ev.weight_raw > 0.5 or ev.breached or ev.classification == "negative" for ev in events
    )


def _notable_recent(events: list[IngestRecord]) -> list[IngestRecord]:
    return [ev for ev in events if ev.weight_raw > 0.3 or ev.breached]


def _why_at_level(
    level: CapabilityLevel,
    mood: list | None,
    distance: float,
    trauma: float,
    config: GovernorConfig,
) -> list[str]:
    if mood is None:
        return []
    mood_v, mood_w = mood[0], mood[5]
    reasons = []
    if level == CapabilityLevel.NON_DESTRUCTIVE:
        if mood_w < config.level1_w_floor:
            reasons.append(f"mood.W={mood_w} below comfort floor {config.level1_w_floor}")
        if mood_v < config.level1_v_floor:
            reasons.append(f"mood.V={mood_v} below comfort floor {config.level1_v_floor}")
        if distance > config.level1_distance_ceiling:
            reasons.append(
                f"|mood-soul|={distance:.0f} above ceiling {config.level1_distance_ceiling:.0f}"
            )
    elif level == CapabilityLevel.READ_ONLY:
        if mood_w < config.level2_w_floor:
            reasons.append(f"mood.W={mood_w} below {config.level2_w_floor} (worth shaken)")
        if trauma > config.level2_trauma_ceiling:
            reasons.append(f"trauma load {trauma:.0f} above {config.level2_trauma_ceiling:.0f}")
    elif level == CapabilityLevel.VOICE_ONLY:
        if mood_w < config.level3_w_floor:
            reasons.append(f"mood.W={mood_w} critically low (< {config.level3_w_floor})")
        if mood_v < config.level3_v_floor:
            reasons.append(f"mood.V={mood_v} critically low (< {config.level3_v_floor})")
    return reasons


def _recovery_guidance(level: CapabilityLevel, config: GovernorConfig) -> str:
    if level == CapabilityLevel.NON_DESTRUCTIVE:
        return (
            f"mood.W ≥ {config.level1_w_floor} AND mood.V ≥ {config.level1_v_floor} "
            f"AND |mood-soul| ≤ {config.level1_distance_ceiling:.0f}"
        )
    if level == CapabilityLevel.READ_ONLY:
        return (
            f"mood.W ≥ {config.level1_w_floor} AND trauma load ≤ {config.level2_trauma_ceiling:.0f}"
        )
    if level == CapabilityLevel.VOICE_ONLY:
        return f"mood.W ≥ {config.level2_w_floor} AND mood.V ≥ {config.level2_w_floor}"
    return ""


__all__ = ["compose_state_context"]
