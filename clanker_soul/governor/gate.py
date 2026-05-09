"""``CapabilityGate`` — runtime enforcement of CapabilityProfile policy.

Lives next to :py:class:`GovernorConfig` because it consumes
:py:class:`CapabilityProfile` and emits gating decisions, but it
holds *runtime state* (rate-limit buckets) that the immutable config
can't carry.

Engines / hosts query the gate before dispatching an action:

    decision = gate.evaluate(action, level)
    if decision.permitted:
        host.dispatch_action(action)
    else:
        log_gated(action, decision.reason)

The gate never raises on policy violation — it returns a decision
the caller can interpret. That keeps gating composable; an operator
who wants to do something other than \"silently suppress\" (e.g.
substitute a withdraw, log to a different sink) inspects the
decision and acts on it.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from threading import Lock

from clanker_soul.governor.levels import (
    CapabilityLevel,
    CapabilityProfile,
    GovernorConfig,
)


_PUBLIC_ACTION_KINDS: frozenset[str] = frozenset(
    {
        "post_public",
        "comment_reply",
    }
)


@dataclass(frozen=True)
class GateDecision:
    """The result of evaluating an action against the current level's
    profile. ``permitted`` says whether dispatch should proceed.
    ``reason`` is a stable enum-like string suitable for logs and
    metrics. ``profile`` is the profile the decision was made against
    so callers can introspect what the agent CAN do at this level."""

    permitted: bool
    reason: str  # \"ok\" / \"action_kind_blocked\" / \"rate_limited\" / \"tool_blocked\" / \"user_message_blocked\"
    profile: CapabilityProfile


class CapabilityGate:
    """Runtime gating + rate-limit enforcement.

    Constructed once per agent. Thread-safe — uses a lock around the
    rate-limit bucket because the engine + host may be on different
    threads. State is in-memory only; gate state resets on engine
    restart, which is fine for the learning-tool framing (rate
    limits are about preventing immediate spam, not enforcing
    long-term policy)."""

    def __init__(self, config: GovernorConfig) -> None:
        self._config = config
        self._public_timestamps: deque[float] = deque()
        self._lock = Lock()

    @property
    def config(self) -> GovernorConfig:
        return self._config

    def profile_for(self, level: CapabilityLevel) -> CapabilityProfile:
        """Return the configured profile for ``level``. Falls back to a
        permissive default if the operator's custom dict is missing
        this level (defensive — better to allow than crash)."""
        return self._config.capability_profiles.get(
            level,
            CapabilityProfile(
                allowed_action_kinds=frozenset(
                    {
                        "direct_message",
                        "post_public",
                        "comment_reply",
                        "browse_topic",
                        "withdraw",
                        "tool_invocation",
                    }
                ),
                description=f"permissive fallback (no profile for {level.name})",
            ),
        )

    def evaluate(
        self,
        action_kind: str,
        level: CapabilityLevel,
        *,
        tool_name: str | None = None,
        is_user_message: bool = False,
    ) -> GateDecision:
        """Decide whether an action of ``action_kind`` is permitted at
        ``level``. Returns a :py:class:`GateDecision`.

        ``tool_name``: only inspected for ``tool_invocation`` actions.
        ``is_user_message``: when True, the gate also checks
        ``profile.user_message_allowed``. Hosts should set this for
        DMs targeting the human operator.

        Public actions (``post_public`` / ``comment_reply``) hit the
        rate limiter when the profile sets a positive
        ``public_action_rate_limit_per_hour`` (or when
        ``enable_public_action_lockout`` is True and the profile
        carries any limit). On rate-limit grant, the timestamp is
        recorded; on rate-limit deny, no timestamp is recorded so
        retries the next minute can succeed.
        """
        profile = self.profile_for(level)

        if is_user_message and not profile.user_message_allowed:
            return GateDecision(
                permitted=False,
                reason="user_message_blocked",
                profile=profile,
            )

        if action_kind not in profile.allowed_action_kinds:
            return GateDecision(
                permitted=False,
                reason="action_kind_blocked",
                profile=profile,
            )

        if action_kind == "tool_invocation" and profile.allowed_tool_names is not None:
            if tool_name is None or tool_name not in profile.allowed_tool_names:
                return GateDecision(
                    permitted=False,
                    reason="tool_blocked",
                    profile=profile,
                )

        if action_kind in _PUBLIC_ACTION_KINDS:
            limit = profile.public_action_rate_limit_per_hour
            # When enable_public_action_lockout is True and the
            # profile carries a positive limit, enforce it. When the
            # flag is False, only enforce limits the operator
            # explicitly set on the profile.
            if limit > 0:
                if not self._claim_public_slot(limit):
                    return GateDecision(
                        permitted=False,
                        reason="rate_limited",
                        profile=profile,
                    )

        return GateDecision(permitted=True, reason="ok", profile=profile)

    def _claim_public_slot(self, limit: int) -> bool:
        """Atomically check + record one public action against the
        rolling 60-min window. Returns True on grant, False on deny."""
        now = time.monotonic()
        cutoff = now - 3600.0
        with self._lock:
            # Drop entries older than 1h.
            while self._public_timestamps and self._public_timestamps[0] < cutoff:
                self._public_timestamps.popleft()
            if len(self._public_timestamps) >= limit:
                return False
            self._public_timestamps.append(now)
            return True

    def public_actions_in_window(self) -> int:
        """Inspector for tests + observability — how many public
        actions are in the current rolling 60-min window."""
        now = time.monotonic()
        cutoff = now - 3600.0
        with self._lock:
            while self._public_timestamps and self._public_timestamps[0] < cutoff:
                self._public_timestamps.popleft()
            return len(self._public_timestamps)


__all__ = ["CapabilityGate", "GateDecision"]
