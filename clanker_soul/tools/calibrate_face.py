"""Calibration helpers for PromptFace static VADUGWI affinities."""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import TYPE_CHECKING

from clanker_soul.score import Score

if TYPE_CHECKING:
    from clanker_soul.inference import Inference
    from clanker_soul.pulse import PromptFace


_DIM_NAMES: tuple[str, ...] = ("V", "A", "D", "U", "G", "W", "I")
_INTROSPECTION_FRAME = (
    "A spontaneous thought just surfaced in your mind. This is your own "
    "introspection, not a question from another person. Score how this "
    "thought would tint your felt state."
)


@dataclass(frozen=True)
class CalibrationReport:
    """Advisory comparison between static and inference-scored affinity."""

    face_id: str
    template: str
    samples: int
    static_affinity: tuple[int, int, int, int, int, int, int]
    median_score: tuple[float, float, float, float, float, float, float]
    stdev: tuple[float, float, float, float, float, float, float]
    divergence: tuple[float, float, float, float, float, float, float]
    max_abs_delta: float
    recommendation: str

    def divergence_by_dim(self) -> dict[str, float]:
        return dict(zip(_DIM_NAMES, self.divergence))


async def calibrate_face(
    face: "PromptFace",
    inference: "Inference",
    *,
    samples: int = 5,
    threshold: float = 25.0,
    context: dict | None = None,
) -> CalibrationReport:
    """Score a ``PromptFace`` repeatedly and compare against its affinity.

    The helper is advisory. It never mutates the face and never rewrites
    affinities; authors decide whether to accept the suggested adjustment.
    """
    if samples < 1:
        raise ValueError("samples must be >= 1")
    if threshold < 0:
        raise ValueError("threshold must be >= 0")
    if face.vadugwi_affinity is None:
        raise ValueError(f"PromptFace(id={face.id!r}) has no vadugwi_affinity")

    base_context = {
        "source": "internal_introspection",
        "frame": _INTROSPECTION_FRAME,
        "face_id": face.id,
        "trigger_kinds": sorted(face.trigger_kinds),
        "motif": face.motif,
    }
    if context:
        base_context.update(context)

    scores: list[Score] = []
    for index in range(samples):
        score = await inference.score(
            face.template,
            {**base_context, "sample_index": index},
        )
        scores.append(score)

    columns = list(zip(*(score.as_tuple() for score in scores)))
    median_score = tuple(float(statistics.median(dim_values)) for dim_values in columns)
    stdev = tuple(_pstdev(dim_values) for dim_values in columns)
    static = face.vadugwi_affinity
    divergence = tuple(median_score[i] - static[i] for i in range(7))
    max_abs_delta = max(abs(delta) for delta in divergence)
    recommendation = _recommendation(divergence, threshold)
    return CalibrationReport(
        face_id=face.id,
        template=face.template,
        samples=samples,
        static_affinity=static,
        median_score=median_score,
        stdev=stdev,
        divergence=divergence,
        max_abs_delta=max_abs_delta,
        recommendation=recommendation,
    )


def _pstdev(values: tuple[int, ...]) -> float:
    if len(values) < 2:
        return 0.0
    return float(statistics.pstdev(values))


def _recommendation(
    divergence: tuple[float, float, float, float, float, float, float],
    threshold: float,
) -> str:
    flagged = [
        f"{dim}{delta:+.0f}"
        for dim, delta in zip(_DIM_NAMES, divergence)
        if abs(delta) >= threshold
    ]
    if not flagged:
        return "Static affinity is within calibration threshold."
    return (
        "Review static affinity; LLM median differs beyond threshold on " + ", ".join(flagged) + "."
    )


__all__ = ["CalibrationReport", "calibrate_face"]
