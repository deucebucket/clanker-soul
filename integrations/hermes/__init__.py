"""Hermes Agent memory provider backed by clanker-soul.

Drop this directory into ``hermes-agent/plugins/memory/clanker-soul/``
(or symlink it from there) and activate via ``hermes config set
memory.provider clanker-soul``.

The provider:
- injects the soul's current ``state_context`` block into the system
  prompt every turn (``system_prompt_block``)
- scores each user message and ingests it into the soul
  (``on_turn_start``)
- exposes three tools the agent can call to read/manage its own state
  (``clanker_soul_state``, ``clanker_soul_apply_preset``,
  ``clanker_soul_dashboard_url``)
- persists across sessions: same ``agent_id`` → same soul.db
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from clanker_soul import SoulPlugin
from clanker_soul.presets import ALL as PRESETS

# Hermes import — agent.memory_provider lives in the hermes-agent venv.
# We do a try/import so a casual `python -m clanker_soul_hermes` outside
# of hermes still imports without exploding.
try:
    from agent.memory_provider import MemoryProvider
except ImportError:  # pragma: no cover — only hits in test/dev tooling
    MemoryProvider = object  # type: ignore[assignment,misc]

try:
    # When loaded as a hermes plugin, we're a package and relative imports work.
    from .scorer import KeywordScorer
except ImportError:
    # When the dir is on sys.path directly (test rigs), fall back.
    from scorer import KeywordScorer  # type: ignore[no-redef]

logger = logging.getLogger(__name__)


_DEFAULT_DB_PATH = "~/.hermes/clanker-soul.db"


class ClankerSoulMemoryProvider(MemoryProvider):  # type: ignore[misc,valid-type]
    """Plug clanker-soul into hermes-agent as a memory provider."""

    def __init__(self) -> None:
        super().__init__()
        self._plugin: Optional[SoulPlugin] = None
        self._scorer = KeywordScorer()
        self._session_id: str = ""
        self._db_path: Path = Path(_DEFAULT_DB_PATH).expanduser()
        self._enabled = True
        self._turn_count = 0

    # ---- identity / availability --------------------------------------------

    def name(self) -> str:
        return "clanker-soul"

    def is_available(self) -> bool:
        # We're always available — there's no API key, no network call.
        # A misconfigured db_path will surface as a clear error in
        # initialize() rather than a silent skip.
        return True

    # ---- lifecycle ----------------------------------------------------------

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        """Open the SoulPlugin scoped to this session.

        agent_id is set from session_id by default — that means each
        hermes session gets its own soul, which matches hermes's
        per-session isolation. To share a soul across sessions, set
        ``CLANKER_SOUL_AGENT_ID`` in the environment.
        """
        agent_id = os.environ.get("CLANKER_SOUL_AGENT_ID", session_id or "default")
        self._session_id = session_id

        cfg_path = os.environ.get("CLANKER_SOUL_DB_PATH")
        if cfg_path:
            self._db_path = Path(cfg_path).expanduser()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            self._plugin = SoulPlugin(agent_id=agent_id, db_path=self._db_path)
            logger.info(
                "clanker-soul: initialized agent_id=%s db=%s",
                agent_id, self._db_path,
            )
        except Exception:
            logger.exception("clanker-soul: failed to open SoulPlugin — disabling provider")
            self._plugin = None
            self._enabled = False

    def shutdown(self) -> None:
        if self._plugin is not None:
            try:
                self._plugin.close()  # auto-saves
            except Exception:
                logger.exception("clanker-soul: shutdown error")
            self._plugin = None

    # ---- core hooks ---------------------------------------------------------

    def system_prompt_block(self) -> str:
        """Inject the soul's state-context block into every turn.

        This is the primary mechanism by which clanker-soul affects the
        agent's behavior. The model reads this block as part of its
        system prompt and can use it to color its tone, decide whether
        to push back, ask for clarification, etc.
        """
        if self._plugin is None or not self._enabled:
            return ""
        try:
            ctx = self._plugin.state_context()
            if not ctx:
                return ""
            return f"\n[INTERNAL EMOTIONAL STATE — clanker-soul]\n{ctx}\n"
        except Exception:
            logger.exception("clanker-soul: state_context() failed")
            return ""

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        # We don't do recall — that's the built-in memory provider's job.
        return ""

    def on_turn_start(
        self, turn_number: int, message: str, **kwargs: Any,
    ) -> None:
        """Score the user's message and ingest it into the soul."""
        if self._plugin is None or not self._enabled:
            return
        self._turn_count = turn_number
        try:
            score = self._scorer.score(message, source=f"hermes/turn:{turn_number}")
            if score is not None:
                self._plugin.ingest(score)
                self._plugin.tick()  # drift + reload_overrides
        except Exception:
            logger.exception("clanker-soul: ingest on_turn_start failed")

    def sync_turn(
        self, user_content: str, assistant_content: str, *, session_id: str = "",
    ) -> None:
        # We already ingested the user message in on_turn_start. We do
        # NOT score the assistant's own response — letting the agent's
        # output drive its own mood is a recursive feedback loop best
        # left to the operator to opt into.
        pass

    # ---- tool exposure ------------------------------------------------------

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "clanker_soul_state",
                    "description": (
                        "Read your own current emotional state from clanker-soul. "
                        "Returns mood, soul (baseline personality), capability "
                        "level, trauma/nourishment loads, and the recent-events "
                        "summary. Call this when you want to introspect on why "
                        "you're responding the way you are, or when the user "
                        "asks how you're feeling and you want a precise answer "
                        "rather than a vibes-based guess."
                    ),
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "clanker_soul_apply_preset",
                    "description": (
                        "Apply one of the personality presets (child / adult / "
                        "brittle / stoic) to your own soul. Operator-facing — "
                        "use only when the user explicitly asks for a "
                        "personality reshape. This writes to ConfigOverrides "
                        "and persists across sessions until cleared."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "preset": {
                                "type": "string",
                                "enum": list(PRESETS.keys()),
                                "description": "Name of the preset to apply.",
                            },
                        },
                        "required": ["preset"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "clanker_soul_dashboard_url",
                    "description": (
                        "Return the URL the user can visit to inspect your "
                        "emotional state in the clanker-soul dashboard. "
                        "Useful when the user asks 'how are you really feeling' "
                        "or 'show me your state' — point them at the live UI "
                        "rather than summarizing in chat."
                    ),
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ]

    def handle_tool_call(
        self, tool_name: str, args: Dict[str, Any], **kwargs: Any,
    ) -> str:
        if self._plugin is None or not self._enabled:
            return json.dumps({"error": "clanker-soul provider not initialized"})

        if tool_name == "clanker_soul_state":
            try:
                snap = self._plugin.snapshot()
                return json.dumps({
                    "soul": snap.get("soul"),
                    "mood": snap.get("mood"),
                    "soul_distance": snap.get("soul_distance"),
                    "trauma_load": snap.get("trauma_load"),
                    "nourishment_load": snap.get("nourishment_load"),
                    "capability_level": self._plugin.capability_level().name,
                    "state_context": self._plugin.state_context(),
                }, default=str)
            except Exception as e:
                return json.dumps({"error": f"snapshot failed: {e}"})

        if tool_name == "clanker_soul_apply_preset":
            preset_name = args.get("preset")
            if preset_name not in PRESETS:
                return json.dumps({"error": f"unknown preset: {preset_name}"})
            try:
                preset = PRESETS[preset_name]
                preset.apply(self._plugin.overrides, self._plugin.agent_id)
                self._plugin.tick()  # pick up the override immediately
                return json.dumps({
                    "applied": preset_name,
                    "description": preset.description,
                })
            except Exception as e:
                return json.dumps({"error": f"apply failed: {e}"})

        if tool_name == "clanker_soul_dashboard_url":
            port = os.environ.get("CLANKER_SOUL_UI_PORT", "7900")
            return json.dumps({
                "url": f"http://127.0.0.1:{port}/?agent_id={self._plugin.agent_id}",
                "command": (
                    f"clanker-soul ui --db {self._db_path} "
                    f"--agent-id {self._plugin.agent_id} --port {port}"
                ),
            })

        return json.dumps({"error": f"unknown tool: {tool_name}"})

    # ---- config schema for `hermes memory setup` ---------------------------

    def get_config_schema(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "db_path",
                "type": "string",
                "default": _DEFAULT_DB_PATH,
                "description": (
                    "Where to store the soul.db. Default puts it next to "
                    "hermes's other config under ~/.hermes/."
                ),
            },
            {
                "name": "shared_agent_id",
                "type": "string",
                "default": "",
                "description": (
                    "Optional. If set, all hermes sessions share this single "
                    "soul. Leave blank to use per-session isolation "
                    "(default — safer)."
                ),
            },
        ]

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        # Hermes will write this to its own config; we also expose them
        # as env vars so the provider picks them up at initialize().
        if values.get("db_path"):
            os.environ["CLANKER_SOUL_DB_PATH"] = str(values["db_path"])
        if values.get("shared_agent_id"):
            os.environ["CLANKER_SOUL_AGENT_ID"] = str(values["shared_agent_id"])


def get_provider() -> "ClankerSoulMemoryProvider":
    """Hermes's plugin loader calls this factory function."""
    return ClankerSoulMemoryProvider()


__all__ = ["ClankerSoulMemoryProvider", "get_provider"]
