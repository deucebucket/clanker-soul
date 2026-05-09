"""``CorpusStore`` — SQLite-backed durable storage for ``PromptFace``s
and per-agent ``face_recency`` (M3.3).

Reuses the :py:class:`SoulStore` connection + write lock so a UI
process and an agent process share a single handle. Soft-fail writes
follow the same invariant as :py:class:`SqliteEventLog`: a transient
DB hiccup must warn-and-continue, never raise into the engine.

Public surface:

  * :py:class:`CorpusStore` — face CRUD, recency upserts, agent-scoped
    recency reads, plus a small ``replace_all`` helper used when the
    ``SoulPlugin`` is constructed with ``replace_corpus=True``.
  * :py:class:`PersistentRecencyLog` — drop-in replacement for the
    in-memory :py:class:`RecencyLog`. Preloads from disk; ``note_fired``
    upserts the row. Reads remain dict-fast (no per-sample query).

Why a thin wrapper rather than methods on :py:class:`SoulStore`: the
soul store knows nothing about prompt faces today, and adding face
schema there would couple two otherwise-independent persistence
domains. ``CorpusStore`` follows the same pattern as
:py:class:`SqliteEventLog` and :py:class:`ConfigOverrides`.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from typing import Iterable

from clanker_soul.pulse.corpus import (
    PromptFace,
    RecencyLog,
    VadugwiPredicate,
)
from clanker_soul.soul import SoulStore

logger = logging.getLogger(__name__)


# ── Serialization helpers ───────────────────────────────────────────────


def _face_to_row(face: PromptFace, source: str, created_at: float) -> tuple:
    """Encode a :py:class:`PromptFace` for INSERT into ``prompt_corpus``."""
    return (
        face.id,
        json.dumps(sorted(face.trigger_kinds)),
        json.dumps([asdict(p) for p in face.vadugwi_predicates]),
        json.dumps(sorted(face.situation_tags)),
        face.situation_match,
        face.memory_anchor,
        int(face.cooldown_seconds),
        float(face.base_weight),
        face.motif,
        face.template,
        json.dumps(sorted(face.branch_keys)),
        source,
        float(created_at),
    )


def _row_to_face(row: tuple) -> PromptFace:
    """Decode a ``prompt_corpus`` row into a :py:class:`PromptFace`.

    Raises ValueError on malformed JSON so the caller can drop the bad
    row and continue (we don't want one corrupt face row to nuke the
    whole corpus load).
    """
    (
        face_id,
        trigger_kinds_json,
        predicates_json,
        situation_tags_json,
        situation_match,
        memory_anchor,
        cooldown_seconds,
        base_weight,
        motif,
        template,
        branch_keys_json,
    ) = row
    trigger_kinds = frozenset(json.loads(trigger_kinds_json))
    predicate_dicts = json.loads(predicates_json)
    predicates = tuple(
        VadugwiPredicate(
            dim=p["dim"], op=p["op"], value=int(p["value"]),
            layer=p.get("layer", "mood"),
        )
        for p in predicate_dicts
    )
    situation_tags = frozenset(json.loads(situation_tags_json))
    branch_keys = frozenset(json.loads(branch_keys_json))
    return PromptFace(
        id=face_id,
        trigger_kinds=trigger_kinds,
        vadugwi_predicates=predicates,
        situation_tags=situation_tags,
        situation_match=situation_match,
        memory_anchor=memory_anchor,
        cooldown_seconds=int(cooldown_seconds),
        base_weight=float(base_weight),
        motif=motif,
        template=template,
        branch_keys=branch_keys,
    )


# ── CorpusStore ─────────────────────────────────────────────────────────


class CorpusStore:
    """SQLite-backed durable storage for prompt faces + per-agent recency.

    All writes are soft-fail: a DB hiccup warns and returns instead of
    raising, mirroring :py:class:`SqliteEventLog`. Reads are not
    soft-fail — surfacing read failures lets the UI/tests notice schema
    drift instead of silently degrading.
    """

    def __init__(self, store: SoulStore) -> None:
        self._store = store

    # ------------------------------------------------------------------
    # Face CRUD
    # ------------------------------------------------------------------

    def save_face(
        self, face: PromptFace, *, source: str = "default", created_at: float | None = None,
    ) -> None:
        """INSERT OR REPLACE the face row.

        ``source`` distinguishes default-corpus rows from host-injected
        and operator-edited rows; surfaced for analysis but not used by
        the sampler.
        """
        import time
        ts = float(created_at) if created_at is not None else time.time()
        try:
            with self._store.lock:
                self._store.connection.execute(
                    """
                    INSERT OR REPLACE INTO prompt_corpus (
                        id, trigger_kinds, vadugwi_predicates,
                        situation_tags, situation_match, memory_anchor,
                        cooldown_seconds, base_weight, motif, template,
                        branch_keys, source, created_at, retired_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?, NULL)
                    """,
                    _face_to_row(face, source, ts),
                )
                self._store.connection.commit()
        except Exception as e:
            logger.warning("corpus save_face failed for %r (%s) — continuing", face.id, e)

    def save_faces(
        self, faces: Iterable[PromptFace], *, source: str = "default",
    ) -> None:
        """Bulk save. Each row uses the same ``source``; create_at is
        the current wall-clock time at call-site."""
        import time
        now = time.time()
        rows = []
        for face in faces:
            try:
                rows.append(_face_to_row(face, source, now))
            except Exception as e:
                logger.warning(
                    "corpus encode failed for face %r (%s) — skipping",
                    getattr(face, "id", "?"), e,
                )
        if not rows:
            return
        try:
            with self._store.lock:
                self._store.connection.executemany(
                    """
                    INSERT OR REPLACE INTO prompt_corpus (
                        id, trigger_kinds, vadugwi_predicates,
                        situation_tags, situation_match, memory_anchor,
                        cooldown_seconds, base_weight, motif, template,
                        branch_keys, source, created_at, retired_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?, NULL)
                    """,
                    rows,
                )
                self._store.connection.commit()
        except Exception as e:
            logger.warning("corpus save_faces failed (%s) — continuing", e)

    def load_faces(self) -> tuple[PromptFace, ...]:
        """Return all non-retired faces, ordered by id for deterministic
        iteration. Rows that fail to decode are logged and skipped — one
        bad face must not silence the whole corpus."""
        with self._store.lock:
            rows = self._store.connection.execute(
                """
                SELECT id, trigger_kinds, vadugwi_predicates,
                       situation_tags, situation_match, memory_anchor,
                       cooldown_seconds, base_weight, motif, template,
                       branch_keys
                FROM prompt_corpus
                WHERE retired_at IS NULL
                ORDER BY id
                """,
            ).fetchall()
        out: list[PromptFace] = []
        for row in rows:
            try:
                out.append(_row_to_face(row))
            except Exception as e:
                logger.warning(
                    "corpus row %r failed to decode (%s) — skipping",
                    row[0] if row else "?", e,
                )
        return tuple(out)

    def retire_face(self, face_id: str, *, retired_at: float | None = None) -> None:
        """Soft-delete: mark the row retired. Retired rows stay in the
        DB for audit / undo but are excluded from :py:meth:`load_faces`."""
        import time
        ts = float(retired_at) if retired_at is not None else time.time()
        try:
            with self._store.lock:
                self._store.connection.execute(
                    "UPDATE prompt_corpus SET retired_at = ? WHERE id = ? AND retired_at IS NULL",
                    (ts, face_id),
                )
                self._store.connection.commit()
        except Exception as e:
            logger.warning("corpus retire_face %r failed (%s) — continuing", face_id, e)

    def replace_all(
        self, faces: Iterable[PromptFace], *, source: str = "host",
    ) -> None:
        """Hard-delete every existing face row, then insert ``faces``.

        Used by ``SoulPlugin(replace_corpus=True)`` — operators who want
        a corpus that's *only* their faces, no defaults. Distinct from
        ``retire_face`` which preserves history; ``replace_all`` is a
        clean slate."""
        try:
            with self._store.lock:
                self._store.connection.execute("DELETE FROM prompt_corpus")
                self._store.connection.commit()
        except Exception as e:
            logger.warning("corpus replace_all DELETE failed (%s) — continuing", e)
            return
        self.save_faces(faces, source=source)

    def count_faces(self, *, include_retired: bool = False) -> int:
        with self._store.lock:
            if include_retired:
                row = self._store.connection.execute(
                    "SELECT COUNT(*) FROM prompt_corpus",
                ).fetchone()
            else:
                row = self._store.connection.execute(
                    "SELECT COUNT(*) FROM prompt_corpus WHERE retired_at IS NULL",
                ).fetchone()
        return int(row[0])

    # ------------------------------------------------------------------
    # Recency
    # ------------------------------------------------------------------

    def note_fired(self, agent_id: str, face_id: str, ts: float) -> None:
        """Upsert ``face_recency`` for (agent_id, face_id) — last fire
        wins; fire_count increments. Soft-fail."""
        try:
            with self._store.lock:
                self._store.connection.execute(
                    """
                    INSERT INTO face_recency (agent_id, face_id, last_fired_at, fire_count)
                    VALUES (?, ?, ?, 1)
                    ON CONFLICT(agent_id, face_id) DO UPDATE SET
                        last_fired_at = excluded.last_fired_at,
                        fire_count = face_recency.fire_count + 1
                    """,
                    (agent_id, face_id, float(ts)),
                )
                self._store.connection.commit()
        except Exception as e:
            logger.warning(
                "corpus note_fired(%r, %r) failed (%s) — continuing",
                agent_id, face_id, e,
            )

    def load_recency(self, agent_id: str) -> dict[str, tuple[float, int]]:
        """Return ``{face_id: (last_fired_at, fire_count)}`` for the agent."""
        with self._store.lock:
            rows = self._store.connection.execute(
                "SELECT face_id, last_fired_at, fire_count "
                "FROM face_recency WHERE agent_id = ?",
                (agent_id,),
            ).fetchall()
        return {row[0]: (float(row[1]), int(row[2])) for row in rows}


# ── Persistent recency log ──────────────────────────────────────────────


class PersistentRecencyLog(RecencyLog):
    """Drop-in replacement for :py:class:`RecencyLog` that persists every
    ``note_fired`` to the ``face_recency`` table.

    Construction preloads the agent's existing rows so cooldowns survive
    process restart. Reads stay dict-fast — we never query per-sample.
    """

    def __init__(self, store: CorpusStore, agent_id: str) -> None:
        super().__init__()
        self._store = store
        self._agent_id = agent_id
        # Preload — this is the cross-restart cooldown bit.
        try:
            for face_id, (last_fired, count) in store.load_recency(agent_id).items():
                self.last_fired[face_id] = last_fired
                self.fire_counts[face_id] = count
        except Exception as e:
            logger.warning(
                "PersistentRecencyLog preload for %r failed (%s) — starting empty",
                agent_id, e,
            )

    def note_fired(self, face_id: str, now: float) -> None:
        super().note_fired(face_id, now)
        # Soft-fail flush. Store-side already catches its own exceptions
        # and warns; we don't double-log here.
        self._store.note_fired(self._agent_id, face_id, now)


__all__ = [
    "CorpusStore",
    "PersistentRecencyLog",
]
