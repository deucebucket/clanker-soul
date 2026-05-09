"""``_PulseRunner`` — runs a :py:class:`PulseEngine` in a background
thread on its own asyncio loop.

Hermes's :py:class:`MemoryProvider.initialize` is synchronous, but
:py:class:`PulseEngine` is an asyncio thing. To bridge: we own a
daemon thread that sets up a fresh event loop, builds the engine
inside it, runs ``engine.start()``, and idles awaiting cancellation.
Provider shutdown signals the runner to stop, then joins the thread
with a short timeout.

The runner exposes itself as the engine's :py:class:`PulseHost` —
``snapshot`` proxies to the SoulPlugin, ``dispatch_action`` invokes
the operator-supplied callback, the rest are no-ops or simple
defaults. Hosts wanting richer behavior can subclass.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
import threading
from typing import Awaitable, Callable, Optional, Union

from clanker_soul import (
    ActionOutcome,
    PulseAction,
    PulseConfig,
    PulseEngine,
    PulseTarget,
    SoulPlugin,
)

logger = logging.getLogger(__name__)


# Type alias for the operator-supplied dispatcher. Sync OR async.
PulseDispatcher = Callable[
    [PulseAction],
    Union[ActionOutcome, Awaitable[ActionOutcome]],
]


def _resolve_dispatcher_from_env() -> Optional[PulseDispatcher]:
    """Read ``CLANKER_SOUL_PULSE_DISPATCH=module.path:callable`` and
    import the callable. Returns None when the env var is unset or
    points at something that doesn't import."""
    spec = os.environ.get("CLANKER_SOUL_PULSE_DISPATCH", "").strip()
    if not spec:
        return None
    if ":" not in spec:
        logger.warning(
            "CLANKER_SOUL_PULSE_DISPATCH=%r missing ':' (expected 'module.path:callable')",
            spec,
        )
        return None
    module_path, attr = spec.split(":", 1)
    try:
        mod = importlib.import_module(module_path)
        cb = getattr(mod, attr, None)
        if not callable(cb):
            logger.warning(
                "CLANKER_SOUL_PULSE_DISPATCH=%r resolved to non-callable",
                spec,
            )
            return None
        return cb
    except Exception:
        logger.exception(
            "CLANKER_SOUL_PULSE_DISPATCH=%r failed to import",
            spec,
        )
        return None


class _NoOpDispatcher:
    """Default dispatcher when none is configured. Logs the action and
    returns delivered=False with a 'no_dispatcher_configured' note so
    the loop is observable but harmless."""

    def __call__(self, action: PulseAction) -> ActionOutcome:
        logger.info(
            "pulse fired (no dispatcher configured): kind=%s trigger=%s prompt=%r",
            action.kind,
            action.trigger.kind,
            action.prompt[:80],
        )
        return ActionOutcome(
            delivered=False,
            note="no_dispatcher_configured",
        )


class _PulseHostAdapter:
    """Implements :py:class:`PulseHost` by proxying to a SoulPlugin and
    delegating dispatch to a configurable callback."""

    def __init__(
        self,
        plugin: SoulPlugin,
        dispatcher: PulseDispatcher,
        target_factory: Callable[[], Optional[PulseTarget]] | None = None,
    ) -> None:
        self._plugin = plugin
        self._dispatcher = dispatcher
        self._target_factory = target_factory or (lambda: None)

    def snapshot(self) -> dict:
        return self._plugin.snapshot()

    def slow_drift_tick(self) -> None:
        # SoulPlugin.tick covers drift + reload_overrides.
        try:
            self._plugin.tick()
        except Exception:
            logger.exception("plugin.tick failed in slow_drift_tick")

    def most_recent_target(self) -> Optional[PulseTarget]:
        try:
            return self._target_factory()
        except Exception:
            logger.exception("target_factory raised")
            return None

    def dispatch_action(
        self,
        action: PulseAction,
    ) -> Union[ActionOutcome, Awaitable[ActionOutcome]]:
        """Hand off to the operator-supplied dispatcher. Sync or async
        — the engine handles both via ``asyncio.iscoroutine``."""
        return self._dispatcher(action)

    def due_reminders(self) -> list[dict]:
        # No host-side reminders integration in this default adapter.
        return []

    def deliver_reminder(self, target, reminder) -> None:  # noqa: ARG002
        # Default no-op — operators wanting reminders subclass.
        return None


class PulseRunner:
    """Runs a :py:class:`PulseEngine` in a daemon thread with its own
    asyncio event loop. Constructed with a SoulPlugin and an optional
    dispatcher; started via :py:meth:`start`; stopped (cleanly) via
    :py:meth:`stop`.

    Lifecycle is idempotent — repeated start/stop calls are safe."""

    def __init__(
        self,
        plugin: SoulPlugin,
        *,
        dispatcher: Optional[PulseDispatcher] = None,
        pulse_config: Optional[PulseConfig] = None,
        target_factory: Optional[Callable[[], Optional[PulseTarget]]] = None,
    ) -> None:
        self._plugin = plugin
        self._dispatcher = dispatcher or _resolve_dispatcher_from_env() or _NoOpDispatcher()
        self._pulse_config = pulse_config or PulseConfig()
        self._target_factory = target_factory
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._engine: Optional[PulseEngine] = None
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()

    @property
    def engine(self) -> Optional[PulseEngine]:
        """Inspector for tests + observability. Engine is None before
        the runner finishes initializing."""
        return self._engine

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return  # already running
        self._stop_event.clear()
        self._ready_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="clanker-soul-pulse",
            daemon=True,
        )
        self._thread.start()
        # Wait briefly for engine setup so callers can immediately
        # interact with self.engine after start() returns.
        self._ready_event.wait(timeout=2.0)

    def stop(self, *, timeout: float = 5.0) -> None:
        if self._thread is None:
            return
        self._stop_event.set()
        # Schedule the engine.stop coroutine onto the runner's loop,
        # then ALSO stop the loop itself so run_forever() returns and
        # the thread exits.
        loop = self._loop
        engine = self._engine
        if loop is not None and engine is not None and loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(engine.stop(), loop).result(
                    timeout=timeout,
                )
            except Exception:
                logger.exception("engine.stop failed during runner stop")
            # Now break run_forever() so the thread can exit.
            try:
                loop.call_soon_threadsafe(loop.stop)
            except Exception:
                logger.exception("loop.stop scheduling failed")
        self._thread.join(timeout=timeout)
        self._thread = None
        self._loop = None
        self._engine = None

    def note_outbound(self) -> None:
        """Forward to the engine's note_outbound. Safe to call from any
        thread — uses the runner's loop."""
        engine = self._engine
        loop = self._loop
        if engine is None or loop is None or not loop.is_running():
            return
        loop.call_soon_threadsafe(engine.note_outbound)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _run(self) -> None:
        """Thread entry point. Builds the loop + engine, kicks off the
        engine, idles until stop is signaled."""
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            host = _PulseHostAdapter(
                plugin=self._plugin,
                dispatcher=self._dispatcher,
                target_factory=self._target_factory,
            )
            self._engine = PulseEngine(
                host,
                config=self._pulse_config,
                event_log=self._plugin.event_log,
                agent_id=self._plugin.agent_id,
                physics=self._plugin.physics,  # closes the learning loop
            )
            self._loop.run_until_complete(self._engine.start())
            self._ready_event.set()
            # Idle the loop. The engine ticks itself on its own asyncio
            # task; we just keep the loop alive until stop is called.
            self._loop.run_forever()
        except Exception:
            logger.exception("PulseRunner thread crashed")
            self._ready_event.set()
        finally:
            try:
                if self._loop and self._loop.is_running():
                    self._loop.call_soon_threadsafe(self._loop.stop)
                # Cancel any remaining tasks before closing the loop.
                if self._loop:
                    pending = asyncio.all_tasks(loop=self._loop)
                    for task in pending:
                        task.cancel()
                    self._loop.close()
            except Exception:
                logger.exception("PulseRunner cleanup failed")


__all__ = [
    "PulseRunner",
    "PulseDispatcher",
    "_NoOpDispatcher",
    "_PulseHostAdapter",
    "_resolve_dispatcher_from_env",
]
