"""家人出差：每个月有几天（默认 2~3 天，连着）爸爸妈妈都不在家。

* 这几天她一个人吃饭：自己做或者点外卖，饭菜摆到房间的食物桌上，在房间里吃（状态「一个人在房间吃饭」）；
  不会「和家人一起吃」，也不会有和家人拼图、被妈妈叫去跑腿，和爸爸 / 妈妈 / 父母的事件也不会发生。
* 每个月第一次用到时随机定好是哪几天（存 settings 的 family_trip），之后整个月不变。
"""

from __future__ import annotations

import calendar
import json
import random
from datetime import date, datetime
from typing import Any

from . import db, registry

AWAY_STATES = ("puzzle_family", "errand")             # 家人不在：这些状态不会出现
AWAY_EVENTS = ("mom", "dad", "parents")               # 家人不在：这些事件不会发生


def trip_for(d: date, rng: random.Random | None = None) -> dict[str, Any]:
    """这个月的出差安排 {ym, start（几号）, days}；days = 0 表示这个月不出差。"""
    ym = d.strftime("%Y-%m")
    try:
        cur = json.loads(db.get_settings().get("family_trip") or "{}")
    except json.JSONDecodeError:
        cur = {}
    if isinstance(cur, dict) and cur.get("ym") == ym:
        return cur
    rng = rng or random
    lo, hi = int(registry.num("family_away_days_min")), int(registry.num("family_away_days_max"))
    days = rng.randint(min(lo, hi), max(lo, hi)) if max(lo, hi) > 0 else 0
    month_len = calendar.monthrange(d.year, d.month)[1]
    start = rng.randint(1, max(1, month_len - days + 1)) if days else 0
    cur = {"ym": ym, "start": start, "days": days}
    db.save_settings({"family_trip": json.dumps(cur)})
    return cur


def away(d: date | None = None) -> bool:
    """这一天家人在不在家（出差中 = True）。"""
    d = d or datetime.now().date()
    t = trip_for(d)
    return bool(t.get("days")) and t["start"] <= d.day < t["start"] + t["days"]


def describe(d: date | None = None) -> str:
    """给上下文 / 设置页：这个月哪几天出差。"""
    d = d or datetime.now().date()
    t = trip_for(d)
    if not t.get("days"):
        return "这个月爸妈不出差"
    end = t["start"] + t["days"] - 1
    return f"这个月 {t['start']}~{end} 号爸妈出差"
