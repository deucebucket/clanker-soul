from __future__ import annotations

from pathlib import Path

from clanker_soul import PluginLoader


def test_reference_plugin_loads_from_examples(tmp_path) -> None:
    examples = Path(__file__).parents[2] / "examples" / "plugins"
    master = tmp_path / "plugins.toml"
    master.write_text(
        """
        [plugins.hello_world]
        enabled = true
        priority = 10
        settings = { greeting = "howdy" }
        """,
        encoding="utf-8",
    )

    loaded = PluginLoader(examples, master, "1.0.0").load_enabled()

    assert len(loaded) == 1
    assert loaded[0].manifest.name == "hello_world"
    assert loaded[0].settings_resolved == {"greeting": "howdy"}
    assert loaded[0].instance.greet("operator") == "hello, operator"
