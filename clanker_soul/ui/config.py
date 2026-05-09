"""Config panel data + write helpers.

The panel lets operators tune every :py:class:`PhysicsConfig` field
and every personality :py:class:`SoulState` dim live. Writes go through
the existing :py:class:`ConfigOverrides` from Phase 1 (#4); the UI is
just glue.

Per-field metadata (range, step, description) is defined here rather
than on the dataclass because slider UIs need explicit ranges that
exceed the source-of-truth defaults. Adding new fields means adding a
metadata entry here and the slider auto-renders.
"""

from __future__ import annotations

from dataclasses import dataclass

from clanker_soul.overrides import ConfigOverrides, OverrideBundle
from clanker_soul.physics import PhysicsConfig
from clanker_soul.soul import SoulState


@dataclass(frozen=True)
class FieldMeta:
    name: str
    min: float
    max: float
    step: float
    description: str
    is_int: bool = False


# PhysicsConfig sliders. Ranges chosen to span useful + extreme values
# without forcing absurd defaults.
PHYSICS_FIELDS: tuple[FieldMeta, ...] = (
    FieldMeta(
        "blend_alpha",
        0.0,
        1.0,
        0.01,
        "Fraction of an event that displaces mood per hit. Higher = more reactive.",
    ),
    FieldMeta(
        "mood_decay_half_life_base",
        60.0,
        3600.0,
        30.0,
        "Mood decay half-life in seconds. Lower = quicker recovery.",
    ),
    FieldMeta(
        "armor_max", 0.0, 1.0, 0.01, "Maximum fraction of weight that soul-armor can absorb."
    ),
    FieldMeta(
        "dim_resilience_max",
        0.0,
        1.0,
        0.01,
        "Per-dim soul-pull cap. Higher = mood snaps back to soul harder.",
    ),
    FieldMeta(
        "mood_prime_factor",
        0.0,
        0.5,
        0.01,
        "How strongly the previous mood tints the next score. 0 disables.",
    ),
    FieldMeta(
        "soul_drift_per_hour", 0.0, 0.01, 0.0001, "Hourly drift rate of soul toward sustained mood."
    ),
    FieldMeta(
        "soul_drift_min_distance",
        0.0,
        30.0,
        0.5,
        "Don't drift soul if mood is within this distance.",
    ),
    FieldMeta(
        "breach_threshold",
        10.0,
        100.0,
        1.0,
        "|mood-soul| above which heavy events leak to soul (breach).",
    ),
    FieldMeta(
        "heavy_threshold", 0.3, 1.0, 0.01, "Event weight above which patterns count as 'heavy'."
    ),
    FieldMeta(
        "breach_delta",
        0.0,
        0.3,
        0.005,
        "Max fraction of one heavy event that goes straight to soul.",
    ),
    FieldMeta("healing_rate", 0.0, 0.005, 0.0001, "W/V per-hour drift when nourishment dominates."),
    FieldMeta("wounding_rate", 0.0, 0.005, 0.0001, "W/V per-hour drift when trauma dominates."),
    FieldMeta(
        "trauma_pressure_floor", 0.0, 50.0, 1.0, "Trauma load above which soul actively loses W/V."
    ),
)

# Personality soul fields (V/A/D/U/G/W/I). Bookkeeping fields excluded.
SOUL_FIELDS: tuple[FieldMeta, ...] = (
    FieldMeta("v", 0, 255, 1, "Valence — 0 negative, 128 neutral, 255 positive.", is_int=True),
    FieldMeta("a", 0, 255, 1, "Arousal — 0 calm, 255 intense.", is_int=True),
    FieldMeta("d", 0, 255, 1, "Dominance — 0 helpless, 128 balanced, 255 in-control.", is_int=True),
    FieldMeta(
        "u", 0, 255, 1, "Urgency — intensity, not polarity. 0 none, 255 critical.", is_int=True
    ),
    FieldMeta("g", 0, 255, 1, "Gravity — 0 crushing, 128 grounded, 255 floating.", is_int=True),
    FieldMeta("w", 0, 255, 1, "self-Worth — 0 shattered, 128 stable, 255 strong.", is_int=True),
    FieldMeta("i", 0, 255, 1, "Intent — 0 withdraw, 128 neutral, 255 control.", is_int=True),
)


# Field name → FieldMeta lookups
PHYSICS_FIELD_NAMES = frozenset(f.name for f in PHYSICS_FIELDS)
SOUL_FIELD_NAMES = frozenset(f.name for f in SOUL_FIELDS)


@dataclass(frozen=True)
class FieldRow:
    """One row in the rendered config panel: a slider plus its current
    value (override-or-default) plus whether it's currently overridden."""

    meta: FieldMeta
    current: float
    default: float
    is_overridden: bool


@dataclass(frozen=True)
class ConfigView:
    """Everything the config panel template needs."""

    agent_id: str
    physics_rows: tuple[FieldRow, ...]
    soul_rows: tuple[FieldRow, ...]
    bundle: OverrideBundle


def _physics_default() -> PhysicsConfig:
    return PhysicsConfig()


def _soul_default() -> SoulState:
    return SoulState()


def build_config_view(
    overrides: ConfigOverrides,
    agent_id: str,
) -> ConfigView:
    """Read the current override bundle and assemble per-field rows."""
    bundle = overrides.get(agent_id)
    physics_default = _physics_default()
    soul_default = _soul_default()

    physics_rows = tuple(
        FieldRow(
            meta=meta,
            current=float(bundle.physics.get(meta.name, getattr(physics_default, meta.name))),
            default=float(getattr(physics_default, meta.name)),
            is_overridden=meta.name in bundle.physics,
        )
        for meta in PHYSICS_FIELDS
    )
    soul_rows = tuple(
        FieldRow(
            meta=meta,
            current=float(bundle.soul.get(meta.name, getattr(soul_default, meta.name))),
            default=float(getattr(soul_default, meta.name)),
            is_overridden=meta.name in bundle.soul,
        )
        for meta in SOUL_FIELDS
    )
    return ConfigView(
        agent_id=agent_id,
        physics_rows=physics_rows,
        soul_rows=soul_rows,
        bundle=bundle,
    )


def coerce_value(meta: FieldMeta, raw: str) -> float | int:
    """Convert a form-submitted string to the right numeric type for
    this field. Validates the range — out-of-range raises ValueError."""
    val = float(raw)
    if not (meta.min <= val <= meta.max):
        raise ValueError(f"{meta.name}={val} is out of range [{meta.min}, {meta.max}]")
    return int(val) if meta.is_int else val


def apply_field_override(
    overrides: ConfigOverrides,
    agent_id: str,
    section: str,
    field_name: str,
    value: str,
) -> None:
    """Section is 'physics' or 'soul'. Validates the field name + value
    range. Raises ValueError on bad input."""
    if section == "physics":
        meta = next((f for f in PHYSICS_FIELDS if f.name == field_name), None)
        if meta is None:
            raise ValueError(f"unknown physics field: {field_name}")
        coerced = coerce_value(meta, value)
        overrides.update(agent_id, physics={field_name: coerced})
    elif section == "soul":
        meta = next((f for f in SOUL_FIELDS if f.name == field_name), None)
        if meta is None:
            raise ValueError(f"unknown soul field: {field_name}")
        coerced = coerce_value(meta, value)
        overrides.update(agent_id, soul={field_name: coerced})
    else:
        raise ValueError(f"unknown section: {section}")


def clear_field_override(
    overrides: ConfigOverrides,
    agent_id: str,
    section: str,
    field_name: str,
) -> None:
    """Drop one field from the override bundle, leaving others
    untouched."""
    bundle = overrides.get(agent_id)
    if section == "physics":
        new_physics = {k: v for k, v in bundle.physics.items() if k != field_name}
        overrides.set(agent_id, physics=new_physics, soul=bundle.soul)
    elif section == "soul":
        new_soul = {k: v for k, v in bundle.soul.items() if k != field_name}
        overrides.set(agent_id, physics=bundle.physics, soul=new_soul)
    else:
        raise ValueError(f"unknown section: {section}")


__all__ = [
    "FieldMeta",
    "FieldRow",
    "ConfigView",
    "PHYSICS_FIELDS",
    "SOUL_FIELDS",
    "build_config_view",
    "apply_field_override",
    "clear_field_override",
    "coerce_value",
]
