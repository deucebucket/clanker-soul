from __future__ import annotations

import logging

from clanker_soul.plugins import (
    MasterEntry,
    manifest_from_dict,
    overlay_settings,
    parse_plugins_toml,
)


def test_parse_plugins_toml_reads_enabled_priority_and_settings(tmp_path) -> None:
    path = tmp_path / "plugins.toml"
    path.write_text(
        """
        # Drop a plugin folder into ./plugins/, then enable it here.
        [plugins.phone_tool]
        enabled = true
        priority = 10
        settings = { default_app_timeout_ms = 12000, label = "phone" }

        [plugins.experimental_thing]
        enabled = false
        """,
        encoding="utf-8",
    )

    entries = parse_plugins_toml(path)

    assert entries["phone_tool"] == MasterEntry(
        enabled=True,
        priority=10,
        settings={"default_app_timeout_ms": 12000, "label": "phone"},
    )
    assert entries["experimental_thing"] == MasterEntry(enabled=False, priority=100)


def test_parse_plugins_toml_missing_file_returns_empty(tmp_path) -> None:
    assert parse_plugins_toml(tmp_path / "missing.toml") == {}


def test_parse_plugins_toml_soft_fails_bad_values(tmp_path, caplog) -> None:
    path = tmp_path / "plugins.toml"
    path.write_text(
        """
        [plugins.phone_tool]
        enabled = "yes"
        priority = "first"
        settings = []
        unknown = true
        """,
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING):
        entries = parse_plugins_toml(path)

    assert entries["phone_tool"] == MasterEntry()
    assert "enabled must be boolean" in caplog.text
    assert "priority must be integer" in caplog.text
    assert "ignores settings" in caplog.text
    assert "ignores unknown key unknown" in caplog.text


def test_overlay_settings_merges_master_over_manifest_defaults() -> None:
    manifest = manifest_from_dict(
        {
            "name": "phone_tool",
            "version": "0.3.0",
            "kind": "tool",
            "entrypoint": "phone_tool:PhoneTool",
            "settings": {
                "default_app_timeout_ms": 8000,
                "tap_pause_ms": 200,
            },
        }
    )
    master = MasterEntry(settings={"default_app_timeout_ms": 12000})

    assert overlay_settings(manifest, master) == {
        "default_app_timeout_ms": 12000,
        "tap_pause_ms": 200,
    }


def test_overlay_settings_handles_missing_master_entry() -> None:
    manifest = manifest_from_dict(
        {
            "name": "phone_tool",
            "version": "0.3.0",
            "kind": "tool",
            "entrypoint": "phone_tool:PhoneTool",
            "settings": {"tap_pause_ms": 200},
        }
    )

    assert overlay_settings(manifest, None) == {"tap_pause_ms": 200}
