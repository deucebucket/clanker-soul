"""Tests for integrations/hermes/ that don't require hermes-agent installed.

The plugin module itself imports `agent.memory_provider.MemoryProvider`
with a try/except fallback to `object` — so it loads cleanly without
hermes. Tests here cover:

- The keyword scorer's lexicon + dim deltas
- The provider's lifecycle hooks (initialize, on_turn_start,
  system_prompt_block, handle_tool_call, shutdown)

Live LLM round-trip evidence is in
integrations/hermes/EVIDENCE.md, captured manually against
DeepSeek V4 Flash via OpenRouter — that's not in CI (no LLM in the
loop) but is part of the release contract.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


# Add integrations/hermes/ to the import path so we can load the plugin
# without symlinking it into a hermes-agent tree.
_PLUGIN_DIR = Path(__file__).parent.parent / "integrations" / "hermes"
sys.path.insert(0, str(_PLUGIN_DIR))

# Import after the path mutation. The plugin module also has a fallback
# import that resolves `MemoryProvider` to `object` when hermes is
# absent, which is exactly what we want for these tests.
import importlib  # noqa: E402
import importlib.util  # noqa: E402

# Force clean reload of stale cache from earlier runs.
for _k in list(sys.modules):
    if _k in ("scorer", "clanker_soul_hermes_plugin"):
        sys.modules.pop(_k, None)

# Load both modules explicitly by file path. The plugin dir is not a
# package on disk (no setup.py — it gets symlinked into hermes), so we
# can't `import` it via dotted name.
_scorer_spec = importlib.util.spec_from_file_location(
    "scorer",
    str(_PLUGIN_DIR / "scorer.py"),
)
scorer_mod = importlib.util.module_from_spec(_scorer_spec)
sys.modules["scorer"] = scorer_mod
_scorer_spec.loader.exec_module(scorer_mod)

_plugin_spec = importlib.util.spec_from_file_location(
    "clanker_soul_hermes_plugin",
    str(_PLUGIN_DIR / "__init__.py"),
    submodule_search_locations=[str(_PLUGIN_DIR)],
)
plugin_mod = importlib.util.module_from_spec(_plugin_spec)
sys.modules["clanker_soul_hermes_plugin"] = plugin_mod
_plugin_spec.loader.exec_module(plugin_mod)

KeywordScorer = scorer_mod.KeywordScorer
ClankerSoulMemoryProvider = plugin_mod.ClankerSoulMemoryProvider


# ---------------------------------------------------------------------------
# Scorer
# ---------------------------------------------------------------------------


def test_scorer_neutral_message_returns_neutral_score() -> None:
    s = KeywordScorer()
    score = s.score("the weather forecast looks fine for tuesday")
    assert score is not None
    assert score.patterns == ("NEUTRAL_TURN",)


def test_scorer_blank_returns_none() -> None:
    s = KeywordScorer()
    assert s.score("") is None
    assert s.score("   ") is None


def test_scorer_gratitude_lifts_v_and_w() -> None:
    s = KeywordScorer()
    score = s.score("Thanks so much, really appreciate it!")
    assert score is not None
    assert "GRATITUDE" in score.patterns
    assert score.v > 128  # baseline is 128
    assert score.w > 128


def test_scorer_abandonment_drops_v_and_w_hard() -> None:
    s = KeywordScorer()
    score = s.score("I should just stop talking to you, I'm leaving.")
    assert score is not None
    assert "ABANDONMENT" in score.patterns
    assert score.v < 100  # significant drop
    assert score.w < 110


def test_scorer_first_person_introspection_flips_direction() -> None:
    s = KeywordScorer()
    a = s.score("you're being scary")
    b = s.score("I'm scared")
    assert a is not None and b is not None
    assert a.direction == "OBSERVATION"
    assert b.direction == "SELF_DIRECTED"


def test_scorer_multiple_patterns_stack() -> None:
    s = KeywordScorer()
    score = s.score("This is useless and you've betrayed me.")
    assert score is not None
    # Both DEHUMANIZATION and BETRAYAL should fire
    assert "DEHUMANIZATION" in score.patterns
    assert "BETRAYAL" in score.patterns
    # Stacked deltas push V much further down than either alone
    assert score.v < 50


def test_scorer_clamps_to_valid_range() -> None:
    s = KeywordScorer()
    # Cluster of negatives that would push below 0 if unclamped
    score = s.score(
        "useless worthless pathetic stupid garbage trash betrayed lied "
        "you don't matter pointless meaningless"
    )
    assert score is not None
    assert 0 <= score.v <= 255
    assert 0 <= score.w <= 255


# ---------------------------------------------------------------------------
# Provider lifecycle (no LLM, no hermes)
# ---------------------------------------------------------------------------


def test_provider_name_and_availability() -> None:
    p = ClankerSoulMemoryProvider()
    assert p.name() == "clanker-soul"
    assert p.is_available() is True


def test_provider_initialize_creates_db(tmp_path) -> None:
    db = tmp_path / "ts.db"
    p = ClankerSoulMemoryProvider()
    p._db_path = db  # bypass env-var path for deterministic test
    p.initialize(session_id="alice")
    assert db.exists()
    p.shutdown()


def test_provider_system_prompt_block_empty_before_events(tmp_path) -> None:
    p = ClankerSoulMemoryProvider()
    p._db_path = tmp_path / "ts.db"
    p.initialize(session_id="alice")
    block = p.system_prompt_block()
    # Without events, state_context is short but not empty.
    # The wrapper format ("[INTERNAL EMOTIONAL STATE...]") may still appear.
    assert isinstance(block, str)
    p.shutdown()


def test_provider_on_turn_start_then_system_prompt_block_is_populated(tmp_path) -> None:
    p = ClankerSoulMemoryProvider()
    p._db_path = tmp_path / "ts.db"
    p.initialize(session_id="alice")
    p.on_turn_start(turn_number=1, message="You're useless and I'm leaving you.")
    block = p.system_prompt_block()
    assert "[INTERNAL EMOTIONAL STATE" in block
    assert "ABANDONMENT" in block or "DEHUMANIZATION" in block
    p.shutdown()


def test_provider_state_tool_returns_snapshot(tmp_path) -> None:
    p = ClankerSoulMemoryProvider()
    p._db_path = tmp_path / "ts.db"
    p.initialize(session_id="alice")
    p.on_turn_start(turn_number=1, message="Thanks, that was great!")
    raw = p.handle_tool_call("clanker_soul_state", {})
    snap = json.loads(raw)
    assert "soul" in snap
    assert "mood" in snap
    assert "felt_state" in snap
    assert "VADUGWI" not in snap["felt_state"]
    assert snap["capability_level"] == "UNRESTRICTED"
    p.shutdown()


def test_provider_apply_preset_tool(tmp_path) -> None:
    p = ClankerSoulMemoryProvider()
    p._db_path = tmp_path / "ts.db"
    p.initialize(session_id="alice")
    raw = p.handle_tool_call("clanker_soul_apply_preset", {"preset": "stoic"})
    res = json.loads(raw)
    assert res.get("applied") == "stoic"
    p.shutdown()


def test_provider_apply_unknown_preset_returns_error(tmp_path) -> None:
    p = ClankerSoulMemoryProvider()
    p._db_path = tmp_path / "ts.db"
    p.initialize(session_id="alice")
    raw = p.handle_tool_call("clanker_soul_apply_preset", {"preset": "nonexistent"})
    res = json.loads(raw)
    assert "error" in res
    p.shutdown()


def test_provider_dashboard_url_tool(tmp_path) -> None:
    p = ClankerSoulMemoryProvider()
    p._db_path = tmp_path / "ts.db"
    p.initialize(session_id="alice")
    raw = p.handle_tool_call("clanker_soul_dashboard_url", {})
    res = json.loads(raw)
    assert "url" in res
    assert "agent_id=alice" in res["url"]
    assert "command" in res
    p.shutdown()


def test_provider_unknown_tool_returns_error(tmp_path) -> None:
    p = ClankerSoulMemoryProvider()
    p._db_path = tmp_path / "ts.db"
    p.initialize(session_id="alice")
    raw = p.handle_tool_call("totally_made_up", {})
    res = json.loads(raw)
    assert "error" in res
    p.shutdown()


def test_provider_three_tool_schemas_exposed() -> None:
    p = ClankerSoulMemoryProvider()
    schemas = p.get_tool_schemas()
    names = {s["name"] for s in schemas}
    assert names == {
        "clanker_soul_state",
        "clanker_soul_apply_preset",
        "clanker_soul_dashboard_url",
    }
    assert all("type" not in s for s in schemas)
    assert all("function" not in s for s in schemas)


def test_provider_config_schema_includes_db_path() -> None:
    p = ClankerSoulMemoryProvider()
    schema = p.get_config_schema()
    names = {f["name"] for f in schema}
    assert "db_path" in names
    assert "shared_agent_id" in names
