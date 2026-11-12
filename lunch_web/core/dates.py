"""Local civil-date helpers. Holidays are intentionally not modeled."""

from datetime import date, timedelta


def is_workday(day: date) -> bool:
    return day.weekday() < 5


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def previous_workday(day: date) -> date:
    result = day - timedelta(days=1)
    while not is_workday(result):
        result -= timedelta(days=1)
    return result


def month_bounds(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return start, end
