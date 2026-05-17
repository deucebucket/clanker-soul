"""Reference plugin loader (#54 Slice 3)."""

from __future__ import annotations

import importlib
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from clanker_soul.plugins.manifest import PluginManifest, parse_manifest_json
from clanker_soul.plugins.master import MasterEntry, overlay_settings, parse_plugins_toml

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LoadedPlugin:
    manifest: PluginManifest
    instance: Any
    settings_resolved: dict[str, Any]


class PluginLoader:
    """Reference loader for the manifest-folder plugin standard."""

    def __init__(
        self,
        plugin_dir: str | Path,
        master_file: str | Path,
        host_version: str,
    ) -> None:
        self.plugin_dir = Path(plugin_dir)
        self.master_file = Path(master_file)
        self.host_version = host_version
        self._source_dirs: dict[str, Path] = {}

    def discover(self) -> list[PluginManifest]:
        """Walk ``plugin_dir`` and parse every ``plugin.json`` found."""

        self._source_dirs = {}
        if not self.plugin_dir.exists():
            return []
        manifests: list[PluginManifest] = []
        for manifest_path in sorted(self.plugin_dir.glob("*/plugin.json")):
            manifest = parse_manifest_json(manifest_path)
            if manifest is None:
                continue
            if manifest.name in self._source_dirs:
                logger.warning(
                    "duplicate plugin name %s; skipping %s", manifest.name, manifest_path
                )
                continue
            self._source_dirs[manifest.name] = manifest_path.parent
            manifests.append(manifest)
        return manifests

    def load_enabled(self) -> list[LoadedPlugin]:
        """Load enabled, compatible plugins in dependency-safe order."""

        manifests = {manifest.name: manifest for manifest in self.discover()}
        master = parse_plugins_toml(self.master_file)

        enabled: dict[str, tuple[PluginManifest, MasterEntry]] = {}
        for name, manifest in manifests.items():
            entry = master.get(name)
            if entry is None or not entry.enabled:
                continue
            if not _compatible(manifest.compatible_with, self.host_version):
                logger.warning(
                    "plugin %s skipped: incompatible with host_version=%s",
                    name,
                    self.host_version,
                )
                continue
            missing_env = [key for key in manifest.requires_env if not os.environ.get(key)]
            if missing_env:
                logger.warning(
                    "plugin %s missing required env vars: %s", name, ", ".join(missing_env)
                )
            enabled[name] = (manifest, entry)

        enabled = self._drop_missing_dependencies(enabled)
        ordered = self._toposort(enabled)

        loaded: list[LoadedPlugin] = []
        for manifest, entry in ordered:
            try:
                instance = self._load_entrypoint(manifest)
            except Exception:
                logger.exception("plugin %s failed to load; skipping", manifest.name)
                continue
            loaded.append(
                LoadedPlugin(
                    manifest=manifest,
                    instance=instance,
                    settings_resolved=overlay_settings(manifest, entry),
                )
            )
        return loaded

    def _drop_missing_dependencies(
        self,
        enabled: dict[str, tuple[PluginManifest, MasterEntry]],
    ) -> dict[str, tuple[PluginManifest, MasterEntry]]:
        changed = True
        kept = dict(enabled)
        while changed:
            changed = False
            for name, (manifest, _entry) in list(kept.items()):
                missing = [dep for dep in manifest.depends_on if dep not in kept]
                if missing:
                    logger.warning(
                        "plugin %s skipped: missing enabled dependencies %s", name, missing
                    )
                    del kept[name]
                    changed = True
        return kept

    def _toposort(
        self,
        enabled: dict[str, tuple[PluginManifest, MasterEntry]],
    ) -> list[tuple[PluginManifest, MasterEntry]]:
        ordered: list[tuple[PluginManifest, MasterEntry]] = []
        temporary: set[str] = set()
        permanent: set[str] = set()

        def visit(name: str) -> None:
            if name in permanent:
                return
            if name in temporary:
                raise ValueError(f"plugin dependency cycle at {name}")
            temporary.add(name)
            manifest, _entry = enabled[name]
            for dep in sorted(manifest.depends_on, key=lambda d: enabled[d][1].priority):
                visit(dep)
            temporary.remove(name)
            permanent.add(name)
            ordered.append(enabled[name])

        for name in sorted(enabled, key=lambda n: (enabled[n][1].priority, n)):
            try:
                visit(name)
            except ValueError as e:
                logger.warning("%s; skipping all enabled plugins", e)
                return []
        return ordered

    def _load_entrypoint(self, manifest: PluginManifest) -> Any:
        module_name, attr_name = manifest.entrypoint.split(":", 1)
        source_dir = self._source_dirs[manifest.name]
        previous_module = sys.modules.pop(module_name, None)
        sys.path.insert(0, str(source_dir))
        try:
            importlib.invalidate_caches()
            module = importlib.import_module(module_name)
        finally:
            try:
                sys.path.remove(str(source_dir))
            except ValueError:
                pass
            if previous_module is not None:
                sys.modules[module_name] = previous_module
            else:
                sys.modules.pop(module_name, None)
        target: Any = module
        for part in attr_name.split("."):
            target = getattr(target, part)
        return target() if callable(target) else target


def _compatible(requirement: str, host_version: str) -> bool:
    if not requirement.strip():
        return True
    clauses = _version_clauses(requirement)
    if not clauses:
        return True
    host = _version_tuple(host_version)
    if host is None:
        logger.warning("host_version %r is not semver-like; treating as incompatible", host_version)
        return False
    for op, version in clauses:
        target = _version_tuple(version)
        if target is None:
            return False
        if op == ">=" and not (host >= target):
            return False
        if op == ">" and not (host > target):
            return False
        if op == "<=" and not (host <= target):
            return False
        if op == "<" and not (host < target):
            return False
        if op == "==" and not (host == target):
            return False
    return True


def _version_clauses(requirement: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for raw in requirement.replace(",", " ").split():
        for op in (">=", "<=", "==", ">", "<"):
            if raw.startswith(op):
                out.append((op, raw[len(op) :]))
                break
    return out


def _version_tuple(value: str) -> tuple[int, int, int] | None:
    raw = value.strip()
    if " " in raw:
        raw = raw.split()[-1]
    parts = raw.split(".")
    if len(parts) < 2:
        return None
    try:
        major = int(parts[0])
        minor = int(parts[1])
        patch = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        return None
    return (major, minor, patch)
