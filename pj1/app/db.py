"""SQLite 数据层。

四张表，职责严格分离：

1. ``messages``  —— 当前会话的聊天记录，短期上下文（最近 N 条）从这里取。
2. ``archive``   —— 全部历史归档（所有会话，永不删除），供搜索 / 偶尔提起。
3. ``memories``  —— 长期记忆条目（人设之外的"事实"），注入到系统侧上下文。
4. ``settings``  —— 人设 + 配置，键值表。

关键约束：``save_settings()`` 只写 settings 表，绝不会清理或重建 messages / archive。
"""

from __future__ import annotations

import difflib
import json
import logging
import re
import sqlite3
import threading
import uuid
from datetime import datetime
from typing import Any, Iterable

from .config import DATA_DIR, DB_PATH, DEFAULT_SETTINGS

log = logging.getLogger(__name__)

_lock = threading.RLock()
_fts_ok = False
_conn: sqlite3.Connection | None = None

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT    NOT NULL,
    role        TEXT    NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content     TEXT    NOT NULL,
    quote_id    INTEGER,
    quote_role  TEXT,
    quote_text  TEXT,
    created_at  TEXT    NOT NULL,
    ts          REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages (session_id, id);

CREATE TABLE IF NOT EXISTS archive (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    msg_id      INTEGER,
    session_id  TEXT    NOT NULL,
    role        TEXT    NOT NULL,
    content     TEXT    NOT NULL,
    quote_id    INTEGER,
    quote_role  TEXT,
    quote_text  TEXT,
    created_at  TEXT    NOT NULL,
    ts          REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_archive_ts ON archive (ts);
CREATE INDEX IF NOT EXISTS idx_archive_session ON archive (session_id, id);

CREATE TABLE IF NOT EXISTS memories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT    NOT NULL DEFAULT 'fact',
    content     TEXT    NOT NULL,
    session_id  TEXT,
    source      TEXT    NOT NULL DEFAULT 'manual',
    created_at  TEXT    NOT NULL,
    ts          REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- 日历里的任务。day 是它归属的那一天（YYYY-MM-DD）
CREATE TABLE IF NOT EXISTS tasks (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    title              TEXT    NOT NULL,
    day                TEXT    NOT NULL,
    status             TEXT    NOT NULL DEFAULT 'open',   -- open | done | skipped
    stamp_label        TEXT,                              -- 印章上的字，如「不想干」「完成」
    stamp_tone         TEXT,                              -- red | green | neutral
    remind_after_days  INTEGER NOT NULL DEFAULT 3,        -- 几天没做就提醒
    reminded_on        TEXT,                              -- 最近提醒过的日期，避免重复轰炸
    note               TEXT,
    source             TEXT    NOT NULL DEFAULT 'manual', -- manual | chat
    created_at         TEXT    NOT NULL,
    updated_at         TEXT    NOT NULL,
    ts                 REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_day ON tasks (day, id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks (status, day);

-- 重复任务的「系列」：每 repeat_every 天一条，实例提前生成到 next_day 之前
CREATE TABLE IF NOT EXISTS task_series (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    title              TEXT    NOT NULL,
    repeat_every       INTEGER NOT NULL,                      -- 每几天（日历日）
    priority           TEXT    NOT NULL DEFAULT 'minor',      -- main 主要 | minor 次要
    remind_after_days  INTEGER NOT NULL DEFAULT 3,
    next_day           TEXT    NOT NULL,                      -- 下一条还没生成的日期
    active             INTEGER NOT NULL DEFAULT 1,
    created_at         TEXT    NOT NULL
);

-- 书架。正文在 data/books/<id>/ 里，这里只放元数据和双方的阅读进度
CREATE TABLE IF NOT EXISTS books (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT    NOT NULL,
    author       TEXT    NOT NULL DEFAULT '',
    filename     TEXT    NOT NULL DEFAULT '',
    fmt          TEXT    NOT NULL DEFAULT 'txt',
    chars        INTEGER NOT NULL DEFAULT 0,
    n_chapters   INTEGER NOT NULL DEFAULT 0,
    my_chapter   INTEGER NOT NULL DEFAULT 0,      -- 我读到第几章（从 0 开始）
    my_offset    INTEGER NOT NULL DEFAULT 0,      -- 我读到这一章的第几个字符
    my_read_at   TEXT,
    phal_chapter INTEGER NOT NULL DEFAULT 0,      -- Phal 读到哪
    phal_offset  INTEGER NOT NULL DEFAULT 0,
    phal_status  TEXT    NOT NULL DEFAULT 'unread',  -- unread | reading | paused | finished | dropped
    phal_read_at TEXT,
    added_at     TEXT    NOT NULL,
    ts           REAL    NOT NULL
);

-- 我在书页上用画笔随手画的东西：每本书每一页（key）一行，strokes 是 JSON（坐标按页面宽高归一到 0~1）
CREATE TABLE IF NOT EXISTS book_ink (
    book_id     INTEGER NOT NULL,
    key         TEXT    NOT NULL,
    strokes     TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL,
    PRIMARY KEY (book_id, key)
);

CREATE TABLE IF NOT EXISTS bookmarks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id     INTEGER NOT NULL,
    chapter     INTEGER NOT NULL,
    offset      INTEGER NOT NULL,
    snippet     TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bookmarks_book ON bookmarks (book_id, chapter, offset);

-- Phal 写在书页上的注释。锚点 = 章节 + 字符偏移 + 一小段原文（兜底）
CREATE TABLE IF NOT EXISTS annotations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id     INTEGER NOT NULL,
    chapter     INTEGER NOT NULL,
    offset      INTEGER NOT NULL,
    length      INTEGER NOT NULL,
    snippet     TEXT    NOT NULL,
    note        TEXT    NOT NULL,
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_annotations_book ON annotations (book_id, chapter, offset);

-- Phal 读书时的私人笔记（以后读书和聊天时用）
CREATE TABLE IF NOT EXISTS reading_notes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id     INTEGER NOT NULL,
    chapter     INTEGER NOT NULL,
    text        TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    ts          REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_book ON reading_notes (book_id, id);

-- 草稿本：Phal 试图创作的东西
CREATE TABLE IF NOT EXISTS drafts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT    NOT NULL DEFAULT '片段',     -- 诗 | 片段 | 开头
    title       TEXT    NOT NULL DEFAULT '',
    content     TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'done',    -- done 写成了 | abandoned 放弃了
    shared      INTEGER NOT NULL DEFAULT 0,
    inspired_by TEXT,
    created_at  TEXT    NOT NULL,
    ts          REAL    NOT NULL
);

-- 主动消息记录（做频率限制用）
CREATE TABLE IF NOT EXISTS proactive_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT    NOT NULL,                   -- reading | read_done | create | wish
    message_id  INTEGER,
    ts          REAL    NOT NULL
);

-- 写作文档的元信息（正文是 data/writing/ 和 data/my_writing/ 里的 .txt 文件）
CREATE TABLE IF NOT EXISTS docs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT    NOT NULL,                 -- phal 给 Phal 写的 | mine 我的文字 | letter 她写给我的信（只读）
    filename    TEXT    NOT NULL,
    title       TEXT    NOT NULL,
    chars       INTEGER NOT NULL DEFAULT 0,       -- 字数（不含空白）
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL,
    dated       TEXT    NOT NULL DEFAULT ''       -- 信的日期 YYYY-MM-DD（月末那天；页面上显示成「2026年10月31日」）
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_docs_file ON docs (kind, filename);

-- Phal 自己写下的每一段（记下来，是为了她心情低落删东西时只能删「她自己写的」，不碰你写 / 改过的）
CREATE TABLE IF NOT EXISTS doc_chunks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id      INTEGER NOT NULL,
    text        TEXT    NOT NULL,
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON doc_chunks (doc_id, id);

-- 她身边固定的人：家人和朋友。draft=1 是等我审阅的草稿（不进上下文）
CREATE TABLE IF NOT EXISTS people (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    kind        TEXT    NOT NULL DEFAULT 'friend',     -- family | friend
    relation    TEXT    NOT NULL DEFAULT '',
    personality TEXT    NOT NULL DEFAULT '',
    how         TEXT    NOT NULL DEFAULT '',
    recent      TEXT    NOT NULL DEFAULT '[]',         -- JSON：[{date, text}]，最近发生过的事
    draft       INTEGER NOT NULL DEFAULT 0,
    sort        INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL
);

-- 零花钱账本。金额的单位是「币」（1 钱 = 10 币），收入为正、花出去的为负；余额 = 全部相加
CREATE TABLE IF NOT EXISTS money_ledger (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT    NOT NULL,
    day         TEXT    NOT NULL,
    kind        TEXT    NOT NULL,                      -- grant 零花钱 | spend 消费 | adjust 手动调整 | expire 月底清零
    category    TEXT    NOT NULL DEFAULT '',           -- 奶茶 / 衣服 / 外卖 ……（money_defs 里的键）
    item        TEXT    NOT NULL DEFAULT '',
    delta       INTEGER NOT NULL,
    reason      TEXT    NOT NULL DEFAULT '',           -- 她的理由
    shared      INTEGER NOT NULL DEFAULT 0,            -- 她有没有主动跟我说过
    room_item   INTEGER NOT NULL DEFAULT 0,            -- 放进房间的物品 id
    event_id    INTEGER NOT NULL DEFAULT 0,            -- 事件日志里对应的那条
    room_book   INTEGER NOT NULL DEFAULT 0             -- 买的是书的话：房间里那本书（room_books）的 id
);
CREATE INDEX IF NOT EXISTS idx_money_day ON money_ledger (day, id);

-- 她房间里的东西。category / location 存的是当时的名字（设置里改了名字，页面上归到「其他」/「别处」，数据不动）
CREATE TABLE IF NOT EXISTS room_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    category    TEXT    NOT NULL DEFAULT '',
    location    TEXT    NOT NULL DEFAULT '',
    source      TEXT    NOT NULL DEFAULT 'original',      -- gift 我送的 | mail 我寄的 | bought 她自己买的 | original 本来就有的
    got_on      TEXT    NOT NULL,                         -- 获得日期 YYYY-MM-DD
    note        TEXT    NOT NULL DEFAULT '',              -- 她的一句备注
    created_at  TEXT    NOT NULL,
    look        TEXT    NOT NULL DEFAULT ''               -- 外观参数（JSON）；空 = 按名字自动生成
);

-- 书桌上的杂物区：买来的零食、奶茶这类「当日消耗品」，到点（至少半小时、最迟睡前）就收掉。时间是 epoch 秒
CREATE TABLE IF NOT EXISTS room_clutter (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT    NOT NULL,                         -- 食物种类：dessert 甜点 | milktea 奶茶 | snack 零食 | fruit 水果 | meal 饭菜
    name        TEXT    NOT NULL,
    look        TEXT    NOT NULL DEFAULT '',              -- 外观参数（JSON，随机生成）
    created_at  REAL    NOT NULL,
    expires_at  REAL    NOT NULL,                         -- 保质期到哪一刻（过期的在睡前清理书桌时收掉）
    ledger_id   INTEGER NOT NULL DEFAULT 0,
    place       TEXT    NOT NULL DEFAULT 'desk',          -- desk 书桌杂物区 | table 食物桌
    portions    INTEGER NOT NULL DEFAULT 1,               -- 一共分几次吃
    portions_left INTEGER NOT NULL DEFAULT 1,             -- 还剩几次
    source      TEXT    NOT NULL DEFAULT 'self'           -- self 自己买的 | user 你放的 | bake 烤的 | cook 做的 | takeout 外卖
);

-- 房间里的摆设书（不可阅读）：Phal 自己买的、或我手动加的。导入的书不在这里（它们在 books 表里，书脊按默认封面生成）
CREATE TABLE IF NOT EXISTS room_books (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT    NOT NULL,
    author      TEXT    NOT NULL DEFAULT '',
    look        TEXT    NOT NULL DEFAULT '{}',            -- 外观参数（JSON，见 app/booklook.py）：封面和书脊都由它生成
    place       TEXT    NOT NULL DEFAULT 'shelf',         -- shelf 书架（放满的堆在地上）| storage 收纳篮（不展示，但一直保存）
    source      TEXT    NOT NULL DEFAULT 'phal',          -- phal 她自己买的 | user 我手动加的
    price       INTEGER NOT NULL DEFAULT 0,               -- 买的时候花了多少币
    note        TEXT    NOT NULL DEFAULT '',
    got_on      TEXT    NOT NULL,
    created_at  TEXT    NOT NULL
);

-- 每份文档的故事笔记：notes 是 JSON 数组，第 i 项对应文档里第 i 段（用分隔符隔开的），写下一段之前先看它，保证情节往前走
CREATE TABLE IF NOT EXISTS doc_story (
    doc_id      INTEGER PRIMARY KEY,
    notes       TEXT    NOT NULL DEFAULT '[]',
    updated_at  TEXT    NOT NULL
);

-- 每次改动前的快照，随时可以撤销
CREATE TABLE IF NOT EXISTS doc_versions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id      INTEGER NOT NULL,
    content     TEXT    NOT NULL,
    reason      TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_versions_doc ON doc_versions (doc_id, id);

-- 节日：我在日历里添加的（名称、日期、是否每年重复、备注）
CREATE TABLE IF NOT EXISTS festivals (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    name               TEXT    NOT NULL,
    month              INTEGER NOT NULL,
    day                INTEGER NOT NULL,
    year               INTEGER,                         -- 只对「不每年重复」的有意义
    yearly             INTEGER NOT NULL DEFAULT 1,
    note               TEXT    NOT NULL DEFAULT '',
    last_greeted_year  INTEGER NOT NULL DEFAULT 0,      -- 哪一年已经发过祝福（每个节日每年只发一次）
    created_at         TEXT    NOT NULL
);

-- 用户亲手改过的 AI 回复：Phal 学说话风格时的「标准答案」
CREATE TABLE IF NOT EXISTS edits (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    msg_id      INTEGER NOT NULL,
    session_id  TEXT    NOT NULL,
    user_text   TEXT,                       -- 当时它回应的那句话
    before      TEXT    NOT NULL,           -- 最初的回复
    after       TEXT    NOT NULL,           -- 用户改成的样子
    created_at  TEXT    NOT NULL,
    ts          REAL    NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_edits_msg ON edits (session_id, msg_id);

-- 她一天里经历的事（读到精彩处、写成了东西……）：影响心情，也是晚上总结的材料
CREATE TABLE IF NOT EXISTS life_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    day         TEXT    NOT NULL,
    kind        TEXT    NOT NULL,
    text        TEXT    NOT NULL,
    mood        TEXT,
    created_at  TEXT    NOT NULL,
    ts          REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_day ON life_events (day, id);

-- Phal 每天的心情和一句话
CREATE TABLE IF NOT EXISTS moods (
    day         TEXT PRIMARY KEY,                          -- YYYY-MM-DD
    mood        TEXT    NOT NULL,
    line        TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    ts          REAL    NOT NULL
);
"""


# --------------------------------------------------------------------------
# 连接
# --------------------------------------------------------------------------
def connect() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.executescript(SCHEMA)
            _migrate(_conn)
            _conn.commit()
            _setup_fts(_conn)
        return _conn


def _migrate(conn: sqlite3.Connection) -> None:
    """给旧库补列（只加不删）。"""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(tasks)")}
    if "series_id" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN series_id INTEGER")
    if "priority" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN priority TEXT NOT NULL DEFAULT 'minor'")
    moodcols = {r["name"] for r in conn.execute("PRAGMA table_info(moods)")}
    if "kind" not in moodcols:
        conn.execute("ALTER TABLE moods ADD COLUMN kind TEXT NOT NULL DEFAULT 'baseline'")   # baseline 随机定的 | summary 晚上总结的
    if "recap" not in moodcols:
        conn.execute("ALTER TABLE moods ADD COLUMN recap TEXT NOT NULL DEFAULT ''")           # 当天经历的小结
    for col in ("v", "e", "base", "sleep"):                                                    # v8：当天的平均情绪 / 精力、早上的基线、昨晚的睡眠
        if col not in moodcols:
            conn.execute(f"ALTER TABLE moods ADD COLUMN {col} REAL")
    mcols = {r["name"] for r in conn.execute("PRAGMA table_info(messages)")}
    if "source" not in mcols:
        conn.execute("ALTER TABLE messages ADD COLUMN source TEXT")      # 主动消息的来源：reading / create / wish …
    if "meta" not in mcols:
        conn.execute("ALTER TABLE messages ADD COLUMN meta TEXT")        # JSON：引用的书、位置等
    scols = {r["name"] for r in conn.execute("PRAGMA table_info(task_series)")}
    if "priority" not in scols:
        conn.execute("ALTER TABLE task_series ADD COLUMN priority TEXT NOT NULL DEFAULT 'minor'")
    if "auto_adjust" not in scols:
        # 自动调整：拖延了一天完成，下一次就从完成那天重新起算
        conn.execute("ALTER TABLE task_series ADD COLUMN auto_adjust INTEGER NOT NULL DEFAULT 0")
    evcols = {r["name"] for r in conn.execute("PRAGMA table_info(life_events)")}
    if "shared" not in evcols:
        conn.execute("ALTER TABLE life_events ADD COLUMN shared INTEGER NOT NULL DEFAULT 1")     # 0 = 她没主动告诉我的事（不进日总结）
    if "etype" not in evcols:
        conn.execute("ALTER TABLE life_events ADD COLUMN etype TEXT")                            # 生活事件的类型（神秘学 / 和妈妈 / …）
    if "people" not in evcols:
        conn.execute("ALTER TABLE life_events ADD COLUMN people TEXT")                           # JSON：涉及的人物名字
    if "tone" not in evcols:
        conn.execute("ALTER TABLE life_events ADD COLUMN tone TEXT")                             # v9：基调 good 好事 / neutral 平常 / bad 不顺
    ccols = {r["name"] for r in conn.execute("PRAGMA table_info(room_clutter)")}
    for col, ddl in (("place", "TEXT NOT NULL DEFAULT 'desk'"), ("portions", "INTEGER NOT NULL DEFAULT 1"), ("portions_left", "INTEGER NOT NULL DEFAULT 1"),
                     ("source", "TEXT NOT NULL DEFAULT 'self'")):
        if ccols and col not in ccols:
            conn.execute(f"ALTER TABLE room_clutter ADD COLUMN {col} {ddl}")                   # v10：食物（放在哪、分几次吃、谁放的）
    ricols = {r["name"] for r in conn.execute("PRAGMA table_info(room_items)")}
    if "look" not in ricols:
        conn.execute("ALTER TABLE room_items ADD COLUMN look TEXT NOT NULL DEFAULT ''")        # 小物件的外观参数（空 = 自动生成）
    mlcols = {r["name"] for r in conn.execute("PRAGMA table_info(money_ledger)")}
    if "room_book" not in mlcols:
        conn.execute("ALTER TABLE money_ledger ADD COLUMN room_book INTEGER NOT NULL DEFAULT 0")
    doccols = {r["name"] for r in conn.execute("PRAGMA table_info(docs)")}
    if "dated" not in doccols:
        conn.execute("ALTER TABLE docs ADD COLUMN dated TEXT NOT NULL DEFAULT ''")        # 信的日期（月末那天）；别的文档不用
    memcols = {r["name"] for r in conn.execute("PRAGMA table_info(memories)")}
    if "category" not in memcols:
        # 长期记忆改成三类具体条目：about_user 关于她 / phal_fact Phal 自己说过的 / shared 之间发生过的
        conn.execute("ALTER TABLE memories ADD COLUMN category TEXT NOT NULL DEFAULT 'about_user'")
        conn.execute("UPDATE memories SET category = 'shared' WHERE kind = 'event'")
    if "replaced_by" not in memcols:
        conn.execute("ALTER TABLE memories ADD COLUMN replaced_by INTEGER")     # 被更新的记忆替换掉了（不再出现）
    # 以前 content 全局唯一；现在同一句话可以分属不同类别，所以改成 (类别, 内容) 唯一
    conn.execute("DROP INDEX IF EXISTS idx_memories_content")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_cat_content ON memories (category, content)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_series ON tasks (series_id, day)")


FTS_SCHEMA = """
-- trigram 分词：中文不用分词也能做子串匹配（要求关键词 >= 3 个字，更短的走 LIKE）
CREATE VIRTUAL TABLE IF NOT EXISTS archive_fts USING fts5(
    content, content='archive', content_rowid='id', tokenize='trigram'
);
CREATE TRIGGER IF NOT EXISTS archive_fts_ai AFTER INSERT ON archive BEGIN
    INSERT INTO archive_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS archive_fts_ad AFTER DELETE ON archive BEGIN
    INSERT INTO archive_fts(archive_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;
CREATE TRIGGER IF NOT EXISTS archive_fts_au AFTER UPDATE OF content ON archive BEGIN
    INSERT INTO archive_fts(archive_fts, rowid, content) VALUES ('delete', old.id, old.content);
    INSERT INTO archive_fts(rowid, content) VALUES (new.id, new.content);
END;
"""


def _setup_fts(conn: sqlite3.Connection) -> None:
    """建全文索引。SQLite 不支持 FTS5/trigram 时静默退回 LIKE。只加索引，不动 archive 里的数据。"""
    global _fts_ok
    try:
        existed = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='archive_fts'"
        ).fetchone()
        conn.executescript(FTS_SCHEMA)
        if not existed:
            conn.execute("INSERT INTO archive_fts(archive_fts) VALUES ('rebuild')")
        conn.commit()
        _fts_ok = True
    except sqlite3.Error as exc:
        log.warning("FTS5 不可用，历史检索退回 LIKE：%s", exc)
        _fts_ok = False


def seed_settings() -> dict[str, str]:
    """第一次建库时写进 settings 表的初始值：config 里的默认值，角色配置里调好的数值优先（名字、模型、温度、用词表……）。
    测试设 CHAT_CODE_DEFAULTS=1 时只用 config 里的。"""
    import json
    import os

    from . import character

    out = dict(DEFAULT_SETTINGS)
    if os.getenv("CHAT_CODE_DEFAULTS"):
        return out
    for k, v in (character.get("settings") or {}).items():
        if k in out:
            out[k] = ("1" if v else "0") if isinstance(v, bool) else (json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else str(v))
    return out


def init_db() -> None:
    global _settings_cache
    conn = connect()
    with _lock:
        _settings_cache = None
        for key, value in seed_settings().items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                (key, value, _stamp()),
            )
        conn.commit()
    ensure_session()


def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _rows(cur: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(r) for r in cur.fetchall()]


def _one(cur: sqlite3.Cursor) -> dict[str, Any] | None:
    row = cur.fetchone()
    return dict(row) if row else None


# --------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------
_settings_cache: dict[str, str] | None = None


def get_settings() -> dict[str, str]:
    """settings 很小但每个请求要读好几次，进程内缓存一份；save_settings 时失效。"""
    global _settings_cache
    conn = connect()
    with _lock:
        if _settings_cache is None:
            cur = conn.execute("SELECT key, value FROM settings")
            stored = {r["key"]: r["value"] for r in cur.fetchall()}
            merged = seed_settings()
            merged.update(stored)
            _settings_cache = merged
        return dict(_settings_cache)


def save_settings(values: dict[str, Any]) -> dict[str, str]:
    """只写 settings 表。

    这里**故意**不包含任何对 messages / archive 的操作：改人设绝不清空聊天记录。
    """
    conn = connect()
    payload = {k: ("" if v is None else str(v)) for k, v in values.items()}
    if not payload:
        return get_settings()
    stamp = _stamp()
    global _settings_cache
    with _lock:
        conn.executemany(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            [(k, v, stamp) for k, v in payload.items()],
        )
        conn.commit()
        _settings_cache = None
    return get_settings()


def get_setting(key: str, default: str = "") -> str:
    return get_settings().get(key, default)


# --------------------------------------------------------------------------
# 会话
# --------------------------------------------------------------------------
def ensure_session() -> str:
    sid = get_settings().get("current_session_id") or ""
    if not sid:
        sid = new_session()
    return sid


def current_session_id() -> str:
    return ensure_session()


def new_session() -> str:
    sid = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    save_settings({"current_session_id": sid})
    return sid


def clear_current_session() -> str:
    """开一段新对话：只清空当前会话窗口，archive 里的历史一条不动。"""
    clear_window(current_session_id())
    return new_session()


def list_sessions() -> list[dict[str, Any]]:
    conn = connect()
    with _lock:
        cur = conn.execute(
            "SELECT session_id, COUNT(*) AS n, MIN(created_at) AS started_at, MAX(created_at) AS ended_at "
            "FROM archive GROUP BY session_id ORDER BY MAX(ts) DESC LIMIT 50"
        )
        return _rows(cur)


# --------------------------------------------------------------------------
# 消息
# --------------------------------------------------------------------------
def _quote_snapshot(
    quote_id: int | None,
    fallback_text: str | None,
    fallback_role: str | None = None,
) -> tuple[str | None, str | None]:
    """返回 (quote_role, quote_text)。

    优先用数据库里的真实记录；找不到时退回到前端传上来的文本快照
    （比如引用的是历史归档里、已经不在当前会话中的消息）。
    """
    if quote_id:
        conn = connect()
        with _lock:
            cur = conn.execute(
                "SELECT role, content FROM messages WHERE id = ? "
                "UNION ALL SELECT role, content FROM archive WHERE id = ? LIMIT 1",
                (int(quote_id), int(quote_id)),
            )
            row = _one(cur)
        if row:
            return row["role"], row["content"]
    if fallback_text:
        return fallback_role, fallback_text
    return None, None


def add_message(
    session_id: str,
    role: str,
    content: str,
    quote_id: int | None = None,
    quote_text: str | None = None,
    quote_role: str | None = None,
    source: str | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """写入一条消息：messages（短期上下文来源）+ archive（全量历史，双写）。"""
    conn = connect()
    q_role, q_text = (None, None)
    if quote_id or quote_text:
        q_role, q_text = _quote_snapshot(quote_id, quote_text, quote_role)
    now = datetime.now()
    created_at = now.strftime("%Y-%m-%d %H:%M:%S")
    ts = now.timestamp()
    with _lock:
        cur = conn.execute(
            "INSERT INTO messages (session_id, role, content, quote_id, quote_role, quote_text, created_at, ts, source, meta) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, role, content, quote_id, q_role, q_text, created_at, ts, source,
             json.dumps(meta, ensure_ascii=False) if meta else None),
        )
        msg_id = cur.lastrowid
        conn.execute(
            "INSERT INTO archive (msg_id, session_id, role, content, quote_id, quote_role, quote_text, created_at, ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (msg_id, session_id, role, content, quote_id, q_role, q_text, created_at, ts),
        )
        conn.commit()
    return {
        "id": msg_id,
        "session_id": session_id,
        "role": role,
        "content": content,
        "quote_id": quote_id,
        "quote_role": q_role,
        "quote_text": q_text,
        "created_at": created_at,
        "ts": ts,
        "source": source,
        "meta": json.dumps(meta, ensure_ascii=False) if meta else None,
    }


def messages_after_id(after_id: int, source_only: bool = True, limit: int = 20) -> list[dict[str, Any]]:
    """某个 id 之后的新消息（页面轮询时拿 Phal 主动发来的）。"""
    conn = connect()
    sql = "SELECT * FROM messages WHERE id > ?"
    if source_only:
        sql += " AND source IS NOT NULL"
    with _lock:
        return _rows(conn.execute(sql + " ORDER BY id ASC LIMIT ?", (int(after_id), int(limit))))


def last_user_message_ts() -> float:
    with _lock:
        row = connect().execute("SELECT MAX(ts) AS t FROM messages WHERE role = 'user'").fetchone()
    return float(row["t"]) if row and row["t"] else 0.0


def recent_messages(session_id: str, limit: int, before_id: int | None = None) -> list[dict[str, Any]]:
    """最近 limit 条，按时间正序返回 —— 短期上下文。

    ``before_id`` 用来排除刚入库的那条新消息：拼上下文时新消息要单独放在最后，
    如果不排除，它会既出现在历史里、又被追加一次，模型就会看到同一句话说两遍。
    """
    conn = connect()
    sql = "SELECT * FROM (SELECT * FROM messages WHERE session_id = ?"
    params: list[Any] = [session_id]
    if before_id is not None:
        sql += " AND id < ?"
        params.append(int(before_id))
    sql += " ORDER BY id DESC LIMIT ?) ORDER BY id ASC"
    params.append(int(limit))
    with _lock:
        cur = conn.execute(sql, params)
        return _rows(cur)


def session_messages(session_id: str, limit: int = 200) -> list[dict[str, Any]]:
    conn = connect()
    with _lock:
        cur = conn.execute(
            "SELECT * FROM (SELECT * FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?) ORDER BY id ASC",
            (session_id, int(limit)),
        )
        return _rows(cur)


def messages_after(session_id: str, after_id: int, limit: int = 60) -> list[dict[str, Any]]:
    """某个 id 之后的新消息（自动整理记忆时只看上次整理之后新增的部分）。"""
    conn = connect()
    with _lock:
        cur = conn.execute(
            "SELECT * FROM (SELECT * FROM messages WHERE session_id = ? AND id > ? ORDER BY id DESC LIMIT ?) ORDER BY id ASC",
            (session_id, int(after_id), int(limit)),
        )
        return _rows(cur)


def get_message(message_id: int) -> dict[str, Any] | None:
    """按 id 读一条消息（连发：生成前现读，撤回了就是 None、改过了就是改后的）。"""
    conn = connect()
    with _lock:
        return _one(conn.execute("SELECT * FROM messages WHERE id = ?", (int(message_id),)))


def edit_message(message_id: int, content: str) -> dict[str, Any] | None:
    """用户改 AI 的回复：窗口 + 归档里的同一条一起改，并留下「改前 / 改后」当作风格范例。"""
    content = (content or "").strip()
    if not content:
        return None
    conn = connect()
    with _lock:
        row = _one(conn.execute("SELECT * FROM messages WHERE id = ?", (int(message_id),)))
        if not row:
            return None
        if row["content"] == content:
            return row
        prev_user = _one(conn.execute(
            "SELECT content FROM messages WHERE session_id = ? AND id < ? AND role = 'user' ORDER BY id DESC LIMIT 1",
            (row["session_id"], int(message_id)),
        ))
        conn.execute("UPDATE messages SET content = ? WHERE id = ?", (content, int(message_id)))
        conn.execute(
            "UPDATE archive SET content = ? WHERE session_id = ? AND msg_id = ?",
            (content, row["session_id"], int(message_id)),
        )
        if row["role"] == "assistant":
            now = datetime.now()
            existing = _one(conn.execute(
                "SELECT id FROM edits WHERE session_id = ? AND msg_id = ?", (row["session_id"], int(message_id))
            ))
            if existing:
                # 改了又改：保留最初的「改前」，只更新「改后」
                conn.execute(
                    "UPDATE edits SET after = ?, created_at = ?, ts = ? WHERE id = ?",
                    (content, now.strftime("%Y-%m-%d %H:%M:%S"), now.timestamp(), existing["id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO edits (msg_id, session_id, user_text, before, after, created_at, ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (int(message_id), row["session_id"], prev_user["content"] if prev_user else None,
                     row["content"], content, now.strftime("%Y-%m-%d %H:%M:%S"), now.timestamp()),
                )
        conn.commit()
        return _one(conn.execute("SELECT * FROM messages WHERE id = ?", (int(message_id),)))


def recent_edits(limit: int = 6) -> list[dict[str, Any]]:
    conn = connect()
    with _lock:
        return _rows(conn.execute("SELECT * FROM edits ORDER BY ts DESC LIMIT ?", (int(limit),)))


def count_edits() -> int:
    with _lock:
        return int(connect().execute("SELECT COUNT(*) FROM edits").fetchone()[0])


def count_assistant_archive() -> int:
    with _lock:
        return int(connect().execute("SELECT COUNT(*) FROM archive WHERE role = 'assistant'").fetchone()[0])


def style_samples(recent: int = 40, spread: int = 60) -> list[str]:
    """全部历史里 AI 的回复样本：最近的 recent 条 + 更早的均匀抽 spread 条（覆盖整个聊天历史）。"""
    conn = connect()
    with _lock:
        rows = [r["content"] for r in conn.execute(
            "SELECT content FROM archive WHERE role = 'assistant' AND length(content) >= 4 ORDER BY ts ASC"
        )]
    if len(rows) <= recent + spread:
        return rows
    older, newest = rows[:-recent], rows[-recent:]
    step = len(older) / spread
    picked = [older[int(i * step)] for i in range(spread)]
    return picked + newest


def previous_session_tail(session_id: str, n: int = 6) -> list[dict[str, Any]]:
    """上一段对话（新开对话之前）的最后几条，让它接得上「上次聊到哪」。"""
    conn = connect()
    with _lock:
        last = _one(conn.execute(
            "SELECT session_id FROM archive WHERE session_id <> ? ORDER BY ts DESC LIMIT 1", (session_id,)
        ))
        if not last:
            return []
        rows = _rows(conn.execute(
            "SELECT * FROM (SELECT * FROM archive WHERE session_id = ? ORDER BY id DESC LIMIT ?) ORDER BY id ASC",
            (last["session_id"], int(n)),
        ))
    return rows


def max_message_id(session_id: str) -> int:
    conn = connect()
    with _lock:
        cur = conn.execute("SELECT COALESCE(MAX(id), 0) AS m FROM messages WHERE session_id = ?", (session_id,))
        row = _one(cur)
    return int(row["m"]) if row else 0


# --------------------------------------------------------------------------
# 历史归档检索（给模型的 search_history 工具 & 界面的搜索框共用）
# --------------------------------------------------------------------------
def _like_escape(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_archive(
    query: str,
    limit: int = 6,
    exclude_session: str | None = None,
    newest_first: bool = True,
) -> tuple[list[dict[str, Any]], int]:
    """在全部历史归档里检索。

    * 多个关键词是「或」：命中的词越多排得越前，同分按时间。
      （旧版要求每个词都命中，模型一次传两三个词经常一条都搜不到。）
    * 3 个字以上的词走 FTS5 trigram 索引；更短的词走 LIKE。
    """
    terms = list(dict.fromkeys(t for t in re.split(r"\s+", (query or "").strip()) if t))[:6]
    conn = connect()

    match_parts: list[str] = []
    match_params: list[Any] = []
    score_parts: list[str] = []
    score_params: list[Any] = []
    for term in terms:
        if _fts_ok and len(term) >= 3:
            phrase = '"' + term.replace('"', '""') + '"'
            match_parts.append("id IN (SELECT rowid FROM archive_fts WHERE archive_fts MATCH ?)")
            match_params.append(phrase)
        else:
            match_parts.append("content LIKE ? ESCAPE '\\'")
            match_params.append(f"%{_like_escape(term)}%")
        score_parts.append("(instr(content, ?) > 0)")
        score_params.append(term)

    where: list[str] = []
    params: list[Any] = []
    if match_parts:
        where.append("(" + " OR ".join(match_parts) + ")")
        params += match_params
    if exclude_session:
        where.append("session_id <> ?")
        params.append(exclude_session)
    clause = ("WHERE " + " AND ".join(where)) if where else ""

    direction = "DESC" if newest_first else "ASC"
    score_expr = " + ".join(score_parts) if score_parts else "0"
    with _lock:
        total_row = _one(conn.execute(f"SELECT COUNT(*) AS n FROM archive {clause}", params))
        cur = conn.execute(
            f"SELECT *, ({score_expr}) AS score FROM archive {clause} "
            f"ORDER BY score DESC, ts {direction} LIMIT ?",
            score_params + params + [int(limit)],
        )
        rows = _rows(cur)
    for r in rows:
        r.pop("score", None)
    return rows, int(total_row["n"]) if total_row else 0


def archive_context_lines(rows: Iterable[dict[str, Any]], body_limit: int = 320) -> str:
    """把归档记录格式化成给模型看的文本。"""
    labels = {"user": "用户", "assistant": "你", "system": "系统"}
    lines = []
    for r in rows:
        body = (r["content"] or "").strip().replace("\n", " ")
        if len(body) > body_limit:
            body = body[:body_limit] + "…"
        lines.append(f"[{r['created_at']}] {labels.get(r['role'], r['role'])}：{body}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 长期记忆
# --------------------------------------------------------------------------
_KIND_PRIORITY = "CASE kind WHEN 'fact' THEN 0 WHEN 'preference' THEN 0 WHEN 'todo' THEN 1 ELSE 2 END"


def list_memories(limit: int = 50, category: str | None = None) -> list[dict[str, Any]]:
    """记忆库列表（界面用）：新的在前。被更新的记忆替换掉的不显示。"""
    conn = connect()
    sql = "SELECT * FROM memories WHERE replaced_by IS NULL"
    params: list[Any] = []
    if category:
        sql += " AND category = ?"
        params.append(category)
    with _lock:
        return _rows(conn.execute(sql + " ORDER BY ts DESC LIMIT ?", params + [int(limit)]))


def get_memory(memory_id: int) -> dict[str, Any] | None:
    with _lock:
        return _one(connect().execute("SELECT * FROM memories WHERE id = ?", (int(memory_id),)))


def supersede_memory(old_id: int, new_id: int) -> None:
    """冲突时以新的为准：旧的标记为被替换，之后不再出现。"""
    with _lock:
        conn = connect()
        conn.execute("UPDATE memories SET replaced_by = ? WHERE id = ?", (int(new_id), int(old_id)))
        conn.commit()


def memories_for_context(limit: int) -> list[dict[str, Any]]:
    """注入上下文的记忆：长期有效的（事实 / 偏好）优先，其次待办，最后才是一次性的事件。

    同一类里新的在前。这样不会出现「十条最近的小事把名字和偏好挤出去」。
    """
    conn = connect()
    with _lock:
        cur = conn.execute(
            f"SELECT * FROM memories WHERE replaced_by IS NULL ORDER BY {_KIND_PRIORITY}, ts DESC LIMIT ?", (int(limit),)
        )
        return _rows(cur)


_PUNCT = re.compile(r"[\s，,。.！!？?；;：:、\"'「」『』（）()\-—~·…]+")


def _norm(text: str) -> str:
    return _PUNCT.sub("", text or "").lower()


def _is_duplicate(content: str, existing: list[str]) -> bool:
    """措辞略有不同的重复记忆也算重复：规整后相等 / 互相包含 / 相似度很高。"""
    n = _norm(content)
    if not n:
        return True
    nums = re.findall(r"[0-9零一二三四五六七八九十百千万两]+", content)
    for other in existing:
        # 数字 / 价格不一样（「六十钱」「七十钱」）是不同的事实，不算重复：交给 replaces 去更新
        if re.findall(r"[0-9零一二三四五六七八九十百千万两]+", other) != nums:
            continue
        o = _norm(other)
        if not o:
            continue
        if n == o:
            return True
        if min(len(n), len(o)) >= 6 and (n in o or o in n):
            return True
        if difflib.SequenceMatcher(None, n, o).ratio() >= 0.85:
            return True
    return False


MEMORY_CATEGORIES = ("about_user", "phal_fact", "shared")


def add_memory(
    content: str, kind: str = "fact", session_id: str | None = None, source: str = "manual",
    category: str | None = None,
) -> dict[str, Any] | None:
    content = (content or "").strip()
    if not content:
        return None
    if category not in MEMORY_CATEGORIES:
        category = "shared" if kind == "event" else "about_user"
    now = datetime.now()
    conn = connect()
    with _lock:
        existing = [r["content"] for r in conn.execute(
            "SELECT content FROM memories WHERE category = ? AND replaced_by IS NULL ORDER BY ts DESC LIMIT 300", (category,))]
        if _is_duplicate(content, existing):
            return None
        cur = conn.execute(
            "INSERT OR IGNORE INTO memories (kind, category, content, session_id, source, created_at, ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (kind, category, content, session_id, source, now.strftime("%Y-%m-%d %H:%M:%S"), now.timestamp()),
        )
        conn.commit()
        if cur.rowcount == 0:
            return None
        mem_id = cur.lastrowid
    return {"id": mem_id, "kind": kind, "category": category, "content": content,
            "created_at": now.strftime("%Y-%m-%d %H:%M:%S")}


def delete_memory(memory_id: int) -> bool:
    conn = connect()
    with _lock:
        cur = conn.execute("DELETE FROM memories WHERE id = ?", (int(memory_id),))
        conn.commit()
    return cur.rowcount > 0


def clear_memories() -> int:
    conn = connect()
    with _lock:
        cur = conn.execute("DELETE FROM memories")
        conn.commit()
    return cur.rowcount


# --------------------------------------------------------------------------
# 删除：网页上看到的和本地存的，一次删干净
#
# messages（当前窗口）和 archive（历史归档）是两张表，只删一张会导致
# "网页上没了但库里还在"或者反过来。所以这里所有删除动作都是成对执行的。
# --------------------------------------------------------------------------
def delete_message(message_id: int) -> dict[str, int]:
    """删掉一条消息：当前窗口 + 历史归档里对应的那条，一起删。"""
    conn = connect()
    with _lock:
        row = _one(conn.execute("SELECT session_id FROM messages WHERE id = ?", (int(message_id),)))
        window = archive = 0
        if row:
            cur = conn.execute(
                "DELETE FROM archive WHERE session_id = ? AND msg_id = ?",
                (row["session_id"], int(message_id)),
            )
            archive = cur.rowcount
            cur = conn.execute("DELETE FROM messages WHERE id = ?", (int(message_id),))
            window = cur.rowcount
            conn.execute("DELETE FROM edits WHERE session_id = ? AND msg_id = ?", (row["session_id"], int(message_id)))
        conn.commit()
    return {"window": window, "archive": archive}


def delete_archive_entry(archive_id: int) -> dict[str, int]:
    """删掉历史归档里的一条（如果它还在当前窗口里，窗口里那条也一起删）。"""
    conn = connect()
    with _lock:
        row = _one(conn.execute("SELECT session_id, msg_id FROM archive WHERE id = ?", (int(archive_id),)))
        window = archive = 0
        if row:
            cur = conn.execute("DELETE FROM archive WHERE id = ?", (int(archive_id),))
            archive = cur.rowcount
            if row["msg_id"]:
                cur = conn.execute(
                    "DELETE FROM messages WHERE id = ? AND session_id = ?",
                    (int(row["msg_id"]), row["session_id"]),
                )
                window = cur.rowcount
        conn.commit()
    return {"window": window, "archive": archive}


def clear_window(session_id: str) -> int:
    """只清当前会话的窗口（archive 保留）——「开新对话」用这个。"""
    conn = connect()
    with _lock:
        cur = conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        conn.commit()
    return cur.rowcount


def clear_conversation(session_id: str) -> dict[str, int]:
    """彻底删掉当前这段对话：窗口 + 归档里同一段会话，一起删。"""
    conn = connect()
    with _lock:
        cur = conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        window = cur.rowcount
        cur = conn.execute("DELETE FROM archive WHERE session_id = ?", (session_id,))
        archive = cur.rowcount
        conn.execute("DELETE FROM edits WHERE session_id = ?", (session_id,))
        conn.commit()
    return {"window": window, "archive": archive}


def clear_all_history(with_memories: bool = False) -> dict[str, int]:
    """清空全部聊天记录（窗口 + 归档），可选连长期记忆一起清。"""
    conn = connect()
    with _lock:
        window = conn.execute("DELETE FROM messages").rowcount
        archive = conn.execute("DELETE FROM archive").rowcount
        conn.execute("DELETE FROM edits")
        memories = conn.execute("DELETE FROM memories").rowcount if with_memories else 0
        conn.commit()
    return {"window": window, "archive": archive, "memories": memories}


# --------------------------------------------------------------------------
# 日历：任务
# --------------------------------------------------------------------------
TASK_STATUS = ("open", "done", "skipped")


def _task_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def add_task(
    title: str,
    day: str,
    remind_after_days: int = 3,
    source: str = "manual",
    note: str | None = None,
    series_id: int | None = None,
    priority: str = "minor",
) -> dict[str, Any]:
    title = (title or "").strip()
    if not title:
        raise ValueError("任务内容不能为空")
    now = datetime.now()
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    with _lock:
        conn = connect()
        cur = conn.execute(
            "INSERT INTO tasks (title, day, status, remind_after_days, note, source, series_id, priority, created_at, updated_at, ts) "
            "VALUES (?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?)",
            (title, day, max(0, int(remind_after_days)), note, source, series_id,
             "main" if priority == "main" else "minor", stamp, stamp, now.timestamp()),
        )
        conn.commit()
        return _task_row(conn.execute("SELECT * FROM tasks WHERE id = ?", (cur.lastrowid,)).fetchone())


# 重复任务：系列 + 提前生成的实例
REPEAT_HORIZON_DAYS = 60      # 往后提前生成多远
REPEAT_MAX_PER_CALL = 120     # 单次最多生成多少条（防止 repeat_every=1 且很久没打开时爆量）


def get_series(series_id: int) -> dict[str, Any] | None:
    with _lock:
        conn = connect()
        return _task_row(conn.execute("SELECT * FROM task_series WHERE id = ?", (int(series_id),)).fetchone())


def create_series(
    title: str, first_day: str, repeat_every: int, remind_after_days: int = 3, source: str = "manual",
    priority: str = "minor", auto_adjust: bool = False,
) -> dict[str, Any]:
    """建一个重复系列，并把第一条（first_day 当天）以及到 horizon 之前的实例都生成出来。返回第一条任务。"""
    from datetime import date, timedelta

    every = max(1, int(repeat_every))
    with _lock:
        conn = connect()
        cur = conn.execute(
            "INSERT INTO task_series (title, repeat_every, priority, remind_after_days, next_day, active, created_at, auto_adjust) "
            "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
            (title.strip(), every, "main" if priority == "main" else "minor",
             max(0, int(remind_after_days)), first_day, _stamp(), 1 if auto_adjust else 0),
        )
        series_id = cur.lastrowid
        conn.commit()
    first = None
    # 第一条是用户指定的那天；之后按间隔继续
    topup_series(series_id, max(date.today(), date.fromisoformat(first_day)) + timedelta(days=REPEAT_HORIZON_DAYS), source=source)
    with _lock:
        row = connect().execute(
            "SELECT * FROM tasks WHERE series_id = ? ORDER BY day ASC, id ASC LIMIT 1", (series_id,)
        ).fetchone()
        first = _task_row(row)
    return first  # type: ignore[return-value]


def topup_series(series_id: int, horizon: Any, source: str = "manual") -> int:
    """把某个系列的实例补到 horizon（date）为止，返回新生成条数。"""
    from datetime import date, timedelta

    made = 0
    with _lock:
        series = get_series(series_id)
        if not series or not series["active"]:
            return 0
        every = max(1, int(series["repeat_every"]))
        day = date.fromisoformat(series["next_day"])
        while day <= horizon and made < REPEAT_MAX_PER_CALL:
            add_task(
                series["title"], day.isoformat(), series["remind_after_days"],
                source="repeat" if source == "manual" else source, series_id=series_id,
                priority=series.get("priority") or "minor",
            )
            made += 1
            day += timedelta(days=every)
        if made:
            conn = connect()
            conn.execute("UPDATE task_series SET next_day = ? WHERE id = ?", (day.isoformat(), series_id))
            conn.commit()
    return made


def topup_all_series() -> int:
    """日历每次打开 / 启动时调用：所有活跃的重复系列都补齐到未来 REPEAT_HORIZON_DAYS 天。"""
    from datetime import date, timedelta

    horizon = date.today() + timedelta(days=REPEAT_HORIZON_DAYS)
    with _lock:
        ids = [r["id"] for r in connect().execute("SELECT id FROM task_series WHERE active = 1")]
    return sum(topup_series(i, horizon) for i in ids)


def stop_series(series_id: int, from_day: str | None = None) -> int:
    """停止重复，并删掉 from_day（含）之后还没做的实例。from_day 为空 = 只停止，不删已有的。"""
    removed = 0
    with _lock:
        conn = connect()
        conn.execute("UPDATE task_series SET active = 0 WHERE id = ?", (int(series_id),))
        if from_day:
            cur = conn.execute(
                "DELETE FROM tasks WHERE series_id = ? AND day >= ? AND status = 'open'",
                (int(series_id), from_day),
            )
            removed = cur.rowcount
            # 删光的实例不再有 series 引用意义；已盖章的保留，仍记着它属于哪个系列
        conn.commit()
    return removed


def active_series() -> list[dict[str, Any]]:
    with _lock:
        return _rows(connect().execute("SELECT * FROM task_series WHERE active = 1"))


def update_task_series(task_id: int, series_id: int | None) -> dict[str, Any] | None:
    with _lock:
        conn = connect()
        conn.execute("UPDATE tasks SET series_id = ? WHERE id = ?", (series_id, int(task_id)))
        conn.commit()
    return get_task(task_id)


def start_series_from(task: dict[str, Any], every: int, auto_adjust: bool = False) -> int:
    """把已有的一条任务变成重复系列的第一条：之后的实例从 day + every 开始生成。"""
    from datetime import date, timedelta

    every = max(1, int(every))
    first_next = (date.fromisoformat(task["day"]) + timedelta(days=every)).isoformat()
    with _lock:
        conn = connect()
        cur = conn.execute(
            "INSERT INTO task_series (title, repeat_every, priority, remind_after_days, next_day, active, created_at, auto_adjust) "
            "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
            (task["title"], every, task.get("priority") or "minor", task["remind_after_days"], first_next, _stamp(),
             1 if auto_adjust else 0),
        )
        sid = cur.lastrowid
        conn.commit()
    topup_series(sid, date.today() + timedelta(days=REPEAT_HORIZON_DAYS))
    return sid


def set_series_auto_adjust(series_id: int, on: bool) -> None:
    with _lock:
        conn = connect()
        conn.execute("UPDATE task_series SET auto_adjust = ? WHERE id = ?", (1 if on else 0, int(series_id)))
        conn.commit()


def rebase_series(series_id: int, from_day: str) -> int:
    """自动调整：从 from_day（完成那天）重新起算——它之后还没做的旧实例删掉，下一次排在 from_day + 间隔。返回删掉几条。"""
    from datetime import date, timedelta

    series = get_series(series_id)
    if not series or not series["active"]:
        return 0
    every = max(1, int(series["repeat_every"]))
    with _lock:
        conn = connect()
        cur = conn.execute(
            "DELETE FROM tasks WHERE series_id = ? AND day > ? AND status = 'open'", (int(series_id), from_day)
        )
        removed = cur.rowcount
        conn.execute("UPDATE task_series SET next_day = ? WHERE id = ?",
                     ((date.fromisoformat(from_day) + timedelta(days=every)).isoformat(), int(series_id)))
        conn.commit()
    topup_series(series_id, date.today() + timedelta(days=REPEAT_HORIZON_DAYS))
    return removed


def reset_delay(task: dict[str, Any], today: str) -> dict[str, Any] | None:
    """取消拖延：把这条任务挪到今天，拖延天数清零。重复任务本身不受影响（后面的实例照旧）。
    同名、今天及以前还没做的其它实例并入这一条（同名任务同时只留一个）。"""
    with _lock:
        conn = connect()
        conn.execute(
            "DELETE FROM tasks WHERE status = 'open' AND title = ? AND day <= ? AND id <> ?",
            (task["title"], today, task["id"]),
        )
        conn.commit()
    return update_task(task["id"], day=today)


def collapse_series(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同名任务（含重复任务拖了好几条）只留一条给提醒 / 上下文：同名最多同时出现一个。
    留最早的那条（它的拖延天数最能说明问题）。"""
    first: dict[str, dict[str, Any]] = {}
    for t in tasks:
        key = (t.get("title") or "").strip()
        if key not in first or (t["day"], t["id"]) < (first[key]["day"], first[key]["id"]):
            first[key] = t
    return sorted(first.values(), key=lambda t: (t["day"], t["id"]))


def overdue_open(today: str) -> list[dict[str, Any]]:
    """今天之前就该做、还没做的任务。"""
    with _lock:
        return _rows(connect().execute(
            "SELECT * FROM tasks WHERE status = 'open' AND day < ? ORDER BY day ASC, id ASC", (today,)
        ))


def overdue_stamped_today(today: str) -> list[dict[str, Any]]:
    """今天之前的任务、但是今天才盖的章（今天还留在今天那一格，隔天回原日期）。"""
    with _lock:
        return _rows(connect().execute(
            "SELECT * FROM tasks WHERE status <> 'open' AND day < ? AND substr(updated_at, 1, 10) = ? "
            "ORDER BY updated_at DESC, id DESC", (today, today),
        ))


def close_open_siblings(task: dict[str, Any], status: str, today: str) -> int:
    """一条拖延的任务被标记了，同名的其它没做的（前几天漏掉的重复实例，以及今天自己那条）一并标掉：
    它们在日历上本来就是合并成一条显示的，免得第二天又冒出来，或者今天出现两条。"""
    stamp = _stamp()
    tone = {"done": "green", "skipped": "red"}.get(status)
    label = {"done": "完成", "skipped": "不干"}.get(status)
    with _lock:
        conn = connect()
        cur = conn.execute(
            "UPDATE tasks SET status = ?, stamp_label = ?, stamp_tone = ?, updated_at = ? "
            "WHERE status = 'open' AND title = ? AND day <= ? AND id <> ?",
            (status, label, tone, stamp, task["title"], today, task["id"]),
        )
        conn.commit()
        return cur.rowcount


def update_series(
    series_id: int, from_day: str, title: str | None = None, remind: int | None = None,
    priority: str | None = None,
) -> None:
    """改了一条重复任务的名字 / 提醒天数：系列本身和这一天起还没做的实例一起改。"""
    with _lock:
        conn = connect()
        if title is not None:
            conn.execute("UPDATE task_series SET title = ? WHERE id = ?", (title, int(series_id)))
            conn.execute(
                "UPDATE tasks SET title = ? WHERE series_id = ? AND day >= ? AND status = 'open'",
                (title, int(series_id), from_day),
            )
        if priority is not None:
            conn.execute("UPDATE task_series SET priority = ? WHERE id = ?", (priority, int(series_id)))
            conn.execute(
                "UPDATE tasks SET priority = ? WHERE series_id = ? AND day >= ? AND status = 'open'",
                (priority, int(series_id), from_day),
            )
        if remind is not None:
            conn.execute("UPDATE task_series SET remind_after_days = ? WHERE id = ?", (int(remind), int(series_id)))
            conn.execute(
                "UPDATE tasks SET remind_after_days = ? WHERE series_id = ? AND day >= ? AND status = 'open'",
                (int(remind), int(series_id), from_day),
            )
        conn.commit()


def get_task(task_id: int) -> dict[str, Any] | None:
    with _lock:
        conn = connect()
        return _task_row(conn.execute("SELECT * FROM tasks WHERE id = ?", (int(task_id),)).fetchone())


def month_overview(first_day: str, last_day: str) -> dict[str, dict[str, int]]:
    """月历每一天的任务概况：总数 / 没做 / 完成 / 不干。"""
    with _lock:
        rows = _rows(connect().execute(
            "SELECT day, status, COUNT(*) AS n FROM tasks WHERE day >= ? AND day <= ? GROUP BY day, status",
            (first_day, last_day),
        ))
    out: dict[str, dict[str, int]] = {}
    for r in rows:
        d = out.setdefault(r["day"], {"total": 0, "open": 0, "done": 0, "skipped": 0})
        d["total"] += r["n"]
        d[r["status"]] = d.get(r["status"], 0) + r["n"]
    return out


def update_task(task_id: int, **fields: Any) -> dict[str, Any] | None:
    allowed = {"title", "day", "status", "stamp_label", "stamp_tone", "remind_after_days", "note", "reminded_on", "priority"}
    patch = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not patch:
        return get_task(task_id)
    if "status" in patch and patch["status"] not in TASK_STATUS:
        raise ValueError(f"status 只能是 {TASK_STATUS}")
    patch["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    columns = ", ".join(f"{k} = ?" for k in patch)
    with _lock:
        conn = connect()
        cur = conn.execute(f"UPDATE tasks SET {columns} WHERE id = ?", (*patch.values(), int(task_id)))
        conn.commit()
        if cur.rowcount == 0:
            return None
        return _task_row(conn.execute("SELECT * FROM tasks WHERE id = ?", (int(task_id),)).fetchone())


def delete_task(task_id: int) -> bool:
    with _lock:
        conn = connect()
        cur = conn.execute("DELETE FROM tasks WHERE id = ?", (int(task_id),))
        conn.commit()
    return cur.rowcount > 0


def tasks_on(day: str) -> list[dict[str, Any]]:
    with _lock:
        conn = connect()
        cur = conn.execute("SELECT * FROM tasks WHERE day = ? ORDER BY id ASC", (day,))
        return _rows(cur)


def tasks_between(start: str, end: str) -> list[dict[str, Any]]:
    with _lock:
        conn = connect()
        cur = conn.execute(
            "SELECT * FROM tasks WHERE day >= ? AND day <= ? ORDER BY day ASC, id ASC", (start, end)
        )
        return _rows(cur)


def open_tasks_upto(day: str) -> list[dict[str, Any]]:
    """到今天为止还没做完的任务（含拖了很多天的）。"""
    with _lock:
        conn = connect()
        cur = conn.execute(
            "SELECT * FROM tasks WHERE status = 'open' AND day <= ? ORDER BY day ASC, id ASC", (day,)
        )
        return _rows(cur)


def mark_reminded(task_ids: list[int], day: str) -> None:
    if not task_ids:
        return
    marks = ",".join("?" for _ in task_ids)
    with _lock:
        conn = connect()
        conn.execute(f"UPDATE tasks SET reminded_on = ? WHERE id IN ({marks})", (day, *task_ids))
        conn.commit()


def first_message_day() -> str | None:
    """最早一条记录是哪天（Phal「存在」的第一天）。"""
    with _lock:
        conn = connect()
        row = _one(conn.execute("SELECT MIN(substr(created_at, 1, 10)) AS d FROM archive"))
    return row["d"] if row and row["d"] else None


def archive_on(day: str, limit: int = 80) -> list[dict[str, Any]]:
    """某一天的聊天记录（按时间正序）。"""
    with _lock:
        conn = connect()
        cur = conn.execute(
            "SELECT * FROM (SELECT * FROM archive WHERE substr(created_at, 1, 10) = ? "
            "ORDER BY id DESC LIMIT ?) ORDER BY id ASC",
            (day, int(limit)),
        )
        return _rows(cur)


# --------------------------------------------------------------------------
# 日历：每天的心情
# --------------------------------------------------------------------------
def get_mood(day: str) -> dict[str, Any] | None:
    with _lock:
        conn = connect()
        return _task_row(conn.execute("SELECT * FROM moods WHERE day = ?", (day,)).fetchone())


def moods_between(start: str, end: str) -> dict[str, dict[str, Any]]:
    with _lock:
        conn = connect()
        cur = conn.execute("SELECT * FROM moods WHERE day >= ? AND day <= ?", (start, end))
        return {r["day"]: dict(r) for r in cur.fetchall()}


def add_event(day: str, kind: str, text: str, mood: str | None = None, shared: bool = True,
              etype: str | None = None, people: str | None = None, tone: str | None = None) -> int:
    now = datetime.now()
    with _lock:
        conn = connect()
        cur = conn.execute(
            "INSERT INTO life_events (day, kind, text, mood, created_at, ts, shared, etype, people, tone) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (day, kind, text[:120], mood, now.strftime("%Y-%m-%d %H:%M:%S"), now.timestamp(), int(shared), etype, people, tone),
        )
        conn.commit()
        return int(cur.lastrowid)


def mark_event_shared(event_id: int, shared: bool = True) -> None:
    with _lock:
        conn = connect()
        conn.execute("UPDATE life_events SET shared = ? WHERE id = ?", (int(shared), int(event_id)))
        conn.commit()


def events_on(day: str, limit: int = 40, shared_only: bool = False) -> list[dict[str, Any]]:
    """某天的事件。shared_only=True：去掉她没主动告诉我的生活事件（日总结用）。"""
    with _lock:
        return _rows(connect().execute(
            "SELECT * FROM life_events WHERE day = ?" + (" AND shared = 1" if shared_only else "") + " ORDER BY id ASC LIMIT ?",
            (day, int(limit)),
        ))


def upsert_mood(day: str, mood: str, line: str, kind: str = "baseline", recap: str = "") -> dict[str, Any]:
    now = datetime.now()
    with _lock:
        conn = connect()
        conn.execute(
            "INSERT INTO moods (day, mood, line, kind, recap, created_at, ts) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(day) DO UPDATE SET mood = excluded.mood, line = excluded.line, kind = excluded.kind, "
            "recap = excluded.recap, created_at = excluded.created_at, ts = excluded.ts",
            (day, mood, line, kind, recap, now.strftime("%Y-%m-%d %H:%M:%S"), now.timestamp()),
        )
        conn.commit()
        return _task_row(conn.execute("SELECT * FROM moods WHERE day = ?", (day,)).fetchone())


def set_mood_numbers(day: str, **cols: float | None) -> None:
    """记某天的心情数值（v 平均情绪、e 平均精力、base 早上的基线、sleep 昨晚睡眠）。那天还没有心情行就先不记。"""
    cols = {k: float(v) for k, v in cols.items() if k in ("v", "e", "base", "sleep") and v is not None}
    if not cols:
        return
    with _lock:
        conn = connect()
        conn.execute(f"UPDATE moods SET {', '.join(f'{k} = ?' for k in cols)} WHERE day = ?", (*cols.values(), day))
        conn.commit()


def stats() -> dict[str, int]:
    conn = connect()
    with _lock:
        def count(sql: str, params: tuple = ()) -> int:
            row = _one(conn.execute(sql, params))
            return int(row["n"]) if row else 0

        return {
            "session_messages": count("SELECT COUNT(*) AS n FROM messages WHERE session_id = ?", (current_session_id(),)),
            "archive_messages": count("SELECT COUNT(*) AS n FROM archive"),
            "memories": count("SELECT COUNT(*) AS n FROM memories WHERE replaced_by IS NULL"),
            "sessions": count("SELECT COUNT(DISTINCT session_id) AS n FROM archive"),
        }
