"""地点库（作者层的硬数据，见 docs/已完成的计划/小本子与世界地图.md 第二节）：世界上有哪些地方、谁是谁的上级、离家多远、在地图上的哪儿。

* 存在 ``data/world/_places.json``（所有角色共用；设置页「世界 → 地点」可编辑，也可以直接改文件）。第一次用时按人设里写的地方
  和 ``data/world/世界常识/`` 的文件名生成骨架（坐标、车程这些等你补）。
* 每个地点：id、名字、kind（world / country / region / city / county / district / street / landmark / shop / home）、
  parent（上级 id）、travel_min（从家出发大概多少分钟）、visit（平时自己去的权重，0 = 不会自己去）、
  price_place（用价格带里哪个地点系数；不填就跟上级）、secret（她不知道这个地方：将来地图上对她是迷雾）、
  map（在上级地图里的坐标 {x, y}，0~1000；将来做大世界地图用，现在可以不填）、note（备注）。
* 她出门（「在外面」的状态）时由代码先定目的地（`pick_destination`）：大多数时候就在县城里，偶尔去隔壁；远的地方只在
  周末 / 和朋友一起时「特地出门」，这一趟会拉长到够来回。连着几个在外面的状态（和朋友出去 → 出门吃饭）留在同一个地方，
  回家就清掉（`here()` / `set_here()`）。目的地决定价格的地点系数，也是以后检索小本子的关键词。
* 不调模型。
"""

from __future__ import annotations

import json
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from . import character as C
from . import db, world

KINDS = {
    "world": "世界", "country": "国家", "region": "州 / 省", "city": "市", "county": "县", "district": "区 / 镇",
    "street": "街道", "landmark": "地标", "shop": "店", "home": "家",
}
PRICE_PLACES = tuple(p[0] for p in C.price_places())       # 价格的地点系数按这些 id 算（角色配置）
HOME_ID = "home"

# 第一次用时的骨架：角色配置里的「places」（Phal 的在 character.py 的默认值里：家 → 新昌 → 绍兴 → ……）
SKELETON: list[dict[str, Any]] = C.get("places") or []

# 出门的状态：哪些能「特地出门」去远的地方（周末 / 和朋友一起时）
FAR_STATES = ("friends", "shop", "boardgame_friends", "photo")
LOCAL_RATIO = 0.6            # 一趟来回的路程最多占这个状态时长的多少（超出的地方这次去不了，除非是「特地出门」）
FAR_EXTRA_MIN = (90, 180)    # 特地出门：在那边待多久（再加上来回路程）
LATEST_BACK_H = 21           # 特地出门要能在这个钟点前回到家


# ───────────────────────── 读写 ─────────────────────────
def _clean(p: dict[str, Any]) -> dict[str, Any]:
    m = p.get("map") or {}
    try:
        mp = {"x": max(0.0, min(1000.0, float(m["x"]))), "y": max(0.0, min(1000.0, float(m["y"])))} if m.get("x") is not None and m.get("y") is not None else None
    except (TypeError, ValueError, KeyError):
        mp = None
    kind = str(p.get("kind") or "landmark")
    return {
        "id": str(p.get("id") or "").strip(),
        "name": str(p.get("name") or "").strip()[:30],
        "kind": kind if kind in KINDS else "landmark",
        "parent": str(p.get("parent") or "").strip(),
        "travel_min": max(0, int(float(p.get("travel_min") or 0))),
        "visit": max(0.0, float(p.get("visit") or 0)),
        "price_place": p.get("price_place") if p.get("price_place") in PRICE_PLACES else "",
        "secret": bool(p.get("secret")),
        "map": mp,
        "note": str(p.get("note") or "").strip()[:200],
        "auto": bool(p.get("auto")),
    }


def _world_countries() -> list[str]:
    """世界常识文件夹里的文件名（不含总纲）：多半是国家，先当成国家放进骨架。"""
    return sorted(world.category_titles("世界常识"))


def skeleton() -> list[dict[str, Any]]:
    out = [_clean(p) for p in SKELETON]
    names = {p["name"] for p in out}
    for i, name in enumerate(_world_countries()):
        if name not in names:
            out.append(_clean({"id": f"c{i + 1}", "name": name, "kind": "country", "parent": "world", "auto": True,
                               "note": "从「世界常识」的文件名生成的；不是国家的话改一下种类和上级"}))
    return out


def load() -> list[dict[str, Any]]:
    try:
        data = world.read_places()
        if data is None:
            save(skeleton())
            data = world.read_places()
        rows = data.get("places") if isinstance(data, dict) else data
        return [_clean(p) for p in rows or [] if isinstance(p, dict) and p.get("id")]
    except (OSError, json.JSONDecodeError):
        return [_clean(p) for p in SKELETON]


def validate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """检查：id 不重复、名字不空、上级存在、没有绕圈、只有一个「家」。新行没有 id 就自动给一个。"""
    clean = [_clean(r) for r in rows]
    used = {p["id"] for p in clean if p["id"]}
    n = 1
    for p in clean:
        if not p["id"]:
            while f"p{n}" in used:
                n += 1
            p["id"] = f"p{n}"
            used.add(p["id"])
        if not re.fullmatch(r"[A-Za-z0-9_\-]{1,40}", p["id"]):
            raise ValueError(f"地点 id「{p['id']}」只能用字母、数字、下划线")
        if not p["name"]:
            raise ValueError("地点名字不能为空")
    ids = [p["id"] for p in clean]
    if len(ids) != len(set(ids)):
        raise ValueError("地点 id 重复了")
    by = {p["id"]: p for p in clean}
    for p in clean:
        if p["parent"] and p["parent"] not in by:
            raise ValueError(f"「{p['name']}」的上级不存在")
        seen, cur = set(), p
        while cur and cur["parent"]:
            if cur["id"] in seen:
                raise ValueError(f"「{p['name']}」的上级绕成了圈")
            seen.add(cur["id"])
            cur = by.get(cur["parent"])
    if sum(1 for p in clean if p["kind"] == "home") > 1:
        raise ValueError("只能有一个「家」")
    return clean


def save(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clean = validate(rows)
    world.write_places({"version": 1, "places": clean})
    return clean


def by_id() -> dict[str, dict[str, Any]]:
    return {p["id"]: p for p in load()}


def get(pid: str) -> dict[str, Any] | None:
    return by_id().get(pid)


def chain(pid: str) -> list[dict[str, Any]]:
    """从这个地点往上一直到最顶上：[自己, 上级, 上上级……]。"""
    by = by_id()
    out, cur, seen = [], by.get(pid), set()
    while cur and cur["id"] not in seen:
        out.append(cur)
        seen.add(cur["id"])
        cur = by.get(cur["parent"])
    return out


def home() -> dict[str, Any]:
    by = by_id()
    return next((p for p in by.values() if p["kind"] == "home"), None) or by.get(C.home_price_place()) or _clean(next((p for p in SKELETON if p["kind"] == "home"), SKELETON[0]))


def price_place(pid: str | None) -> str:
    """这个地点用价格带里的哪个地点系数：自己没填就跟上级；都没有就按她住的县。"""
    for p in chain(pid or home()["id"]):
        if p["price_place"]:
            return p["price_place"]
    return C.home_price_place()


def label(pid: str | None) -> str:
    """说给她 / 模型听的地点名：县 / 城市一级就是名字；更小的地方带上它所在的县城（「新昌的老街」）。"""
    if not pid:
        return ""
    ch = chain(pid)
    if not ch:
        return ""
    p = ch[0]
    if p["kind"] in ("street", "landmark", "shop", "district"):
        town = next((x for x in ch[1:] if x["kind"] in ("county", "city")), None)
        return f"{town['name']}的{p['name']}" if town else p["name"]
    return p["name"]


# ───────────────────────── 她现在在哪 ─────────────────────────
def here() -> dict[str, Any]:
    """她现在在的地方：出门了就是目的地，否则是家。"""
    p = get(_here_raw().get("place") or "")
    return p or home()


def _here_raw() -> dict[str, Any]:
    try:
        h = json.loads(db.get_settings().get("life_here") or "{}")
    except json.JSONDecodeError:
        h = {}
    return h if isinstance(h, dict) else {}


def set_here(pid: str | None, far: bool = False) -> None:
    """出门到了某处（pid）/ 回家了（None）。回家时记下刚才在哪（回家那条消息要说从哪回来）。
    far：这一趟是特地出门去远的地方（记在这里而不是状态上：中途吃饭、吃完接着玩换了几个状态也还记得）。"""
    last = _here_raw().get("place") if not pid else None
    db.save_settings({"life_here": json.dumps({"place": pid, "last": last, "far": bool(pid and far), "since": time.time()})})


def far_trip() -> bool:
    """她现在是不是在特地出门的路上 / 那边。"""
    return bool(_here_raw().get("far")) and away_from_home()


def last_place() -> str | None:
    """刚回到家：之前在哪。"""
    return _here_raw().get("last")


def away_from_home() -> bool:
    return here()["kind"] != "home"


# ───────────────────────── 出门去哪 ─────────────────────────
def candidates(key: str, duration_min: float, ctx: dict[str, Any], far_ok: bool) -> list[tuple[dict[str, Any], float, bool]]:
    """能去的地方 [(地点, 权重, 是不是特地出门)]。近的：来回路程不超过这个状态时长的 LOCAL_RATIO；远的：只在 far_ok 时，按 visit 很低的权重。"""
    out: list[tuple[dict[str, Any], float, bool]] = []
    for p in load():
        if p["visit"] <= 0 or p["secret"] or p["kind"] == "home":
            continue
        w = p["visit"]
        near = p["travel_min"] * 2 <= duration_min * LOCAL_RATIO
        if not near:
            if not far_ok:
                continue
            w *= 0.25 if ctx.get("raining") else 1.0
            w *= 0.3 if ctx.get("money_tight") else 1.0
            w *= 0.4 if ctx.get("tired") else 1.0
        elif p["travel_min"] >= 15:
            w *= 0.5 if ctx.get("raining") else 1.0
        out.append((p, w, not near))
    return out


def pick_destination(key: str, duration_min: float, ctx: dict[str, Any] | None = None, now: float | None = None,
                     rng: random.Random | None = None) -> dict[str, Any] | None:
    """出门的状态开始时定目的地。返回 {place, far, planned_min}（特地出门时 planned_min 是拉长后的时长），没地方可去返回 None。
    ctx：weekend、with_friends、raining、money_tight、tired（不给就自己看天气 / 余额 / 星期）。"""
    rng = rng or random
    ctx = dict(ctx or _ctx_now(now))
    now = now or time.time()
    hour = datetime.fromtimestamp(now).hour + datetime.fromtimestamp(now).minute / 60
    far_ok = key in FAR_STATES and (ctx.get("weekend") or ctx.get("with_friends"))
    cands = candidates(key, duration_min, ctx, bool(far_ok))
    # 远的地方：要能在 LATEST_BACK_H 点前回来
    cands = [c for c in cands if not c[2] or hour + (c[0]["travel_min"] * 2 + FAR_EXTRA_MIN[0]) / 60 <= LATEST_BACK_H]
    if not cands:
        return None
    p, _w, far = rng.choices(cands, [c[1] for c in cands])[0]
    planned = duration_min
    if far:
        stay = rng.randint(*FAR_EXTRA_MIN)
        planned = min(p["travel_min"] * 2 + stay, (LATEST_BACK_H - hour) * 60)
    return {"place": p["id"], "far": far, "planned_min": planned}


def _ctx_now(now: float | None = None) -> dict[str, Any]:
    from . import money, weather
    from .workdays import is_weekend

    d = datetime.fromtimestamp(now or time.time())
    try:
        tight = money.enabled() and money.tier() != "ok"
    except Exception:  # noqa: BLE001
        tight = False
    return {"weekend": is_weekend(d.date()), "raining": weather.is_raining() or weather.is_snowing(), "money_tight": tight}


def context_line() -> str:
    """聊天上下文：她出门在外时，说一句在哪。在家不说。"""
    p = here()
    if p["kind"] == "home":
        return ""
    far = p["travel_min"] >= 45
    return f"- 你现在在{label(p['id'])}" + ("（特地出门来的，离家挺远）" if far else "") + "。"
