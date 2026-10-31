"""工作日 / 休息日。

任务「拖了几天」只数工作日：周末是休息日，任务变成可选，不增加拖延天数，也不提醒。
只看周六、周日，不处理法定节假日和调休（那需要联网的节假日表）。
"""

from __future__ import annotations

from datetime import date, timedelta


def is_weekend(day: date | str) -> bool:
    if isinstance(day, str):
        day = date.fromisoformat(day)
    return day.weekday() >= 5


PRIORITIES = ("main", "minor")


def pending_for(priority: str | None, start: date | str, today: date | str) -> int:
    """拖延天数，按任务的主次算：

    * 主要任务（main）：每一天都算，周末也一样，一直拖下去；
    * 次要任务（minor，默认）：只数工作日，周末变可选、不再增加。
    """
    if priority == "main":
        if isinstance(start, str):
            start = date.fromisoformat(start)
        if isinstance(today, str):
            today = date.fromisoformat(today)
        return max(0, (today - start).days)
    return pending_workdays(start, today)


def pending_workdays(start: date | str, today: date | str) -> int:
    """从 start 之后到 today（含）之间，经过了多少个工作日。

    周一的任务在周三是 2 天；周五的任务到下周一是 1 天（周末不算）。
    start 晚于 today（未来的任务）返回 0。
    """
    if isinstance(start, str):
        start = date.fromisoformat(start)
    if isinstance(today, str):
        today = date.fromisoformat(today)
    total = (today - start).days
    if total <= 0:
        return 0
    full_weeks, rest = divmod(total, 7)
    count = full_weeks * 5
    cursor = start + timedelta(days=full_weeks * 7)
    for i in range(1, rest + 1):
        if (cursor + timedelta(days=i)).weekday() < 5:
            count += 1
    return count
