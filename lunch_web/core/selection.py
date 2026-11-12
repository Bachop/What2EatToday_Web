"""Pure candidate filtering followed by weighted random selection.

Precedence: hard weekday limits > rotation coverage (>5 options) > avoiding
the previous workweek > different reroll if possible > configured weights.
Only the final persisted result of a date is part of history.
"""

import random
from collections import Counter
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Protocol

from .dates import is_workday, previous_workday, week_start
from .models import FoodOption, LunchError, LunchRecord


class RandomSource(Protocol):
    def random(self) -> float: ...


class SelectionEngine:
    def __init__(self, rng: RandomSource | None = None) -> None:
        self.rng = rng if rng is not None else random.SystemRandom()

    def candidates(
        self,
        options: Sequence[FoodOption],
        history: Sequence[LunchRecord],
        day: date,
        exclude_id: int | None = None,
    ) -> list[FoodOption]:
        if not options:
            raise LunchError("请先添加至少一个午餐选项。")
        eligible = list(options)
        past = sorted(
            (record for record in history if record.day < day and is_workday(record.day)),
            key=lambda record: record.day,
        )
        if is_workday(day):
            if len(options) == 2:
                last_day = previous_workday(day)
                earlier_day = previous_workday(last_day)
                by_day = {record.day: record.option_id for record in past}
                last_id = by_day.get(last_day)
                if last_id is not None and by_day.get(earlier_day) == last_id:
                    eligible = [option for option in eligible if option.id != last_id]
            elif len(options) > 2:
                counts = Counter(
                    record.option_id for record in past if record.day >= week_start(day)
                )
                eligible = [option for option in eligible if counts[option.id] < 2]
        if not eligible:
            # This cannot occur with normal forward calendar use: before Friday
            # there are at most four final records, and at least three options.
            raise LunchError("当前历史记录下没有符合防重复规则的选项，请检查系统日期。")

        if len(options) > 5 and is_workday(day):
            active_ids = {option.id for option in options}
            served: set[int] = set()
            # Reconstruct coverage from final records, so rerolls, deleted options
            # and newly added options never leave stale rotation counters behind.
            for record in past:
                if record.option_id in active_ids:
                    served.add(record.option_id)
                    if served == active_ids:
                        served.clear()
            unserved = [option for option in eligible if option.id not in served]
            if unserved:
                eligible = unserved
            last_week = week_start(day) - timedelta(days=7)
            previous_ids = {
                record.option_id
                for record in past
                if last_week <= record.day < week_start(day)
            }
            fresh = [option for option in eligible if option.id not in previous_ids]
            if fresh:
                eligible = fresh
        different = [option for option in eligible if option.id != exclude_id]
        if different:
            eligible = different
        return eligible

    def choose(
        self,
        options: Sequence[FoodOption],
        history: Sequence[LunchRecord],
        day: date,
        exclude_id: int | None = None,
    ) -> FoodOption:
        candidates = self.candidates(options, history, day, exclude_id)
        # The service validates positive weights before they reach this boundary.
        total = sum(option.weight for option in candidates)
        threshold = self.rng.random() * total
        cumulative = 0.0
        for option in candidates:
            cumulative += option.weight
            if threshold < cumulative:
                return option
        return candidates[-1]  # Guard against floating-point accumulation error.
