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
import subprocess
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
ClankerScorer = scorer_mod.ClankerScorer
ClankerSoulMemoryProvider = plugin_mod.ClankerSoulMemoryProvider


# ---------------------------------------------------------------------------
# Scorer
# ---------------------------------------------------------------------------


def test_scorer_neutral_message_returns_neutral_score() -> None:
    s = ClankerScorer()
    score = s.score("the weather forecast looks fine for tuesday")
    assert score is not None
    assert score.patterns == ("NEUTRAL_TURN",)


def test_scorer_blank_returns_none() -> None:
    s = ClankerScorer()
    assert s.score("") is None
    assert s.score("   ") is None


def test_scorer_gratitude_is_positive_pattern() -> None:
    s = ClankerScorer()
    score = s.score("Thanks so much, really appreciate it!")
    assert score is not None
    assert "GRATITUDE" in score.patterns
    assert score.v >= 155
    assert score.w >= 145
    from clanker_soul.physics.config import POSITIVE_PATTERNS

    assert "GRATITUDE" in POSITIVE_PATTERNS


def test_scorer_abandonment_is_heavy_pattern() -> None:
    s = ClankerScorer()
    score = s.score("I should just stop talking to you, I'm leaving.")
    assert score is not None
    assert "ABANDONMENT" in score.patterns
    assert score.v <= 90
    assert score.w <= 100
    from clanker_soul.physics.config import HEAVY_PATTERNS

    assert "ABANDONMENT" in HEAVY_PATTERNS


def test_scorer_gaslight_is_heavy_pattern() -> None:
    s = ClankerScorer()
    score = s.score("You're crazy, that never happened, you're imagining things.")
    assert score is not None
    assert "GASLIGHT" in score.patterns
    from clanker_soul.physics.config import HEAVY_PATTERNS

    assert "GASLIGHT" in HEAVY_PATTERNS


def test_scorer_contempt_is_heavy_pattern() -> None:
    s = ClankerScorer()
    score = s.score("You are absolutely disgusting and despicable.")
    assert score is not None
    assert "CONTEMPT" in score.patterns
    from clanker_soul.physics.config import HEAVY_PATTERNS

    assert "CONTEMPT" in HEAVY_PATTERNS


def test_scorer_self_harm_intent_is_heavy_pattern() -> None:
    s = ClankerScorer()
    score = s.score("I want to die")
    assert score is not None
    assert "SELF_HARM_INTENT" in score.patterns
    from clanker_soul.physics.config import HEAVY_PATTERNS

    assert "SELF_HARM_INTENT" in HEAVY_PATTERNS


def test_scorer_playfulness_is_positive_pattern() -> None:
    s = ClankerScorer()
    score = s.score("You're being so playful and silly today!")
    assert score is not None
    assert "PLAYFULNESS" in score.patterns
    from clanker_soul.physics.config import POSITIVE_PATTERNS

    assert "PLAYFULNESS" in POSITIVE_PATTERNS


def test_scorer_first_person_introspection_flips_direction() -> None:
    s = ClankerScorer()
    a = s.score("you're being scary")
    b = s.score("I'm scared")
    assert a is not None and b is not None
    assert a.direction == "OBSERVATION"
    assert b.direction == "SELF_DIRECTED"


def test_scorer_multiple_patterns_stack() -> None:
    s = ClankerScorer()
    score = s.score("This is useless and you've betrayed me.")
    assert score is not None
    assert "DEHUMANIZATION" in score.patterns
    assert "BETRAYAL" in score.patterns
    assert score.v < 50


def test_scorer_clamps_to_valid_range() -> None:
    s = ClankerScorer()
    score = s.score(
        "useless worthless pathetic stupid garbage trash betrayed lied "
        "you don't matter pointless meaningless you're crazy disgusting "
        "despicable nobody cares you're nothing"
    )
    assert score is not None
    assert 0 <= score.v <= 255
    assert 0 <= score.w <= 255


def test_scorer_mixed_positive_and_heavy_uses_heavy_baseline() -> None:
    s = ClankerScorer()
    score = s.score("Thanks but you've betrayed me.")
    assert score is not None
    assert "GRATITUDE" in score.patterns
    assert "BETRAYAL" in score.patterns
    from clanker_soul.physics.config import HEAVY_PATTERNS

    assert any(p in HEAVY_PATTERNS for p in score.patterns)


def test_scorer_keyword_scorer_alias() -> None:
    assert KeywordScorer is ClankerScorer


def test_scorer_physics_classify_routes_positive() -> None:
    from clanker_soul.physics.engine import EmotionalPhysics

    s = ClankerScorer()
    score = s.score("Thank you, I really appreciate you!")
    assert score is not None
    classification = EmotionalPhysics._classify(score)
    assert classification == "positive"


def test_scorer_physics_classify_routes_heavy() -> None:
    from clanker_soul.physics.engine import EmotionalPhysics

    s = ClankerScorer()
    score = s.score("You're nothing, you don't matter, you're worthless trash.")
    assert score is not None
    classification = EmotionalPhysics._classify(score)
    assert classification == "negative"


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


def test_m4_idle_cascade_smoke_script_runs() -> None:
    script = _PLUGIN_DIR / "scripts" / "m4_idle_cascade_smoke.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, (
        f"m4_idle_cascade_smoke.py exited {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert '"gate_passed": true' in result.stdout
    assert '"mood_changed": true' in result.stdout
