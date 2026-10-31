"""她的房间：物品、区域、分类。

* 每件物品：名称、分类、摆放位置（区域）、来源、获得日期、她的一句备注。
* 物品怎么进来：你在聊天里送 / 寄她东西 → 她回复末尾附 ``add_item`` 动作（``add_from_chat``）；
  她自己买了会留下来的东西（衣服、游戏、饰品）→ 消费流程调 ``add_auto``；你也可以在房间页面上手动加、改、删。
* 分类：不写规则代码，进来时由模型从设置里的分类表选一个（``classify``），选错了在页面上改。
* 分类表、区域表都在设置里（room_categories / room_areas）。改了名字，旧的东西不会丢：
  分类归到最后一个分类，位置归到「别处」，只是显示，数据库里的原字不动。
"""

from __future__ import annotations

import json
import logging
import random
import re
from datetime import datetime, timedelta
from typing import Any

from . import booklook, clutter, db, llm, registry, weather
from . import character as C
from .planner import _json_object

log = logging.getLogger(__name__)

SOURCES = {"gift": "我送的", "mail": "我寄的", "bought": "她自己买的", "original": "本来就有的"}
SOURCE_CTX = {"gift": "用户送的", "mail": "用户寄的", "bought": "你自己买的", "original": "本来就有的"}
OTHER_AREA = "别处"            # 区域表里没有的位置，在页面上归到这里
NAME_MAX, NOTE_MAX = 30, 60
CHAT_DEDUPE_MIN = 30           # 聊天里同名同来源的东西，这么久内只收一次（重新生成回复时动作会再来一遍）
MAX_PER_REPLY = 3

# 分类的图标：写的是页面（web/room.js）里内置的线条图标名，也可以直接写一个 emoji
ICON_KEYS = ("plant", "bear", "book", "dress", "game", "lamp", "gift")
DEFAULT_ICONS = {"植物": "plant", "小物件": "bear", "书与本子": "book", "衣服饰品": "dress", "电子与游戏": "game", "家具摆设": "lamp", "其他": "gift"}
FALLBACK_AREA = {"植物": "窗边", "小物件": "书桌", "书与本子": "书架", "衣服饰品": "衣柜", "电子与游戏": "书桌", "家具摆设": "床"}
FOOD_CAT, FOOD_ICON = "食物", "🍰"     # 系统自带的分类（设置里删不掉）：这一类的东西进的是食物表（clutter.py），会被她吃掉、有保质期
FOOD_LOCATIONS = ("书桌", "食物桌")     # 食物只能放这两处


def _q(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    with db._lock:
        return [dict(r) for r in db.connect().execute(sql, params).fetchall()]


def _exec(sql: str, params: tuple = ()) -> int:
    with db._lock:
        conn = db.connect()
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid if sql.lstrip().upper().startswith("INSERT") else cur.rowcount


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# ───────────────────────── 分类 / 区域（来自设置） ─────────────────────────
def categories() -> list[dict[str, str]]:
    out = []
    for s in registry.get("room_categories") or []:
        name, _, icon = str(s).strip().partition(" ")
        if name and name != FOOD_CAT:
            out.append({"name": name, "icon": icon.strip() or DEFAULT_ICONS.get(name, "gift")})
    out = out or [{"name": "其他", "icon": "gift"}]
    other = [i for i, c in enumerate(out) if c["name"] == "其他"]
    out.insert(other[0] if other else len(out), {"name": FOOD_CAT, "icon": FOOD_ICON})      # 放在「其他」前面
    return out


def category_names() -> list[str]:
    return [c["name"] for c in categories()]


def areas() -> list[str]:
    return [a for a in (registry.get("room_areas") or []) if a] or ["别处"]


def fallback_category() -> str:
    names = [n for n in category_names() if n != FOOD_CAT]
    return "其他" if "其他" in names else names[-1]


def _norm_category(name: str | None) -> str | None:
    name = (name or "").strip()
    return name if name in category_names() else None


def _norm_area(name: str | None) -> str | None:
    name = (name or "").strip()
    return name if name in areas() else None


def _norm_source(src: str | None, default: str = "gift") -> str:
    src = (src or "").strip().lower()
    if src in SOURCES:
        return src
    return "gift" if src in ("user", "") else default


def _fallback_area(category: str) -> str:
    want = FALLBACK_AREA.get(category)
    return want if want in areas() else areas()[0]


# ───────────────────────── 读写 ─────────────────────────
def icon_of(category: str) -> str:
    return next((c["icon"] for c in categories() if c["name"] == category), "gift")


def _row(r: dict[str, Any]) -> dict[str, Any]:
    cat = _norm_category(r["category"]) or fallback_category()
    loc = _norm_area(r["location"]) or OTHER_AREA
    icon = icon_of(cat)
    try:
        look = json.loads(r.get("look") or "") or None
    except json.JSONDecodeError:
        look = None
    return {"id": r["id"], "name": r["name"], "category": cat, "location": loc, "source": r["source"], "icon": icon,
            "source_label": SOURCES.get(r["source"], r["source"]), "got_on": r["got_on"], "note": r["note"],
            "look": look or booklook.item_look(icon, r["name"], r["id"])}          # 外观参数：没单独存过就按名字自动生成（稳定）


def list_items() -> list[dict[str, Any]]:
    return [_row(r) for r in _q("SELECT * FROM room_items ORDER BY got_on DESC, id DESC")]


def get(item_id: int) -> dict[str, Any] | None:
    rows = _q("SELECT * FROM room_items WHERE id = ?", (int(item_id),))
    return _row(rows[0]) if rows else None


def _clean_date(v: str | None) -> str:
    v = (v or "").strip() or _today()
    try:
        datetime.strptime(v, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("日期格式应为 年-月-日，比如 2026-10-22") from exc
    return v


def add_food(name: str, source: str = "gift", location: str | None = None, kind: str | None = None, expires_h: float | None = None,
             portions: int | None = None, remember: bool = False) -> dict[str, Any]:
    """「食物」分类的东西：进食物表。种类不给就按名字猜（猜不出算零食）；位置只能书桌 / 食物桌（不给就放食物桌）；
    保质期不给就按种类随机；分几次不给就随机。返回的东西带 location（书桌 / 食物桌）和 food = True。"""
    name = (name or "").strip()
    if not name:
        raise ValueError("名字不能为空")
    if source not in clutter.SOURCE_LABEL:
        raise ValueError("来源只能是：" + "、".join(SOURCES.values()))
    if kind is not None and kind not in clutter.KINDS:
        raise ValueError("种类只能是：" + "、".join(clutter.KINDS.values()))
    place = clutter.place_of(location) if location else "table"
    if not place:
        raise ValueError("食物只能放在书桌或食物桌")
    import time as _time

    exp = _time.time() + float(expires_h) * 3600 if expires_h else None
    f = clutter.spawn(kind or clutter.guess_kind(name) or "snack", name, force=True, place=place, expires_at=exp, portions=portions, source=source)
    if f is None:
        raise ValueError("放不进去")
    if remember:
        verb = {"gift": "送了她", "mail": "寄给了她"}.get(source)
        text = f"{_today()[5:]} 用户{verb}{name}，放在房间的{f['location']}" if verb else f"{_today()[5:]} 我买了{name}，放在房间的{f['location']}"
        db.add_memory(text, kind="event", category="shared" if verb else "phal_fact", source="room")
    return dict(f, food=True, category=FOOD_CAT)


def add_item(name: str, category: str | None = None, location: str | None = None, source: str = "original",
             note: str = "", got_on: str | None = None, remember: bool = False, strict: bool = False) -> dict[str, Any]:
    """放一件东西进房间。category / location 缺了或不在表里：strict 时报错，否则按默认归类。remember=True 同时写进记忆。
    分类是「食物」：进食物表（见 add_food）。"""
    if (category or "").strip() == FOOD_CAT:
        return add_food(name, source, location or None, remember=remember)
    name = (name or "").strip()
    if not name:
        raise ValueError("名字不能为空")
    if len(name) > NAME_MAX:
        raise ValueError(f"名字太长了（最多 {NAME_MAX} 个字）")
    if source not in SOURCES:
        raise ValueError("来源只能是：" + "、".join(SOURCES.values()))
    cat = _norm_category(category)
    if not cat:
        if strict and (category or "").strip():
            raise ValueError(f"没有「{category}」这个分类")
        cat = fallback_category()
    loc = _norm_area(location)
    if not loc:
        if strict and (location or "").strip():
            raise ValueError(f"没有「{location}」这个区域")
        loc = _fallback_area(cat)
    day = _clean_date(got_on)
    iid = _exec(
        "INSERT INTO room_items (name, category, location, source, got_on, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (name, cat, loc, source, day, (note or "").strip()[:NOTE_MAX], datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    item = get(iid)
    assert item is not None
    if remember:
        verb = {"gift": "送了她", "mail": "寄给了她"}.get(source)
        text = f"{day[5:]} 用户{verb}{name}，放在房间的{loc}" if verb else f"{day[5:]} 我买了{name}，放在房间的{loc}"
        db.add_memory(text, kind="event", category="shared" if verb else "phal_fact", source="room")
    return item


def update_item(item_id: int, **fields: Any) -> dict[str, Any] | None:
    if not _q("SELECT id FROM room_items WHERE id = ?", (int(item_id),)):
        return None
    patch: dict[str, Any] = {}
    if fields.get("name") is not None:
        name = str(fields["name"]).strip()
        if not name:
            raise ValueError("名字不能为空")
        if len(name) > NAME_MAX:
            raise ValueError(f"名字太长了（最多 {NAME_MAX} 个字）")
        patch["name"] = name
    if fields.get("category") is not None:
        if fields["category"].strip() == FOOD_CAT:          # 改成「食物」：搬进食物表（原来那件删掉）
            it = get(item_id)
            loc = next((x for x in (fields.get("location"), it["location"]) if clutter.place_of(x)), None)      # 原来放书桌的还放书桌
            food = add_food(fields.get("name") or it["name"], it["source"], loc)
            delete_item(item_id)
            return food
        if not _norm_category(fields["category"]):
            raise ValueError(f"没有「{fields['category']}」这个分类")
        patch["category"] = fields["category"].strip()
    if fields.get("location") is not None:
        if not _norm_area(fields["location"]):
            raise ValueError(f"没有「{fields['location']}」这个区域")
        patch["location"] = fields["location"].strip()
    if fields.get("source") is not None:
        if fields["source"] not in SOURCES:
            raise ValueError("来源只能是：" + "、".join(SOURCES.values()))
        patch["source"] = fields["source"]
    if fields.get("got_on") is not None:
        patch["got_on"] = _clean_date(fields["got_on"])
    if fields.get("note") is not None:
        patch["note"] = str(fields["note"]).strip()[:NOTE_MAX]
    if patch:
        cols = ", ".join(f"{k} = ?" for k in patch)
        _exec(f"UPDATE room_items SET {cols} WHERE id = ?", (*patch.values(), int(item_id)))
    return get(item_id)


def delete_item(item_id: int) -> bool:
    return bool(_exec("DELETE FROM room_items WHERE id = ?", (int(item_id),)))


# ───────────────────────── 让模型归类 ─────────────────────────
CLASSIFY_PROMPT = """一个叫「{name}」的人要把一件新东西放进自己的房间。
东西：{item}{note}
请决定它的分类和摆放位置，输出 JSON，不要别的：{{"category": "...", "location": "..."}}
- category 只能从这些里选一个：{cats}
- location 只能从这些里选一个：{areas}（比如植物放窗边，书放书架，衣服放衣柜，游戏机放书桌）
- 吃的喝的（蛋糕、水果、零食、奶茶、饭菜……）category 选「食物」，location 只能是「书桌」或「食物桌」"""


async def classify(settings: dict[str, Any], name: str, note: str = "") -> tuple[str, str]:
    """分类 + 摆放位置。模型没接入 / 出错 / 答案不在表里：分类归到「其他」，位置按分类给个常规的。"""
    cat, loc = None, None
    if not llm.is_echo(settings):
        try:
            raw = await llm.complete(
                settings,
                [{"role": "system", "content": CLASSIFY_PROMPT.format(
                    name=C.display_name(settings), item=name, note=f"（{note}）" if note else "",
                    cats="、".join(category_names()), areas="、".join(list(areas()) + ["食物桌"]))},
                 {"role": "user", "content": "决定吧。"}],
                temperature=0.2,
            )
            data = _json_object(raw)
            cat = _norm_category(str(data.get("category") or ""))
            loc = str(data.get("location") or "").strip() if cat == FOOD_CAT else _norm_area(str(data.get("location") or ""))
        except Exception as exc:  # noqa: BLE001 —— 模型 / 网络出错，用默认
            log.warning("物品归类失败，用默认：%s: %s", type(exc).__name__, exc)
    if not cat and clutter.guess_kind(name):
        cat = FOOD_CAT                                     # 没模型 / 模型没给：名字一看就是吃的
    cat = cat or fallback_category()
    if cat == FOOD_CAT:
        return cat, loc if clutter.place_of(loc) else "食物桌"
    return cat, loc or _fallback_area(cat)


async def add_auto(name: str, source: str, note: str = "", category: str | None = None, location: str | None = None,
                   got_on: str | None = None, remember: bool = False, settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """放进房间，缺的分类 / 位置让模型补。给消费流程（她买了会留下来的东西）和聊天动作用。"""
    cat, loc = _norm_category(category), _norm_area(location)
    if cat == FOOD_CAT:
        return add_food(name, source, location if clutter.place_of(location) else None, remember=remember)
    if not (cat and loc):
        c2, l2 = await classify(settings or db.get_settings(), name, note)
        cat = cat or c2
        loc = loc or (_fallback_area(cat) if cat != c2 else l2)
    if cat == FOOD_CAT:
        return add_food(name, source, loc, remember=remember)
    return add_item(name, cat, loc, source, note, got_on, remember)


# ───────────────────────── 聊天里的 add_item 动作 ─────────────────────────
# 这句话像不像在送 / 寄东西：像才把 add_item 的说明带进上下文（宽松一点，真假由模型判断）
GIFT_HINT = re.compile(r"送|寄|给你|给了你|礼物|快递|包裹|收到|带来|带了|买了|放你|摆你|拿去|拿着")


def action_block() -> str:
    """教模型：用户把东西送 / 寄给她时，在回复末尾附一个 add_item 动作（系统执行并对用户隐藏）。"""
    cats = "、".join(category_names())
    return "\n".join([
        "【收下东西】如果用户明确说送给你、寄给你了某样东西，你收下后，在回复**最后另起一行**附上动作"
        "（系统会把它放进你的房间并对用户隐藏，不要解释它）：",
        '{"action":"add_item","name":"东西的名字","category":"分类","location":"摆放位置","from":"gift","note":"你对它的一句话"}',
        f"- category 只能从这些里选：{cats}",
        f"- location 由你决定放哪（比如植物放窗边），只能从这些里选：{'、'.join(areas())}",
        "- 吃的喝的（蛋糕、水果、零食、奶茶、饭菜……）category 填「食物」，location 只能填「书桌」或「食物桌」；"
        "再加一个 kind，按名字判断是哪种：甜点 / 奶茶 / 零食 / 水果 / 饭菜（你之后可能会把它吃掉）",
        "- from：用户当面给 / 说「送你」填 gift；说「寄给你」「快递」填 mail",
        "- note 是你对它的一句简短感想，可以留空",
        "- 正文里照常用你的口吻回应，可以自然地说一句把它放哪了（比如「先放窗边了」），别提动作、分类这些词",
        "- 只有用户真的把东西给了你才附；只是聊到、想送还没送、你自己的东西，都不要附",
    ])


async def add_from_chat(call: dict[str, Any], settings: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """执行模型附的 add_item 动作。返回新放进去的物品（没放返回 None）。"""
    if not registry.flag("room_chat_add"):
        return None
    name = str(call.get("name") or "").strip()[:NAME_MAX]
    if not name:
        return None
    source = _norm_source(str(call.get("from") or ""))
    if source not in ("gift", "mail"):
        source = "gift"
    cutoff = (datetime.now() - timedelta(minutes=CHAT_DEDUPE_MIN)).strftime("%Y-%m-%d %H:%M:%S")
    if _q("SELECT id FROM room_items WHERE name = ? AND source = ? AND created_at >= ?", (name, source, cutoff)):
        return None
    cat = str(call.get("category") or "").strip()
    if cat == FOOD_CAT or (not _norm_category(cat) or cat == fallback_category()) and clutter.guess_kind(name):
        if _q("SELECT id FROM room_clutter WHERE name = ? AND source = ? AND created_at >= ?", (name, source, _time_ago(CHAT_DEDUPE_MIN))):
            return None
        kind = {v: k for k, v in clutter.KINDS.items()}.get(str(call.get("kind") or "").strip()) or (call.get("kind") if call.get("kind") in clutter.KINDS else None)
        try:
            return add_food(name, source, str(call.get("location") or "") or None if clutter.place_of(call.get("location")) else None, kind=kind, remember=True)
        except ValueError as exc:
            log.warning("收下吃的失败：%s", exc)
            return None
    try:
        return await add_auto(name, source, str(call.get("note") or ""), str(call.get("category") or ""),
                              str(call.get("location") or ""), remember=True, settings=settings)
    except ValueError as exc:
        log.warning("收下东西失败：%s", exc)
        return None


def _time_ago(minutes: float) -> float:
    import time as _time

    return _time.time() - minutes * 60


# ───────────────────────── 给上下文 ─────────────────────────
def overview_block() -> str:
    """稳定部分：房间有哪些区域（你自己的房间，平时不用提）。"""
    n = len(_q("SELECT id FROM room_items"))
    lay = layout()
    mine = sum(1 for b in lay["shelf"] + lay["floor"] if b["kind"] != "imported")
    books = ""
    if lay["shelf"] or lay["storage"]:
        books = (f"书架上有 {lay['imported']} 本导入的书（她给你买的，你读的就是这些）和 {mine} 本你自己买的摆设书（只是摆着，不能读）；"
                 + ("放不下的堆在书桌下面靠墙的地上；" if lay["floor"] else "") + (f"收纳篮里还收着 {len(lay['storage'])} 本。" if lay["storage"] else ""))
    return ("【你的房间】你有一间自己的房间，里面分成这几处：" + "、".join(areas()) + "。"
            + (f"现在放着 {n} 件东西。" if n else "现在还很空。")
            + books + "聊到房间里的东西时，只能说下面【你房间里……】给出的，别凭空多出东西。")


def _grams(text: str) -> set[str]:
    t = "".join(ch for ch in (text or "") if ch.isalnum())
    return {t[i : i + 2] for i in range(len(t) - 1)}


def _area_hit(area: str, text: str) -> bool:
    if len(area) >= 2:
        return area in text
    return bool(re.search(re.escape(area) + r"[上边头里前下旁]", text))


def _ago(day: str) -> str:
    try:
        d = (datetime.now().date() - datetime.strptime(day, "%Y-%m-%d").date()).days
    except ValueError:
        return day
    return "今天" if d <= 0 else "昨天" if d == 1 else f"{d} 天前" if d < 30 else day[5:]


def _line(it: dict[str, Any]) -> str:
    bits = [it["category"], SOURCE_CTX.get(it["source"], ""), _ago(it["got_on"])]
    note = f"，你的备注：{it['note']}" if it["note"] else ""
    return f"- {it['location']}：{it['name']}（{'，'.join(b for b in bits if b)}{note}）"


def relevant(text: str, items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """和这句话有关的物品：提到了名字（含名字里的词）、区域或分类。"""
    text = text or ""
    if not text.strip() or limit <= 0:
        return []
    grams = _grams(text)
    area_names = areas()
    cat_names = [c for c in category_names() if c != "其他"]
    scored = []
    for it in items:
        s = 0.0
        if it["name"] in text:
            s += 3
        elif len(it["name"]) >= 2 and grams & _grams(it["name"]):
            s += 1.5
        if it["location"] in area_names and _area_hit(it["location"], text):
            s += 1
        if it["category"] in cat_names and it["category"] in text:
            s += 1
        if s >= 1:
            scored.append((s, it))
    scored.sort(key=lambda x: (-x[0], -x[1]["id"]))
    return [it for _, it in scored[:limit]]


def context_block(user_text: str = "", rng: random.Random | None = None) -> str:
    """volatile 部分：和话题相关的物品 + 最近新添的 + 偶尔想起一件。"""
    items = list_items()
    if not items:
        return ""
    rng = rng or random
    lines: list[str] = []
    shown: set[int] = set()
    for it in relevant(user_text, items, int(registry.num("room_relevant_max"))):
        lines.append(_line(it))
        shown.add(it["id"])
    days = int(registry.num("room_recent_days"))
    if days > 0:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        fresh = [it for it in items if it["got_on"] >= cutoff and it["id"] not in shown][:4]
        if fresh:
            lines.append("最近新添的：")
            lines += [_line(it) for it in fresh]
            shown.update(it["id"] for it in fresh)
    out = ""
    if lines:
        out = "【你房间里相关的东西】（是你自己的房间，你很熟悉；聊到才提，别逐件背）\n" + "\n".join(lines)
    rest = [it for it in items if it["id"] not in shown]
    if rest and rng.random() < registry.num("room_mention_prob"):
        it = rng.choice(rest)
        out += ("\n" if out else "") + (f"【你想起房间里的】{it['location']}的{it['name']}。"
                                      "如果话头合适，可以自然地提一句（比如它今天怎么样了）；不合适就别提。")
    return out


# ───────────────────────── 摆设书（不可阅读）+ 书架 / 地上 / 收纳篮 ─────────────────────────
def _brow(r: dict[str, Any]) -> dict[str, Any]:
    try:
        look = json.loads(r["look"] or "{}")
    except json.JSONDecodeError:
        look = {}
    return {"id": f"p{r['id']}", "pid": r["id"], "kind": r["source"], "title": r["title"], "author": r["author"], "look": look or booklook.random_look(random.Random(r["id"])),
            "readable": False, "place": r["place"], "price": r["price"], "note": r["note"], "got_on": r["got_on"]}


def list_phal_books(place: str | None = None) -> list[dict[str, Any]]:
    rows = _q("SELECT * FROM room_books" + (" WHERE place = ?" if place else "") + " ORDER BY id", (place,) if place else ())
    return [_brow(r) for r in rows]


def get_book(pid: int) -> dict[str, Any] | None:
    rows = _q("SELECT * FROM room_books WHERE id = ?", (int(pid),))
    return _brow(rows[0]) if rows else None


def _clean_title(title: str) -> str:
    t = (title or "").strip().strip("《》「」\"'")
    if not t:
        raise ValueError("书名不能为空")
    return t[:20]


def add_phal_book(title: str, author: str = "", look: dict[str, Any] | None = None, source: str = "phal", price: int = 0, note: str = "",
                  got_on: str | None = None, rng: random.Random | None = None) -> dict[str, Any]:
    """放一本摆设书上书架（不可阅读）。购买时随机生成外观参数；放满了按规则堆在地上 / 收纳（tidy）。"""
    rng = rng or random
    title = _clean_title(title)
    look = look or booklook.random_look(rng)
    bid = _exec("INSERT INTO room_books (title, author, look, place, source, price, note, got_on, created_at) VALUES (?, ?, ?, 'shelf', ?, ?, ?, ?, ?)",
                (title, (author or booklook.random_author(rng))[:12], json.dumps(look, ensure_ascii=False), source if source in ("phal", "user") else "phal",
                 int(price), (note or "").strip()[:60], _clean_date(got_on), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    if source == "phal":
        tidy(rng)
    return get_book(bid)  # type: ignore[return-value]


def update_book(pid: int, **fields: Any) -> dict[str, Any] | None:
    if not get_book(pid):
        return None
    patch: dict[str, Any] = {}
    if fields.get("title") is not None:
        patch["title"] = _clean_title(fields["title"])
    if fields.get("author") is not None:
        patch["author"] = str(fields["author"]).strip()[:12]
    if fields.get("note") is not None:
        patch["note"] = str(fields["note"]).strip()[:60]
    if fields.get("look") is not None:
        if not isinstance(fields["look"], dict):
            raise ValueError("外观参数要是一个对象")
        patch["look"] = json.dumps(fields["look"], ensure_ascii=False)
    if fields.get("place") is not None:
        if fields["place"] not in ("shelf", "storage"):
            raise ValueError("位置只能是 shelf（书架）/ storage（收纳篮）")
        patch["place"] = fields["place"]
    if patch:
        _exec(f"UPDATE room_books SET {', '.join(f'{k} = ?' for k in patch)} WHERE id = ?", (*patch.values(), int(pid)))
    return get_book(pid)


def delete_book(pid: int) -> bool:
    return bool(_exec("DELETE FROM room_books WHERE id = ?", (int(pid),)))


def swap_books(a: int, b: int) -> None:
    """交换两本摆设书的位置（书架上的和收纳篮里的互换）。"""
    ba, bb = get_book(a), get_book(b)
    if not ba or not bb:
        raise ValueError("没有这本书")
    if ba["place"] == bb["place"]:
        raise ValueError("要换的两本书得一本在书架上、一本在收纳篮里")
    _exec("UPDATE room_books SET place = ? WHERE id = ?", (bb["place"], int(a)))
    _exec("UPDATE room_books SET place = ? WHERE id = ?", (ba["place"], int(b)))


def _imported_books() -> list[dict[str, Any]]:
    from . import library as lib

    out = []
    for b in sorted(lib.list_books(), key=lambda x: x["id"]):
        out.append({"id": f"b{b['id']}", "book_id": b["id"], "kind": "imported", "title": b["title"], "author": b.get("author") or "", "look": booklook.default_look(b),
                    "readable": True, "place": "shelf"})
    return out


def layout() -> dict[str, Any]:
    """房间里的书：书架（先导入的书，再她买的书）→ 放满了的堆在地上 → 再多的不展示；收纳篮里的单独列出。"""
    cap, fcap = int(registry.num("room_shelf_cap")), int(registry.num("room_floor_cap"))
    shown = _imported_books() + list_phal_books("shelf")
    return {"shelf": shown[:cap], "floor": shown[cap : cap + fcap], "hidden": max(0, len(shown) - cap - fcap), "storage": list_phal_books("storage"),
            "caps": {"shelf": cap, "floor": fcap}, "imported": sum(1 for b in shown if b["kind"] == "imported")}


def tidy(rng: random.Random | None = None) -> list[int]:
    """书架放满、地上也堆满了：她把自己买的书「收纳」进收纳篮（最新的几本不收）。返回被收纳的书的 id。"""
    rng = rng or random
    cap, fcap, batch = int(registry.num("room_shelf_cap")), int(registry.num("room_floor_cap")), int(registry.num("room_store_batch"))
    moved: list[int] = []
    n_imported = len(_imported_books())
    while True:
        mine = _q("SELECT id, title FROM room_books WHERE place = 'shelf' ORDER BY id")
        over = n_imported + len(mine) - cap - fcap
        cands = mine[:-3] if len(mine) > 3 else []
        if over <= 0 or not cands:
            break
        pick = rng.sample(cands, min(len(cands), max(over, batch)))
        for r in pick:
            _exec("UPDATE room_books SET place = 'storage' WHERE id = ?", (r["id"],))
            moved.append(r["id"])
    if moved:
        rows = _q(f"SELECT title FROM room_books WHERE id IN ({','.join('?' * len(moved))}) ORDER BY id", tuple(moved))
        titles = "、".join(f"《{r['title']}》" for r in rows[:4])
        db.add_memory(f"{_today()[5:]} 书架放满了，把{titles}等 {len(moved)} 本书收进了收纳篮", kind="event", category="phal_fact", source="room")
    return moved


# ───────────────────────── 给页面 ─────────────────────────
def snapshot() -> dict[str, Any]:
    return {
        "categories": categories(),
        "areas": areas(),
        "other_area": OTHER_AREA,
        "sources": [{"key": k, "label": v} for k, v in SOURCES.items()],
        "items": list_items(),
        "books": layout(),
        "today": _today(),
        "clutter": clutter.active(),                 # 食物：书桌的杂物区 + 北墙边的食物桌（每份带 place / area、保质期、还剩几次）
        "clutter_area": clutter.AREA,
        "food_areas": [clutter.AREA, clutter.TABLE_AREA],
        "food_cat": FOOD_CAT, "food_locations": list(FOOD_LOCATIONS),
        "food_kinds": [{"key": k, "label": v} for k, v in clutter.KINDS.items()],
        "food_life_h": {k: [registry.num(f"food_life_{k}_min_h"), registry.num(f"food_life_{k}_max_h")] for k in clutter.KINDS},
        "ambient": weather.ambient(),                # 窗外的天气 / 钟点 / 日出日落（页面画天空、夜里调暗房间用）
        "render_quality": registry.get("room_render_quality") or "high",     # 窗外动画的渲染精度（高 / 中 / 低）
    }
