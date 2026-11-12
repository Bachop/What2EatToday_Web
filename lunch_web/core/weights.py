"""Reusable probability operations; all live options have positive weight.

Strict positivity ensures that anti-repetition rules can always use every option.
Floating-point totals are normalized to machine precision, not rounded for storage.
"""

import math
from collections.abc import Iterable

from .models import LunchError


def normalize(values: Iterable[float]) -> list[float]:
    items = list(values)
    if not items:
        return []
    if any(not math.isfinite(value) or value <= 0 for value in items):
        raise LunchError("每个选项的权重都必须是有限的正数。")
    # Scaling first also avoids overflow when reusing this helper with large scores.
    scale = max(items)
    scaled = [value / scale for value in items]
    total = math.fsum(scaled)
    result = [value / total for value in scaled]
    if any(value == 0 for value in result):
        raise LunchError("权重之间的差距过大，请缩小差距。")
    largest = result.index(max(result))
    result[largest] += 1.0 - math.fsum(result)
    return result


def equal_weights(count: int) -> list[float]:
    if count < 0:
        raise LunchError("选项数量不能为负数。")
    return normalize([1.0] * count)


def weights_after_addition(old: Iterable[float], added_count: int) -> list[float]:
    """For N existing and M final options: old sum N/M, each new weight 1/M."""
    if added_count < 0:
        raise LunchError("新增数量不能为负数。")
    previous = normalize(old)
    if added_count == 0:
        return previous
    final_count = len(previous) + added_count
    return normalize(
        [value * len(previous) / final_count for value in previous]
        + [1.0 / final_count] * added_count
    )


def redistribute_weight(values: Iterable[float], index: int, target: float) -> list[float]:
    """Change one probability and preserve the ratios among all other entries."""
    result = normalize(values)
    if not 0 <= index < len(result):
        raise LunchError("找不到需要修改的选项。")
    if len(result) == 1:
        return [1.0]
    if not math.isfinite(target) or not 0 < target < 1:
        raise LunchError("多个选项时，单项权重必须大于 0% 且小于 100%。")
    other_indices = [position for position in range(len(result)) if position != index]
    others = normalize(result[position] for position in other_indices)
    for position, weight in zip(other_indices, others):
        result[position] = weight * (1 - target)
    result[index] = target
    return normalize(result)
