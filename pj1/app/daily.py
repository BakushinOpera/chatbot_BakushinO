"""Phal 的作息：和用户一样，每晚十点睡，早上七点醒；晚上九点总结一天。

用的是本机时钟（和聊天里注入的当前时间一致）。想关掉睡觉：settings 里 life_sleep = 0。
"""

from __future__ import annotations

import time
from datetime import datetime

from . import db, registry

# 默认值：晚上 10 点睡、早上 7 点醒、晚上 9 点总结一天。设置页里可改（registry: sleep_hour / wake_hour / summary_hour）
SLEEP_START = 22
WAKE_HOUR = 7
SUMMARY_HOUR = 21


def sleep_hour() -> int:
    return int(registry.num("sleep_hour"))


def wake_hour() -> int:
    return int(registry.num("wake_hour"))


def summary_hour() -> int:
    return int(registry.num("summary_hour"))


def _hour(now: float | None) -> int:
    return datetime.fromtimestamp(now or time.time()).hour


def sleep_enabled() -> bool:
    return registry.flag("life_sleep")


def is_sleeping(now: float | None = None) -> bool:
    if not sleep_enabled():
        return False
    from . import intents             # 她说过「今晚早点睡 / 明天晚点起」（照做的话）：多睡的那一段

    try:
        extra = intents.sleep_window(now)
    except Exception:  # noqa: BLE001
        extra = None
    if extra and extra[0] <= (now or time.time()) < extra[1]:
        return True
    h = _hour(now)
    sleep, wake = sleep_hour(), wake_hour()
    if sleep > wake:
        return h >= sleep or h < wake
    return sleep <= h < wake          # 睡觉点比起床点早（比如 1 点睡 9 点起）


def today_str(now: float | None = None) -> str:
    return datetime.fromtimestamp(now or time.time()).strftime("%Y-%m-%d")


def summary_due(now: float | None = None) -> bool:
    """到了晚上九点之后、今天还没总结过。"""
    return _hour(now) >= summary_hour() and db.get_settings().get("life_summary_day") != today_str(now)
