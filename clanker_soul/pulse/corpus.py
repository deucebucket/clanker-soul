"""``PromptCorpus`` — a weighted dice over self-prompt candidates.

Replaces the static ``compose_self_prompt(trigger)`` mapping (one trigger
kind → one fixed string) with a sampler over a pool of *prompt faces*.
Each face declares the conditions under which it's eligible (trigger
kinds, VADUGWI predicates, situational tags, memory anchors, recency
cooldown) and the engine rolls a weighted die over the eligible set when
a trigger fires.

Net effect: same trigger fires *different* prompts depending on the
agent's emotional shape AND what just happened in the world AND what's
in memory AND what the agent already said recently.

This module is M3.1 — pure in-memory data model + sampler math. No
persistence, no engine wiring, no default corpus. Those land in
follow-up slices:

  * **M3.2** — wire ``compose_self_prompt`` to the corpus, ship a
    baseline default corpus, thread situation tags through the engine.
  * **M3.3** — SQLite persistence, ``SoulPlugin`` extension API,
    cross-restart recency.
  * **M3.4** — branch trees, memory anchors via ``PulseHost``.

Design choices locked here:

  * **Predicates AND-combine.** Multiple ``VadugwiPredicate`` entries on
    one face must all be satisfied. Disjunction = author multiple faces.
  * **No-eligible-faces returns None.** ``corpus.sample()`` returning
    ``None`` is the signal to the caller (engine / ``compose_self_prompt``)
    to fall back to whatever default behavior it wants. The corpus
    itself never invents a prompt.
  * **Linear VADUGWI affinity.** A face requiring ``W >= 150`` weights
    higher when ``W = 200`` than when ``W = 150``; the multiplier ramps
    from 1.0 at the predicate boundary to 2.0 at the dim extreme. Simple,
    audit-friendly, tunable in M3.2 if needed.
  * **Strict host situation tags.** The engine does not derive
    ``situation_tags`` from ``Trigger.metrics``; the host passes them in.
    A ``default_tags_from_metrics()`` helper for hosts that want
    something quick lives alongside but is opt-in.
  * **In-memory recency only for M3.1.** ``RecencyLog`` is a dict
    persisted by the caller (engine state) until M3.3 introduces SQLite.
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import Callable, Iterable

from clanker_soul.pulse.triggers import Trigger

logger = logging.getLogger(__name__)


# ── Constants ───────────────────────────────────────────────────────────

_VALID_DIMS: frozenset[str] = frozenset({"V", "A", "D", "U", "G", "W", "I"})
_VALID_OPS: frozenset[str] = frozenset({"<=", ">=", "<", ">", "=="})
_VALID_LAYERS: frozenset[str] = frozenset({"mood", "soul", "primed"})
_VALID_SITUATION_MATCH: frozenset[str] = frozenset({"any", "all"})
_VALID_MOTIFS: frozenset[str] = frozenset(
    {"informational", "relational", "exploratory", "regulatory"}
)

# Mapping from VADUGWI dim letter to mood-list index. Mood is stored
# as a 7-int list in V/A/D/U/G/W/I order; soul is a dict with
# lowercase keys.
_DIM_TO_MOOD_INDEX: dict[str, int] = {
    "V": 0, "A": 1, "D": 2, "U": 3, "G": 4, "W": 5, "I": 6,
}


# ── Data model ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class VadugwiPredicate:
    """A single threshold constraint on one VADUGWI dim of one state layer.

    Multiple predicates on a face are AND-combined. Use multiple faces
    if you want OR semantics — the corpus is the place where alternatives
    live.

    ``layer`` selects which state vector to evaluate against:

      * ``"mood"`` — the working state from ``Trigger.mood``. Most faces
        use this; mood is what the agent is *feeling right now*.
      * ``"soul"`` — the slow baseline from ``Trigger.soul``. Use when
        you want a face that fires only for agents with a particular
        personality (e.g. low-W brittle characters).
      * ``"primed"`` — the mood-primed state used by physics ingestion.
        Falls back to ``"mood"`` when no primed vector is supplied;
        the M3.2 engine wiring will optionally pass primed through.
    """

    dim: str
    op: str
    value: int
    layer: str = "mood"

    def __post_init__(self) -> None:
        if self.dim not in _VALID_DIMS:
            raise ValueError(
                f"VadugwiPredicate.dim={self.dim!r} must be one of "
                f"{sorted(_VALID_DIMS)}"
            )
        if self.op not in _VALID_OPS:
            raise ValueError(
                f"VadugwiPredicate.op={self.op!r} must be one of "
                f"{sorted(_VALID_OPS)}"
            )
        if self.layer not in _VALID_LAYERS:
            raise ValueError(
                f"VadugwiPredicate.layer={self.layer!r} must be one of "
                f"{sorted(_VALID_LAYERS)}"
            )
        if not 0 <= self.value <= 255:
            raise ValueError(
                f"VadugwiPredicate.value={self.value} must be in [0, 255]"
            )

    def evaluate(
        self,
        mood: list[int] | None,
        soul: dict,
        primed: list[int] | None = None,
    ) -> bool:
        """Return True iff this predicate is satisfied by the given state."""
        actual = self._extract(mood, soul, primed)
        if actual is None:
            return False
        if self.op == "<=":
            return actual <= self.value
        if self.op == ">=":
            return actual >= self.value
        if self.op == "<":
            return actual < self.value
        if self.op == ">":
            return actual > self.value
        # ==
        return actual == self.value

    def margin(
        self,
        mood: list[int] | None,
        soul: dict,
        primed: list[int] | None = None,
    ) -> int:
        """How far past the threshold the actual value sits.

        Used by ``vadugwi_affinity`` to give faces deeper into their
        predicate region a higher weight. Returns 0 when the predicate
        is barely satisfied (right at the boundary), grows to 255 - value
        for ``>=`` / ``>``, and to ``value`` for ``<=`` / ``<``.

        Returns 0 when unsatisfied (caller is expected to filter first).
        """
        actual = self._extract(mood, soul, primed)
        if actual is None or not self.evaluate(mood, soul, primed):
            return 0
        if self.op in (">=", ">"):
            return max(0, actual - self.value)
        if self.op in ("<=", "<"):
            return max(0, self.value - actual)
        # == has no margin — exact match is exact
        return 0

    def _extract(
        self,
        mood: list[int] | None,
        soul: dict,
        primed: list[int] | None = None,
    ) -> int | None:
        """Pull the dim's actual value from the requested layer.

        Returns ``None`` if the requested layer is unavailable (e.g.
        ``mood`` predicate but mood not yet established). Caller treats
        None as predicate-unsatisfied.
        """
        if self.layer == "mood":
            if mood is None:
                return None
            return mood[_DIM_TO_MOOD_INDEX[self.dim]]
        if self.layer == "primed":
            # Fall back to mood when no primed vector — M3.1 doesn't
            # require hosts to pass primed; the wiring slice (M3.2) can
            # opt into it.
            target = primed if primed is not None else mood
            if target is None:
                return None
            return target[_DIM_TO_MOOD_INDEX[self.dim]]
        # soul
        return soul.get(self.dim.lower())


@dataclass(frozen=True)
class PromptFace:
    """A single candidate self-prompt the corpus may roll up.

    ``id`` must be globally unique within the corpus — used for recency
    tracking and pulse-log audit. Stable identifiers like
    ``"core.distress.relational.checkin"`` are preferred over UUIDs so
    operators can grep logs.

    ``trigger_kinds`` — set of trigger kinds this face is eligible for.
    Multi-tag is allowed (a comfort face might fire for both ``distress``
    and ``trauma_pressure``).

    ``vadugwi_predicates`` — AND-combined predicates on the agent's
    state. An empty tuple means "always eligible by state" (gate by
    situation/trigger only).

    ``situation_tags`` — set of host-supplied tags this face needs. The
    interpretation depends on ``situation_match``:

      * ``"any"`` (default) — face is eligible if any of its required
        tags appear in the host's current situation set, OR if its
        ``situation_tags`` is empty (universal face).
      * ``"all"`` — every required tag must be present.

    ``memory_anchor`` — a topic key the host can answer "yes I have
    memories about this." Faces with an anchor are filtered out unless
    the anchor returns True. M3.1 stores the field but the sampler call
    site decides the callback shape — defaulting to "anchor present" if
    the caller supplies no callback. (M3.4 wires this end-to-end via
    ``PulseHost``.)

    ``cooldown_seconds`` — the face cannot fire again until N seconds
    after its last fire. 0 = no cooldown.

    ``base_weight`` — pre-state probability mass. Faces with higher
    base weight are more likely to fire among an eligible pool of
    equally-affined faces.

    ``motif`` — broad mode of the prompt. Used by ``motif_bias`` to
    lift the right kind of face into prominence based on (state,
    situation). See module-level docs for the four modes.

    ``template`` — the prompt string. M3.1 stores it as-is; M3.2 wires
    in ``str.format``-based rendering with a curated namespace.

    ``branch_keys`` — optional parent-face hints. Faces that name a
    parent get a weight bump when that parent was the immediately
    previous fire. M3.1 stores the field; M3.4 wires the lookup.
    """

    id: str
    trigger_kinds: frozenset[str]
    vadugwi_predicates: tuple[VadugwiPredicate, ...] = ()
    situation_tags: frozenset[str] = frozenset()
    situation_match: str = "any"
    memory_anchor: str | None = None
    cooldown_seconds: int = 0
    base_weight: float = 1.0
    motif: str = "informational"
    template: str = ""
    branch_keys: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("PromptFace.id must be non-empty")
        if not self.trigger_kinds:
            raise ValueError(
                f"PromptFace(id={self.id!r}) must declare at least one "
                f"trigger kind"
            )
        if self.situation_match not in _VALID_SITUATION_MATCH:
            raise ValueError(
                f"PromptFace(id={self.id!r}).situation_match="
                f"{self.situation_match!r} must be one of "
                f"{sorted(_VALID_SITUATION_MATCH)}"
            )
        if self.motif not in _VALID_MOTIFS:
            raise ValueError(
                f"PromptFace(id={self.id!r}).motif={self.motif!r} must be "
                f"one of {sorted(_VALID_MOTIFS)}"
            )
        if self.cooldown_seconds < 0:
            raise ValueError(
                f"PromptFace(id={self.id!r}).cooldown_seconds must be >= 0"
            )
        if self.base_weight < 0:
            raise ValueError(
                f"PromptFace(id={self.id!r}).base_weight must be >= 0"
            )
        if not self.template:
            raise ValueError(
                f"PromptFace(id={self.id!r}).template must be non-empty"
            )

    def situation_eligible(self, present_tags: frozenset[str]) -> bool:
        if not self.situation_tags:
            return True  # universal face
        if self.situation_match == "any":
            return bool(self.situation_tags & present_tags)
        # all
        return self.situation_tags.issubset(present_tags)

    def state_eligible(
        self,
        mood: list[int] | None,
        soul: dict,
        primed: list[int] | None = None,
    ) -> bool:
        return all(
            p.evaluate(mood, soul, primed) for p in self.vadugwi_predicates
        )


# ── Recency tracking ────────────────────────────────────────────────────


@dataclass
class RecencyLog:
    """Per-face last-fired timestamp + fire count.

    M3.1 keeps this in-memory; M3.3 will back it with SQLite so the
    cooldown survives restarts. The recency log is a *separate* concept
    from the corpus itself — many corpus instances can share one log
    (e.g. agent process holds the log; multiple corpora plug in).
    """

    last_fired: dict[str, float] = field(default_factory=dict)
    fire_counts: dict[str, int] = field(default_factory=dict)

    def note_fired(self, face_id: str, now: float) -> None:
        self.last_fired[face_id] = now
        self.fire_counts[face_id] = self.fire_counts.get(face_id, 0) + 1

    def seconds_since(self, face_id: str, now: float) -> float | None:
        last = self.last_fired.get(face_id)
        if last is None:
            return None
        return max(0.0, now - last)


# ── Weight helpers ──────────────────────────────────────────────────────


def vadugwi_affinity(
    predicates: Iterable[VadugwiPredicate],
    mood: list[int] | None,
    soul: dict,
    primed: list[int] | None = None,
) -> float:
    """Multiplier in ``[1.0, 2.0]`` for how deep into the predicate region
    the agent's state sits.

    Predicates barely satisfied → 1.0. State at the extreme of every
    constrained dim → ~2.0. Faces with no predicates always return 1.0.

    The ramp is linear in the per-dim margin, averaged across constraints.
    Not configurable in M3.1 — easy to swap for a sigmoid in M3.2 if the
    distribution feels too flat or too sharp in practice.
    """
    preds = list(predicates)
    if not preds:
        return 1.0
    accum = 0.0
    for p in preds:
        margin = p.margin(mood, soul, primed)
        # Max possible margin depends on op direction and dim:
        #   >= / > : up to 255 - p.value
        #   <= / < : up to p.value
        #   ==     : 0
        max_margin: int
        if p.op in (">=", ">"):
            max_margin = max(1, 255 - p.value)
        elif p.op in ("<=", "<"):
            max_margin = max(1, p.value)
        else:
            max_margin = 1  # == has no ramp; treat as flat 1.0 contribution
        ratio = min(1.0, margin / max_margin)
        accum += 1.0 + ratio  # 1.0 at boundary, 2.0 at extreme
    return accum / len(preds)


def novelty(
    face_id: str,
    cooldown_seconds: int,
    recency: RecencyLog,
    now: float,
) -> float:
    """Multiplier in ``[0.0, 1.0]`` reflecting freshness of this face.

    Returns:

      * ``1.0`` — face has never fired.
      * ``0.0`` — face fired within ``cooldown_seconds`` (face is in
        cooldown; the sampler treats this as ineligible).
      * Linear ramp from ``0.0`` at end-of-cooldown to ``1.0`` at
        ``2 * cooldown_seconds`` past last fire (so freshly-out-of-cooldown
        faces don't immediately dominate the dice over genuinely fresh
        faces).

    A face with ``cooldown_seconds == 0`` is always 1.0 — no cooldown
    semantics, the corpus author opted out of recency for that face.
    """
    elapsed = recency.seconds_since(face_id, now)
    if elapsed is None:
        return 1.0
    if cooldown_seconds <= 0:
        return 1.0
    if elapsed < cooldown_seconds:
        return 0.0
    extra = elapsed - cooldown_seconds
    ramp = min(1.0, extra / cooldown_seconds)
    return ramp


def branch_bias(face: "PromptFace", previous_face_id: str | None) -> float:
    """Multiplier reflecting whether this face is a coherent follow-up
    to the previous fire.

    A face's :py:attr:`PromptFace.branch_keys` is the set of parent face
    ids it wants to follow. When the immediately previous delivered fire
    is in that set, the face gets a bump so the conversation feels like
    a sequence rather than independent rolls.

    Returns ``1.0`` when no parent was provided, when the face declares
    no branch keys, or when the previous face id isn't a declared
    parent. Returns ``1.5`` on a hit — moderate bias, enough to swing
    the dice without making branch trees deterministic.

    The bonus magnitude is intentionally not configurable in M3.4 — the
    issue calls for a "soft preference," and tunability lands later if
    the distribution feels off in production.
    """
    if previous_face_id is None or not face.branch_keys:
        return 1.0
    if previous_face_id in face.branch_keys:
        return 1.5
    return 1.0


def motif_bias(motif: str, mood: list[int] | None, soul: dict) -> float:
    """Up-weight faces whose motif fits the agent's current shape.

    Mappings (M3.1 baseline; tunable by replacing this function in
    ``PromptCorpus`` subclasses):

      * ``relational`` ↑ when soul_distance is high AND W is low
        (the agent is shaken and self-worth is dipped — what helps is
        contact, not information).
      * ``exploratory`` ↑ when V high AND A elevated
        (the agent is in a curious-and-energetic shape).
      * ``regulatory`` ↑ when A very high AND U very high
        (overheated; the agent needs a wind-down prompt).
      * ``informational`` — always 1.0 (default).
    """
    if motif == "informational":
        return 1.0
    if mood is None:
        return 1.0  # no mood = no shape signal; don't bias anything
    v, a, _d, u, _g, w, _i = mood[:7]

    if motif == "relational":
        # soul_distance is roughly the L2-style mood-vs-soul gap; we
        # approximate cheaply with V/W per-dim deltas — relational
        # is most apt when the agent is far from baseline AND dipped W.
        soul_v = soul.get("v", 128)
        soul_w = soul.get("w", 128)
        v_gap = abs(v - soul_v)
        w_gap = max(0, soul_w - w)  # only counts if W has DROPPED
        if v_gap >= 30 and w_gap >= 25:
            return 1.6
        if v_gap >= 20 or w_gap >= 15:
            return 1.25
        return 1.0

    if motif == "exploratory":
        if v >= 140 and a >= 130:
            return 1.5
        if v >= 130 and a >= 120:
            return 1.2
        return 1.0

    if motif == "regulatory":
        if a >= 180 and u >= 150:
            return 1.7
        if a >= 160 and u >= 120:
            return 1.3
        return 1.0

    return 1.0


# ── PromptCorpus ────────────────────────────────────────────────────────


class PromptCorpus:
    """A weighted dice over ``PromptFace`` candidates.

    Construction is cheap — pass an iterable of faces; the corpus
    indexes them by trigger kind. Hosts that want runtime mutation
    will get a hot-reloadable variant in M3.3 alongside SQLite
    persistence; M3.1 corpora are intentionally frozen-after-build to
    keep the data model simple while the core sampler stabilises.
    """

    def __init__(
        self,
        faces: Iterable[PromptFace] = (),
        *,
        rng: random.Random | None = None,
    ) -> None:
        self._faces: tuple[PromptFace, ...] = tuple(faces)
        # Detect duplicate ids early — they break recency tracking.
        seen: set[str] = set()
        for f in self._faces:
            if f.id in seen:
                raise ValueError(
                    f"PromptCorpus: duplicate face id {f.id!r}"
                )
            seen.add(f.id)
        self._rng = rng or random.Random()
        # Index by trigger kind for cheap eligibility filtering.
        self._by_trigger: dict[str, list[PromptFace]] = {}
        for f in self._faces:
            for kind in f.trigger_kinds:
                self._by_trigger.setdefault(kind, []).append(f)

    @property
    def faces(self) -> tuple[PromptFace, ...]:
        return self._faces

    def faces_for(
        self,
        trigger: Trigger,
        situation_tags: frozenset[str] = frozenset(),
        memory_topics_present: Callable[[str], bool] | None = None,
        recency: RecencyLog | None = None,
        now: float = 0.0,
        *,
        primed: list[int] | None = None,
        previous_face_id: str | None = None,
    ) -> list[tuple[PromptFace, float]]:
        """Return ``(face, weight)`` for every face eligible to fire.

        Eligibility: trigger kind matches, all VADUGWI predicates
        satisfied, situation tags satisfied, memory anchor present (if
        the face has one and the host supplied a callback), face not in
        cooldown.

        Weight: ``base_weight * vadugwi_affinity * novelty * motif_bias
        * branch_bias``. ``previous_face_id`` (M3.4) is the id of the
        immediately previous *delivered* fire — faces that name it in
        ``branch_keys`` get a moderate bump so the conversation feels
        like a sequence.
        """
        recency = recency or RecencyLog()
        candidates = self._by_trigger.get(trigger.kind, ())

        def _anchor_ok(face: PromptFace) -> bool:
            if face.memory_anchor is None:
                return True
            if memory_topics_present is None:
                # Without a callback we treat anchored faces as ineligible —
                # better to skip than to fire a memory-anchored prompt
                # without confirmation that the memory exists.
                return False
            try:
                return bool(memory_topics_present(face.memory_anchor))
            except Exception:
                logger.exception(
                    "memory_topics_present(%r) raised; treating face %r "
                    "as ineligible",
                    face.memory_anchor, face.id,
                )
                return False

        out: list[tuple[PromptFace, float]] = []
        for face in candidates:
            if not face.state_eligible(trigger.mood, trigger.soul, primed):
                continue
            if not face.situation_eligible(situation_tags):
                continue
            if not _anchor_ok(face):
                continue
            nov = novelty(face.id, face.cooldown_seconds, recency, now)
            if nov <= 0.0:
                continue
            affinity = vadugwi_affinity(
                face.vadugwi_predicates,
                trigger.mood,
                trigger.soul,
                primed,
            )
            bias = motif_bias(face.motif, trigger.mood, trigger.soul)
            branch = branch_bias(face, previous_face_id)
            weight = face.base_weight * affinity * nov * bias * branch
            if weight <= 0.0:
                continue
            out.append((face, weight))
        return out

    def sample(
        self,
        trigger: Trigger,
        situation_tags: frozenset[str] = frozenset(),
        memory_topics_present: Callable[[str], bool] | None = None,
        recency: RecencyLog | None = None,
        now: float = 0.0,
        *,
        primed: list[int] | None = None,
        previous_face_id: str | None = None,
    ) -> PromptFace | None:
        """Roll a weighted die over eligible faces. Returns None if none."""
        weighted = self.faces_for(
            trigger, situation_tags, memory_topics_present,
            recency, now, primed=primed,
            previous_face_id=previous_face_id,
        )
        if not weighted:
            return None
        faces, weights = zip(*weighted)
        # random.choices is stable across Python versions and supports
        # float weights directly.
        chosen = self._rng.choices(list(faces), weights=list(weights), k=1)
        return chosen[0]


# ── Optional helper hosts can use to derive situation tags ──────────────


def default_tags_from_metrics(trigger: Trigger) -> frozenset[str]:
    """Cheap default mapping from ``Trigger.metrics`` to situation tags.

    Hosts that don't yet have a richer situation model can call this
    to bootstrap. Lifts a few obvious signals into tags:

      * ``autonomy_idle``      — when ``idle_seconds > 0`` and no other
        situation hints are present (long-silence-style triggers).
      * ``operator_silent_long`` — when ``idle_seconds >= 1800`` (30 min).
      * ``post_conversation`` — when ``idle_seconds < 300`` and an outbound
        was recent (the trigger fires shortly after the last reply).
      * ``trauma_pressure``   — when ``trauma_load`` metric is set.
      * ``sustained_care``    — when ``nourishment_load`` metric is set.

    The engine never calls this — it's offered for hosts. Strict-host
    tags from the host's situation model take precedence; this helper
    exists to short-circuit early-stage integration work.
    """
    tags: set[str] = set()
    metrics = trigger.metrics or {}

    idle = int(metrics.get("idle_seconds", 0) or 0)
    if idle >= 1800:
        tags.add("operator_silent_long")
    if idle > 0:
        tags.add("autonomy_idle")
    if 0 < idle < 300:
        tags.add("post_conversation")

    if metrics.get("trauma_load"):
        tags.add("trauma_pressure")
    if metrics.get("nourishment_load"):
        tags.add("sustained_care")

    return frozenset(tags)


__all__ = [
    "VadugwiPredicate",
    "PromptFace",
    "PromptCorpus",
    "RecencyLog",
    "vadugwi_affinity",
    "novelty",
    "motif_bias",
    "branch_bias",
    "default_tags_from_metrics",
]
