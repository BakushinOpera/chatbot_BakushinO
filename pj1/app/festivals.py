"""节日：我在日历里添加 / 编辑（名称、日期、是否每年重复、一句备注）。

节日当天：
* 打开网页后，Phal 主动发一条节日祝福，**每个节日每年只发一次**（记在 last_greeted_year 里）；
* 「今天是 X 节」注入聊天上下文；
* 心情有下限：当天不会低落（负面情绪一律抬成「开心」）。
日期和现实世界同步（本机日期）。
"""

from __future__ import annotations

import random
from datetime import date, datetime
from typing import Any

from . import db

NEGATIVE_MOODS = {"忧郁", "孤单", "烦躁", "委屈", "焦虑", "沮丧", "空落落", "心不在焉", "怅然", "犯困", "闷闷的", "小纠结", "恍惚"}
FLOOR_MOOD = "开心"

GREETINGS = [
    "{n}快乐", "{n}快乐呀", "今天{n}诶\n{n}快乐", "{n}快乐\n今天心情还不赖", "嗯 {n}快乐\n你也是",
]


def _q(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    with db._lock:  # noqa: SLF001
        return [dict(r) for r in db.connect().execute(sql, params).fetchall()]


def _exec(sql: str, params: tuple = ()) -> int:
    with db._lock:  # noqa: SLF001
        conn = db.connect()
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid if sql.lstrip().upper().startswith("INSERT") else cur.rowcount


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat((value or "").strip())
    except ValueError as exc:
        raise ValueError("日期格式应为 YYYY-MM-DD") from exc


def _view(r: dict[str, Any]) -> dict[str, Any]:
    r = dict(r)
    r["yearly"] = bool(r["yearly"])
    r["date"] = f"{r['year'] or '----'}-{r['month']:02d}-{r['day']:02d}" if not r["yearly"] else f"{r['month']:02d}-{r['day']:02d}"
    return r


def list_all() -> list[dict[str, Any]]:
    return [_view(r) for r in _q("SELECT * FROM festivals ORDER BY month, day, id")]


def get(fid: int) -> dict[str, Any] | None:
    rows = _q("SELECT * FROM festivals WHERE id = ?", (int(fid),))
    return _view(rows[0]) if rows else None


def add(name: str, date_str: str, yearly: bool = True, note: str = "") -> dict[str, Any]:
    name = (name or "").strip()
    if not name:
        raise ValueError("节日名称不能是空的")
    d = _parse_date(date_str)
    fid = _exec(
        "INSERT INTO festivals (name, month, day, year, yearly, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (name[:30], d.month, d.day, None if yearly else d.year, 1 if yearly else 0, (note or "").strip()[:60],
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    return get(fid)  # type: ignore[return-value]


def update(fid: int, name: str | None = None, date_str: str | None = None, yearly: bool | None = None,
           note: str | None = None) -> dict[str, Any] | None:
    cur = get(fid)
    if not cur:
        return None
    new_name = (name if name is not None else cur["name"]).strip()
    if not new_name:
        raise ValueError("节日名称不能是空的")
    is_yearly = cur["yearly"] if yearly is None else bool(yearly)
    if date_str:
        d = _parse_date(date_str)
        month, day, year = d.month, d.day, d.year
    else:
        month, day, year = cur["month"], cur["day"], cur.get("year") or date.today().year
    _exec(
        "UPDATE festivals SET name = ?, month = ?, day = ?, year = ?, yearly = ?, note = ?, last_greeted_year = "
        "CASE WHEN month <> ? OR day <> ? THEN 0 ELSE last_greeted_year END WHERE id = ?",
        (new_name[:30], month, day, None if is_yearly else year, 1 if is_yearly else 0,
         (note if note is not None else cur["note"] or "").strip()[:60], month, day, int(fid)),
    )
    return get(fid)


def delete(fid: int) -> bool:
    return bool(_exec("DELETE FROM festivals WHERE id = ?", (int(fid),)))


def on_date(day: date | str) -> list[dict[str, Any]]:
    """某一天有哪些节日（每年重复的按月日匹配；不重复的要年份也对得上）。"""
    d = day if isinstance(day, date) else date.fromisoformat(day)
    rows = _q(
        "SELECT * FROM festivals WHERE month = ? AND day = ? AND (yearly = 1 OR year = ?) ORDER BY id",
        (d.month, d.day, d.year),
    )
    return [_view(r) for r in rows]


def today() -> list[dict[str, Any]]:
    return on_date(date.today())


def floor_mood(word: str, day: date | str | None = None) -> str:
    """节日当天心情的下限：不会低落。"""
    if word in NEGATIVE_MOODS and on_date(day or date.today()):
        return FLOOR_MOOD
    return word


def context_note(day: date | None = None) -> str:
    fs = on_date(day or date.today())
    if not fs:
        return ""
    names = "、".join(f["name"] for f in fs)
    notes = "；".join(f"{f['name']}：{f['note']}" for f in fs if f.get("note"))
    return (f"【今天是{names}】" + (notes + "。" if notes else "")
            + "今天过节，你心情不错；聊天里自然地带到节日就好，别刻意。")


def greeting_due(now: datetime | None = None) -> list[dict[str, Any]]:
    """今天的节日里，今年还没祝福过的。"""
    now = now or datetime.now()
    return [f for f in on_date(now.date()) if int(f.get("last_greeted_year") or 0) != now.year]


def mark_greeted(fid: int, year: int) -> None:
    _exec("UPDATE festivals SET last_greeted_year = ? WHERE id = ?", (int(year), int(fid)))


def local_greeting(name: str) -> list[str]:
    return [p for p in random.choice(GREETINGS).format(n=name).split("\n") if p.strip()]
