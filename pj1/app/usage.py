"""运行开销统计：每次调模型记一行（用途、耗时、输入 / 输出 token），按「一轮对话」归组，设置页「模型与记忆」里看最近几轮。

* 用途不用调用方传：按调用 `llm` 的那个模块名推出来（memory = 记忆提取、writing = 写作……）。
* 一轮对话：聊天开始生成时 `start_round()` 设一个 contextvar；这一轮里起的后台任务（比如回复后的记忆提取）会继承它，所以也算进这一轮。
  没有轮次的（她自己过日子时的调用）记在「后台」里。
* 只留最近 `usage_keep_days` 天。
"""

from __future__ import annotations

import contextvars
import logging
import sqlite3
import sys
import time
from datetime import datetime
from typing import Any

from . import db, registry

log = logging.getLogger(__name__)

PURPOSE = {
    "main": "聊天回复", "burst": "聊天回复", "memory": "记忆 / 意图提取", "moodsync": "心情同步", "life": "生活",
    "writing": "写作", "reading": "读书", "events": "生活事件", "meals": "吃饭", "money": "买东西", "letters": "月末的信",
    "planner": "日历 / 心情", "notebook_write": "小本子", "people": "人物档案", "room": "房间", "style": "说话风格", "engine": "QQ",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_usage (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         REAL    NOT NULL,
    round_id   TEXT    NOT NULL DEFAULT '',
    purpose    TEXT    NOT NULL,
    ms         INTEGER NOT NULL,
    prompt     INTEGER NOT NULL DEFAULT 0,
    completion INTEGER NOT NULL DEFAULT 0,
    cached     INTEGER NOT NULL DEFAULT 0,
    ok         INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON llm_usage (ts);
CREATE INDEX IF NOT EXISTS idx_usage_round ON llm_usage (round_id);
"""

_round: contextvars.ContextVar[str] = contextvars.ContextVar("phal_usage_round", default="")
_ready = False
_last_prune = 0.0


def _conn() -> sqlite3.Connection:
    global _ready
    conn = db.connect()
    if not _ready:
        with db._lock:
            conn.executescript(SCHEMA)
            conn.commit()
        _ready = True
    return conn


def start_round(label: str = "") -> str:
    """聊天这一轮开始生成：之后（含这一轮起的后台任务）的调用都归到这一轮。"""
    rid = f"{time.time():.3f}" + (f":{label}" if label else "")
    _round.set(rid)
    return rid


def purpose_of_caller() -> str:
    """调用 llm 的那个模块（跳过 llm 和本模块自己）。"""
    f = sys._getframe(1)
    for _ in range(16):
        if f is None:
            break
        mod = f.f_globals.get("__name__", "")
        if mod.startswith("app.") and mod not in ("app.llm", "app.usage"):
            return mod.split(".")[-1]
        f = f.f_back
    return "other"


def record(purpose: str, ms: float, usage: dict[str, Any] | None, ok: bool = True) -> None:
    if not registry.flag("usage_enabled"):
        return
    u = usage or {}
    try:
        prompt = int(u.get("prompt_tokens") or 0)
        completion = int(u.get("completion_tokens") or 0)
        cached = int(u.get("prompt_cache_hit_tokens") or (u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
    except (TypeError, ValueError, AttributeError):
        prompt = completion = cached = 0
    try:
        conn = _conn()
        with db._lock:
            conn.execute("INSERT INTO llm_usage (ts, round_id, purpose, ms, prompt, completion, cached, ok) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (time.time(), _round.get(), purpose, int(ms), prompt, completion, cached, 1 if ok else 0))
            conn.commit()
        _prune()
    except Exception:  # noqa: BLE001
        log.exception("记开销出错")


def _prune() -> None:
    global _last_prune
    now = time.time()
    if now - _last_prune < 3600:
        return
    _last_prune = now
    keep = max(1.0, registry.num("usage_keep_days")) * 86400
    conn = _conn()
    with db._lock:
        conn.execute("DELETE FROM llm_usage WHERE ts < ?", (now - keep,))
        conn.commit()


def _rows(sql: str, args: tuple = ()) -> list[dict[str, Any]]:
    conn = _conn()
    with db._lock:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def _sum(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by: dict[str, dict[str, int]] = {}
    for r in rows:
        b = by.setdefault(PURPOSE.get(r["purpose"], r["purpose"]), {"calls": 0, "ms": 0, "prompt": 0, "completion": 0})
        b["calls"] += 1
        b["ms"] += r["ms"]
        b["prompt"] += r["prompt"]
        b["completion"] += r["completion"]
    return {
        "calls": len(rows), "ms": sum(r["ms"] for r in rows), "prompt": sum(r["prompt"] for r in rows),
        "completion": sum(r["completion"] for r in rows), "cached": sum(r["cached"] for r in rows),
        "failed": sum(1 for r in rows if not r["ok"]), "by": by,
    }


def summary(rounds: int = 10) -> dict[str, Any]:
    """最近 N 轮对话各自的开销，加上今天后台（她自己过日子）的开销。"""
    rids = [r["round_id"] for r in _rows(
        "SELECT round_id, MAX(ts) AS t FROM llm_usage WHERE round_id != '' GROUP BY round_id ORDER BY t DESC LIMIT ?", (int(rounds),))]
    out = []
    for rid in rids:
        rows = _rows("SELECT * FROM llm_usage WHERE round_id = ? ORDER BY ts", (rid,))
        s = _sum(rows)
        s["at"] = datetime.fromtimestamp(rows[0]["ts"]).strftime("%m-%d %H:%M:%S") if rows else ""
        out.append(s)
    day0 = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    bg = _sum(_rows("SELECT * FROM llm_usage WHERE round_id = '' AND ts >= ?", (day0,)))
    today = _sum(_rows("SELECT * FROM llm_usage WHERE ts >= ?", (day0,)))
    return {"rounds": out, "background_today": bg, "today": today, "enabled": registry.flag("usage_enabled")}


def clear() -> int:
    conn = _conn()
    with db._lock:
        n = conn.execute("DELETE FROM llm_usage").rowcount
        conn.commit()
    return n
