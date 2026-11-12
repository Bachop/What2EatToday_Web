"""Immutable values shared by the domain, storage and presentation layers."""

from dataclasses import dataclass
from datetime import date, datetime

MAX_REROLLS = 3
MAX_NAME_LENGTH = 40


class LunchError(ValueError):
    """A recoverable domain error whose message can be shown to the user."""


@dataclass(frozen=True)
class FoodOption:
    id: int
    name: str
    weight: float
    color: str


@dataclass(frozen=True)
class LunchRecord:
    day: date
    option_id: int
    option_name: str
    color: str
    rerolls: int
    chosen_at: datetime

    @property
    def remaining_rerolls(self) -> int:
        return MAX_REROLLS - self.rerolls
