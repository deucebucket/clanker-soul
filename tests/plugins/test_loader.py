from __future__ import annotations

import json
import logging

from clanker_soul.plugins import PluginLoader


def _write_plugin(
    root,
    name: str,
    *,
    priority: int,
    enabled: bool = True,
    depends_on=(),
    compatible_with: str = "",
    requires_env=(),
    settings=None,
) -> None:
    folder = root / name
    folder.mkdir(parents=True)
    class_name = "".join(part.title() for part in name.split("_"))
    (folder / f"{name}.py").write_text(
        f"class {class_name}:\n    name = {name!r}\n    def ping(self):\n        return {name!r}\n",
        encoding="utf-8",
    )
    (folder / "plugin.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "1.0.0",
                "kind": "tool",
                "entrypoint": f"{name}:{class_name}",
                "depends_on": list(depends_on),
                "compatible_with": compatible_with,
                "requires_env": list(requires_env),
                "settings": settings or {"origin": "manifest"},
            }
        ),
        encoding="utf-8",
    )


def _write_master(root, entries: dict[str, tuple[bool, int, dict | None]]) -> None:
    lines = []
    for name, (enabled, priority, settings) in entries.items():
        lines.append(f"[plugins.{name}]")
        lines.append(f"enabled = {'true' if enabled else 'false'}")
        lines.append(f"priority = {priority}")
        if settings:
            inner = ", ".join(
                f"{key} = {value!r}".replace("'", '"') for key, value in settings.items()
            )
            lines.append(f"settings = {{ {inner} }}")
        lines.append("")
    (root / "plugins.toml").write_text("\n".join(lines), encoding="utf-8")


def test_discover_skips_invalid_manifests(tmp_path) -> None:
    plugins = tmp_path / "plugins"
    _write_plugin(plugins, "good_tool", priority=10)
    bad = plugins / "bad_tool"
    bad.mkdir()
    (bad / "plugin.json").write_text("{bad", encoding="utf-8")

    loader = PluginLoader(plugins, tmp_path / "plugins.toml", "1.0.0")

    assert [manifest.name for manifest in loader.discover()] == ["good_tool"]


def test_load_enabled_orders_dependencies_before_priority(tmp_path) -> None:
    plugins = tmp_path / "plugins"
    _write_plugin(plugins, "base_tool", priority=50)
    _write_plugin(plugins, "phone_tool", priority=10, depends_on=("base_tool",))
    _write_master(
        tmp_path,
        {
            "phone_tool": (True, 10, None),
            "base_tool": (True, 50, None),
        },
    )

    loaded = PluginLoader(plugins, tmp_path / "plugins.toml", "1.0.0").load_enabled()

    assert [item.manifest.name for item in loaded] == ["base_tool", "phone_tool"]
    assert [item.instance.ping() for item in loaded] == ["base_tool", "phone_tool"]


def test_load_enabled_applies_master_settings_overlay(tmp_path) -> None:
    plugins = tmp_path / "plugins"
    _write_plugin(
        plugins,
        "phone_tool",
        priority=10,
        settings={"default_app_timeout_ms": 8000, "tap_pause_ms": 200},
    )
    _write_master(tmp_path, {"phone_tool": (True, 10, {"default_app_timeout_ms": 12000})})

    loaded = PluginLoader(plugins, tmp_path / "plugins.toml", "1.0.0").load_enabled()

    assert loaded[0].settings_resolved == {
        "default_app_timeout_ms": 12000,
        "tap_pause_ms": 200,
    }


def test_load_enabled_skips_disabled_and_incompatible(tmp_path, caplog) -> None:
    plugins = tmp_path / "plugins"
    _write_plugin(plugins, "disabled_tool", priority=10)
    _write_plugin(plugins, "future_tool", priority=20, compatible_with="host >=2.0, <3.0")
    _write_master(
        tmp_path,
        {
            "disabled_tool": (False, 10, None),
            "future_tool": (True, 20, None),
        },
    )

    with caplog.at_level(logging.WARNING):
        loaded = PluginLoader(plugins, tmp_path / "plugins.toml", "1.0.0").load_enabled()

    assert loaded == []
    assert "incompatible" in caplog.text


def test_load_enabled_warns_for_missing_env_but_loads(tmp_path, caplog) -> None:
    plugins = tmp_path / "plugins"
    _write_plugin(plugins, "phone_tool", priority=10, requires_env=("MISSING_PHONE_ENV",))
    _write_master(tmp_path, {"phone_tool": (True, 10, None)})

    with caplog.at_level(logging.WARNING):
        loaded = PluginLoader(plugins, tmp_path / "plugins.toml", "1.0.0").load_enabled()

    assert [item.manifest.name for item in loaded] == ["phone_tool"]
    assert "missing required env vars" in caplog.text


def test_load_enabled_skips_missing_dependency(tmp_path, caplog) -> None:
    plugins = tmp_path / "plugins"
    _write_plugin(plugins, "phone_tool", priority=10, depends_on=("missing_tool",))
    _write_master(tmp_path, {"phone_tool": (True, 10, None)})

    with caplog.at_level(logging.WARNING):
        loaded = PluginLoader(plugins, tmp_path / "plugins.toml", "1.0.0").load_enabled()

    assert loaded == []
    assert "missing enabled dependencies" in caplog.text


def test_load_enabled_detects_dependency_cycle(tmp_path, caplog) -> None:
    plugins = tmp_path / "plugins"
    _write_plugin(plugins, "tool_a", priority=10, depends_on=("tool_b",))
    _write_plugin(plugins, "tool_b", priority=20, depends_on=("tool_a",))
    _write_master(
        tmp_path,
        {
            "tool_a": (True, 10, None),
            "tool_b": (True, 20, None),
        },
    )

    with caplog.at_level(logging.WARNING):
        loaded = PluginLoader(plugins, tmp_path / "plugins.toml", "1.0.0").load_enabled()

    assert loaded == []
    assert "dependency cycle" in caplog.text
