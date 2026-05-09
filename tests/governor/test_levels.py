"""CapabilityLevel + GovernorConfig — shape, defaults, descriptions."""
from __future__ import annotations

from clanker_soul.governor import CapabilityLevel, GovernorConfig


def test_levels_are_an_ordered_gradient() -> None:
    """Higher level = more restricted. Comparable via ints."""
    assert CapabilityLevel.UNRESTRICTED < CapabilityLevel.NON_DESTRUCTIVE
    assert CapabilityLevel.NON_DESTRUCTIVE < CapabilityLevel.READ_ONLY
    assert CapabilityLevel.READ_ONLY < CapabilityLevel.VOICE_ONLY
    assert CapabilityLevel.VOICE_ONLY < CapabilityLevel.CRISIS_LOCKOUT


def test_each_level_has_human_readable_description() -> None:
    for lvl in CapabilityLevel:
        assert isinstance(lvl.description, str)
        assert len(lvl.description) > 10


def test_default_config_disables_crisis_lockout() -> None:
    """Per user direction: opt-in only."""
    cfg = GovernorConfig()
    assert cfg.enable_crisis_lockout is False
