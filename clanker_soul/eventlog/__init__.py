"""``clanker_soul.eventlog`` — durable per-event log for the UI to read.

Re-exports the public surface of the three submodules:
  - :py:mod:`.records` — :py:class:`IngestRecord`, :py:class:`PulseRecord`
  - :py:mod:`.protocol` — :py:class:`EventLog`, :py:class:`NullEventLog`
  - :py:mod:`.sqlite` — :py:class:`SqliteEventLog`

``from clanker_soul.eventlog import X`` keeps working unchanged."""

from clanker_soul.eventlog.protocol import EventLog, NullEventLog
from clanker_soul.eventlog.records import IngestRecord, PulseRecord
from clanker_soul.eventlog.sqlite import SqliteEventLog

__all__ = [
    "IngestRecord",
    "PulseRecord",
    "EventLog",
    "NullEventLog",
    "SqliteEventLog",
]
