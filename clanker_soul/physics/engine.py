"""``EmotionalPhysics`` — the stateful engine that turns scored events
into Mood + Soul updates.

Per-event pipeline (``ingest``):
  1. compute event_weight w from raw VADUGWI (severity-aware)
  2. compute soul_armor a from current Soul (high W/D = resilient)
  3. effective weight ``w_eff = w * (1 - a * armor_max)``
  4. mood blend: ``M_new = M * (1 - w_eff·α) + B * (w_eff·α)``
  5. per-dim soul pull pulls each dim back toward its soul anchor
     (cushioned proportionally to the soul value on that dim,
     weight-gated so heavy events still punch through)
  6. on heavy events with breached mood: direct soul leak
  7. trauma reservoir += w_eff for each negative pattern
  8. nourishment reservoir += w_eff for positive patterns

Periodic (``soul_drift``, called per turn or by background tick):
  • mood decay toward Soul (not toward neutral) with half-life
    depending on Soul.W
  • soul drift: rolling mood mean nudges Soul; trauma/nourishment
    imbalance nudges Soul.W and Soul.V proportionally.

Stateful, NOT thread-safe — run from a single pipeline worker per agent.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import fields as dc_fields, replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from clanker_soul.physics.config import (
    CORRECTION_PATTERNS,
    HEAVY_PATTERNS,
    MISTAKE_PATTERNS,
    POSITIVE_PATTERNS,
    PhysicsConfig,
)
from clanker_soul.physics.math import (
    _clamp,
    _decay_half_life,
    _why,
    event_weight,
    soul_armor,
    soul_distance,
)
from clanker_soul.physics.contemplation import ContemplationResult
from clanker_soul.physics.tick import PhysicsTick
from clanker_soul.score import Score
from clanker_soul.soul import (
    MistakeReservoir,
    NourishmentReservoir,
    SoulState,
    TraumaReservoir,
)

if TYPE_CHECKING:
    from clanker_soul.eventlog import EventLog
    from clanker_soul.overrides import ConfigOverrides
    from clanker_soul.pulse.corpus import PromptFace

logger = logging.getLogger(__name__)


class EmotionalPhysics:
    """Stateful emotional engine for one agent.

    Holds the Soul, current Mood, and the trauma/nourishment
    reservoirs. Not thread-safe — run from a single pipeline worker
    per agent."""

    def __init__(
        self,
        soul: SoulState,
        trauma: TraumaReservoir | None = None,
        nourishment: NourishmentReservoir | None = None,
        config: PhysicsConfig | None = None,
        *,
        event_log: "EventLog | None" = None,
        overrides: "ConfigOverrides | None" = None,
        agent_id: str | None = None,
        mistakes: MistakeReservoir | None = None,
    ) -> None:
        if (event_log is not None or overrides is not None) and not agent_id:
            raise ValueError(
                "agent_id is required when event_log or overrides is provided "
                "(rows are scoped per-agent)"
            )
        self.soul = soul
        self.trauma = trauma if trauma is not None else TraumaReservoir()
        self.nourishment = nourishment if nourishment is not None else NourishmentReservoir()
        # M4 #97 — additive kwarg, defaults to an empty reservoir so
        # callers that don't pass it (v0.17.x and earlier hosts) get
        # zero behaviour change.
        self.mistakes = mistakes if mistakes is not None else MistakeReservoir()
        self.config = config or PhysicsConfig()
        self._mood: Score | None = None
        self._mood_time: float = 0.0  # perf_counter
        self._last_tick: PhysicsTick | None = None
        self._event_log = event_log
        self._agent_id = agent_id
        self._overrides_provider = overrides
        # Snapshot constructor values so removing an override can revert
        # cleanly. We track which fields are *currently* overridden so
        # un-overridden soul fields (which may have drifted) aren't
        # clobbered.
        self._original_config: dict[str, object] = {
            f.name: getattr(self.config, f.name) for f in dc_fields(self.config)
        }
        self._original_soul: dict[str, object] = {
            f.name: getattr(self.soul, f.name) for f in dc_fields(self.soul)
        }
        self._active_physics_overrides: set[str] = set()
        self._active_soul_overrides: set[str] = set()

    def reload_overrides(self) -> None:
        """Pull the current override bundle for this agent and apply
        deltas in-place to ``self.config`` and ``self.soul``.

        Field-level reversion: when a field that was previously
        overridden is no longer in the bundle, that field is restored
        to its constructor value. Soul fields that were *never*
        overridden are left alone — drift is preserved.

        No-op if no overrides provider was supplied at construction.
        Cheap enough to call at the start of every tick."""
        if self._overrides_provider is None or not self._agent_id:
            return
        bundle = self._overrides_provider.get(self._agent_id)

        # PHYSICS CONFIG
        new_physics_keys = set(bundle.physics.keys())
        physics_field_names = {f.name for f in dc_fields(self.config)}
        # Revert fields that were overridden but no longer are.
        for field_name in self._active_physics_overrides - new_physics_keys:
            if field_name in self._original_config:
                setattr(self.config, field_name, self._original_config[field_name])
        # Apply current overrides.
        for field_name, value in bundle.physics.items():
            if field_name not in physics_field_names:
                logger.warning(
                    "ignoring unknown PhysicsConfig override: %r",
                    field_name,
                )
                continue
            setattr(self.config, field_name, value)
        self._active_physics_overrides = new_physics_keys & physics_field_names

        # SOUL
        new_soul_keys = set(bundle.soul.keys())
        soul_field_names = {f.name for f in dc_fields(self.soul)}
        for field_name in self._active_soul_overrides - new_soul_keys:
            if field_name in self._original_soul:
                setattr(self.soul, field_name, self._original_soul[field_name])
        for field_name, value in bundle.soul.items():
            if field_name not in soul_field_names:
                logger.warning(
                    "ignoring unknown SoulState override: %r",
                    field_name,
                )
                continue
            setattr(self.soul, field_name, value)
        self._active_soul_overrides = new_soul_keys & soul_field_names

    @property
    def mood(self) -> Score | None:
        if self._mood is None:
            return None
        return self._apply_mood_decay(self._mood, self._mood_time)

    @property
    def last_tick(self) -> PhysicsTick | None:
        return self._last_tick

    def reset_mood(self) -> None:
        self._mood = None
        self._mood_time = 0.0

    def absorb_echo(self, echo: Score, weight: float = 0.1) -> None:
        """Blend a non-event Score (memory echoes, recalled emotional
        state) into the current mood at reduced weight. Does NOT update
        Soul or reservoirs — echoes are not events."""
        w = max(0.0, min(0.5, weight))
        if w == 0.0:
            return
        if self._mood is None:
            anchor = self._mood_anchor()
            blended = self._blend(anchor, echo, w)
            self._mood = self._apply_dim_resilience(blended)
            self._mood_time = time.perf_counter()
            return
        decayed = self._apply_mood_decay(self._mood, self._mood_time)
        blended = self._blend(decayed, echo, w)
        self._mood = self._apply_dim_resilience(blended)
        self._mood_time = time.perf_counter()

    def ingest(self, event: Score, *, raw: Score | None = None) -> PhysicsTick:
        """Update mood, soul, and reservoirs from a scored event.

        ``raw``: optional pre-mood-prime version of the score. If a
        host applied :func:`mood_prime_score` before ingest, pass the
        pre-prime score here so the event log records both. If
        omitted, ``event`` is logged as both raw and primed=None (no
        priming recorded)."""
        cfg = self.config
        soul_before = replace(self.soul) if self._event_log is not None else None
        mood_before = self.mood if self._event_log is not None else None

        decayed_mood = (
            self._apply_mood_decay(self._mood, self._mood_time)
            if self._mood is not None
            else self._mood_anchor()
        )

        weight = event_weight(event)
        armor = soul_armor(self.soul)
        w_eff = weight * (1.0 - armor * cfg.armor_max)

        alpha = min(0.95, cfg.blend_alpha * (0.4 + w_eff))
        new_mood = self._blend(decayed_mood, event, alpha)
        new_mood = self._apply_dim_resilience(new_mood, event_weight=weight)

        dist_before = soul_distance(decayed_mood, self.soul)
        breached = False
        breach_applied = 0.0
        if (
            weight >= cfg.heavy_threshold
            and dist_before > cfg.breach_threshold
            and self._has_heavy_pattern(event)
        ):
            breached = True
            wound_factor = min(1.0, (dist_before - cfg.breach_threshold) / 50.0)
            event_factor = (weight - cfg.heavy_threshold) / max(0.01, 1.0 - cfg.heavy_threshold)
            breach_applied = cfg.breach_delta * wound_factor * event_factor * (1.0 - armor * 0.4)
            self._apply_breach(event, breach_applied)

        self._mood = new_mood
        self._mood_time = time.perf_counter()

        self._update_reservoirs(event, w_eff)

        tick = PhysicsTick(
            weight_raw=round(weight, 4),
            armor=round(armor, 4),
            weight_effective=round(w_eff, 4),
            breached=breached,
            breach_delta_applied=round(breach_applied, 5),
            soul_distance_before=round(dist_before, 2),
            patterns=list(event.patterns or ()),
        )
        self._last_tick = tick

        if self._event_log is not None:
            self._emit_ingest_log(
                event=event,
                raw=raw,
                soul_before=soul_before,  # type: ignore[arg-type]
                mood_before=mood_before,
                tick=tick,
            )
        return tick

    def _emit_ingest_log(
        self,
        *,
        event: Score,
        raw: Score | None,
        soul_before: SoulState,
        mood_before: Score | None,
        tick: PhysicsTick,
    ) -> None:
        """Build an IngestRecord and ship it to the configured sink.

        Soft-fail: if the sink raises, log a warning and continue. The
        :py:class:`SqliteEventLog` impl already catches its own
        exceptions, but custom impls might not, and physics must
        remain robust either way."""
        from clanker_soul.eventlog import IngestRecord

        if raw is not None and raw != event:
            primed_for_log: Score | None = event
            raw_for_log = raw
        else:
            primed_for_log = None
            raw_for_log = event

        try:
            rec = IngestRecord(
                ts=datetime.now(timezone.utc).timestamp(),
                agent_id=self._agent_id or "",
                raw=raw_for_log,
                primed=primed_for_log,
                mood_before=mood_before,
                mood_after=self.mood or self._mood_anchor(),
                soul_before=soul_before,
                soul_after=replace(self.soul),
                weight_raw=tick.weight_raw,
                armor=tick.armor,
                weight_effective=tick.weight_effective,
                breached=tick.breached,
                breach_delta=tick.breach_delta_applied,
                patterns=tuple(event.patterns or ()),
                classification=self._classify(event),
                why=_why(tick, event),
            )
            self._event_log.log_ingest(rec)
        except Exception:
            logger.exception("event_log.log_ingest raised — physics continuing")

    def soul_drift(self, *, now_ts: float | None = None) -> dict:
        """Slowly nudge Soul based on time-elapsed × current mood +
        trauma/nourishment imbalance. Idempotent — uses
        ``soul.last_drift_ts``."""
        cfg = self.config
        now = now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp()
        elapsed_h = max(0.0, (now - self.soul.last_drift_ts) / 3600.0)
        if elapsed_h < 0.05:  # less than 3 min — skip
            return {"skipped": True, "elapsed_hours": round(elapsed_h, 4)}

        moved: list[tuple[str, int, int]] = []
        if self._mood is not None:
            cur_mood = self._apply_mood_decay(self._mood, self._mood_time)
            for dim in ("v", "a", "d", "u", "g", "w", "i"):
                m_val = getattr(cur_mood, dim)
                s_val = getattr(self.soul, dim)
                gap = m_val - s_val
                if abs(gap) >= cfg.soul_drift_min_distance:
                    nudge = gap * cfg.soul_drift_per_hour * elapsed_h
                    new_val = _clamp(s_val + nudge)
                    if new_val != s_val:
                        setattr(self.soul, dim, new_val)
                        moved.append((dim, s_val, new_val))

        trauma_load = self.trauma.load(now_ts=now)
        nourishment_load = self.nourishment.load(now_ts=now)
        imbalance = nourishment_load - trauma_load

        if trauma_load > cfg.trauma_pressure_floor and imbalance < 0:
            magnitude = min(1.0, -imbalance / 50.0)
            self.soul.w = _clamp(self.soul.w - cfg.wounding_rate * magnitude * elapsed_h * 100)
            self.soul.v = _clamp(self.soul.v - cfg.wounding_rate * magnitude * elapsed_h * 60)
            self.soul.g = _clamp(self.soul.g - cfg.wounding_rate * magnitude * elapsed_h * 40)
        elif imbalance > cfg.trauma_pressure_floor:
            magnitude = min(1.0, imbalance / 50.0)
            self.soul.w = _clamp(self.soul.w + cfg.healing_rate * magnitude * elapsed_h * 100)
            self.soul.v = _clamp(self.soul.v + cfg.healing_rate * magnitude * elapsed_h * 60)
            self.soul.g = _clamp(self.soul.g + cfg.healing_rate * magnitude * elapsed_h * 30)

        # ── Mistake wear — slow soul drift DOWN from sustained
        # being-wrong. Distinct from and weaker than trauma's
        # wounding_rate. Notably we do NOT touch G (gravity/grounding)
        # the way trauma does — being wrong makes you feel uncertain,
        # not crushed.
        mistakes_load = self.mistakes.load(now_ts=now)
        if mistakes_load > cfg.mistake_pressure_floor:
            mistake_magnitude = min(
                1.0, (mistakes_load - cfg.mistake_pressure_floor) / 100.0
            )
            self.soul.w = _clamp(
                self.soul.w - cfg.mistake_wounding_rate * mistake_magnitude * elapsed_h * 80
            )
            self.soul.v = _clamp(
                self.soul.v - cfg.mistake_wounding_rate * mistake_magnitude * elapsed_h * 40
            )

        # ── Recovery resilience — slow soul drift UP. Healthy
        # correction-to-mistake ratio sustained over the half-life
        # window raises the persistent W and D baseline (mastery
        # experiences inoculate against future learned helplessness —
        # see docs/research/m4_failure_response_matrix.md). Gated on
        # ``correction_load > mistakes_load`` so the agent has to be
        # *winning* the correction contest before resilience builds.
        # Default rate is 0.0 (OFF) — opt-in only.
        correction_load = self.nourishment.by_pattern_filtered(
            CORRECTION_PATTERNS, now_ts=now
        )
        resilience_uplift_applied = False
        if (
            cfg.recovery_resilience_rate > 0.0
            and correction_load > cfg.resilience_correction_floor
            and correction_load > mistakes_load
        ):
            resilience_uplift_applied = True
            resilience_magnitude = min(
                1.0, (correction_load - cfg.resilience_correction_floor) / 100.0
            )
            self.soul.w = _clamp(
                self.soul.w
                + cfg.recovery_resilience_rate * resilience_magnitude * elapsed_h * 80
            )
            self.soul.d = _clamp(
                self.soul.d
                + cfg.recovery_resilience_rate * resilience_magnitude * elapsed_h * 60
            )

        self.soul.last_drift_ts = now
        return {
            "elapsed_hours": round(elapsed_h, 4),
            "trauma_load": round(trauma_load, 4),
            "nourishment_load": round(nourishment_load, 4),
            "imbalance": round(imbalance, 4),
            "mistakes_load": round(mistakes_load, 4),
            "correction_load": round(correction_load, 4),
            "resilience_uplift": resilience_uplift_applied,
            "drifted_dims": moved,
            "soul_now": self.soul.to_dict(),
        }

    def contemplate(
        self,
        face: "PromptFace",
        *,
        weight_scale: float = 1.0,
    ) -> ContemplationResult:
        """Ingest a face's ``vadugwi_affinity`` as a synthetic mood-shift.

        Distinct from :py:meth:`ingest`: contemplation is the M4
        primitive for "the agent thought about a thing." It updates
        Mood (so cascade layers can route off the delta) but does NOT
        update soul reservoirs, trigger breach, or write an event-log
        IngestRecord — a thought is not an event.

        ``weight_scale`` (default ``1.0``) attenuates how strongly the
        affinity blends in. Use ``< 1.0`` for fleeting thoughts and
        ``> 1.0`` for charged ones; values outside ``[0.0, 5.0]`` are
        clamped.

        Mood-anchoring: the blend starts from current mood (or the
        Soul-anchored fallback for first-ever contemplation), so the
        affinity acts as a *target* the mood drifts toward, not an
        absolute jump. A high-W agent contemplating a heavy-G face
        moves less than a low-W agent contemplating the same face —
        the existing ``soul_armor`` × ``dim_resilience`` machinery
        keeps personality load-bearing here too.

        Raises ``ValueError`` if ``face.vadugwi_affinity is None`` —
        contemplation needs an explicit affinity, not a guess.
        """
        if face.vadugwi_affinity is None:
            raise ValueError(
                f"PromptFace(id={face.id!r}).vadugwi_affinity is None — "
                "cannot contemplate a face without a 7-tuple affinity"
            )

        ws = max(0.0, min(5.0, weight_scale))
        cfg = self.config
        affinity = face.vadugwi_affinity
        score = Score(
            v=_clamp(affinity[0]),
            a=_clamp(affinity[1]),
            d=_clamp(affinity[2]),
            u=_clamp(affinity[3]),
            g=_clamp(affinity[4]),
            w=_clamp(affinity[5]),
            i=_clamp(affinity[6]),
            patterns=("CONTEMPLATION",),
        )

        pre = (
            self._apply_mood_decay(self._mood, self._mood_time)
            if self._mood is not None
            else self._mood_anchor()
        )

        if ws == 0.0:
            # Zero weight = "I didn't actually think about it." No-op:
            # mood unchanged, no decay update, but report a result so
            # callers can still observe pre/post symmetry.
            return ContemplationResult(
                pre_mood=pre.as_tuple(),
                post_mood=pre.as_tuple(),
                delta=(0, 0, 0, 0, 0, 0, 0),
                score=score,
            )

        weight = event_weight(score) * ws
        armor = soul_armor(self.soul)
        w_eff = weight * (1.0 - armor * cfg.armor_max)
        alpha = min(0.95, cfg.blend_alpha * (0.4 + w_eff))

        blended = self._blend(pre, score, alpha)
        post = self._apply_dim_resilience(blended, event_weight=weight)

        self._mood = post
        self._mood_time = time.perf_counter()

        delta = (
            post.v - pre.v,
            post.a - pre.a,
            post.d - pre.d,
            post.u - pre.u,
            post.g - pre.g,
            post.w - pre.w,
            post.i - pre.i,
        )

        return ContemplationResult(
            pre_mood=pre.as_tuple(),
            post_mood=post.as_tuple(),
            delta=delta,
            score=score,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _mood_anchor(self) -> Score:
        """Mood at session start equals Soul (not 128). The agent wakes
        up as itself."""
        s = self.soul
        return Score(v=s.v, a=s.a, d=s.d, u=s.u, g=s.g, w=s.w, i=s.i)

    def _apply_mood_decay(self, mood: Score, last_time: float) -> Score:
        """Decay mood toward Soul (not 128). Half-life modulated by Soul.W."""
        elapsed = time.perf_counter() - last_time
        if elapsed <= 0:
            return mood
        hl = _decay_half_life(self.soul, self.config.mood_decay_half_life_base)
        decay = math.exp(-0.6931 * elapsed / hl)
        s = self.soul

        def toward_soul(m_val: int, s_val: int) -> int:
            return _clamp(s_val + (m_val - s_val) * decay)

        return Score(
            v=toward_soul(mood.v, s.v),
            a=toward_soul(mood.a, s.a),
            d=toward_soul(mood.d, s.d),
            u=toward_soul(mood.u, s.u),
            g=toward_soul(mood.g, s.g),
            w=toward_soul(mood.w, s.w),
            i=toward_soul(mood.i, s.i),
            patterns=mood.patterns,
        )

    @staticmethod
    def _blend(mood: Score, event: Score, alpha: float) -> Score:
        a = max(0.0, min(0.95, alpha))
        b = 1.0 - a

        def mix(m_val: int, e_val: int) -> int:
            return _clamp(m_val * b + e_val * a)

        return Score(
            v=mix(mood.v, event.v),
            a=mix(mood.a, event.a),
            d=mix(mood.d, event.d),
            u=mix(mood.u, event.u),
            g=mix(mood.g, event.g),
            w=mix(mood.w, event.w),
            i=mix(mood.i, event.i),
            patterns=event.patterns,
        )

    def _apply_dim_resilience(
        self,
        mood: Score,
        event_weight: float = 0.0,
    ) -> Score:
        """Pull each mood dim back toward its soul anchor by the per-dim
        resilience factor. Composes with the global armor — armor reduces
        blend strength, this layer adds a soul-centered cushion on top.

        Pull strength scales DOWN with event weight: ordinary messages
        get strong cushioning, but heavy hits punch through so the
        breach mechanic can still fire on sustained attack."""
        from clanker_soul.physics.math import dim_resilience as _dim_resilience

        weight_scale = max(0.0, 1.0 - min(1.0, event_weight))
        if weight_scale == 0.0:
            return mood
        pulls = _dim_resilience(self.soul, self.config.dim_resilience_max)
        soul = self.soul.as_tuple()
        cur = (mood.v, mood.a, mood.d, mood.u, mood.g, mood.w, mood.i)
        pulled = tuple(_clamp(c + (s - c) * p * weight_scale) for c, s, p in zip(cur, soul, pulls))
        return Score(
            v=pulled[0],
            a=pulled[1],
            d=pulled[2],
            u=pulled[3],
            g=pulled[4],
            w=pulled[5],
            i=pulled[6],
            patterns=mood.patterns,
        )

    def _has_heavy_pattern(self, event: Score) -> bool:
        if not event.patterns:
            return event.v < 60 or event.w < 60
        return any(p.upper() in HEAVY_PATTERNS for p in event.patterns)

    def _apply_breach(self, event: Score, delta: float) -> None:
        """Direct soul leak — fraction `delta` of event lands on Soul.
        Only on the wounded dimensions to avoid wholesale soul rewrite."""
        d = max(0.0, min(0.4, delta))
        s = self.soul
        s.v = _clamp(s.v * (1 - d) + event.v * d)
        s.w = _clamp(s.w * (1 - d) + event.w * d)
        s.g = _clamp(s.g * (1 - d) + event.g * d)

    def _update_reservoirs(self, event: Score, weight: float) -> None:
        """Route weight into the right reservoir based on event content.

        Five outcomes from ``_classify``: ``positive`` → nourishment,
        ``negative`` → trauma, ``mistake`` → mistakes,
        ``correction`` → nourishment AND active relief on mistakes,
        ``None`` → no reservoir change."""
        if weight <= 0.05:
            return
        bucket = self._classify(event)
        if bucket is None:
            return

        now = datetime.now(timezone.utc).timestamp()
        patterns = (
            list(event.patterns)
            if event.patterns
            else [
                "WARMTH"
                if bucket in {"positive", "correction"}
                else (
                    "GENERIC_MISTAKE" if bucket == "mistake" else "GENERIC_NEGATIVE"
                )
            ]
        )
        per_pattern = weight * 100.0 / max(1, len(patterns))

        if bucket == "correction":
            # Two-step: feed nourishment AND actively relieve mistakes.
            # The relief weight tracks the full event weight (×100 to
            # match the same scaling factor used for reservoir adds),
            # scaled by the operator-tunable correction_relief_factor.
            for p in patterns:
                self.nourishment.add(p, per_pattern, now_ts=now)
            relief = weight * 100.0 * self.config.correction_relief_factor
            if relief > 0.0:
                self.mistakes.relieve(relief, now_ts=now)
            return

        if bucket == "mistake":
            target: TraumaReservoir = self.mistakes
        elif bucket == "positive":
            target = self.nourishment
        else:  # negative
            target = self.trauma
        for p in patterns:
            target.add(p, per_pattern, now_ts=now)

    @staticmethod
    def _classify(event: Score) -> str | None:
        """Return ``'positive'``, ``'negative'``, ``'mistake'``,
        ``'correction'``, or ``None`` (ambiguous).

        Pattern routing wins over the V/W heuristic — explicit beats
        implicit. Order is **mistake → correction → positive →
        negative**: a Score that somehow carries both a mistake and a
        correction pattern is malformed (host bug) and routes to
        mistake so the bug surfaces in soul-wear rather than getting
        silently swallowed by relief."""
        patterns_upper = [p.upper() for p in (event.patterns or ())]

        if any(p in MISTAKE_PATTERNS for p in patterns_upper):
            return "mistake"
        if any(p in CORRECTION_PATTERNS for p in patterns_upper):
            return "correction"
        if any(p in POSITIVE_PATTERNS for p in patterns_upper):
            return "positive"
        if any(p in HEAVY_PATTERNS for p in patterns_upper):
            return "negative"

        if event.v >= 155 and event.w >= 145:
            return "positive"
        if event.v <= 90 and event.w <= 100:
            return "negative"

        return None


__all__ = ["EmotionalPhysics"]
