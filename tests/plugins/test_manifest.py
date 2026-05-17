from __future__ import annotations

import json
import logging

import pytest

from clanker_soul import PluginManifest
from clanker_soul.plugins import STANDARD_PLUGIN_KINDS, manifest_from_dict, parse_manifest_json


def _manifest(**overrides):
    data = {
        "name": "phone_tool",
        "version": "0.3.0",
        "kind": "tool",
        "entrypoint": "phone_tool:PhoneTool",
        "description": "Driver for the operator phone.",
        "compatible_with": "carl >=0.9, <0.11",
        "requires_env": ["ADB_HOST", "PHONE_DEVICE_ID"],
        "requires_packages": ["adb-shell>=0.4"],
        "depends_on": ["screenshot_tool"],
        "settings": {"default_app_timeout_ms": 8000, "tap_pause_ms": 200},
        "permissions": ["filesystem_write", "network"],
        "license": "MIT",
        "homepage": "https://example.test/plugin",
    }
    data.update(overrides)
    return data


def test_manifest_from_dict_normalizes_all_fields() -> None:
    manifest = manifest_from_dict(_manifest())

    assert manifest == PluginManifest(
        name="phone_tool",
        version="0.3.0",
        kind="tool",
        entrypoint="phone_tool:PhoneTool",
        description="Driver for the operator phone.",
        compatible_with="carl >=0.9, <0.11",
        requires_env=("ADB_HOST", "PHONE_DEVICE_ID"),
        requires_packages=("adb-shell>=0.4",),
        depends_on=("screenshot_tool",),
        settings={"default_app_timeout_ms": 8000, "tap_pause_ms": 200},
        permissions=("filesystem_write", "network"),
        license="MIT",
        homepage="https://example.test/plugin",
    )


def test_parse_manifest_json_round_trips(tmp_path) -> None:
    path = tmp_path / "plugin.json"
    path.write_text(json.dumps(_manifest()), encoding="utf-8")

    manifest = parse_manifest_json(path)

    assert manifest is not None
    assert manifest.name == "phone_tool"
    assert manifest.kind == "tool"


def test_parse_manifest_json_soft_fails_malformed_json(tmp_path, caplog) -> None:
    path = tmp_path / "plugin.json"
    path.write_text("{not valid", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        manifest = parse_manifest_json(path)

    assert manifest is None
    assert "invalid plugin manifest" in caplog.text


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"name": "PhoneTool"}, "name must match"),
        ({"version": "1.2"}, "version must be semver"),
        ({"kind": "unknown"}, "kind must be one of"),
        ({"entrypoint": "missing_colon"}, "entrypoint must be module:attribute"),
        ({"requires_env": "ADB_HOST"}, "requires_env must be a list"),
        ({"settings": []}, "settings must be a JSON object"),
        ({"depends_on": ["BadName"]}, "depends_on entries must match"),
    ],
)
def test_manifest_from_dict_validates_schema(overrides, match) -> None:
    with pytest.raises(ValueError, match=match):
        manifest_from_dict(_manifest(**overrides))


def test_manifest_missing_required_field_soft_fails(tmp_path, caplog) -> None:
    path = tmp_path / "plugin.json"
    data = _manifest()
    del data["entrypoint"]
    path.write_text(json.dumps(data), encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        manifest = parse_manifest_json(path)

    assert manifest is None
    assert "missing required field" in caplog.text


def test_settings_mapping_is_read_only() -> None:
    manifest = manifest_from_dict(_manifest())

    with pytest.raises(TypeError):
        manifest.settings["tap_pause_ms"] = 300  # type: ignore[index]


def test_standard_plugin_kinds_match_spec() -> None:
    assert STANDARD_PLUGIN_KINDS == frozenset(
        {
            "tool",
            "channel",
            "scorer",
            "corpus",
            "dispatcher",
            "motivation_pack",
            "persona",
            "integration",
        }
    )
