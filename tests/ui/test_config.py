"""Config panel — view builder, write helpers, /config routes."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from clanker_soul import SoulStore
from clanker_soul.overrides import ConfigOverrides
from clanker_soul.presets import ALL as PRESETS
from clanker_soul.ui.app import create_app
from clanker_soul.ui.config import (
    PHYSICS_FIELDS,
    SOUL_FIELDS,
    apply_field_override,
    build_config_view,
    clear_field_override,
    coerce_value,
)


# ---------------------------------------------------------------------------
# build_config_view — pure
# ---------------------------------------------------------------------------


def _fresh_db(tmp_path) -> str:
    db = tmp_path / "config.db"
    SoulStore(db)  # touches schema
    return str(db)


def test_view_has_all_physics_and_soul_rows(tmp_path) -> None:
    db = _fresh_db(tmp_path)
    overrides = ConfigOverrides(SoulStore.get(db))
    view = build_config_view(overrides, "alice")
    assert len(view.physics_rows) == len(PHYSICS_FIELDS)
    assert len(view.soul_rows) == len(SOUL_FIELDS)
    physics_names = {r.meta.name for r in view.physics_rows}
    assert physics_names == {f.name for f in PHYSICS_FIELDS}
    soul_names = {r.meta.name for r in view.soul_rows}
    assert soul_names == {f.name for f in SOUL_FIELDS}


def test_view_no_overrides_means_current_equals_default(tmp_path) -> None:
    db = _fresh_db(tmp_path)
    overrides = ConfigOverrides(SoulStore.get(db))
    view = build_config_view(overrides, "alice")
    for row in view.physics_rows:
        assert row.current == row.default
        assert row.is_overridden is False
    for row in view.soul_rows:
        assert row.current == row.default
        assert row.is_overridden is False


def test_view_reflects_existing_override(tmp_path) -> None:
    db = _fresh_db(tmp_path)
    overrides = ConfigOverrides(SoulStore.get(db))
    overrides.update("alice", physics={"blend_alpha": 0.42}, soul={"v": 200})
    view = build_config_view(overrides, "alice")
    blend = next(r for r in view.physics_rows if r.meta.name == "blend_alpha")
    assert blend.current == 0.42
    assert blend.is_overridden is True
    v_row = next(r for r in view.soul_rows if r.meta.name == "v")
    assert v_row.current == 200
    assert v_row.is_overridden is True
    # untouched fields stay at default
    other = next(r for r in view.physics_rows if r.meta.name == "armor_max")
    assert other.is_overridden is False


# ---------------------------------------------------------------------------
# coerce_value — range validation + type coercion
# ---------------------------------------------------------------------------


def test_coerce_int_field_returns_int() -> None:
    meta = next(f for f in SOUL_FIELDS if f.name == "v")
    assert coerce_value(meta, "180") == 180
    assert isinstance(coerce_value(meta, "180"), int)


def test_coerce_float_field_returns_float() -> None:
    meta = next(f for f in PHYSICS_FIELDS if f.name == "blend_alpha")
    assert coerce_value(meta, "0.5") == 0.5
    assert isinstance(coerce_value(meta, "0.5"), float)


def test_coerce_rejects_out_of_range_high() -> None:
    meta = next(f for f in SOUL_FIELDS if f.name == "v")
    with pytest.raises(ValueError, match="out of range"):
        coerce_value(meta, "999")


def test_coerce_rejects_out_of_range_low() -> None:
    meta = next(f for f in PHYSICS_FIELDS if f.name == "blend_alpha")
    with pytest.raises(ValueError, match="out of range"):
        coerce_value(meta, "-0.1")


# ---------------------------------------------------------------------------
# apply_field_override / clear_field_override
# ---------------------------------------------------------------------------


def test_apply_physics_field_override(tmp_path) -> None:
    db = _fresh_db(tmp_path)
    overrides = ConfigOverrides(SoulStore.get(db))
    apply_field_override(overrides, "alice", "physics", "blend_alpha", "0.7")
    bundle = overrides.get("alice")
    assert bundle.physics["blend_alpha"] == 0.7


def test_apply_soul_field_override_coerces_int(tmp_path) -> None:
    db = _fresh_db(tmp_path)
    overrides = ConfigOverrides(SoulStore.get(db))
    apply_field_override(overrides, "alice", "soul", "v", "200")
    bundle = overrides.get("alice")
    assert bundle.soul["v"] == 200
    assert isinstance(bundle.soul["v"], int)


def test_apply_unknown_section_raises(tmp_path) -> None:
    db = _fresh_db(tmp_path)
    overrides = ConfigOverrides(SoulStore.get(db))
    with pytest.raises(ValueError, match="unknown section"):
        apply_field_override(overrides, "alice", "ghosts", "v", "100")


def test_apply_unknown_field_raises(tmp_path) -> None:
    db = _fresh_db(tmp_path)
    overrides = ConfigOverrides(SoulStore.get(db))
    with pytest.raises(ValueError, match="unknown physics field"):
        apply_field_override(overrides, "alice", "physics", "bogus", "0.5")


def test_clear_one_field_leaves_others(tmp_path) -> None:
    db = _fresh_db(tmp_path)
    overrides = ConfigOverrides(SoulStore.get(db))
    overrides.update("alice", physics={"blend_alpha": 0.7, "armor_max": 0.4})
    clear_field_override(overrides, "alice", "physics", "blend_alpha")
    bundle = overrides.get("alice")
    assert "blend_alpha" not in bundle.physics
    assert bundle.physics["armor_max"] == 0.4


# ---------------------------------------------------------------------------
# /config GET — full page + partial
# ---------------------------------------------------------------------------


def test_config_page_renders_sliders(tmp_path) -> None:
    db = _fresh_db(tmp_path)
    overrides = ConfigOverrides(SoulStore.get(db))
    overrides.update("alice", physics={"blend_alpha": 0.5})  # ensure agent exists
    app = create_app(db)
    with TestClient(app) as client:
        res = client.get("/config?agent_id=alice")
    assert res.status_code == 200
    assert "blend_alpha" in res.text
    assert 'type="range"' in res.text
    # presets visible
    assert "child" in res.text
    assert "adult" in res.text


def test_config_partial_returns_only_panel(tmp_path) -> None:
    db = _fresh_db(tmp_path)
    overrides = ConfigOverrides(SoulStore.get(db))
    overrides.update("alice", physics={"blend_alpha": 0.5})
    app = create_app(db)
    with TestClient(app) as client:
        res = client.get("/config?agent_id=alice&partial=1")
    assert res.status_code == 200
    assert "<html" not in res.text.lower()
    assert 'type="range"' in res.text


# ---------------------------------------------------------------------------
# /config POST — override / clear / preset
# ---------------------------------------------------------------------------


def test_post_override_persists(tmp_path) -> None:
    db = _fresh_db(tmp_path)
    app = create_app(db)
    with TestClient(app) as client:
        res = client.post(
            "/config/override",
            data={
                "agent_id": "alice",
                "section": "physics",
                "field": "blend_alpha",
                "value": "0.65",
            },
        )
    assert res.status_code == 200
    overrides = ConfigOverrides(SoulStore.get(db))
    assert overrides.get("alice").physics["blend_alpha"] == 0.65


def test_post_override_rejects_out_of_range(tmp_path) -> None:
    db = _fresh_db(tmp_path)
    app = create_app(db)
    with TestClient(app) as client:
        res = client.post(
            "/config/override",
            data={"agent_id": "alice", "section": "soul", "field": "v", "value": "9999"},
        )
    assert res.status_code == 400


def test_post_clear_field_removes_one_only(tmp_path) -> None:
    db = _fresh_db(tmp_path)
    overrides = ConfigOverrides(SoulStore.get(db))
    overrides.update("alice", physics={"blend_alpha": 0.7, "armor_max": 0.4})
    app = create_app(db)
    with TestClient(app) as client:
        res = client.post(
            "/config/clear",
            data={"agent_id": "alice", "section": "physics", "field": "blend_alpha"},
        )
    assert res.status_code == 200
    bundle = ConfigOverrides(SoulStore.get(db)).get("alice")
    assert "blend_alpha" not in bundle.physics
    assert bundle.physics["armor_max"] == 0.4


def test_post_clear_all_wipes_bundle(tmp_path) -> None:
    db = _fresh_db(tmp_path)
    overrides = ConfigOverrides(SoulStore.get(db))
    overrides.update("alice", physics={"blend_alpha": 0.7}, soul={"v": 220})
    app = create_app(db)
    with TestClient(app) as client:
        res = client.post("/config/clear", data={"agent_id": "alice"})
    assert res.status_code == 200
    bundle = ConfigOverrides(SoulStore.get(db)).get("alice")
    assert bundle.physics == {}
    assert bundle.soul == {}


def test_post_preset_applies_bundle(tmp_path) -> None:
    db = _fresh_db(tmp_path)
    app = create_app(db)
    with TestClient(app) as client:
        res = client.post(
            "/config/preset",
            data={"agent_id": "alice", "preset": "child"},
        )
    assert res.status_code == 200
    bundle = ConfigOverrides(SoulStore.get(db)).get("alice")
    # CHILD overrides soul + soul_drift_per_hour
    assert bundle.soul["v"] == PRESETS["child"].soul.v
    assert bundle.physics["soul_drift_per_hour"] == PRESETS["child"].config.soul_drift_per_hour


def test_post_preset_rejects_unknown(tmp_path) -> None:
    db = _fresh_db(tmp_path)
    app = create_app(db)
    with TestClient(app) as client:
        res = client.post(
            "/config/preset",
            data={"agent_id": "alice", "preset": "doesnotexist"},
        )
    assert res.status_code == 400
