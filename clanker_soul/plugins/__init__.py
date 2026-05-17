"""Plugin manifest specification helpers."""

from clanker_soul.plugins.manifest import (
    STANDARD_PLUGIN_KINDS,
    PluginManifest,
    manifest_from_dict,
    parse_manifest_json,
)
from clanker_soul.plugins.loader import LoadedPlugin, PluginLoader
from clanker_soul.plugins.master import MasterEntry, overlay_settings, parse_plugins_toml

__all__ = [
    "STANDARD_PLUGIN_KINDS",
    "LoadedPlugin",
    "PluginManifest",
    "PluginLoader",
    "MasterEntry",
    "manifest_from_dict",
    "overlay_settings",
    "parse_manifest_json",
    "parse_plugins_toml",
]
