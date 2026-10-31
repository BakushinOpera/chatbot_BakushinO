"""食物（v10）：书桌的「杂物区」和北墙边的「食物桌」上放着的吃的。

* 种类：甜点 / 奶茶 / 零食 / 水果 / 饭菜。来源：她自己买的（零花钱）、烤的（烘焙 → 甜点）、做的或点的外卖（家人出差时 → 饭菜，摆在食物桌）、
  你在房间里加的（「添加物品」选分类「食物」，保质期你自己定）。
* 保质期：你加的按你设的；她自己的按种类随机（设置 → 她的房间 → 食物）：甜点 15 小时~2 天、奶茶 12 小时、零食 / 水果 1~3 天、饭菜 12 小时~1 天。
* 过期的不会马上消失，而是在每天睡前清理书桌时一起收掉（读取时按「上一次睡觉的时刻」判断，应用关了一整夜也一样）。
  过期的她不会再吃。
* 每份食物可以分几次吃完（portions），吃一次少一份，吃完就没了。吃东西的规则在 life.py（_maybe_snack）。
* 放哪：饭菜放食物桌；别的随机放食物桌或书桌杂物区（设置里可调比例）。每处放满了，新的顶掉最早的。

页面上放哪、怎么画，见 ``web/room3d-extras.js``；这里只管数据。
"""

from __future__ import annotations

import json
import random
import time
from datetime import datetime, timedelta
from typing import Any

from . import booklook, daily, db, registry

AREA = "杂物区"                                    # 书桌上那一小块（页面上的区域名，系统自己管）
TABLE_AREA = "食物桌"                              # 北墙边、书桌和衣柜之间的小桌子
KINDS = {"dessert": "甜点", "milktea": "奶茶", "snack": "零食", "fruit": "水果", "meal": "饭菜"}
PLACES = {"desk": AREA, "table": TABLE_AREA}
LOCATIONS = {"desk": "书桌", "table": "食物桌"}      # 「位置」里的说法（食物只能放这两处；书桌上放在杂物区）
CAP = {"desk": 6, "table": 10}                     # 每处最多放几份（和页面上的格子数一致）
LIFE_DEFAULT_H = {"dessert": (15, 48), "milktea": (12, 12), "snack": (24, 72), "fruit": (24, 72), "meal": (12, 24)}
# 来源：和房间里的物品一样（我送的 / 我寄的 / 她自己买的 / 本来就有的），再加她自己弄来的几种
SOURCE_LABEL = {"gift": "我送的", "mail": "我寄的", "bought": "她自己买的", "original": "本来就有的",
                "bake": "她烤的", "cook": "她做的", "takeout": "外卖", "self": "她自己买的", "user": "我送的"}
USER_SOURCES = ("gift", "mail", "bought", "original")
# 按名字猜种类（她收到 / 买到一样吃的，自己判断是什么）：先认饭菜和奶茶，再甜点、水果，剩下算零食
KIND_WORDS = [
    ("milktea", ("奶茶", "果茶", "茶饮", "拿铁", "咖啡", "可可", "奶昔", "饮料", "柠檬茶", "杨枝甘露")),
    ("meal", ("饭", "面", "粥", "饺", "馄饨", "汤", "便当", "炒", "盖浇", "套餐", "米线", "粉", "包子", "饼卷", "沙拉", "排骨", "鸡腿", "鸡块", "烤鱼", "红烧肉", "炒菜", "家常菜")),
    ("dessert", ("蛋糕", "布丁", "甜点", "泡芙", "蛋挞", "马卡龙", "慕斯", "司康", "玛芬", "派", "提拉米苏", "冰淇淋", "雪糕", "曲奇", "月饼", "糕")),
    ("fruit", ("苹果", "橘", "橙", "香蕉", "葡萄", "草莓", "梨", "桃", "猕猴桃", "奇异果", "蓝莓", "樱桃", "西瓜", "芒果", "柚", "荔枝", "水果", "哈密瓜", "榴莲", "菠萝")),
    ("snack", ("零食", "薯片", "饼干", "糖", "巧克力", "果冻", "坚果", "瓜子", "辣条", "海苔", "面包", "肉干", "话梅")),
]


def guess_kind(name: str) -> str | None:
    """按名字猜是哪种吃的；看不出是吃的返回 None。命中的词越长越准（「面包」比「面」准），一样长按表里的先后。"""
    best, best_len = None, 0
    for kind, words in KIND_WORDS:
        for w in words:
            if w in (name or "") and len(w) > best_len:
                best, best_len = kind, len(w)
    return best


def place_of(location: str | None) -> str | None:
    """「书桌 / 杂物区 / desk」→ desk，「食物桌 / 桌子 / table」→ table；别的返回 None。"""
    loc = (location or "").strip()
    if loc in ("desk", "书桌", AREA, "书桌杂物区"):
        return "desk"
    if loc in ("table", TABLE_AREA, "桌子", "小桌子"):
        return "table"
    return None


def _q(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    with db._lock:
        return [dict(r) for r in db.connect().execute(sql, params).fetchall()]


def _exec(sql: str, params: tuple = ()) -> int:
    with db._lock:
        conn = db.connect()
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid if sql.lstrip().upper().startswith("INSERT") else cur.rowcount


# ───────────────────────── 睡觉点 ─────────────────────────
def bedtime(now: float | None = None) -> float:
    """下一次上床睡觉的时刻（epoch 秒）。睡觉关了就当「今晚 24 点」。"""
    now = now if now is not None else time.time()
    t = datetime.fromtimestamp(now)
    if not daily.sleep_enabled():
        return (t.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).timestamp()
    target = t.replace(hour=daily.sleep_hour() % 24, minute=0, second=0, microsecond=0)
    if target.timestamp() <= now:
        target += timedelta(days=1)
    return target.timestamp()


def last_bedtime(now: float | None = None) -> float:
    """上一次睡前清理书桌的时刻（= 上一个睡觉点）。"""
    now = now if now is not None else time.time()
    nxt = datetime.fromtimestamp(bedtime(now))
    return (nxt - timedelta(days=1)).timestamp()          # 按墙上的钟往前一天（夏令时切换那天也是 22 点，不是 21 / 23 点）


def shelf_life_s(kind: str, rng: random.Random | None = None) -> float:
    """她自己的食物的保质期（秒）：按种类在设置的范围里随机。"""
    rng = rng or random
    lo_d, hi_d = LIFE_DEFAULT_H.get(kind, (24, 24))
    lo = registry.num(f"food_life_{kind}_min_h") if f"food_life_{kind}_min_h" in registry.BY_KEY else lo_d
    hi = registry.num(f"food_life_{kind}_max_h") if f"food_life_{kind}_max_h" in registry.BY_KEY else hi_d
    return rng.uniform(min(lo, hi), max(lo, hi)) * 3600


# ───────────────────────── 读写 ─────────────────────────
def _view(r: dict[str, Any], now: float | None = None) -> dict[str, Any]:
    now = now if now is not None else time.time()
    try:
        look = json.loads(r.get("look") or "") or None
    except json.JSONDecodeError:
        look = None
    exp = datetime.fromtimestamp(r["expires_at"])
    left_h = (r["expires_at"] - now) / 3600
    return {"id": r["id"], "kind": r["kind"], "label": KINDS.get(r["kind"], r["kind"]), "name": r["name"],
            "look": look or booklook.clutter_look(r["kind"], r["name"], r["id"]),
            "created_at": r["created_at"], "expires_at": r["expires_at"], "expired": r["expires_at"] <= now,
            "place": r.get("place") or "desk", "area": PLACES.get(r.get("place") or "desk", AREA),
            "location": LOCATIONS.get(r.get("place") or "desk", "书桌"),
            "portions": int(r.get("portions") or 1), "portions_left": int(r.get("portions_left") or 1),
            "source": r.get("source") or "self", "source_label": SOURCE_LABEL.get(r.get("source") or "self", ""),
            "bought_hm": datetime.fromtimestamp(r["created_at"]).strftime("%H:%M"),
            "expires_hm": exp.strftime("%H:%M") if exp.date() == datetime.fromtimestamp(now).date() else exp.strftime("%m-%d %H:%M"),
            "expires_text": "已过期" if left_h <= 0 else (f"还有 {left_h:.0f} 小时过期" if left_h < 48 else f"还有 {left_h / 24:.0f} 天过期")}


def purge(now: float | None = None) -> int:
    """睡前清理书桌：收掉在上一个睡觉点之前就过期的（以及吃完的）。"""
    now = now if now is not None else time.time()
    return _exec("DELETE FROM room_clutter WHERE expires_at <= ? OR portions_left <= 0", (last_bedtime(now),))


def active(now: float | None = None) -> list[dict[str, Any]]:
    """现在放着的食物（先做一次睡前清理），按放上来的先后。"""
    now = now if now is not None else time.time()
    purge(now)
    return [_view(r, now) for r in _q("SELECT * FROM room_clutter ORDER BY created_at, id")]


def available(now: float | None = None, places: tuple[str, ...] = ("desk", "table")) -> list[dict[str, Any]]:
    """能吃的：没过期、还有剩。"""
    now = now if now is not None else time.time()
    return [f for f in active(now) if not f["expired"] and f["portions_left"] > 0 and f["place"] in places]


def get(food_id: int) -> dict[str, Any] | None:
    rows = _q("SELECT * FROM room_clutter WHERE id = ?", (int(food_id),))
    return _view(rows[0]) if rows else None


def spawn(kind: str, name: str, now: float | None = None, rng: random.Random | None = None, ledger_id: int = 0, force: bool = False,
          place: str | None = None, expires_at: float | None = None, portions: int | None = None, source: str = "self") -> dict[str, Any] | None:
    """放一份食物。睡着时返回 None（force：你手动加的、测试用，不受限制）。
    place 不给：饭菜放食物桌，别的按「放书桌的比例」随机。expires_at 不给：按种类随机保质期。portions 不给：随机 1~最多几次。"""
    if kind not in KINDS:
        return None
    now = now if now is not None else time.time()
    rng = rng or random
    if not force and daily.is_sleeping(now):
        return None
    if place not in PLACES:
        place = "table" if kind == "meal" else ("desk" if rng.random() < registry.num("food_desk_share") else "table")
    if expires_at is None:
        expires_at = now + shelf_life_s(kind, rng)
    if portions is None:
        portions = rng.randint(1, max(1, int(registry.num("food_portions_max"))))
    portions = max(1, int(portions))
    name = (name or "").strip()[:30] or KINDS[kind]
    look = booklook.clutter_look(kind, name, int(now))
    rid = _exec("INSERT INTO room_clutter (kind, name, look, created_at, expires_at, ledger_id, place, portions, portions_left, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (kind, name, json.dumps(look, ensure_ascii=False), now, float(expires_at), int(ledger_id), place, portions, portions, source))
    extra = _q("SELECT id FROM room_clutter WHERE place = ? ORDER BY created_at DESC, id DESC LIMIT -1 OFFSET ?", (place, CAP[place]))
    for e in extra:                                    # 放满了：顶掉最早的
        _exec("DELETE FROM room_clutter WHERE id = ?", (e["id"],))
    row = _q("SELECT * FROM room_clutter WHERE id = ?", (rid,))
    return _view(row[0], now) if row else None


def update(food_id: int, **fields: Any) -> dict[str, Any] | None:
    """改一份食物：名字、种类、位置（书桌 / 食物桌）、来源、保质期（expires_at，或 expires_h = 从现在起还有几小时）、还剩几次。"""
    if not get(food_id):
        return None
    patch: dict[str, Any] = {}
    if fields.get("name") is not None:
        name = str(fields["name"]).strip()
        if not name:
            raise ValueError("名字不能为空")
        patch["name"] = name[:30]
    if fields.get("kind") is not None:
        if fields["kind"] not in KINDS:
            raise ValueError("种类只能是：" + "、".join(KINDS.values()))
        patch["kind"] = fields["kind"]
    if fields.get("location") is not None:
        pl = place_of(fields["location"])
        if not pl:
            raise ValueError("食物只能放在书桌或食物桌")
        patch["place"] = pl
    if fields.get("source") is not None:
        if fields["source"] not in SOURCE_LABEL:
            raise ValueError("来源不对")
        patch["source"] = fields["source"]
    if fields.get("expires_h") is not None:
        h = float(fields["expires_h"])
        if h <= 0:
            raise ValueError("保质期要大于 0")
        patch["expires_at"] = time.time() + h * 3600
    elif fields.get("expires_at") is not None:
        patch["expires_at"] = float(fields["expires_at"])
    if fields.get("portions_left") is not None:
        n = int(fields["portions_left"])
        if n < 1:
            raise ValueError("至少还剩 1 次（吃完了就直接收掉）")
        patch["portions_left"] = n
    if patch:
        if "portions_left" in patch:
            cur = get(food_id)
            patch["portions"] = max(int(cur["portions"]), patch["portions_left"]) if cur else patch["portions_left"]
        cols = ", ".join(f"{k} = ?" for k in patch)
        _exec(f"UPDATE room_clutter SET {cols} WHERE id = ?", (*patch.values(), int(food_id)))
    return get(food_id)


def eat_one(food_id: int) -> dict[str, Any] | None:
    """吃掉一份：剩的减一，吃完就收走。返回吃之前的那份（带 finished = 这次吃完了）。"""
    f = get(food_id)
    if not f or f["portions_left"] <= 0:
        return None
    left = f["portions_left"] - 1
    if left <= 0:
        _exec("DELETE FROM room_clutter WHERE id = ?", (f["id"],))
    else:
        _exec("UPDATE room_clutter SET portions_left = ? WHERE id = ?", (left, f["id"]))
    return dict(f, finished=left <= 0, portions_left=left)


def remove(clutter_id: int) -> bool:
    return _exec("DELETE FROM room_clutter WHERE id = ?", (int(clutter_id),)) > 0


def clear() -> int:
    return _exec("DELETE FROM room_clutter")
