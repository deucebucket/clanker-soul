"""Master ``plugins.toml`` parser and settings overlay (#54 Slice 2)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from clanker_soul.plugins.manifest import PluginManifest

logger = logging.getLogger(__name__)

_SECTION_RE = re.compile(r"^\[plugins\.([a-z][a-z0-9_]*)\]$")
_KEY_VALUE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+)$")


@dataclass(frozen=True)
class MasterEntry:
    """One ``[plugins.<name>]`` entry from ``plugins.toml``."""

    enabled: bool = False
    priority: int = 100
    settings: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "settings", MappingProxyType(dict(self.settings)))


def parse_plugins_toml(path: str | Path) -> dict[str, MasterEntry]:
    """Parse the v1 ``plugins.toml`` master file.

    The supported shape is intentionally narrow and matches the issue #54
    spec: sections named ``[plugins.<name>]`` with ``enabled``, ``priority``,
    and optional inline ``settings = { ... }``. Missing files soft-fail to an
    empty mapping so hosts can boot with no plugins enabled.
    """

    master_path = Path(path)
    try:
        text = master_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as e:
        logger.warning("failed to read plugins master file %s: %s", master_path, e)
        return {}

    return _parse_plugins_toml_text(text, source=str(master_path))


def overlay_settings(manifest: PluginManifest, master_entry: MasterEntry | None) -> dict[str, Any]:
    """Merge operator settings over manifest defaults."""

    settings = dict(manifest.settings)
    if master_entry is not None:
        settings.update(master_entry.settings)
    return settings


def _parse_plugins_toml_text(text: str, *, source: str) -> dict[str, MasterEntry]:
    entries: dict[str, dict[str, Any]] = {}
    current: str | None = None

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = _strip_comment(raw_line).strip()
        if not line:
            continue

        section = _SECTION_RE.match(line)
        if section:
            current = section.group(1)
            entries.setdefault(current, {})
            continue

        if current is None:
            logger.warning(
                "plugins master %s:%s ignores value outside a plugin section", source, lineno
            )
            continue

        kv = _KEY_VALUE_RE.match(line)
        if kv is None:
            logger.warning("plugins master %s:%s ignores malformed line", source, lineno)
            continue

        key, value_text = kv.group(1), kv.group(2).strip()
        try:
            value = _parse_value(value_text)
        except ValueError as e:
            logger.warning("plugins master %s:%s ignores %s: %s", source, lineno, key, e)
            continue

        if key not in {"enabled", "priority", "settings"}:
            logger.warning("plugins master %s:%s ignores unknown key %s", source, lineno, key)
            continue
        entries[current][key] = value

    out: dict[str, MasterEntry] = {}
    for name, raw in entries.items():
        enabled = raw.get("enabled", False)
        priority = raw.get("priority", 100)
        settings = raw.get("settings", {})
        if not isinstance(enabled, bool):
            logger.warning("plugins master %s: plugin %s enabled must be boolean", source, name)
            enabled = False
        if not isinstance(priority, int) or isinstance(priority, bool):
            logger.warning("plugins master %s: plugin %s priority must be integer", source, name)
            priority = 100
        if not isinstance(settings, Mapping):
            logger.warning("plugins master %s: plugin %s settings must be table", source, name)
            settings = {}
        out[name] = MasterEntry(enabled=enabled, priority=priority, settings=dict(settings))
    return out


def _strip_comment(line: str) -> str:
    in_string = False
    escaped = False
    out: list[str] = []
    for ch in line:
        if ch == "\\" and in_string and not escaped:
            escaped = True
            out.append(ch)
            continue
        if ch == '"' and not escaped:
            in_string = not in_string
        if ch == "#" and not in_string:
            break
        out.append(ch)
        escaped = False
    return "".join(out)


def _parse_value(text: str) -> Any:
    if text == "true":
        return True
    if text == "false":
        return False
    if text.startswith('"') and text.endswith('"'):
        return text[1:-1]
    if text.startswith("{") and text.endswith("}"):
        return _parse_inline_table(text[1:-1])
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError as e:
        raise ValueError(f"unsupported value {text!r}") from e


def _parse_inline_table(text: str) -> dict[str, Any]:
    if not text.strip():
        return {}
    out: dict[str, Any] = {}
    for part in _split_inline_items(text):
        kv = _KEY_VALUE_RE.match(part.strip())
        if kv is None:
            raise ValueError("settings entries must be key = value")
        out[kv.group(1)] = _parse_value(kv.group(2).strip())
    return out


def _split_inline_items(text: str) -> list[str]:
    items: list[str] = []
    current: list[str] = []
    in_string = False
    escaped = False
    for ch in text:
        if ch == "\\" and in_string and not escaped:
            escaped = True
            current.append(ch)
            continue
        if ch == '"' and not escaped:
            in_string = not in_string
        if ch == "," and not in_string:
            items.append("".join(current))
            current = []
        else:
            current.append(ch)
        escaped = False
    if current:
        items.append("".join(current))
    return items
