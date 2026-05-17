"""Plugin manifest data model and JSON parser (#54).

The manifest layer is intentionally dependency-free: hosts can use it
without pulling in packaging libraries or TOML parsers. Invalid manifests
soft-fail through ``parse_manifest_json`` so a single broken plugin folder
does not prevent the host from booting.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

logger = logging.getLogger(__name__)

STANDARD_PLUGIN_KINDS: frozenset[str] = frozenset(
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

_REQUIRED_FIELDS: frozenset[str] = frozenset({"name", "version", "kind", "entrypoint"})
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\."
    r"(0|[1-9]\d*)\."
    r"(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?"
    r"(?:\+[0-9A-Za-z.-]+)?$"
)
_ENTRYPOINT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_.]*$")


@dataclass(frozen=True)
class PluginManifest:
    """Validated ``plugin.json`` metadata.

    ``settings`` is exposed as a read-only mapping; sequence fields are
    normalized to tuples so the dataclass is safe to pass around as an
    immutable manifest record.
    """

    name: str
    version: str
    kind: str
    entrypoint: str
    description: str = ""
    compatible_with: str = ""
    requires_env: tuple[str, ...] = ()
    requires_packages: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    settings: Mapping[str, Any] = field(default_factory=dict)
    permissions: tuple[str, ...] = ()
    license: str = ""
    homepage: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "requires_env", tuple(self.requires_env))
        object.__setattr__(self, "requires_packages", tuple(self.requires_packages))
        object.__setattr__(self, "depends_on", tuple(self.depends_on))
        object.__setattr__(self, "permissions", tuple(self.permissions))
        object.__setattr__(self, "settings", MappingProxyType(dict(self.settings)))


def parse_manifest_json(path: str | Path) -> PluginManifest | None:
    """Parse ``path`` as a plugin manifest.

    Returns ``None`` and logs a warning for malformed JSON, missing files,
    missing required fields, or validation failures. This is the host-facing
    soft-fail path: callers can discover every plugin directory and skip
    the bad ones without aborting boot.
    """

    manifest_path = Path(path)
    try:
        with manifest_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("invalid plugin manifest %s: %s", manifest_path, e)
        return None

    try:
        return manifest_from_dict(data)
    except ValueError as e:
        logger.warning("invalid plugin manifest %s: %s", manifest_path, e)
        return None


def manifest_from_dict(data: Mapping[str, Any]) -> PluginManifest:
    """Build a validated :class:`PluginManifest` from decoded JSON data.

    Raises ``ValueError`` on schema violations. Use ``parse_manifest_json``
    when discovering plugin folders and you want soft-fail behavior.
    """

    if not isinstance(data, Mapping):
        raise ValueError("manifest root must be a JSON object")

    missing = sorted(_REQUIRED_FIELDS - data.keys())
    if missing:
        raise ValueError(f"missing required field(s): {', '.join(missing)}")

    name = _required_string(data, "name")
    if not _NAME_RE.match(name):
        raise ValueError("name must match ^[a-z][a-z0-9_]*$")

    version = _required_string(data, "version")
    if not _SEMVER_RE.match(version):
        raise ValueError("version must be semver like X.Y.Z")

    kind = _required_string(data, "kind")
    if kind not in STANDARD_PLUGIN_KINDS:
        raise ValueError(f"kind must be one of {sorted(STANDARD_PLUGIN_KINDS)}")

    entrypoint = _required_string(data, "entrypoint")
    if not _ENTRYPOINT_RE.match(entrypoint):
        raise ValueError("entrypoint must be module:attribute")

    settings = data.get("settings", {})
    if not isinstance(settings, Mapping):
        raise ValueError("settings must be a JSON object")

    return PluginManifest(
        name=name,
        version=version,
        kind=kind,
        entrypoint=entrypoint,
        description=_optional_string(data, "description"),
        compatible_with=_optional_string(data, "compatible_with"),
        requires_env=_string_tuple(data, "requires_env"),
        requires_packages=_string_tuple(data, "requires_packages"),
        depends_on=_string_tuple(data, "depends_on", validate_names=True),
        settings=dict(settings),
        permissions=_string_tuple(data, "permissions"),
        license=_optional_string(data, "license"),
        homepage=_optional_string(data, "homepage"),
    )


def _required_string(data: Mapping[str, Any], key: str) -> str:
    value = data[key]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _optional_string(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _string_tuple(
    data: Mapping[str, Any],
    key: str,
    *,
    validate_names: bool = False,
) -> tuple[str, ...]:
    if key not in data:
        return ()
    value = data.get(key)
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a list of strings")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{key} must be a list of non-empty strings")
        if validate_names and not _NAME_RE.match(item):
            raise ValueError(f"{key} entries must match ^[a-z][a-z0-9_]*$")
        out.append(item)
    return tuple(out)
