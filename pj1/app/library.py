"""书架 / 注释 / 读书笔记 / 草稿本 / 主动消息记录 的数据读写。"""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any

from . import books as bk
from . import db

PHAL_STATUS = ("unread", "reading", "paused", "finished", "dropped")
STATUS_LABEL = {
    "unread": "还没读", "reading": "在读", "paused": "搁置了", "finished": "读完了", "dropped": "弃了",
}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _q(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    with db._lock:  # noqa: SLF001
        return [dict(r) for r in db.connect().execute(sql, params).fetchall()]


def _one(sql: str, params: tuple = ()) -> dict[str, Any] | None:
    rows = _q(sql, params)
    return rows[0] if rows else None


def _exec(sql: str, params: tuple = ()) -> int:
    with db._lock:  # noqa: SLF001
        conn = db.connect()
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid if sql.lstrip().upper().startswith("INSERT") else cur.rowcount


# --------------------------------------------------------------------------
# 书架
# --------------------------------------------------------------------------
def add_book(parsed: dict[str, Any], original: bytes, filename: str) -> dict[str, Any]:
    now = datetime.now()
    book_id = _exec(
        "INSERT INTO books (title, author, filename, fmt, chars, n_chapters, added_at, ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (parsed["title"].strip(), parsed["author"].strip(), filename, parsed["fmt"], parsed["chars"],
         len(parsed["chapters"]), now.strftime("%Y-%m-%d %H:%M:%S"), now.timestamp()),
    )
    bk.save_book_files(book_id, original, filename, parsed["chapters"], parsed.get("pages"))
    return get_book(book_id)  # type: ignore[return-value]


def get_book(book_id: int) -> dict[str, Any] | None:
    return _one("SELECT * FROM books WHERE id = ?", (int(book_id),))


def update_book(book_id: int, title: str | None = None, author: str | None = None) -> dict[str, Any] | None:
    """改书名 / 作者（房间里右键「修改书籍详情」用）。书名不能为空；书的文件、进度、批注都按 id 存，不受影响。"""
    if not get_book(book_id):
        return None
    patch: dict[str, Any] = {}
    if title is not None:
        t = title.strip().strip("《》")
        if not t:
            raise ValueError("书名不能为空")
        patch["title"] = t[:80]
    if author is not None:
        patch["author"] = author.strip()[:40]
    if patch:
        _exec(f"UPDATE books SET {', '.join(f'{k} = ?' for k in patch)} WHERE id = ?", (*patch.values(), int(book_id)))
    return get_book(book_id)


def list_books() -> list[dict[str, Any]]:
    rows = _q("SELECT * FROM books ORDER BY COALESCE(my_read_at, added_at) DESC, id DESC")
    for r in rows:
        r["phal_status_label"] = STATUS_LABEL.get(r["phal_status"], r["phal_status"])
        r["my_percent"] = percent(r, "my")
        r["phal_percent"] = percent(r, "phal")
    return rows


def percent(book: dict[str, Any], who: str) -> int:
    """粗略读到百分之几（按章节数 + 章内偏移估算，只用来显示）。"""
    n = max(1, int(book["n_chapters"]))
    ch = int(book[f"{who}_chapter"])
    off = int(book[f"{who}_offset"])
    if who == "phal" and book["phal_status"] == "finished":
        return 100
    chapters = bk.load_chapters(book["id"])
    inner = 0.0
    if chapters and 0 <= ch < len(chapters):
        inner = min(1.0, off / max(1, len(chapters[ch]["text"])))
    return int(min(100, (ch + inner) / n * 100))


def delete_book(book_id: int) -> bool:
    n = _exec("DELETE FROM books WHERE id = ?", (int(book_id),))
    if n:
        for t in ("bookmarks", "annotations", "reading_notes", "book_ink"):
            _exec(f"DELETE FROM {t} WHERE book_id = ?", (int(book_id),))
        bk.remove_book_files(book_id)
    return bool(n)


def get_ink(book_id: int) -> dict[str, Any]:
    """这本书所有页上的笔迹：{页 key: [笔画…]}。"""
    out: dict[str, Any] = {}
    for r in _q("SELECT key, strokes FROM book_ink WHERE book_id = ?", (int(book_id),)):
        try:
            out[r["key"]] = json.loads(r["strokes"])
        except json.JSONDecodeError:
            continue
    return out


def set_ink(book_id: int, key: str, strokes: list[Any]) -> None:
    if not strokes:
        _exec("DELETE FROM book_ink WHERE book_id = ? AND key = ?", (int(book_id), key))
        return
    _exec("INSERT INTO book_ink (book_id, key, strokes, updated_at) VALUES (?, ?, ?, ?) "
          "ON CONFLICT(book_id, key) DO UPDATE SET strokes = excluded.strokes, updated_at = excluded.updated_at",
          (int(book_id), key, json.dumps(strokes, ensure_ascii=False, separators=(",", ":")), _now()))


def save_my_progress(book_id: int, chapter: int, offset: int) -> None:
    _exec(
        "UPDATE books SET my_chapter = ?, my_offset = ?, my_read_at = ? WHERE id = ?",
        (max(0, int(chapter)), max(0, int(offset)), _now(), int(book_id)),
    )


def save_phal_progress(book_id: int, chapter: int, offset: int, status: str | None = None) -> None:
    if status and status not in PHAL_STATUS:
        status = None
    _exec(
        "UPDATE books SET phal_chapter = ?, phal_offset = ?, phal_read_at = ?, "
        "phal_status = COALESCE(?, phal_status) WHERE id = ?",
        (int(chapter), int(offset), _now(), status, int(book_id)),
    )


def set_phal_status(book_id: int, status: str) -> None:
    if status in PHAL_STATUS:
        _exec("UPDATE books SET phal_status = ? WHERE id = ?", (status, int(book_id)))


def phal_has_read(book: dict[str, Any], chapter: int, offset: int) -> bool:
    """Phal 有没有读到过 (chapter, offset) 这个位置。"""
    if book["phal_status"] == "finished":
        return True
    if book["phal_status"] == "unread":
        return False
    return (int(book["phal_chapter"]), int(book["phal_offset"])) > (int(chapter), int(offset))


# --------------------------------------------------------------------------
# 书签
# --------------------------------------------------------------------------
def list_bookmarks(book_id: int) -> list[dict[str, Any]]:
    return _q("SELECT * FROM bookmarks WHERE book_id = ? ORDER BY chapter, offset", (int(book_id),))


def add_bookmark(book_id: int, chapter: int, offset: int, snippet: str) -> dict[str, Any]:
    # 同一页附近重复点，等于取消 —— 由前端决定；这里只负责存
    bid = _exec(
        "INSERT INTO bookmarks (book_id, chapter, offset, snippet, created_at) VALUES (?, ?, ?, ?, ?)",
        (int(book_id), int(chapter), int(offset), (snippet or "")[:60], _now()),
    )
    return _one("SELECT * FROM bookmarks WHERE id = ?", (bid,))  # type: ignore[return-value]


def delete_bookmark(bookmark_id: int) -> bool:
    return bool(_exec("DELETE FROM bookmarks WHERE id = ?", (int(bookmark_id),)))


# --------------------------------------------------------------------------
# 注释
# --------------------------------------------------------------------------
def add_annotation(book_id: int, chapter: int, offset: int, length: int, snippet: str, note: str) -> dict[str, Any] | None:
    note = (note or "").strip()
    if not note:
        return None
    # 同一个位置已经有注释了就不重复
    if _one("SELECT id FROM annotations WHERE book_id = ? AND chapter = ? AND offset = ?",
            (int(book_id), int(chapter), int(offset))):
        return None
    aid = _exec(
        "INSERT INTO annotations (book_id, chapter, offset, length, snippet, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (int(book_id), int(chapter), int(offset), int(length), snippet, note[:200], _now()),
    )
    return _one("SELECT * FROM annotations WHERE id = ?", (aid,))


def annotations_in_chapter(book_id: int, chapter: int) -> list[dict[str, Any]]:
    return _q("SELECT * FROM annotations WHERE book_id = ? AND chapter = ? ORDER BY offset",
              (int(book_id), int(chapter)))


def get_annotation(annotation_id: int) -> dict[str, Any] | None:
    return _one("SELECT * FROM annotations WHERE id = ?", (int(annotation_id),))


def annotations_near(book_id: int, chapter: int, offset: int, span: int = 400) -> list[dict[str, Any]]:
    return _q(
        "SELECT * FROM annotations WHERE book_id = ? AND chapter = ? AND offset BETWEEN ? AND ? ORDER BY offset LIMIT 3",
        (int(book_id), int(chapter), int(offset) - span, int(offset) + span),
    )


# --------------------------------------------------------------------------
# 读书笔记
# --------------------------------------------------------------------------
def add_note(book_id: int, chapter: int, text: str) -> None:
    text = (text or "").strip()
    if not text:
        return
    _exec(
        "INSERT INTO reading_notes (book_id, chapter, text, created_at, ts) VALUES (?, ?, ?, ?, ?)",
        (int(book_id), int(chapter), text[:200], _now(), time.time()),
    )


def notes_for_book(book_id: int, limit: int = 6) -> list[dict[str, Any]]:
    rows = _q("SELECT * FROM reading_notes WHERE book_id = ? ORDER BY id DESC LIMIT ?", (int(book_id), int(limit)))
    return list(reversed(rows))


def recent_notes(limit: int = 4) -> list[dict[str, Any]]:
    return _q(
        "SELECT n.*, b.title AS book_title FROM reading_notes n JOIN books b ON b.id = n.book_id "
        "ORDER BY n.id DESC LIMIT ?", (int(limit),),
    )


def notes_in_chapter(book_id: int, chapter: int, limit: int = 2) -> list[dict[str, Any]]:
    return _q("SELECT * FROM reading_notes WHERE book_id = ? AND chapter = ? ORDER BY id DESC LIMIT ?",
              (int(book_id), int(chapter), int(limit)))


# --------------------------------------------------------------------------
# 草稿本
# --------------------------------------------------------------------------
def add_draft(kind: str, title: str, content: str, status: str, inspired_by: str | None = None) -> dict[str, Any]:
    now = datetime.now()
    did = _exec(
        "INSERT INTO drafts (kind, title, content, status, inspired_by, created_at, ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (kind, title.strip()[:40], content.strip(), status if status in ("done", "abandoned") else "done",
         inspired_by, now.strftime("%Y-%m-%d %H:%M:%S"), now.timestamp()),
    )
    return _one("SELECT * FROM drafts WHERE id = ?", (did,))  # type: ignore[return-value]


def list_drafts(limit: int = 100) -> list[dict[str, Any]]:
    return _q("SELECT * FROM drafts ORDER BY id DESC LIMIT ?", (int(limit),))


def mark_draft_shared(draft_id: int) -> None:
    _exec("UPDATE drafts SET shared = 1 WHERE id = ?", (int(draft_id),))


def delete_draft(draft_id: int) -> bool:
    return bool(_exec("DELETE FROM drafts WHERE id = ?", (int(draft_id),)))


# --------------------------------------------------------------------------
# 主动消息记录
# --------------------------------------------------------------------------
def log_proactive(kind: str, message_id: int | None) -> None:
    _exec("INSERT INTO proactive_log (kind, message_id, ts) VALUES (?, ?, ?)", (kind, message_id, time.time()))


def proactive_count_since(seconds: float) -> int:
    row = _one("SELECT COUNT(*) AS n FROM proactive_log WHERE ts >= ?", (time.time() - seconds,))
    return int(row["n"]) if row else 0
