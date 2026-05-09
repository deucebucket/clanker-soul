"""``clanker_soul.soul`` — persistent emotional state.

Re-exports:
  - :py:class:`SoulState` from :py:mod:`.state`
  - :py:class:`TraumaReservoir` / :py:class:`NourishmentReservoir`
    from :py:mod:`.reservoirs`
  - :py:class:`SoulStore` from :py:mod:`.store`
  - :py:data:`RESERVOIR_HALF_LIFE_S` / :py:data:`RESERVOIR_CAP`
    from :py:mod:`.reservoirs`

Importing ``from clanker_soul.soul import X`` keeps working for every
name that was at the top of the old monolithic ``soul.py``."""

from clanker_soul.soul.reservoirs import (
    RESERVOIR_CAP,
    RESERVOIR_HALF_LIFE_S,
    NourishmentReservoir,
    TraumaReservoir,
)
from clanker_soul.soul.state import SoulState
from clanker_soul.soul.store import SoulStore

__all__ = [
    "SoulState",
    "SoulStore",
    "TraumaReservoir",
    "NourishmentReservoir",
    "RESERVOIR_HALF_LIFE_S",
    "RESERVOIR_CAP",
]
