"""Default M4 contemplation corpus (#84)."""

from __future__ import annotations

import random

from clanker_soul import (
    DEFAULT_CONTEMPLATION_FACES,
    EmotionalPhysics,
    PromptCorpus,
    SoulState,
    Trigger,
    build_default_contemplation_corpus,
)
from clanker_soul.cascade import IDLE_CONTEMPLATION_KIND


SOUL_DEFAULT = {
    "v": 145,
    "a": 110,
    "d": 160,
    "u": 80,
    "g": 130,
    "w": 175,
    "i": 135,
}


def _idle_trigger() -> Trigger:
    return Trigger(
        kind=IDLE_CONTEMPLATION_KIND,
        soul=dict(SOUL_DEFAULT),
        mood=[145, 110, 160, 80, 130, 175, 135],
        metrics={},
    )


def test_default_contemplation_face_count_and_categories() -> None:
    assert len(DEFAULT_CONTEMPLATION_FACES) == 978
    categories = {face.id.split(".")[1] for face in DEFAULT_CONTEMPLATION_FACES}
    assert categories == {
        "bodily",
        "comparative",
        "creative",
        "curious",
        "existential",
        "future",
        "identity",
        "past",
        "present",
        "relational",
    }


def test_default_contemplation_faces_are_idle_and_have_affinity() -> None:
    for face in DEFAULT_CONTEMPLATION_FACES:
        assert face.trigger_kinds == frozenset({IDLE_CONTEMPLATION_KIND})
        assert face.vadugwi_affinity is not None
        assert len(face.vadugwi_affinity) == 7
        assert all(0 <= value <= 255 for value in face.vadugwi_affinity)


def test_default_contemplation_face_ids_are_unique() -> None:
    ids = [face.id for face in DEFAULT_CONTEMPLATION_FACES]
    assert len(ids) == len(set(ids))


def test_build_default_contemplation_corpus_samples_idle_face() -> None:
    corpus = build_default_contemplation_corpus(rng=random.Random(7))
    assert isinstance(corpus, PromptCorpus)
    face = corpus.sample(_idle_trigger())
    assert face is not None
    assert face in DEFAULT_CONTEMPLATION_FACES


def test_default_contemplation_face_can_be_contemplated() -> None:
    physics = EmotionalPhysics(SoulState())
    face = DEFAULT_CONTEMPLATION_FACES[0]
    result = physics.contemplate(face)
    assert result.score.patterns == ("CONTEMPLATION",)
    assert result.delta != (0, 0, 0, 0, 0, 0, 0)
