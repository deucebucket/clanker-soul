"""``EventLog`` Protocol + ``NullEventLog`` noop default.

Sinks are opt-in. Hosts that don't want logging use ``NullEventLog``
(the default everywhere); production hosts use
:py:class:`SqliteEventLog`; tests can use a list-capturing impl that
satisfies the protocol via :py:func:`isinstance` (it's
``runtime_checkable``).

**Soft-fail invariant:** implementations MUST NOT raise on write
failure. Log a warning and continue. Physics catches sink exceptions
too as defense-in-depth; not relying on that is still the contract.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from clanker_soul.eventlog.records import IngestRecord, PulseRecord


@runtime_checkable
class EventLog(Protocol):
    """Sink interface. Implementations MUST be soft-fail — a write
    failure must not raise into the caller."""

    def log_ingest(self, record: IngestRecord) -> None: ...
    def log_pulse(self, record: PulseRecord) -> None: ...


class NullEventLog:
    """No-op sink. Default for hosts that don't want logging."""

    def log_ingest(self, record: IngestRecord) -> None:  # noqa: ARG002
        return None

    def log_pulse(self, record: PulseRecord) -> None:  # noqa: ARG002
        return None


__all__ = ["EventLog", "NullEventLog"]
