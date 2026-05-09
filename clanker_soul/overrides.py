"""ConfigOverrides — live-tunable PhysicsConfig + SoulState surface.

The UI in Phase 2 needs to mutate physics knobs and soul values without
restarting the agent. This module provides the storage + apply layer.

Design:
  - Overrides are PARTIAL — only fields explicitly set are stored
  - Removing a field reverts that field to its constructor value (NOT to
    the dataclass default — the value the agent was originally
    constructed with)
  - Drift on un-overridden soul fields is preserved across reloads;
    only fields that were *previously overridden and are no longer in
    the bundle* get reset
  - Unknown keys are logged at WARNING and ignored — forward-compat
    with future PhysicsConfig fields and survives a v0.2 ↔ v0.3 schema
    skew between agent process and UI process

Concurrency: ConfigOverrides reuses the SoulStore connection + lock,
so a UI process writing an override while the agent process is
mid-tick won't tear (SQLite serializes the writes).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, fields
from datetime import datetime, timezone

from clanker_soul.physics import PhysicsConfig
from clanker_soul.soul import SoulState, SoulStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OverrideBundle:
    """Partial overrides for a single agent.

    ``physics`` and ``soul`` map field-name → desired-value. Only fields
    explicitly present in the dict are overridden; other fields use the
    constructor value (or, for soul fields that have drifted, their
    drift-current value)."""

    physics: dict
    soul: dict


def apply_overrides(
    config: PhysicsConfig,
    soul: SoulState,
    bundle: OverrideBundle,
) -> tuple[PhysicsConfig, SoulState]:
    """Pure: returns merged COPIES, leaves the inputs untouched.

    Used by tests and by hosts that want a non-mutating view. Most
    callers want :py:meth:`EmotionalPhysics.reload_overrides` instead,
    which mutates the running engine in-place and tracks the
    "previously-overridden" set so removing a field reverts cleanly."""
    physics_field_names = {f.name for f in fields(PhysicsConfig)}
    soul_field_names = {f.name for f in fields(SoulState)}

    p_kwargs = {}
    for k, v in bundle.physics.items():
        if k in physics_field_names:
            p_kwargs[k] = v
        else:
            logger.warning(
                "ignoring unknown PhysicsConfig override: %r",
                k,
            )

    s_kwargs = {}
    for k, v in bundle.soul.items():
        if k in soul_field_names:
            s_kwargs[k] = v
        else:
            logger.warning(
                "ignoring unknown SoulState override: %r",
                k,
            )

    from dataclasses import replace

    return replace(config, **p_kwargs), replace(soul, **s_kwargs)


class ConfigOverrides:
    """Reads/writes the ``config_overrides`` table from schema v0.2.

    All operations go through the shared SoulStore connection + lock
    so a UI process writing while the agent process reads doesn't
    tear."""

    def __init__(self, store: SoulStore) -> None:
        self._store = store

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get(self, agent_id: str) -> OverrideBundle:
        with self._store.lock:
            row = self._store.connection.execute(
                "SELECT physics_config_overrides, soul_overrides "
                "FROM config_overrides WHERE agent_id = ?",
                (agent_id,),
            ).fetchone()
        if row is None:
            return OverrideBundle(physics={}, soul={})
        try:
            physics_blob = json.loads(row[0]) if row[0] else {}
            soul_blob = json.loads(row[1]) if row[1] else {}
        except json.JSONDecodeError as e:
            logger.warning(
                "config_overrides row for %r is corrupt (%s) — treating as empty",
                agent_id,
                e,
            )
            return OverrideBundle(physics={}, soul={})
        return OverrideBundle(
            physics=dict(physics_blob),
            soul=dict(soul_blob),
        )

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def set(
        self,
        agent_id: str,
        *,
        physics: dict | None = None,
        soul: dict | None = None,
    ) -> None:
        """Replace the overrides for ``agent_id`` outright. Pass empty
        dicts to clear all overrides while keeping the row."""
        existing = self.get(agent_id)
        new_physics = physics if physics is not None else existing.physics
        new_soul = soul if soul is not None else existing.soul
        now = datetime.now(timezone.utc).timestamp()
        with self._store.lock:
            self._store.connection.execute(
                "INSERT OR REPLACE INTO config_overrides "
                "(agent_id, physics_config_overrides, soul_overrides, last_modified) "
                "VALUES (?, ?, ?, ?)",
                (agent_id, json.dumps(new_physics), json.dumps(new_soul), now),
            )
            self._store.connection.commit()

    def update(
        self,
        agent_id: str,
        *,
        physics: dict | None = None,
        soul: dict | None = None,
    ) -> None:
        """Merge new fields into the existing overrides. Existing fields
        not in the update are preserved (use :py:meth:`set` to replace
        outright)."""
        existing = self.get(agent_id)
        merged_physics = {**existing.physics, **(physics or {})}
        merged_soul = {**existing.soul, **(soul or {})}
        self.set(agent_id, physics=merged_physics, soul=merged_soul)

    def clear(self, agent_id: str) -> None:
        """Remove the row entirely. Equivalent to ``set(agent_id,
        physics={}, soul={})`` for read purposes, but actually drops
        the row."""
        with self._store.lock:
            self._store.connection.execute(
                "DELETE FROM config_overrides WHERE agent_id = ?",
                (agent_id,),
            )
            self._store.connection.commit()


__all__ = [
    "OverrideBundle",
    "ConfigOverrides",
    "apply_overrides",
]
