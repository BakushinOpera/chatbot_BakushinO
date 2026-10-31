"""小本子（见 docs/已完成的计划/小本子与世界地图.md 第三节、docs/已完成的计划/phal-notebook-spec.md）：她自己认知中的世界——去过的地方、常去的店、吃过的东西、
价格印象、听来的传闻、神秘学见闻、你告诉她的事。可能有误解，你可以改。

* 按角色存：``data/characters/<角色>/notebook.db``（单独一个 SQLite 文件；角色见 ``config.CHARACTER_ID``，现在只有 phal）。
  每次改动后导出一份 Markdown 到 ``notebook_export/``（每个章节一个文件，方便直接读 / 备份）。
* 条目：章节、标题（同章节里不重复，比较时忽略空格标点）、关键词（标题自动算，另外可以补）、正文（她的口吻）、追加（锁定后她只能在
  末尾追加，单独标出）、结构化信息（店铺：地点、档次、东西和价格）、可信度（她的看法 / 已确认）、锁定、来源、地点、你改过的片段、状态
  （active / pending：初始抄写待你审）、时间。每次修改前留快照（``entry_history``），可以找回。
* 你改过的条目自动锁定；你改过的片段记在 ``user_spans``（页面上换颜色）。
* 店铺章节顺便管价格的一致性（取代 v13 的 ``price_book`` 表，第一次用时迁移过来并删掉旧表）：同一家店同一样东西，第一次的价格为准，
  之后在它的 ±10% 里浮动（``recall_price`` / ``remember_price``，由 prices.py 调用）。
* 这一步只有存储、页面和价格；她写入（第 3 步）、检索进上下文（第 4 步）之后做。不调模型。
"""

from __future__ import annotations

import difflib
import json
import re
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config
from . import character as C

SOURCES = {"experience": "亲身经历", "heard": "听说", "told": "她说的", "copied": "初始抄写"}
TRUSTS = {"opinion": "她的看法", "confirmed": "已确认"}
ACCURACY = {"accurate": "尽量准确（写入时用作者层校对）", "exaggerate": "允许夸张", "misread": "允许误解"}
DEFAULT_CHAPTERS = [
    ("place", "地方", "accurate"), ("shop", "店铺", "accurate"), ("food", "吃的", "exaggerate"), ("price", "物价印象", "accurate"),
    ("heard", "听说的事", "exaggerate"), ("occult", "神秘学", "misread"), ("told", f"{C.pronoun()}说的", "accurate"),
]
if not C.feature("occult"):                    # 角色没开神秘学：新本子里没有这一章
    DEFAULT_CHAPTERS = [c for c in DEFAULT_CHAPTERS if c[0] != "occult"]
SHOP = "shop"
NO_SHOP = "没记店名的"         # 店名是空的（模型没给）：价格记在这一条里
TITLE_MAX, BODY_MAX, KW_MAX = 40, 5000, 20

SCHEMA = """
CREATE TABLE IF NOT EXISTS chapters (
    key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    accuracy TEXT NOT NULL DEFAULT 'accurate',
    sort INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chapter TEXT NOT NULL,
    title TEXT NOT NULL,
    norm TEXT NOT NULL,
    keywords TEXT NOT NULL DEFAULT '[]',
    body TEXT NOT NULL DEFAULT '',
    addenda TEXT NOT NULL DEFAULT '[]',
    info TEXT NOT NULL DEFAULT '{}',
    trust TEXT NOT NULL DEFAULT 'opinion',
    locked INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'experience',
    place TEXT NOT NULL DEFAULT '',
    user_spans TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'active',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS entries_title ON entries(chapter, norm);
CREATE TABLE IF NOT EXISTS entry_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL,
    snapshot TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    by TEXT NOT NULL DEFAULT '',
    at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS entry_history_e ON entry_history(entry_id, at);
CREATE TABLE IF NOT EXISTS entry_flags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    at REAL NOT NULL,
    resolved INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS day_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    ref INTEGER NOT NULL DEFAULT 0,
    at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS day_notes_day ON day_notes(day, id);
"""
_JSON_COLS = ("keywords", "addenda", "info", "user_spans")
_lock = threading.RLock()
_conns: dict[str, sqlite3.Connection] = {}


# ───────────────────────── 连接 ─────────────────────────
def home_dir(character: str | None = None) -> Path:
    return config.DATA_DIR / "characters" / (character or config.CHARACTER_ID)


def db_path(character: str | None = None) -> Path:
    return home_dir(character) / "notebook.db"


def _conn() -> sqlite3.Connection:
    path = str(db_path())
    with _lock:
        conn = _conns.get(path)
        if conn is None:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.executescript(SCHEMA)
            if not conn.execute("SELECT 1 FROM chapters LIMIT 1").fetchone():
                conn.executemany("INSERT INTO chapters (key, name, accuracy, sort) VALUES (?, ?, ?, ?)",
                                 [(k, n, a, i) for i, (k, n, a) in enumerate(DEFAULT_CHAPTERS)])
            conn.commit()
            _conns[path] = conn
            _migrate_price_book(conn)
        return conn


def _rows(sql: str, args: tuple = ()) -> list[dict[str, Any]]:
    with _lock:
        return [dict(r) for r in _conn().execute(sql, args).fetchall()]


def _exec(sql: str, args: tuple = ()) -> int:
    with _lock:
        conn = _conn()
        cur = conn.execute(sql, args)
        conn.commit()
        return int(cur.lastrowid or 0)


def norm(s: str) -> str:
    """比较标题 / 店名 / 东西名用：去掉空格和标点。"""
    return re.sub(r"[\s，。,.·、!！?？:：;；\"“”'‘’()（）\[\]【】-]", "", str(s or "")).lower()[:30]


def _load(r: dict[str, Any]) -> dict[str, Any]:
    for k in _JSON_COLS:
        try:
            r[k] = json.loads(r.get(k) or ("{}" if k == "info" else "[]"))
        except json.JSONDecodeError:
            r[k] = {} if k == "info" else []
    r["locked"] = bool(r.get("locked"))
    return r


# ───────────────────────── 章节 ─────────────────────────
def chapters() -> list[dict[str, Any]]:
    rows = _rows("SELECT key, name, accuracy, sort FROM chapters ORDER BY sort, rowid")
    counts = {r["chapter"]: r["n"] for r in _rows("SELECT chapter, COUNT(*) AS n FROM entries WHERE status = 'active' GROUP BY chapter")}
    for r in rows:
        r["count"] = counts.get(r["key"], 0)
    return rows


def chapter_keys() -> set[str]:
    return {r["key"] for r in _rows("SELECT key FROM chapters")}


def save_chapters(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """整份替换章节表：可以改名、改准确度、调顺序、加新的；删掉的章节里不能还有条目。"""
    clean, used = [], set()
    for i, r in enumerate(rows):
        name = str(r.get("name") or "").strip()[:12]
        if not name:
            raise ValueError("章节名字不能为空")
        key = str(r.get("key") or "").strip()
        if not key:
            n = 1
            while f"c{n}" in used or f"c{n}" in chapter_keys():
                n += 1
            key = f"c{n}"
        if not re.fullmatch(r"[A-Za-z0-9_]{1,24}", key):
            raise ValueError(f"章节 id「{key}」只能用字母、数字、下划线")
        if key in used:
            raise ValueError("章节重复了")
        used.add(key)
        acc = r.get("accuracy") if r.get("accuracy") in ACCURACY else "accurate"
        clean.append((key, name, acc, i))
    if len({c[1] for c in clean}) != len(clean):
        raise ValueError("章节名字重复了")
    if SHOP not in used:
        raise ValueError("「店铺」章节不能删（价格的一致性靠它）")
    gone = chapter_keys() - used
    for key in gone:
        n = _rows("SELECT COUNT(*) AS n FROM entries WHERE chapter = ?", (key,))[0]["n"]
        if n:
            name = _rows("SELECT name FROM chapters WHERE key = ?", (key,))[0]["name"]
            raise ValueError(f"「{name}」里还有 {n} 条，先把它们挪到别的章节或删掉")
    with _lock:
        conn = _conn()
        conn.execute("DELETE FROM chapters")
        conn.executemany("INSERT INTO chapters (key, name, accuracy, sort) VALUES (?, ?, ?, ?)", clean)
        conn.commit()
    export()
    return chapters()


# ───────────────────────── 条目 ─────────────────────────
def list_entries(chapter: str | None = None, status: str | None = "active") -> list[dict[str, Any]]:
    sql, args = "SELECT * FROM entries WHERE 1 = 1", []
    if chapter:
        sql += " AND chapter = ?"
        args.append(chapter)
    if status:
        sql += " AND status = ?"
        args.append(status)
    return [_load(r) for r in _rows(sql + " ORDER BY updated_at DESC", tuple(args))]


def get(eid: int) -> dict[str, Any] | None:
    rows = _rows("SELECT * FROM entries WHERE id = ?", (int(eid),))
    return _load(rows[0]) if rows else None


def find(chapter: str, title: str) -> dict[str, Any] | None:
    rows = _rows("SELECT * FROM entries WHERE chapter = ? AND norm = ?", (chapter, norm(title)))
    return _load(rows[0]) if rows else None


def keywords_of(e: dict[str, Any]) -> list[str]:
    """检索用的关键词：标题自动算一个 + 补充的 + 所在地点的名字。"""
    out = [e["title"], *e.get("keywords", [])]
    if e.get("place"):
        from . import places

        p = places.get(e["place"])
        if p:
            out.append(p["name"])
    seen, res = set(), []
    for k in out:
        if k and norm(k) not in seen:
            seen.add(norm(k))
            res.append(k)
    return res


def _clean_fields(f: dict[str, Any], old: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "chapter" in f:
        if f["chapter"] not in chapter_keys():
            raise ValueError("没有这个章节")
        out["chapter"] = f["chapter"]
    if "title" in f:
        t = re.sub(r"\s+", " ", str(f["title"] or "")).strip()[:TITLE_MAX]
        if not norm(t):
            raise ValueError("标题不能为空")
        out["title"], out["norm"] = t, norm(t)
    if "keywords" in f:
        kws = f["keywords"]
        if isinstance(kws, str):
            kws = re.split(r"[,，、;；\s]+", kws)
        seen, clean = set(), []
        for k in kws or []:
            k = str(k).strip()[:20]
            if k and norm(k) not in seen:
                seen.add(norm(k))
                clean.append(k)
        out["keywords"] = clean[:KW_MAX]
    if "body" in f:
        out["body"] = str(f["body"] or "").replace("\r\n", "\n").strip()[:BODY_MAX]
    if "addenda" in f:
        out["addenda"] = [{"text": str(a.get("text") or "").strip()[:BODY_MAX], "kind": a.get("kind") if a.get("kind") in ("append", "opinion") else "append",
                           "at": float(a.get("at") or time.time())} for a in f["addenda"] or [] if isinstance(a, dict) and str(a.get("text") or "").strip()]
    if "info" in f:
        out["info"] = f["info"] if isinstance(f["info"], dict) else {}
    if "trust" in f:
        out["trust"] = f["trust"] if f["trust"] in TRUSTS else "opinion"
    if "source" in f:
        out["source"] = f["source"] if f["source"] in SOURCES else "experience"
    if "locked" in f:
        out["locked"] = 1 if f["locked"] else 0
    if "place" in f:
        out["place"] = str(f["place"] or "").strip()[:40]
    if "status" in f:
        out["status"] = "pending" if f["status"] == "pending" else "active"
    chapter = out.get("chapter") or (old or {}).get("chapter")
    n = out.get("norm") if "norm" in out else (old or {}).get("norm")
    if chapter and n and ("norm" in out or "chapter" in out):
        dup = _rows("SELECT id, title FROM entries WHERE chapter = ? AND norm = ? AND id != ?", (chapter, n, int((old or {}).get("id") or 0)))
        if dup:
            raise ValueError(f"这个章节里已经有「{dup[0]['title']}」了：同一个地方 / 同一家店只能有一条")
    return out


def _spans_after(old_body: str, new_body: str, old_spans: list, mine: bool) -> list[list[int]]:
    """正文改了以后，你改过的片段在新正文里的位置：原来的片段跟着挪；这次是你改的，新加 / 换掉的文字也算进去。"""
    out: list[list[int]] = []
    sm = difflib.SequenceMatcher(None, old_body, new_body, autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            for s, e in old_spans:
                a, b = max(s, i1), min(e, i2)
                if a < b:
                    out.append([j1 + a - i1, j1 + b - i1])
        elif mine and op in ("insert", "replace") and j2 > j1:
            out.append([j1, j2])
    out.sort()
    merged: list[list[int]] = []
    for s, e in out:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return merged


def _snapshot(e: dict[str, Any], reason: str, by: str) -> None:
    snap = {k: e[k] for k in ("chapter", "title", "keywords", "body", "addenda", "info", "trust", "locked", "source", "place", "user_spans", "status")}
    _exec("INSERT INTO entry_history (entry_id, snapshot, reason, by, at) VALUES (?, ?, ?, ?, ?)",
          (e["id"], json.dumps(snap, ensure_ascii=False), reason, by, time.time()))


def create(fields: dict[str, Any], by: str = "user") -> dict[str, Any]:
    """新建一条。by：user（你）/ her（她写的）/ system（代码，比如价格记录）。你新建的整条正文都算你写的，自动锁定。"""
    f = {"trust": "confirmed" if by == "user" else "opinion", "source": "told" if by == "user" else "experience", "body": "",
         **fields}
    if "chapter" not in f or "title" not in f:
        raise ValueError("要有章节和标题")
    c = _clean_fields(f, None)
    now = time.time()
    if by == "user":
        c.setdefault("locked", 1)
        c["user_spans"] = [[0, len(c.get("body", ""))]] if c.get("body") else []
    cols = {"keywords": [], "addenda": [], "info": {}, "user_spans": [], **c, "created_at": now, "updated_at": now}
    for k in _JSON_COLS:
        cols[k] = json.dumps(cols[k], ensure_ascii=False)
    eid = _exec(f"INSERT INTO entries ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", tuple(cols.values()))
    export()
    return get(eid)  # type: ignore[return-value]


def update(eid: int, fields: dict[str, Any], by: str = "user", reason: str = "") -> dict[str, Any]:
    """改一条（改之前留快照）。你改了内容 → 自动锁定，改动的片段记下来（页面上换颜色）。"""
    old = get(eid)
    if not old:
        raise ValueError("没有这一条")
    c = _clean_fields(fields, old)
    changed = {k: v for k, v in c.items() if old.get(k) != v and not (k == "locked" and bool(v) == old["locked"])}
    if not changed:
        return old
    content = set(changed) - {"locked", "status"}
    if by == "her" and old["locked"]:
        if content - {"addenda", "keywords"} or "locked" in changed or len(changed.get("addenda", old["addenda"])) < len(old["addenda"]) \
                or changed.get("addenda", old["addenda"])[:len(old["addenda"])] != old["addenda"]:
            raise ValueError("锁定的条目她只能在末尾追加，不能改原文")
    if "body" in changed:
        changed["user_spans"] = _spans_after(old["body"], changed["body"], old["user_spans"], by == "user")
    if by == "user" and content and "locked" not in c:
        changed["locked"] = 1
    _snapshot(old, reason or ("你改的" if by == "user" else "她改的" if by == "her" else "自动更新"), by)
    sets = dict(changed, updated_at=time.time())
    for k in _JSON_COLS:
        if k in sets:
            sets[k] = json.dumps(sets[k], ensure_ascii=False)
    _exec(f"UPDATE entries SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?", (*sets.values(), int(eid)))
    export()
    return get(eid)  # type: ignore[return-value]


def set_locked(eid: int, locked: bool) -> dict[str, Any]:
    if not get(eid):
        raise ValueError("没有这一条")
    _exec("UPDATE entries SET locked = ? WHERE id = ?", (1 if locked else 0, int(eid)))
    export()
    return get(eid)  # type: ignore[return-value]


def delete(eid: int, by: str = "user") -> None:
    old = get(eid)
    if not old:
        return
    _snapshot(old, "删除", by)
    _exec("DELETE FROM entries WHERE id = ?", (int(eid),))
    _exec("DELETE FROM entry_flags WHERE entry_id = ?", (int(eid),))
    export()


def history(eid: int) -> list[dict[str, Any]]:
    out = []
    for r in _rows("SELECT * FROM entry_history WHERE entry_id = ? ORDER BY at DESC, id DESC", (int(eid),)):
        try:
            r["snapshot"] = json.loads(r["snapshot"])
        except json.JSONDecodeError:
            continue
        out.append(r)
    return out


def restore(eid: int, hid: int, by: str = "user") -> dict[str, Any]:
    """找回某个快照的样子（找回本身也留快照，可以再找回去）。"""
    rows = [h for h in history(eid) if h["id"] == int(hid)]
    if not rows:
        raise ValueError("没有这个历史版本")
    s = rows[0]["snapshot"]
    old = get(eid)
    if not old:
        raise ValueError("没有这一条")
    fields = {k: s[k] for k in ("chapter", "title", "keywords", "body", "addenda", "info", "trust", "source", "place", "status") if k in s}
    fields["locked"] = s.get("locked", old["locked"]) if by != "user" else True
    e = update(eid, fields, by=by, reason="找回旧版本")
    if "user_spans" in s and e["body"] == s.get("body"):
        _exec("UPDATE entries SET user_spans = ? WHERE id = ?", (json.dumps(s["user_spans"]), int(eid)))
        e = get(eid)  # type: ignore[assignment]
    return e  # type: ignore[return-value]


# ───────────────────────── 店铺：价格的一致性（取代 price_book） ─────────────────────────
def _shop_title(shop: str) -> str:
    t = re.sub(r"\s+", " ", str(shop or "")).strip()[:TITLE_MAX]
    return t if norm(t) else NO_SHOP


def _item_key(items: dict[str, Any], item: str) -> str | None:
    n = norm(item)
    return next((k for k in items if norm(k) == n), None)


def recall_price(shop: str, item: str) -> dict[str, Any] | None:
    """这家店的这样东西以前买过没有：返回 {shop, item, band, tier, gimmick, place, price, times, first_at, last_at}（和旧 price_book 的行一样）。"""
    if not norm(item):
        return None
    e = find(SHOP, _shop_title(shop))
    if not e:
        return None
    items = e["info"].get("items") or {}
    k = _item_key(items, item)
    return _price_row(e, k, items[k]) if k else None


def _price_row(e: dict[str, Any], name: str, it: dict[str, Any]) -> dict[str, Any]:
    inf = e["info"]
    return {"shop": "" if e["title"] == NO_SHOP else e["title"], "item": name, "band": it.get("band", ""), "tier": inf.get("tier", "chain"),
            "gimmick": inf.get("gimmick", "none"), "place": inf.get("price_place", C.home_price_place()), "price": float(it.get("price") or 0),
            "times": int(it.get("times") or 1), "first_at": float(it.get("first_at") or e["created_at"]), "last_at": float(it.get("last_at") or e["updated_at"])}


def _shop_place() -> str:
    """她现在在哪家店附近：出门在外就是目的地；在家（外卖 / 网购）就算家所在的县城。"""
    try:
        from . import places

        p = places.here()
        return p["parent"] if p["kind"] == "home" else p["id"]
    except Exception:  # noqa: BLE001
        return ""


def remember_price(shop: str, item: str, info: dict[str, Any], price: float, at: float | None = None, place: str | None = None,
                   impression: str = "", quiet: bool = False) -> None:
    """记一笔：第一次去就新建店铺条目（并在地点库里登记这家店），这样东西记下第一次的价格；再买只加次数，价格不改。
    impression：她对这家店的印象（吃饭 / 消费那次调用顺便给的）：写进正文，锁定的条目就追加在末尾。quiet：迁移旧数据用，不记今天的见闻。"""
    if not norm(item):
        return
    now = at or time.time()
    title = _shop_title(shop)
    with _lock:
        e = find(SHOP, title)
        first = not e
        if not e:
            pid = place if place is not None else _shop_place()
            e = create({"chapter": SHOP, "title": title, "trust": "confirmed", "source": "experience",
                        "place": pid, "info": {"items": {}}}, by="system")
            if not quiet and title != NO_SHOP:
                map_id = _register_shop(title, pid)
                if map_id:
                    e["info"]["map_id"] = map_id
        inf = dict(e["info"])
        items = dict(inf.get("items") or {})
        k = _item_key(items, item)
        if k:
            it = dict(items[k], times=int(items[k].get("times") or 1) + 1, last_at=now, band=info.get("band") or items[k].get("band", ""))
        else:
            k = str(item).strip()[:30]
            it = {"price": round(float(price), 1), "band": info.get("band", ""), "times": 1, "first_at": now, "last_at": now}
        items[k] = it
        inf.update(items=items, tier=info.get("tier", inf.get("tier", "chain")), gimmick=info.get("gimmick", inf.get("gimmick", "none")),
                   price_place=info.get("place", inf.get("price_place", C.home_price_place())))
        _exec("UPDATE entries SET info = ?, updated_at = ? WHERE id = ?", (json.dumps(inf, ensure_ascii=False), max(now, e["updated_at"]), e["id"]))
        if not quiet:
            where = _place_label(e["place"])
            log_day("shop", f"{'第一次去' if first else '又去了'}「{title}」{('（' + where + '）') if where else ''}，买了{k} {float(it['price']):g} {C.unit()}"
                    + (f"；印象：{impression.strip()}" if impression.strip() else ""), ref=e["id"], now=now)
        if impression.strip():
            note_impression(get(e["id"]), impression)  # type: ignore[arg-type]
    export()


def known_prices(bands: tuple[str, ...] | None = None, n: int = 8) -> list[dict[str, Any]]:
    """去过的店买过的东西（最近的在前）：给提示词看「再去同一家买同一样，价格差不多」。bands 不给就是全部。"""
    out = []
    for e in list_entries(SHOP):
        for name, it in (e["info"].get("items") or {}).items():
            if bands is None or it.get("band") in bands:
                out.append(_price_row(e, name, it))
    out.sort(key=lambda r: r["last_at"], reverse=True)
    return out[:int(n)]


def _migrate_price_book(conn: sqlite3.Connection) -> None:
    """v13 的 price_book 表（在 chat.db 里）搬进店铺条目，搬完删掉旧表。只做一次。"""
    if conn.execute("SELECT 1 FROM meta WHERE k = 'price_book_migrated'").fetchone():
        return
    try:
        from . import db

        main = db.connect()
        with db._lock:
            has = main.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'price_book'").fetchone()
            rows = [dict(r) for r in main.execute("SELECT * FROM price_book ORDER BY first_at").fetchall()] if has else []
        for r in rows:
            remember_price(r["shop"], r["item"], {"band": r["band"], "tier": r["tier"], "gimmick": r["gimmick"], "place": r["place"]},
                           r["price"], at=r["first_at"], place="", quiet=True)
            e = find(SHOP, _shop_title(r["shop"]))
            if e:
                inf = e["info"]
                k = _item_key(inf["items"], r["item"])
                inf["items"][k].update(times=int(r["times"]), first_at=float(r["first_at"]), last_at=float(r["last_at"]))
                if inf.get("price_place") in {p[0] for p in C.price_places()} and not e["place"]:
                    conn.execute("UPDATE entries SET place = ? WHERE id = ?", (inf["price_place"], e["id"]))
                conn.execute("UPDATE entries SET info = ?, created_at = MIN(created_at, ?), updated_at = ? WHERE id = ?",
                             (json.dumps(inf, ensure_ascii=False), float(r["first_at"]), float(r["last_at"]), e["id"]))
        conn.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('price_book_migrated', ?)", (str(len(rows)),))
        conn.commit()
        if has:
            with db._lock:
                main.execute("DROP TABLE IF EXISTS price_book")
                main.commit()
    except Exception:  # noqa: BLE001 —— 迁移失败不影响别的；下次启动再试
        import logging

        logging.getLogger(__name__).exception("price_book 迁移出错")


# ───────────────────────── 她写（第 3 步）：今天的见闻、合并规则 ─────────────────────────
def entry_max() -> int:
    from . import registry

    return int(registry.num("notebook_entry_max"))


def _today(now: float | None = None) -> str:
    from . import daily

    return daily.today_str(now or time.time())


def log_day(kind: str, text: str, ref: int = 0, now: float | None = None) -> None:
    """记一笔今天的见闻（place 出门去哪 / shop 去了哪家店 / food 吃了什么 / buy 买了什么）：晚上整理本子时用。"""
    _exec("INSERT INTO day_notes (day, kind, text, ref, at) VALUES (?, ?, ?, ?, ?)", (_today(now), kind, str(text)[:200], int(ref or 0), now or time.time()))


def day_notes(day: str) -> list[dict[str, Any]]:
    return _rows("SELECT * FROM day_notes WHERE day = ? ORDER BY id", (day,))


def meta_get(k: str) -> str:
    rows = _rows("SELECT v FROM meta WHERE k = ?", (k,))
    return rows[0]["v"] if rows else ""


def meta_set(k: str, v: str) -> None:
    _exec("INSERT OR REPLACE INTO meta (k, v) VALUES (?, ?)", (k, v))


def _place_label(pid: str) -> str:
    if not pid:
        return ""
    try:
        from . import places

        return places.label(pid)
    except Exception:  # noqa: BLE001
        return ""


def fit(text: str, limit: int) -> str:
    """超过字数：在句子结尾处截断（她自己精简没做到时的兜底）。"""
    t = str(text or "").strip()
    if len(t) <= limit:
        return t
    cut = t[:limit]
    i = max(cut.rfind(c) for c in "。！？；…\n")
    return cut[:i + 1].strip() if i >= limit * 0.5 else cut.rstrip("，、；： ") + "…"


def _seen(e: dict[str, Any], text: str) -> bool:
    """这段话本子里这一条已经写过了（正文或追加里有）。"""
    n = norm(text)
    return bool(n) and n in norm(e["body"] + "".join(a["text"] for a in e["addenda"]))


def similar(chapter: str, title: str, keywords: list[str] | tuple[str, ...] = ()) -> dict[str, Any] | None:
    """写之前找同一条：同章节同标题；或者标题互相包含（「老街」/「新昌老街」）；或者关键词重合两个以上 / 一半以上。"""
    e = find(chapter, title)
    if e:
        return e
    t = norm(title)
    mine = {norm(k) for k in [title, *keywords] if len(norm(k)) >= 2}
    best, score = None, 0.0
    for x in list_entries(chapter, status=None):
        xt = x["norm"]
        if len(t) >= 2 and len(xt) >= 2 and (t in xt or xt in t):
            return x
        theirs = {norm(k) for k in keywords_of(x) if len(norm(k)) >= 2}
        both = len(mine & theirs)
        if both and (both >= 2 or both / max(1, min(len(mine), len(theirs))) >= 0.5):
            sc = both / max(1, len(mine | theirs))
            if sc > score:
                best, score = x, sc
    return best


def _join(a: str, b: str) -> str:
    a, b = a.strip(), b.strip()
    if not a:
        return b
    return a + ("" if a[-1] in "。！？…" else "。") + b


def her_write(chapter: str, title: str, body: str = "", append: str = "", keywords: list[str] | tuple[str, ...] = (), opinion: bool = False,
              source: str = "experience", target: int | None = None, place: str = "", info: dict[str, Any] | None = None,
              trust: str = "opinion") -> dict[str, Any] | None:
    """她往本子里写一条，按规则：
    * 有同一条（target 指定的，或者同标题 / 关键词高度重合的）就合并进去，不另起；
    * 没锁定：body 是合并后的完整正文（以新的为准，旧的留在修改历史里）；只给了 append 就接在末尾；
    * 锁定了：不碰原文，只在末尾追加一小段（和原文矛盾的当「她的看法」）；
    * 正文超过字数上限就截到句子结尾（她自己精简没做到时的兜底）。返回写进去的条目，没写返回 None。"""
    mx = entry_max()
    e = get(target) if target else None
    if e is None:
        if chapter not in chapter_keys():
            return None
        e = similar(chapter, title, keywords)
    kws = list(keywords or [])
    if e:
        known = {norm(x) for x in keywords_of(e)}
        new_kws = e["keywords"] + [k for k in kws if norm(k) and norm(k) not in known]
        if e["locked"]:
            add = (append or "").strip()
            if not add or _seen(e, add):
                return e if new_kws == e["keywords"] else update(e["id"], {"keywords": new_kws}, by="her", reason="她补了关键词")
            adds = e["addenda"] + [{"text": fit(add, max(60, mx // 3)), "kind": "opinion" if opinion else "append", "at": time.time()}]
            return update(e["id"], {"addenda": adds, "keywords": new_kws}, by="her", reason="她追加了一段")
        nb = (body or "").strip()
        if not nb and append and not _seen(e, append):
            nb = _join(e["body"], append)
        fields: dict[str, Any] = {"keywords": new_kws}
        if nb:
            fields["body"] = fit(nb, mx) if body else nb[:mx * 2]     # 只是接在后面：先不截（太长的晚上整理时她自己精简）
        if info:
            fields["info"] = {**e["info"], **info}
        if place and not e["place"]:
            fields["place"] = place
        return update(e["id"], fields, by="her", reason="她合并了新见闻")
    text = (body or append or "").strip()
    if not norm(title) or not text:
        return None
    return create({"chapter": chapter, "title": title, "body": fit(text, mx), "keywords": kws, "source": source, "trust": trust,
                   "place": place, "info": info or {}}, by="her")


def note_impression(e: dict[str, Any], text: str) -> None:
    """店铺条目：她这次的印象。正文空着就写成正文；没锁定就接在后面（太长的晚上整理时精简）；锁定了就追加在末尾。"""
    t = str(text or "").strip()
    if not t or not e or _seen(e, t):
        return
    if e["locked"]:
        her_write(e["chapter"], e["title"], append=t, target=e["id"])
    elif not e["body"]:
        update(e["id"], {"body": fit(t, entry_max())}, by="her", reason="她写了第一次来的印象")
    else:
        update(e["id"], {"body": _join(e["body"], t)[:entry_max() * 2]}, by="her", reason="她又来了，补了一句")


def visit_place(pid: str | None) -> None:
    """她出门到了某个地方：第一次去就在「地方」章节建一条（结构化信息先填上，正文晚上整理本子时她写），并记进今天的见闻。"""
    if not pid:
        return
    from . import places

    p = places.get(pid)
    if not p or p["kind"] in ("home", "world") or "place" not in chapter_keys():
        return
    e = next((x for x in list_entries("place", status=None) if x["place"] == pid), None) or find("place", p["name"])
    first = e is None
    if e is None:
        parent = places.get(p["parent"]) if p["parent"] else None
        e = create({"chapter": "place", "title": p["name"], "place": pid, "source": "experience", "trust": "confirmed",
                    "info": {"place_id": pid, "kind": p["kind"], "parent": parent["name"] if parent else "", "travel_min": p["travel_min"]}}, by="system")
    log_day("place", f"{'第一次去' if first else '去了'}{places.label(pid) or p['name']}" + ("（小本子里这一条还空着）" if not e["body"] else ""), ref=e["id"])


def _register_shop(title: str, parent: str) -> str:
    """第一次去的店登记进地点库（作者层，将来地图上会慢慢长出她去过的店）：种类「店」，上级是当时在的地方，她不会把它当目的地。"""
    try:
        from . import places

        rows = places.load()
        if not parent or parent not in {p["id"] for p in rows}:
            return ""
        hit = next((p for p in rows if p["kind"] == "shop" and norm(p["name"]) == norm(title) and p["parent"] == parent), None)
        if hit:
            return hit["id"]
        used = {p["id"] for p in rows}
        n = 1
        while f"s{n}" in used:
            n += 1
        rows.append({"id": f"s{n}", "name": title, "kind": "shop", "parent": parent, "travel_min": places.get(parent)["travel_min"], "visit": 0,
                     "auto": True, "note": f"她第一次来是 {datetime.now().strftime('%Y-%m-%d')}（小本子自动登记）"})
        places.save(rows)
        return f"s{n}"
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).exception("登记店铺到地点库出错")
        return ""


def shops_near(pid: str | None, bands: tuple[str, ...]) -> list[tuple[dict[str, Any], int]]:
    """她现在在的这一带（在家就算家所在的县城）去过的、卖这几类东西的店：[(条目, 去过几次)]。"""
    try:
        from . import places

        p = places.get(pid or "") or places.home()
        here = p["parent"] if p["kind"] == "home" else p["id"]
    except Exception:  # noqa: BLE001
        return []
    out = []
    for e in list_entries(SHOP):
        if e["title"] == NO_SHOP or not e["place"]:
            continue
        if e["place"] != here and here not in {x["id"] for x in places.chain(e["place"])}:
            continue
        items = [it for it in (e["info"].get("items") or {}).values() if it.get("band") in bands]
        if items:
            out.append((e, sum(int(it.get("times") or 1) for it in items)))
    return out


def shop_brief(e: dict[str, Any], n: int = 3) -> str:
    """一家去过的店：上次买过什么、多少钱、她的印象（给吃饭 / 消费的提示词）。"""
    items = sorted((e["info"].get("items") or {}).items(), key=lambda kv: kv[1].get("last_at") or 0, reverse=True)[:n]
    s = "、".join(f"{k} {float(v.get('price') or 0):g} {C.unit()}" for k, v in items)
    imp = (e["body"] + "".join(a["text"] for a in e["addenda"]))[:120]
    return (f"你在这儿买过：{s}。" if s else "") + (f"你对这家店的印象：{imp}" if imp else "")


# ───────────────────────── 读（第 4 步）：检索小本子 ─────────────────────────
NOTE_HEAD = "【你的小本子】你自己记在本子上的（是你的认识，可能有记错的地方；没记的细节就含糊过去，别编成很确定的事）："


def search(text: str, n: int, place_ids: list[str] | tuple[str, ...] = (), keys: list[str] | tuple[str, ...] = (),
           chapters: list[str] | tuple[str, ...] = (), exclude: set[int] | None = None) -> list[dict[str, Any]]:
    """按相关度挑条目：关键词（标题 + 补充的 + 所在地点名）出现在 text 里按长度计分；keys（这次行动定好的店名 / 地名）完全对上加分；
    条目的地点在 place_ids 里加分；chapters 里的章节加一点（比如神秘学的事带上神秘学章节）。只看已启用的（待审的不算）。"""
    if n <= 0:
        return []
    text = text or ""
    keyset = {norm(k) for k in keys if norm(k)}
    scored = []
    for e in list_entries():
        if exclude and e["id"] in exclude:
            continue
        kws = keywords_of(e)
        s = sum(len(k) for k in {k for k in kws if len(k) >= 2 and k in text})
        s += 6 * len(keyset & {norm(k) for k in kws})
        if e["place"] and e["place"] in place_ids:
            s += 4
        if s and e["chapter"] in chapters:
            s += 2
        elif not s and e["chapter"] in chapters:
            s = 1
        if s:
            scored.append((s, e["updated_at"], e))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [x[2] for x in scored[:n]]


def note_text(e: dict[str, Any], cap: int | None = None) -> str:
    cap = cap or entry_max()
    names = {c["key"]: c["name"] for c in chapters()}
    body = e["body"] + "".join(f"（{'我的看法' if a['kind'] == 'opinion' else '后来补的'}：{a['text']}）" for a in e["addenda"])
    items = (e["info"] or {}).get("items") or {}
    if items:
        body += ("" if not body else " ") + "买过：" + "、".join(f"{k} {float(v.get('price') or 0):g} {C.unit()}" for k, v in list(items.items())[:4])
    return f"「{e['title']}」（{names.get(e['chapter'], e['chapter'])}）{fit(body, cap) if body else '（只记了个名字）'}"


def block(rows: list[dict[str, Any]]) -> str:
    return (NOTE_HEAD + "\n" + "\n".join(f"- {note_text(e)}" for e in rows)) if rows else ""


def life_block(text: str = "", place: str | None = None, keys: list[str] | tuple[str, ...] = (), chapters: list[str] | tuple[str, ...] = (),
               n: int | None = None) -> str:
    """她自己生活时（吃饭 / 消费 / 出门 / 事件）生成前带上的小本子：用这次行动的地点（连同上级）、店名、类别当关键词。出错返回空。"""
    try:
        from . import places, registry

        n = int(registry.num("notebook_life_n")) if n is None else n
        pids: list[str] = []
        if place:
            p = places.get(place)
            if p and p["kind"] == "home":
                p = places.get(p["parent"]) or p
            if p:
                ch = [x for x in places.chain(p["id"]) if x["kind"] not in ("world", "country", "region")]
                pids = [x["id"] for x in ch]
                text = f"{text} {' '.join(x['name'] for x in ch)}"
        return block(search(text, n, place_ids=pids, keys=keys, chapters=chapters))
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).exception("检索小本子出错")
        return ""


def chat_ready() -> bool:
    """初始抄写审完了（或者你在设置里直接切过去）：聊天改成检索小本子，作者层条目不再直接进上下文。"""
    return meta_get("copy_state") == "approved"


# ───────────────────────── 导出 Markdown ─────────────────────────
def _fmt_time(t: float) -> str:
    return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M")


def entry_markdown(e: dict[str, Any]) -> str:
    tags = [SOURCES.get(e["source"], ""), TRUSTS.get(e["trust"], "")] + (["锁定"] if e["locked"] else []) + (["待审"] if e["status"] == "pending" else [])
    lines = [f"## {e['title']}", "", f"> {' · '.join(t for t in tags if t)} · 更新于 {_fmt_time(e['updated_at'])}"]
    kws = keywords_of(e)[1:]
    if kws:
        lines.append(f"> 关键词：{'、'.join(kws)}")
    items = (e["info"] or {}).get("items") or {}
    if items:
        lines.append("> " + "；".join(f"{k} {float(v.get('price') or 0):g} {C.unit()}" + (f"（{v['times']} 次）" if int(v.get("times") or 1) > 1 else "")
                                      for k, v in items.items()))
    lines.append("")
    if e["body"]:
        lines.append(e["body"])
    for a in e["addenda"]:
        lines += ["", f"（{'她的看法' if a['kind'] == 'opinion' else '追加'} · {_fmt_time(a['at'])}）{a['text']}"]
    return "\n".join(lines).rstrip() + "\n"


def export() -> None:
    """每个章节导出一个 .md（覆盖）。出错不影响保存。"""
    try:
        d = home_dir() / "notebook_export"
        d.mkdir(parents=True, exist_ok=True)
        keep = set()
        for c in chapters():
            es = sorted(list_entries(c["key"], status=None), key=lambda e: e["title"])
            name = re.sub(r'[\\/:*?"<>|]', "_", c["name"]) + ".md"
            keep.add(name)
            text = f"# {c['name']}\n\n" + ("\n".join(entry_markdown(e) for e in es) if es else "（还没有条目）\n")
            (d / name).write_text(text, encoding="utf-8")
        for f in d.glob("*.md"):
            if f.name not in keep:
                f.unlink()
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).exception("小本子导出出错")
