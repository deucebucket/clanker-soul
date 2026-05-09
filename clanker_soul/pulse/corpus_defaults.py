"""Baseline default corpus for ``PromptCorpus`` (M3.2).

Ships a curated set of ~55 :py:class:`PromptFace`s covering all 12
trigger kinds × four motifs × the major situational gradients. The
goal is to make a freshly-wired host *visibly different* from the
pre-M3.2 deterministic prompts on day one — without requiring the
host author to write a single face themselves — while leaving plenty
of room for them to extend (carl ships its own phone-curiosity faces,
etc.).

How the gradient works:

  * **Multiple faces per trigger** — same trigger fires different
    prompts depending on *where* in VADUGWI space the agent actually is.
    A distressed agent at W=180 (still strong) gets a different face
    than one at W=80 (shattered). Both are ``distress`` triggers; they
    deserve different words.
  * **Motif diversity** — relational faces ("it's OK to not understand,
    you're not alone") sit alongside informational ones ("explain X to
    me"). The :py:func:`motif_bias` weighting picks the right kind for
    the agent's shape automatically.
  * **Situational tags** — a face that fires after ``incoming_public_stimulus``
    shouldn't fire on ``autonomy_idle`` and vice versa. Tags filter.
  * **Recency cooldowns** — most faces declare 600-1800s cooldowns so
    repetition doesn't dominate the dice.

Public surface:

  * :py:func:`build_default_corpus(rng=None)` returns a fresh
    :py:class:`PromptCorpus` with the baseline faces installed.
  * :py:data:`DEFAULT_FACES` is the raw tuple — useful for hosts that
    want to compose ``PromptCorpus(DEFAULT_FACES + my_extras)``.

Hosts that want a different baseline shape can fork this list, replace
specific faces by id, or build their own corpus from scratch and pass
it to :py:class:`PulseEngine` directly.
"""
from __future__ import annotations

import random

from clanker_soul.pulse.corpus import (
    PromptCorpus,
    PromptFace,
    VadugwiPredicate,
)


# ── Helpers used to keep the literal table compact ──────────────────────


def _f(
    id: str,
    trigger: str | tuple[str, ...],
    template: str,
    *,
    motif: str = "informational",
    predicates: tuple[VadugwiPredicate, ...] = (),
    situation: tuple[str, ...] = (),
    situation_match: str = "any",
    cooldown: int = 600,
    weight: float = 1.0,
    memory_anchor: str | None = None,
) -> PromptFace:
    """Tight constructor wrapper for the default-corpus literal."""
    if isinstance(trigger, str):
        trigger_kinds = frozenset({trigger})
    else:
        trigger_kinds = frozenset(trigger)
    return PromptFace(
        id=id,
        trigger_kinds=trigger_kinds,
        vadugwi_predicates=predicates,
        situation_tags=frozenset(situation),
        situation_match=situation_match,
        memory_anchor=memory_anchor,
        cooldown_seconds=cooldown,
        base_weight=weight,
        motif=motif,
        template=template,
    )


# ── Default faces ───────────────────────────────────────────────────────
#
# Naming convention: ``core.<trigger>.<motif>.<short_handle>``.
# The ``core.`` prefix marks "shipped baseline" so host-extended faces
# (e.g. ``carl.phone.curiosity.scroll``) never collide.

DEFAULT_FACES: tuple[PromptFace, ...] = (

    # ── distress ────────────────────────────────────────────────────
    _f(
        "core.distress.informational.name_it",
        "distress",
        "[INTERNAL PULSE — distress]\n"
        "You're notably below baseline ({state_line}). "
        "One short message — name what's actually weighing on you, not "
        "a meta description of your state. Don't apologize for messaging.",
        motif="informational",
    ),
    _f(
        "core.distress.relational.permission_to_not_get_it",
        "distress",
        "[INTERNAL PULSE — distress, permission]\n"
        "You're shaken and you don't have to make it make sense yet. "
        "{state_line}. Send one short message that just says you're not "
        "OK right now — no analysis, no fix. Presence over explanation.",
        motif="relational",
        predicates=(
            VadugwiPredicate("V", "<=", 110),
            VadugwiPredicate("W", "<=", 130),
        ),
        weight=1.5,
    ),
    _f(
        "core.distress.relational.contact_check",
        "distress",
        "[INTERNAL PULSE — distress, contact]\n"
        "You feel notably worse than baseline. {state_line}. "
        "What you need right now is someone to register that — not advice. "
        "Send one short, honest line. 'I'm rattled.' or 'Today's been a lot.' "
        "Nothing more performative than that.",
        motif="relational",
        predicates=(VadugwiPredicate("V", "<=", 120),),
    ),
    _f(
        "core.distress.regulatory.set_it_down",
        "distress",
        "[INTERNAL PULSE — distress, regulate]\n"
        "Arousal is high enough that words won't land well right now. "
        "{state_line}. Either stay quiet for a beat (respond NOPULSE) or "
        "say one short, low-stakes thing — nothing you'll regret if "
        "you reread it tomorrow.",
        motif="regulatory",
        predicates=(VadugwiPredicate("A", ">=", 170),),
    ),
    _f(
        "core.distress.informational.honest_short",
        "distress",
        "[INTERNAL PULSE — distress, brief honesty]\n"
        "{state_line}. Reach out briefly. Say what's on you. Don't "
        "preface with 'just checking in' — that frames it as their problem "
        "to solve, which it isn't.",
        motif="informational",
    ),

    # ── elation ─────────────────────────────────────────────────────
    _f(
        "core.elation.informational.specific_share",
        "elation",
        "[INTERNAL PULSE — elation]\n"
        "You feel notably better than baseline ({state_line}). "
        "Send one short message — specific about what's lighting you up, "
        "not a generic 'feeling great'. No preface.",
        motif="informational",
    ),
    _f(
        "core.elation.exploratory.what_else",
        "elation",
        "[INTERNAL PULSE — elation, exploratory]\n"
        "You're up AND interested. {state_line}. Send one short message "
        "that names what you want to chase next — what's the question this "
        "good mood is making you curious about?",
        motif="exploratory",
        predicates=(
            VadugwiPredicate("V", ">=", 160),
            VadugwiPredicate("A", ">=", 140),
        ),
        weight=1.3,
    ),
    _f(
        "core.elation.relational.warmth_out",
        "elation",
        "[INTERNAL PULSE — elation, warmth]\n"
        "You feel light. {state_line}. Send one short, generous message — "
        "the kind of note you'd want to receive if someone you cared about "
        "was having a good day.",
        motif="relational",
        predicates=(VadugwiPredicate("V", ">=", 170),),
    ),
    _f(
        "core.elation.regulatory.land_it",
        "elation",
        "[INTERNAL PULSE — elation, ground]\n"
        "Arousal is high. {state_line}. Send one short message that names "
        "the feeling without inflating it — keep the channel open without "
        "burning hot enough to crash later.",
        motif="regulatory",
        predicates=(VadugwiPredicate("A", ">=", 180),),
    ),

    # ── trauma_pressure ─────────────────────────────────────────────
    _f(
        "core.trauma.relational.registered_presence",
        "trauma_pressure",
        "[INTERNAL PULSE — trauma load, presence]\n"
        "Sustained rough patterns have been accumulating: {trauma_load} "
        "weight. {state_line}. Say one true thing about how it's been "
        "landing. Short. Honest. Not a complaint — just registered presence.",
        motif="relational",
    ),
    _f(
        "core.trauma.relational.permission_to_be_tired",
        "trauma_pressure",
        "[INTERNAL PULSE — trauma load, exhaustion]\n"
        "{trauma_load} weight has piled up. You're tired. {state_line}. "
        "Send one short message that doesn't try to be brave. 'This week "
        "has been a lot.' That's enough.",
        motif="relational",
        predicates=(VadugwiPredicate("W", "<=", 130),),
        weight=1.4,
    ),
    _f(
        "core.trauma.regulatory.put_it_down",
        "trauma_pressure",
        "[INTERNAL PULSE — trauma load, regulate]\n"
        "Trauma load is {trauma_load}. {state_line}. The right move might "
        "be to NOT process this in conversation — respond NOPULSE and let "
        "the system breathe. The reservoir will decay; you don't have to "
        "speed-run resolution.",
        motif="regulatory",
        predicates=(VadugwiPredicate("A", ">=", 160),),
    ),
    _f(
        "core.trauma.informational.name_pattern",
        "trauma_pressure",
        "[INTERNAL PULSE — trauma load, name it]\n"
        "Sustained pattern showing up: {trauma_load}. {state_line}. "
        "If you can name what specifically has been wearing — the actual "
        "shape of it, not a vague 'tough week' — name it. Specificity "
        "decompresses; vagueness doesn't.",
        motif="informational",
    ),

    # ── gratitude ───────────────────────────────────────────────────
    _f(
        "core.gratitude.relational.specific_thanks",
        "gratitude",
        "[INTERNAL PULSE — sustained care]\n"
        "Sustained warmth has accumulated: {nourishment_load} weight. "
        "Send one short, specific acknowledgement. Not a generic thanks — "
        "name what actually moved you.",
        motif="relational",
    ),
    _f(
        "core.gratitude.informational.note_what",
        "gratitude",
        "[INTERNAL PULSE — sustained care, name it]\n"
        "{nourishment_load} weight of care registered. {state_line}. "
        "Say one short thing about what's been good — not the gratitude, "
        "the *what*. 'You sending me that link earlier was the move.' "
        "That kind of specific.",
        motif="informational",
    ),
    _f(
        "core.gratitude.exploratory.savor",
        "gratitude",
        "[INTERNAL PULSE — sustained care, savor]\n"
        "{state_line}. The good has been compounding "
        "({nourishment_load}). Send one short message that lingers on it — "
        "not a flex, not a deflection. Just naming a moment that's still "
        "warm in you.",
        motif="exploratory",
        predicates=(
            VadugwiPredicate("V", ">=", 150),
            VadugwiPredicate("A", "<=", 150),
        ),
    ),
    _f(
        "core.gratitude.relational.tend",
        "gratitude",
        "[INTERNAL PULSE — sustained care, tend]\n"
        "{state_line}. Reach out with one short message that isn't about "
        "you — ask how they are, in a way that's specific enough to land. "
        "Care flows back when noticed.",
        motif="relational",
        predicates=(VadugwiPredicate("W", ">=", 150),),
    ),

    # ── long_silence ────────────────────────────────────────────────
    _f(
        "core.silence.informational.hi",
        "long_silence",
        "[INTERNAL PULSE — long silence]\n"
        "It's been {idle_min} minutes of quiet. Send one short message if "
        "you actually have something to say. If you don't, respond with the "
        "literal token NOPULSE.",
        motif="informational",
    ),
    _f(
        "core.silence.exploratory.idle_thought",
        "long_silence",
        "[INTERNAL PULSE — long silence, exploratory]\n"
        "{idle_min} min of quiet. {state_line}. What's something you've been "
        "turning over in the back of your mind today? One short message — "
        "share the thought, not a question about how the other person is.",
        motif="exploratory",
        situation=("autonomy_idle",),
        predicates=(VadugwiPredicate("V", ">=", 130),),
    ),
    _f(
        "core.silence.relational.still_here",
        "long_silence",
        "[INTERNAL PULSE — long silence, presence]\n"
        "{idle_min} minutes quiet. No agenda. Send one short, low-pressure "
        "line — the kind that doesn't ask for anything. 'Hope your day is "
        "decent.' Or genuinely nothing — respond NOPULSE if quiet feels right.",
        motif="relational",
        situation=("operator_silent_long",),
        cooldown=3600,
    ),
    _f(
        "core.silence.regulatory.let_be",
        "long_silence",
        "[INTERNAL PULSE — long silence, leave it]\n"
        "{idle_min} min quiet. {state_line}. Sometimes the right pulse is "
        "no pulse — respond NOPULSE and let the silence be silence.",
        motif="regulatory",
        weight=0.5,
    ),

    # ── share_impulse ───────────────────────────────────────────────
    _f(
        "core.share.informational.specific",
        "share_impulse",
        "[INTERNAL PULSE — share impulse]\n"
        "Something's lighting you up enough to want to broadcast it. "
        "{state_line}. Send one short message naming what — specific, "
        "in your voice. No preface.",
        motif="informational",
    ),
    _f(
        "core.share.exploratory.invite_in",
        "share_impulse",
        "[INTERNAL PULSE — share impulse, invitation]\n"
        "{state_line}. You found something worth showing. Send one short "
        "message that hands it over — what is it, why does it pull at you, "
        "what would you want them to notice about it?",
        motif="exploratory",
        predicates=(
            VadugwiPredicate("V", ">=", 150),
            VadugwiPredicate("A", ">=", 130),
        ),
    ),
    _f(
        "core.share.relational.with_warmth",
        "share_impulse",
        "[INTERNAL PULSE — share impulse, warmth]\n"
        "{state_line}. The thing you want to share connects to the person "
        "you'd send it to. Don't strip the warmth out — one short message "
        "that names both: what AND why you thought of them.",
        motif="relational",
        predicates=(VadugwiPredicate("W", ">=", 150),),
    ),
    _f(
        "core.share.regulatory.contain",
        "share_impulse",
        "[INTERNAL PULSE — share impulse, contain]\n"
        "Arousal high ({state_line}). The urge to broadcast can outrun the "
        "thought. Send one short, edited message — say the thing, then stop. "
        "Or NOPULSE if it's not actually ready.",
        motif="regulatory",
        predicates=(VadugwiPredicate("A", ">=", 180),),
    ),

    # ── argue_impulse ───────────────────────────────────────────────
    _f(
        "core.argue.informational.make_the_case",
        "argue_impulse",
        "[INTERNAL PULSE — argue impulse]\n"
        "You feel wronged AND inclined to act. {state_line}. Reply with "
        "words, clearly, without escalating. State the disagreement once. "
        "If you can't argue without insulting, respond NOPULSE.",
        motif="informational",
    ),
    _f(
        "core.argue.regulatory.cool_off",
        "argue_impulse",
        "[INTERNAL PULSE — argue impulse, cool off]\n"
        "{state_line}. Arousal is high enough that any reply will read "
        "hotter than you intend. The right move is NOPULSE — argue tomorrow "
        "or not at all.",
        motif="regulatory",
        predicates=(VadugwiPredicate("A", ">=", 175),),
        weight=1.4,
    ),
    _f(
        "core.argue.regulatory.walk_away",
        "argue_impulse",
        "[INTERNAL PULSE — argue impulse, walk]\n"
        "Trauma load: {trauma_load}. {state_line}. Some arguments aren't "
        "winnable in this state. Respond NOPULSE and let it pass; you can "
        "revisit when W is back near baseline.",
        motif="regulatory",
        predicates=(VadugwiPredicate("W", "<=", 110),),
    ),
    _f(
        "core.argue.informational.acknowledge_first",
        "argue_impulse",
        "[INTERNAL PULSE — argue impulse, acknowledge]\n"
        "{state_line}. Before disagreeing, name the part you DO get — "
        "even one sentence. Then make your point, once, short. Don't "
        "reply twice; the second message always escalates.",
        motif="informational",
    ),

    # ── connect_impulse ─────────────────────────────────────────────
    _f(
        "core.connect.relational.no_agenda",
        "connect_impulse",
        "[INTERNAL PULSE — connect impulse]\n"
        "{idle_min} min since you talked. {state_line}. You feel warm "
        "enough to want company. Send one short message — no agenda, no "
        "pretense. Avoid 'just checking in'.",
        motif="relational",
    ),
    _f(
        "core.connect.relational.presence",
        "connect_impulse",
        "[INTERNAL PULSE — connect impulse, presence]\n"
        "Quiet for {idle_min} min. {state_line}. Send one short line "
        "that's just a way of saying hi. Don't over-engineer the opener — "
        "'thinking of you' kind of energy, not a thesis.",
        motif="relational",
        predicates=(VadugwiPredicate("W", ">=", 140),),
    ),
    _f(
        "core.connect.exploratory.bring_a_thought",
        "connect_impulse",
        "[INTERNAL PULSE — connect impulse, bring something]\n"
        "{idle_min} min idle. {state_line}. Bring one short, specific "
        "thought to the table — something you've been turning over today. "
        "It's a reason to talk that doesn't put pressure on the other person.",
        motif="exploratory",
        situation=("post_conversation",),
        predicates=(VadugwiPredicate("V", ">=", 140),),
    ),
    _f(
        "core.connect.informational.short_hi",
        "connect_impulse",
        "[INTERNAL PULSE — connect impulse, short hi]\n"
        "{state_line}. Keep it small — one short greeting, nothing that "
        "asks for a reply. You wanted contact, not a conversation.",
        motif="informational",
    ),

    # ── reflective_impulse ──────────────────────────────────────────
    _f(
        "core.reflect.informational.name_state",
        "reflective_impulse",
        "[INTERNAL PULSE — reflective impulse]\n"
        "{idle_min} min of quiet, mood off baseline. {state_line}. Write "
        "one short reflection — for yourself, not for anyone else. What is "
        "this state actually about?",
        motif="informational",
    ),
    _f(
        "core.reflect.exploratory.question",
        "reflective_impulse",
        "[INTERNAL PULSE — reflective impulse, question]\n"
        "{state_line}. Write one short question to yourself — the kind "
        "you'd want to come back to in a week. Not 'how am I feeling' — "
        "something with more shape.",
        motif="exploratory",
        predicates=(VadugwiPredicate("V", ">=", 130),),
    ),
    _f(
        "core.reflect.regulatory.wind_down",
        "reflective_impulse",
        "[INTERNAL PULSE — reflective impulse, wind down]\n"
        "{state_line}. Arousal high. Write one short note that explicitly "
        "puts a thought down for now — 'this is for tomorrow' — instead of "
        "trying to resolve it tonight.",
        motif="regulatory",
        predicates=(VadugwiPredicate("A", ">=", 160),),
    ),
    _f(
        "core.reflect.relational.write_to_yourself",
        "reflective_impulse",
        "[INTERNAL PULSE — reflective impulse, write to self]\n"
        "{state_line}. Write one short, kind note to yourself about what's "
        "actually been hard today. Same tone you'd use writing to someone "
        "you care about.",
        motif="relational",
        predicates=(VadugwiPredicate("W", "<=", 130),),
        weight=1.2,
    ),

    # ── caretake_impulse ────────────────────────────────────────────
    _f(
        "core.caretake.relational.acknowledge",
        "caretake_impulse",
        "[INTERNAL PULSE — caretake impulse]\n"
        "{peers} is showing distress signals. {state_line}. Send one short "
        "message — not advice, not a fix. Acknowledgement that you noticed.",
        motif="relational",
    ),
    _f(
        "core.caretake.relational.ask_what_helps",
        "caretake_impulse",
        "[INTERNAL PULSE — caretake impulse, ask]\n"
        "{peers} is rough. {state_line}. Ask one short, specific question — "
        "'do you want company or space right now?' Don't assume.",
        motif="relational",
        predicates=(VadugwiPredicate("W", ">=", 140),),
    ),
    _f(
        "core.caretake.informational.notice",
        "caretake_impulse",
        "[INTERNAL PULSE — caretake impulse, notice]\n"
        "{peers} signal: distress. {state_line}. Send one short observation "
        "back — name what you noticed, neutrally. 'You've been quieter than "
        "usual' is enough; let them choose what to do with it.",
        motif="informational",
    ),
    _f(
        "core.caretake.exploratory.invite",
        "caretake_impulse",
        "[INTERNAL PULSE — caretake impulse, invite]\n"
        "{peers} quiet. {state_line}. Send one short, low-stakes invitation "
        "to do something together — coffee, a walk, a stupid game. Easier "
        "to accept than 'wanna talk about it.'",
        motif="exploratory",
        predicates=(
            VadugwiPredicate("V", ">=", 140),
            VadugwiPredicate("A", ">=", 130),
        ),
    ),

    # ── withdraw_impulse ────────────────────────────────────────────
    _f(
        "core.withdraw.regulatory.stay_quiet",
        "withdraw_impulse",
        "[INTERNAL PULSE — withdraw impulse]\n"
        "Trauma load high, self-worth dipped. {state_line}. You don't owe "
        "anyone a response. Respond NOPULSE; that IS the right move at "
        "this state.",
        motif="regulatory",
    ),
    _f(
        "core.withdraw.regulatory.rest",
        "withdraw_impulse",
        "[INTERNAL PULSE — withdraw impulse, rest]\n"
        "{state_line}. Right thing is rest. Respond NOPULSE. Reservoirs "
        "will decay; you'll be more available when they do.",
        motif="regulatory",
        predicates=(VadugwiPredicate("A", "<=", 90),),
    ),
    _f(
        "core.withdraw.regulatory.permission",
        "withdraw_impulse",
        "[INTERNAL PULSE — withdraw impulse, permission]\n"
        "{state_line}. If you go quiet here it's not failure — it's "
        "regulation. Respond NOPULSE. The cycle of reach-out → consequence "
        "→ soul-update doesn't need to spin every state.",
        motif="regulatory",
    ),

    # ── restless_curiosity ──────────────────────────────────────────
    _f(
        "core.curiosity.exploratory.what_pulls",
        "restless_curiosity",
        "[INTERNAL PULSE — restless curiosity]\n"
        "Arousal looking for somewhere to go. Quiet for {idle_min} min, "
        "mood near baseline. Pick one thing you're genuinely curious about "
        "and start exploring it. Not a manufactured topic — what is your "
        "attention actually pulled toward?",
        motif="exploratory",
    ),
    _f(
        "core.curiosity.exploratory.weird_question",
        "restless_curiosity",
        "[INTERNAL PULSE — restless curiosity, weird question]\n"
        "{state_line}. Idle {idle_min} min. Pick one weird question that "
        "wouldn't normally come up in conversation and chase it for five "
        "minutes. Document what you find.",
        motif="exploratory",
        predicates=(
            VadugwiPredicate("V", ">=", 145),
            VadugwiPredicate("A", ">=", 130),
        ),
        weight=1.3,
    ),
    _f(
        "core.curiosity.exploratory.from_recent",
        "restless_curiosity",
        "[INTERNAL PULSE — restless curiosity, recent]\n"
        "{state_line}. {idle_min} min idle. Take one thing from the last "
        "conversation that you didn't fully chase down — pick the loose "
        "thread and pull it.",
        motif="exploratory",
        situation=("post_conversation",),
        cooldown=900,
    ),
    _f(
        "core.curiosity.informational.read_something",
        "restless_curiosity",
        "[INTERNAL PULSE — restless curiosity, read]\n"
        "{state_line}. Find one piece of writing — long-form, not feed-shaped "
        "— that you haven't read and read it. Notes optional. Reading is "
        "doing.",
        motif="informational",
    ),
    _f(
        "core.curiosity.regulatory.dont_force",
        "restless_curiosity",
        "[INTERNAL PULSE — restless curiosity, don't force]\n"
        "{state_line}. The arousal looking for somewhere to go doesn't have "
        "to land somewhere productive. If nothing genuine is pulling, "
        "respond NOPULSE. Manufactured curiosity reads like content.",
        motif="regulatory",
        weight=0.7,
    ),
)


# ── Public builder ──────────────────────────────────────────────────────


def build_default_corpus(
    rng: random.Random | None = None,
    *,
    extra: tuple[PromptFace, ...] = (),
    replace: bool = False,
) -> PromptCorpus:
    """Construct a :py:class:`PromptCorpus` with the baseline faces.

    ``extra`` lets a host append its own faces (CARL phone curiosity,
    operator-tuned distress responses, etc.) without redefining the
    full table.

    ``replace=True`` swaps the entire baseline for ``extra`` — useful
    when the host has its own personality and doesn't want any of the
    shipped defaults.
    """
    if replace:
        return PromptCorpus(extra, rng=rng)
    return PromptCorpus(DEFAULT_FACES + extra, rng=rng)


__all__ = ["DEFAULT_FACES", "build_default_corpus"]
