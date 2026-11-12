"""Application use cases. Each mutation is validated and committed atomically."""

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime

from .models import MAX_NAME_LENGTH, MAX_REROLLS, FoodOption, LunchError, LunchRecord
from .repository import LunchRepository
from .selection import SelectionEngine
from .weights import equal_weights, normalize, weights_after_addition


def local_now() -> datetime:
    return datetime.now().astimezone()


class LunchService:
    def __init__(
        self,
        repository: LunchRepository,
        engine: SelectionEngine | None = None,
        now: Callable[[], datetime] = local_now,
    ) -> None:
        self.repository = repository
        self.engine = engine if engine is not None else SelectionEngine()
        self.now = now

    def options(self) -> list[FoodOption]:
        return self.repository.options()

    def today_record(self) -> LunchRecord | None:
        return self.repository.record(self.now().date())

    def month_records(self, year: int, month: int) -> list[LunchRecord]:
        return self.repository.month(year, month)

    def add_options(self, names: Sequence[str]) -> None:
        cleaned = [name.strip() for name in names if name.strip()]
        if not cleaned:
            raise LunchError("请输入至少一个午餐选项，每行一个。")
        if any(len(name) > MAX_NAME_LENGTH for name in cleaned):
            raise LunchError(f"每个选项最多 {MAX_NAME_LENGTH} 个字符。")
        if any(any(ord(character) < 32 for character in name) for name in cleaned):
            raise LunchError("选项名称不能包含换行或控制字符。")
        keys = [name.casefold() for name in cleaned]
        if len(set(keys)) != len(keys):
            raise LunchError("新增列表中有重复名称，请合并后再添加。")
        with self.repository.transaction():
            current = self.options()
            existing = {option.name.casefold() for option in current}
            duplicates = [name for name in cleaned if name.casefold() in existing]
            if duplicates:
                raise LunchError("这些选项已存在：" + "、".join(duplicates))
            weights = weights_after_addition([option.weight for option in current], len(cleaned))
            self.repository.update_weights([option.id for option in current], weights[:len(current)])
            for name, weight in zip(cleaned, weights[len(current):]):
                self.repository.insert_option(name, weight)

    def delete_options(self, option_ids: Sequence[int]) -> None:
        ids = set(option_ids)
        if not ids:
            raise LunchError("请先选择要删除的选项。")
        with self.repository.transaction():
            current = self.options()
            if not ids <= {option.id for option in current}:
                raise LunchError("选项已发生变化，请重新打开选项管理。")
            remaining = [option for option in current if option.id not in ids]
            self.repository.deactivate(sorted(ids))
            self.repository.update_weights(
                [option.id for option in remaining], normalize(option.weight for option in remaining)
            )

    def set_weights(self, weights: Mapping[int, float]) -> None:
        with self.repository.transaction():
            current = self.options()
            if set(weights) != {option.id for option in current}:
                raise LunchError("选项已发生变化，请重新打开权重设置。")
            normalized = normalize(weights[option.id] for option in current)
            self.repository.update_weights([option.id for option in current], normalized)

    def reset_weights(self) -> None:
        with self.repository.transaction():
            current = self.options()
            self.repository.update_weights(
                [option.id for option in current], equal_weights(len(current))
            )

    def choose(self, *, reroll: bool = False) -> LunchRecord:
        timestamp = self.now()
        day = timestamp.date()
        with self.repository.transaction():
            existing = self.repository.record(day)
            if existing is not None and not reroll:
                return existing
            if reroll and existing is None:
                raise LunchError("今天还没有选择午餐，请先进行首次选择。")
            if reroll and existing is not None and existing.rerolls >= MAX_REROLLS:
                raise LunchError("今天的 3 次重选机会已用完，明天再来吧。")
            option = self.engine.choose(
                self.options(), self.repository.history_before(day), day,
                exclude_id=existing.option_id if reroll and existing else None,
            )
            record = LunchRecord(
                day, option.id, option.name, option.color,
                existing.rerolls + 1 if reroll and existing else 0, timestamp,
            )
            self.repository.save_record(record)
            return record
