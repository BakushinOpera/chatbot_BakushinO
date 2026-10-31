"""QQ 自己的数据库：data/qq/qq.db。

放：配置（kv）、聊天记录（群聊 / 私聊 / 模拟群聊）、群友印象、发言次数（频率计数）。
本地应用的数据库（chat.db）在这里一个字都不会写。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .. import config as app_config

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None
_conn_path: str = ""

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    chat      TEXT    NOT NULL,                 -- g:<群号> | p:<QQ 号> | sim:<名字>
    ts        REAL    NOT NULL,
    user_id   TEXT    NOT NULL DEFAULT '',
    nickname  TEXT    NOT NULL DEFAULT '',
    card      TEXT    NOT NULL DEFAULT '',
    is_owner  INTEGER NOT NULL DEFAULT 0,
    is_self   INTEGER NOT NULL DEFAULT 0,       -- 她自己发的
    text      TEXT    NOT NULL,
    msg_id    TEXT,                             -- OneBot 的 message_id
    reply_to  TEXT,                             -- 引用的 message_id
    quote     TEXT    NOT NULL DEFAULT '',      -- 引用的那句话（还原出来的）
    at_self   INTEGER NOT NULL DEFAULT 0,       -- @ 了她
    has_image INTEGER NOT NULL DEFAULT 0,
    handled   INTEGER NOT NULL DEFAULT 0,
    meta      TEXT
);
CREATE INDEX IF NOT EXISTS idx_msg_chat ON messages(chat, id);
CREATE INDEX IF NOT EXISTS idx_msg_mid ON messages(msg_id);
CREATE TABLE IF NOT EXISTS members (
    chat         TEXT NOT NULL,
    user_id      TEXT NOT NULL,
    nickname     TEXT NOT NULL DEFAULT '',
    call_name    TEXT NOT NULL DEFAULT '',      -- 她怎么称呼这个人
    impression   TEXT NOT NULL DEFAULT '',
    familiarity  REAL NOT NULL DEFAULT 0,
    msg_count    INTEGER NOT NULL DEFAULT 0,
    interactions INTEGER NOT NULL DEFAULT 0,
    since_update INTEGER NOT NULL DEFAULT 0,
    last_seen    REAL NOT NULL DEFAULT 0,
    updated_at   REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (chat, user_id)
);
CREATE TABLE IF NOT EXISTS speaks (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    chat  TEXT NOT NULL,
    ts    REAL NOT NULL,
    kind  TEXT NOT NULL DEFAULT 'reply'          -- reply | proactive | ack
);
CREATE INDEX IF NOT EXISTS idx_speaks ON speaks(chat, ts);
"""


def db_path() -> Path:
    return app_config.DATA_DIR / "qq" / "qq.db"


def connect() -> sqlite3.Connection:
    global _conn, _conn_path
    path = str(db_path())
    with _lock:
        if _conn is None or _conn_path != path:
            if _conn is not None:
                _conn.close()
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(path, check_same_thread=False, timeout=15)
            _conn.row_factory = sqlite3.Row
            _conn.executescript(SCHEMA)
            _conn.commit()
            _conn_path = path
        return _conn


def close() -> None:
    global _conn, _conn_path
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn, _conn_path = None, ""


def _rows(cur: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(r) for r in cur.fetchall()]


def _exec(sql: str, params: tuple = ()) -> int:
    with _lock:
        conn = connect()
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid if sql.lstrip().upper().startswith("INSERT") else cur.rowcount


def _q(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    with _lock:
        return _rows(connect().execute(sql, params))


# ───────────────────────── kv ─────────────────────────
def kv_get(key: str, default: str = "") -> str:
    rows = _q("SELECT value FROM kv WHERE key = ?", (key,))
    return rows[0]["value"] if rows else default


def kv_set(key: str, value: str) -> None:
    _exec("INSERT INTO kv(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


def kv_json(key: str, default: Any = None) -> Any:
    try:
        return json.loads(kv_get(key, "") or "null") if kv_get(key, "") else default
    except json.JSONDecodeError:
        return default


# ───────────────────────── 消息 ─────────────────────────
def add_message(chat: str, user_id: str, text: str, ts: float | None = None, nickname: str = "", card: str = "", is_owner: bool = False,
                is_self: bool = False, msg_id: str | None = None, reply_to: str | None = None, quote: str = "", at_self: bool = False,
                has_image: bool = False, handled: bool = False, meta: dict[str, Any] | None = None) -> int:
    return _exec(
        "INSERT INTO messages (chat, ts, user_id, nickname, card, is_owner, is_self, text, msg_id, reply_to, quote, at_self, has_image, handled, meta) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (chat, ts or time.time(), str(user_id), nickname, card, int(is_owner), int(is_self), text, str(msg_id) if msg_id else None,
         str(reply_to) if reply_to else None, quote, int(at_self), int(has_image), int(handled), json.dumps(meta, ensure_ascii=False) if meta else None),
    )


def get_message(mid: int) -> dict[str, Any] | None:
    rows = _q("SELECT * FROM messages WHERE id = ?", (int(mid),))
    return rows[0] if rows else None


def find_by_msg_id(msg_id: str, chat: str | None = None) -> dict[str, Any] | None:
    sql, params = "SELECT * FROM messages WHERE msg_id = ?", [str(msg_id)]
    if chat:
        sql += " AND chat = ?"
        params.append(chat)
    rows = _q(sql + " ORDER BY id DESC LIMIT 1", tuple(params))
    return rows[0] if rows else None


def recent(chat: str, limit: int, upto_id: int | None = None) -> list[dict[str, Any]]:
    """最近 limit 条（旧→新）。"""
    sql, params = "SELECT * FROM messages WHERE chat = ?", [chat]
    if upto_id:
        sql += " AND id <= ?"
        params.append(int(upto_id))
    rows = _q(sql + " ORDER BY id DESC LIMIT ?", tuple(params + [int(limit)]))
    return list(reversed(rows))


def unhandled(chat: str, since_ts: float = 0.0) -> list[dict[str, Any]]:
    return _q("SELECT * FROM messages WHERE chat = ? AND handled = 0 AND is_self = 0 AND ts >= ? ORDER BY id ASC", (chat, since_ts))


def mark_handled(ids: list[int]) -> None:
    if ids:
        _exec(f"UPDATE messages SET handled = 1 WHERE id IN ({','.join('?' * len(ids))})", tuple(int(i) for i in ids))


def expire(chat: str, before_ts: float) -> int:
    """攒得太久、没处理的消息不再回：标成已处理（记录还在，只是不再当成「要回的」）。"""
    return _exec("UPDATE messages SET handled = 1 WHERE chat = ? AND handled = 0 AND ts < ?", (chat, before_ts))


def set_msg_id(mid: int, msg_id: str | None) -> None:
    if msg_id:
        _exec("UPDATE messages SET msg_id = ? WHERE id = ?", (str(msg_id), int(mid)))


def last_ts(chat: str) -> float:
    rows = _q("SELECT MAX(ts) AS t FROM messages WHERE chat = ?", (chat,))
    return float(rows[0]["t"]) if rows and rows[0]["t"] else 0.0


def clear_chat(chat: str) -> None:
    _exec("DELETE FROM messages WHERE chat = ?", (chat,))
    _exec("DELETE FROM speaks WHERE chat = ?", (chat,))


def prune(retention_days: int, now: float | None = None) -> int:
    cut = (now or time.time()) - retention_days * 86400
    n = _exec("DELETE FROM messages WHERE ts < ? AND chat NOT LIKE 'sim:%'", (cut,))
    _exec("DELETE FROM speaks WHERE ts < ?", (cut,))
    return n


def stats() -> dict[str, int]:
    rows = _q("SELECT COUNT(*) AS n FROM messages")
    return {"messages": int(rows[0]["n"]) if rows else 0}


# ───────────────────────── 发言次数 ─────────────────────────
def add_speak(chat: str, kind: str = "reply", ts: float | None = None) -> None:
    _exec("INSERT INTO speaks (chat, ts, kind) VALUES (?, ?, ?)", (chat, ts or time.time(), kind))


def count_speaks(chat: str, since: float, kinds: tuple[str, ...] = ("reply", "proactive")) -> int:
    rows = _q(f"SELECT COUNT(*) AS n FROM speaks WHERE chat = ? AND ts >= ? AND kind IN ({','.join('?' * len(kinds))})", (chat, since, *kinds))
    return int(rows[0]["n"])


def last_speak(chat: str, kinds: tuple[str, ...] = ("reply", "proactive")) -> float:
    rows = _q(f"SELECT MAX(ts) AS t FROM speaks WHERE chat = ? AND kind IN ({','.join('?' * len(kinds))})", (chat, *kinds))
    return float(rows[0]["t"]) if rows and rows[0]["t"] else 0.0


# ───────────────────────── 群友印象 ─────────────────────────
def touch_member(chat: str, user_id: str, nickname: str, now: float | None = None) -> None:
    now = now or time.time()
    _exec(
        "INSERT INTO members (chat, user_id, nickname, msg_count, last_seen) VALUES (?, ?, ?, 1, ?) "
        "ON CONFLICT(chat, user_id) DO UPDATE SET nickname = CASE WHEN excluded.nickname != '' THEN excluded.nickname ELSE members.nickname END, "
        "msg_count = members.msg_count + 1, last_seen = excluded.last_seen",
        (chat, str(user_id), nickname, now),
    )


def get_member(chat: str, user_id: str) -> dict[str, Any] | None:
    rows = _q("SELECT * FROM members WHERE chat = ? AND user_id = ?", (chat, str(user_id)))
    return rows[0] if rows else None


def list_members(chat: str | None = None) -> list[dict[str, Any]]:
    if chat:
        return _q("SELECT * FROM members WHERE chat = ? ORDER BY familiarity DESC, last_seen DESC", (chat,))
    return _q("SELECT * FROM members ORDER BY chat, familiarity DESC, last_seen DESC")


def bump_interaction(chat: str, user_id: str, gain: float, cap: float) -> dict[str, Any] | None:
    """她和这个人来往了一次：熟悉度慢慢涨（越熟涨得越慢，到上限为止）。"""
    m = get_member(chat, user_id)
    if not m:
        return None
    fam = float(m["familiarity"])
    fam = min(cap, fam + gain * max(0.15, 1.0 - fam / max(1.0, cap)))
    _exec("UPDATE members SET familiarity = ?, interactions = interactions + 1, since_update = since_update + 1 WHERE chat = ? AND user_id = ?",
          (round(fam, 3), chat, str(user_id)))
    return get_member(chat, user_id)


def update_member(chat: str, user_id: str, **fields: Any) -> dict[str, Any] | None:
    allowed = {"nickname", "call_name", "impression", "familiarity"}
    sets = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if "familiarity" in sets:
        sets["familiarity"] = max(0.0, min(100.0, float(sets["familiarity"])))
    if sets:
        _exec(f"UPDATE members SET {', '.join(f'{k} = ?' for k in sets)}, updated_at = ? WHERE chat = ? AND user_id = ?",
              (*sets.values(), time.time(), chat, str(user_id)))
    return get_member(chat, user_id)


def reset_since_update(chat: str, user_id: str) -> None:
    _exec("UPDATE members SET since_update = 0 WHERE chat = ? AND user_id = ?", (chat, str(user_id)))


def delete_member(chat: str, user_id: str) -> bool:
    return bool(_exec("DELETE FROM members WHERE chat = ? AND user_id = ?", (chat, str(user_id))))


def clear_members(chat: str) -> int:
    return _exec("DELETE FROM members WHERE chat = ?", (chat,))
