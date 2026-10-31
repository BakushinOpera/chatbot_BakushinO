"""只读地看本地应用的数据。

QQ 功能对本地应用的数据**只读**：这里每次都用 ``file:...?mode=ro`` 开一个只读连接（再加 ``PRAGMA query_only``），
数据库层面就写不进去。所以别的模块里那些「读着读着会顺手写一下」的函数（定今天的心情词、掷打断骰子、
补发零花钱、第一次读人物档案时写默认值……）这里都不调用，只读出原始数据，自己整理。

世界设定是读文件（data/world），直接用 world 模块；注册表的默认值通过 ``registry.get(key, settings)`` 取（传进来的是只读读出的 settings）。
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import config as app_config
from .. import memory, registry


class LocalView:
    def __init__(self, path: str | Path | None = None) -> None:
        self._path = path

    # ───────────────────────── 底层 ─────────────────────────
    def _connect(self) -> sqlite3.Connection | None:
        path = Path(self._path or app_config.DB_PATH)
        if not path.exists():
            return None
        conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, check_same_thread=False, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        return conn

    def q(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        conn = None
        try:
            conn = self._connect()
            if conn is None:
                return []
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        except sqlite3.Error:
            return []
        finally:
            if conn is not None:
                conn.close()

    # ───────────────────────── 设置 ─────────────────────────
    def settings(self) -> dict[str, str]:
        merged = dict(app_config.DEFAULT_SETTINGS)
        for r in self.q("SELECT key, value FROM settings"):
            merged[r["key"]] = r["value"]
        return merged

    def num(self, key: str, settings: dict[str, str] | None = None) -> float:
        return registry.num(key, settings or self.settings())

    def flag(self, key: str, settings: dict[str, str] | None = None) -> bool:
        return registry.flag(key, settings or self.settings())

    def json_setting(self, key: str, settings: dict[str, str] | None = None) -> Any:
        raw = (settings or self.settings()).get(key) or ""
        try:
            return json.loads(raw) if raw else None
        except json.JSONDecodeError:
            return None

    # ───────────────────────── 作息 / 状态 / 心情 ─────────────────────────
    def is_sleeping(self, now: float | None = None, settings: dict[str, str] | None = None) -> bool:
        s = settings or self.settings()
        if not registry.flag("life_sleep", s):
            return False
        h = datetime.fromtimestamp(now or time.time()).hour
        sleep, wake = int(registry.num("sleep_hour", s)), int(registry.num("wake_hour", s))
        if sleep > wake:
            return h >= sleep or h < wake
        return sleep <= h < wake

    def life_state(self, settings: dict[str, str] | None = None) -> dict[str, Any] | None:
        st = self.json_setting("life_state", settings)
        return st if isinstance(st, dict) and st.get("key") else None

    def state_key(self, now: float | None = None, settings: dict[str, str] | None = None) -> str:
        """她现在的状态键。睡觉的时间段一律算 sleep（页面没来得及推进也一样）。读不到就当发呆。"""
        s = settings or self.settings()
        if self.is_sleeping(now, s):
            return "sleep"
        st = self.life_state(s)
        return str(st["key"]) if st else "daze"

    def mood_view(self, now: float | None = None, settings: dict[str, str] | None = None) -> dict[str, Any] | None:
        """此刻的心情和精力（v8，见 app/mood.py）：按桌面那边存的心情状态现算。只读，不会替她「定」一个；还没有就 None。"""
        from .. import mood as mood_lib

        s = settings or self.settings()
        mst = mood_lib.load(s)
        if not mst.get("day"):
            return None
        return mood_lib.view(mst, now or time.time(), mood_lib.params(s))

    def mood(self, now: float | None = None, settings: dict[str, str] | None = None) -> tuple[str, str]:
        """心情词和原因（没有就看日历上今天的那个词，再没有就空）。"""
        s = settings or self.settings()
        mv = self.mood_view(now, s)
        if mv:
            return mv["word"], mv["why"]
        day = datetime.fromtimestamp(now or time.time()).strftime("%Y-%m-%d")
        rows = self.q("SELECT mood FROM moods WHERE day = ?", (day,))
        return (rows[0]["mood"] if rows else ""), ""

    def meal_note(self, key: str, now: float | None = None, settings: dict[str, str] | None = None) -> str:
        plan = self.json_setting("meal_plan", settings) or {}
        day = datetime.fromtimestamp(now or time.time()).strftime("%Y-%m-%d")
        m = plan.get(plan.get("active") or "") if isinstance(plan, dict) and plan.get("day") == day else None
        if not m or not m.get("dish") or key not in ("cook", "eat", "dine_out"):
            return ""
        return {"cook": "在做", "eat": "吃的是", "dine_out": "吃的是"}[key] + m["dish"]

    def last_desktop_user_ts(self) -> float:
        rows = self.q("SELECT MAX(ts) AS t FROM messages WHERE role = 'user'")
        return float(rows[0]["t"]) if rows and rows[0]["t"] else 0.0

    # ───────────────────────── 事件 ─────────────────────────
    def events_today(self, now: float | None = None) -> list[dict[str, Any]]:
        day = datetime.fromtimestamp(now or time.time()).strftime("%Y-%m-%d")
        return self.q("SELECT * FROM life_events WHERE day = ? ORDER BY id ASC LIMIT 30", (day,))

    # ───────────────────────── 记忆 ─────────────────────────
    def memories(self, limit: int = 400) -> list[dict[str, Any]]:
        return self.q("SELECT * FROM memories WHERE replaced_by IS NULL ORDER BY ts DESC LIMIT ?", (int(limit),))

    def retrieve_memories(self, query: str, total: int = 12, min_fill: int = 3, categories: tuple[str, ...] | None = None,
                          exclude_words: tuple[str, ...] = ()) -> list[dict[str, Any]]:
        """和 memory.retrieve 一样的打分，但读只读连接；可以只要某几类、并滤掉含某些词的条目。"""
        pool = [m for m in self.memories(400)
                if (not categories or (m.get("category") or "about_user") in categories)
                and not any(w and w in (m.get("content") or "") for w in exclude_words)]
        if not pool or total <= 0:
            return []
        grams = memory._bigrams(query)
        sets = {m["id"]: memory._bigrams(m["content"]) for m in pool}
        df: dict[str, int] = {}
        for g in sets.values():
            for x in g:
                df[x] = df.get(x, 0) + 1
        now = time.time()
        scored = []
        for m in pool:
            overlap = sum(1.0 if df[x] <= 3 else 0.25 for x in grams & sets[m["id"]])
            age_days = max(0.0, (now - float(m.get("ts") or now)) / 86400)
            scored.append((overlap + 0.35 / (1.0 + age_days / 14), m))
        scored.sort(key=lambda x: -x[0])
        quota = {c: max(1, round(n * total / 12)) for c, n in memory.SHARE.items()}
        picked: list[dict[str, Any]] = []
        used = {c: 0 for c in quota}
        for score, m in scored:
            c = m.get("category") or "about_user"
            if c not in quota or used[c] >= quota[c]:
                continue
            if score < 1.0 and len(picked) >= min_fill:
                continue
            picked.append(m)
            used[c] += 1
            if len(picked) >= total:
                break
        return picked

    # ───────────────────────── 人物 ─────────────────────────
    def people(self) -> list[dict[str, Any]]:
        rows = self.q("SELECT * FROM people WHERE draft = 0 ORDER BY CASE kind WHEN 'family' THEN 0 ELSE 1 END, sort ASC, id ASC")
        return rows

    # ───────────────────────── 天气 ─────────────────────────
    def weather(self, settings: dict[str, str] | None = None) -> dict[str, dict[str, Any]]:
        st = self.json_setting("weather_state", settings) or {}
        out: dict[str, dict[str, Any]] = {}
        for place in ("her", "user"):
            w = st.get(place) if isinstance(st, dict) else None
            if isinstance(w, dict) and w.get("kind"):
                out[place] = w
        return out

    # ───────────────────────── 书 / 房间 / 钱（只给主人私聊用）─────────────────────────
    def books(self) -> list[dict[str, Any]]:
        return self.q("SELECT id, title, author, phal_status, phal_chapter FROM books ORDER BY id ASC")

    def room_items(self) -> list[dict[str, Any]]:
        return self.q("SELECT name, category, location, source, got_on, note FROM room_items ORDER BY got_on DESC, id DESC LIMIT 60")

    def money_balance(self) -> int | None:
        rows = self.q("SELECT COALESCE(SUM(delta), 0) AS b FROM money_ledger")
        return int(rows[0]["b"]) if rows else None
