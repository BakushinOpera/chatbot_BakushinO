"""「为什么」：每次随机决定（下一个状态、这顿怎么吃、今天的事、买东西）自动记一行原因——抽中了什么、各因素、最终概率。
平时不用看，出问题时在状态栏 / 设置页「生活事件」旁边点「为什么」查。只留最近 why_keep_days 天（默认 7）。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime
from typing import Any

from . import db, registry

log = logging.getLogger(__name__)

KINDS = {"state": "下一个状态", "meal": "这顿怎么吃", "event": "今天的事", "spend": "买东西"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS why_log (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     REAL    NOT NULL,
    kind   TEXT    NOT NULL,
    picked TEXT    NOT NULL,
    prob   REAL,
    text   TEXT    NOT NULL,
    ref    TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_why_ts ON why_log (ts);
"""

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


def odds(weights: dict[str, float], labels: dict[str, str] | None = None, picked: str | None = None, top: int = 3) -> tuple[float | None, str]:
    """权重 → (抽中那个的概率, 「其它：A 20%、B 15%」)。"""
    total = sum(w for w in weights.values() if w > 0)
    if total <= 0:
        return None, ""
    lab = labels or {}
    others = sorted(((k, w) for k, w in weights.items() if w > 0 and k != picked), key=lambda x: -x[1])[:top]
    rest = "、".join(f"{lab.get(k, k)} {w / total:.0%}" for k, w in others)
    return (weights.get(picked, 0.0) / total if picked is not None else None), (f"其它：{rest}" if rest else "")


def record(kind: str, picked: str, factors: list[str] | None = None, prob: float | None = None,
           weights: dict[str, float] | None = None, labels: dict[str, str] | None = None, ref: str = "", now: float | None = None) -> None:
    """记一行。weights 给了就自动算抽中的概率和其它候选；prob 直接给就用它（比如「掷中 30%」这种）。出错不影响正事。"""
    try:
        if not registry.flag("why_enabled"):
            return
        bits = [b for b in (factors or []) if b]
        if weights:
            p, rest = odds(weights, labels, picked)
            prob = p if prob is None else prob
            if rest:
                bits.append(rest)
        name = (labels or {}).get(picked, picked)
        head = f"抽中「{name}」" + (f"（{prob:.0%}）" if prob is not None else "")
        text = head + ("；" + "；".join(bits) if bits else "")
        now = now if now is not None else time.time()
        conn = _conn()
        with db._lock:
            conn.execute("INSERT INTO why_log (ts, kind, picked, prob, text, ref) VALUES (?, ?, ?, ?, ?, ?)",
                         (now, kind, picked, prob, text[:400], ref))
            conn.commit()
        _prune(now)
    except Exception:  # noqa: BLE001
        log.exception("记「为什么」出错")


def _prune(now: float) -> None:
    global _last_prune
    if now - _last_prune < 3600:
        return
    _last_prune = now
    conn = _conn()
    with db._lock:
        conn.execute("DELETE FROM why_log WHERE ts < ?", (now - max(1.0, registry.num("why_keep_days")) * 86400,))
        conn.commit()


def recent(kind: str | None = None, limit: int = 30, ref: str | None = None) -> list[dict[str, Any]]:
    sql, args = "SELECT * FROM why_log", []
    where = []
    if kind:
        where.append("kind = ?")
        args.append(kind)
    if ref:
        where.append("ref = ?")
        args.append(ref)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(int(limit))
    conn = _conn()
    with db._lock:
        rows = [dict(r) for r in conn.execute(sql, tuple(args)).fetchall()]
    for r in rows:
        r["kind_label"] = KINDS.get(r["kind"], r["kind"])
        r["at"] = datetime.fromtimestamp(r["ts"]).strftime("%m-%d %H:%M")
    return rows


def clear() -> int:
    conn = _conn()
    with db._lock:
        n = conn.execute("DELETE FROM why_log").rowcount
        conn.commit()
    return n


def fmt(x: float) -> str:
    """权重 / 系数的简短写法。"""
    return f"{x:.2f}".rstrip("0").rstrip(".") if abs(x) < 100 else f"{x:.0f}"


def dumps(d: dict[str, Any]) -> str:
    return json.dumps(d, ensure_ascii=False)
