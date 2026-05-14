from __future__ import annotations

import pytest

from clanker_soul import PromptFace, Score
from clanker_soul.tools import calibrate_face


class FakeInference:
    def __init__(self, scores: list[Score]) -> None:
        self._scores = scores
        self.calls: list[tuple[str, dict]] = []

    async def score(self, text: str, context: dict) -> Score:
        self.calls.append((text, context))
        return self._scores[len(self.calls) - 1]

    async def act(self, action):  # noqa: ANN001, ARG002
        raise NotImplementedError


def _face(*, affinity: tuple[int, int, int, int, int, int, int] | None = None) -> PromptFace:
    return PromptFace(
        id="face.calibration",
        trigger_kinds=frozenset({"distress", "curiosity"}),
        template="A thought about whether this plan still fits.",
        motif="exploratory",
        vadugwi_affinity=affinity,
    )


@pytest.mark.asyncio
async def test_calibrate_face_reports_median_stdev_and_divergence() -> None:
    face = _face(affinity=(100, 110, 120, 130, 140, 150, 160))
    inference = FakeInference(
        [
            Score(v=90, a=100, d=110, u=120, g=130, w=140, i=150),
            Score(v=100, a=110, d=120, u=130, g=140, w=150, i=160),
            Score(v=130, a=140, d=150, u=160, g=170, w=180, i=190),
        ]
    )

    report = await calibrate_face(face, inference, samples=3, threshold=25.0)

    assert report.face_id == "face.calibration"
    assert report.template == face.template
    assert report.samples == 3
    assert report.static_affinity == (100, 110, 120, 130, 140, 150, 160)
    assert report.median_score == (100, 110, 120, 130, 140, 150, 160)
    assert report.divergence == (0, 0, 0, 0, 0, 0, 0)
    assert report.max_abs_delta == 0
    assert report.stdev[0] == pytest.approx(16.9967, rel=0.001)
    assert report.recommendation == "Static affinity is within calibration threshold."
    assert report.divergence_by_dim() == {
        "V": 0,
        "A": 0,
        "D": 0,
        "U": 0,
        "G": 0,
        "W": 0,
        "I": 0,
    }


@pytest.mark.asyncio
async def test_calibrate_face_passes_introspection_context_and_sample_index() -> None:
    face = _face(affinity=(128, 128, 128, 128, 128, 128, 128))
    inference = FakeInference([Score(v=150), Score(v=150)])

    await calibrate_face(
        face,
        inference,
        samples=2,
        context={"suite": "unit", "source": "override-source"},
    )

    assert [call[0] for call in inference.calls] == [face.template, face.template]
    assert [call[1]["sample_index"] for call in inference.calls] == [0, 1]
    assert inference.calls[0][1]["source"] == "override-source"
    assert inference.calls[0][1]["suite"] == "unit"
    assert inference.calls[0][1]["face_id"] == face.id
    assert inference.calls[0][1]["trigger_kinds"] == ["curiosity", "distress"]
    assert "spontaneous thought" in inference.calls[0][1]["frame"]


@pytest.mark.asyncio
async def test_calibrate_face_recommends_review_when_delta_crosses_threshold() -> None:
    face = _face(affinity=(100, 100, 100, 100, 100, 100, 100))
    inference = FakeInference([Score(v=140, a=60, d=100, u=100, g=100, w=100, i=100)])

    report = await calibrate_face(face, inference, samples=1, threshold=25)

    assert report.stdev == (0, 0, 0, 0, 0, 0, 0)
    assert report.divergence[:2] == (40, -40)
    assert report.max_abs_delta == 40
    assert report.recommendation == (
        "Review static affinity; LLM median differs beyond threshold on V+40, A-40."
    )


@pytest.mark.asyncio
async def test_calibrate_face_validates_inputs() -> None:
    face = _face(affinity=(128, 128, 128, 128, 128, 128, 128))
    inference = FakeInference([Score()])

    with pytest.raises(ValueError, match="samples must be >= 1"):
        await calibrate_face(face, inference, samples=0)

    with pytest.raises(ValueError, match="threshold must be >= 0"):
        await calibrate_face(face, inference, threshold=-1)

    with pytest.raises(ValueError, match="has no vadugwi_affinity"):
        await calibrate_face(_face(), inference)
