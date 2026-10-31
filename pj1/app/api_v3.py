"""第三轮更新的接口：人物档案、天气、世界设定文件。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import clutter, db, events, money, people, places, registry, room, weather, world

router = APIRouter()


# ───────────────────────── 人物 ─────────────────────────
class PersonIn(BaseModel):
    name: str = Field(min_length=1, max_length=20)
    kind: str = "friend"
    relation: str = ""
    personality: str = ""
    how: str = ""


class PersonPatch(BaseModel):
    name: str | None = None
    kind: str | None = None
    relation: str | None = None
    personality: str | None = None
    how: str | None = None
    recent: list[dict[str, Any]] | None = None


class DraftIn(BaseModel):
    n: int | None = Field(default=None, ge=1, le=8)


def _bad(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/api/people")
async def list_people() -> dict[str, Any]:
    return {"people": people.list_people()}


@router.post("/api/people")
async def create_person(payload: PersonIn) -> dict[str, Any]:
    try:
        return {"person": people.add(payload.name, payload.kind, payload.relation, payload.personality, payload.how)}
    except ValueError as exc:
        raise _bad(exc) from exc


@router.patch("/api/people/{person_id}")
async def patch_person(person_id: int, payload: PersonPatch) -> dict[str, Any]:
    try:
        p = people.update(person_id, **payload.model_dump(exclude_none=True))
    except ValueError as exc:
        raise _bad(exc) from exc
    if not p:
        raise HTTPException(status_code=404, detail="没有这个人")
    return {"person": p}


@router.delete("/api/people/{person_id}")
async def delete_person(person_id: int) -> dict[str, Any]:
    if not people.delete(person_id):
        raise HTTPException(status_code=404, detail="没有这个人")
    return {"ok": True}


@router.post("/api/people/{person_id}/approve")
async def approve_person(person_id: int) -> dict[str, Any]:
    try:
        p = people.approve(person_id)
    except ValueError as exc:
        raise _bad(exc) from exc
    if not p:
        raise HTTPException(status_code=404, detail="没有这个人")
    return {"person": p}


@router.post("/api/people/draft-friends")
async def draft_friends(payload: DraftIn) -> dict[str, Any]:
    try:
        drafts = await people.draft_friends(db.get_settings(), payload.n)
    except ValueError as exc:
        raise _bad(exc) from exc
    except Exception as exc:  # noqa: BLE001 —— 模型 / 网络出错
        raise HTTPException(status_code=502, detail=f"生成失败：{exc}") from exc
    return {"drafts": drafts, "people": people.list_people()}


# ───────────────────────── 天气 ─────────────────────────
@router.get("/api/weather")
async def get_weather() -> dict[str, Any]:
    return weather.status()


@router.post("/api/weather/refresh")
async def refresh_weather() -> dict[str, Any]:
    await weather.refresh(force=True)
    return weather.status()


# ───────────────────────── 世界设定文件 ─────────────────────────
class WorldFileIn(BaseModel):
    category: str
    name: str
    content: str = ""


class WorldCategoryIn(BaseModel):
    name: str


@router.get("/api/world/files")
async def world_files() -> dict[str, Any]:
    return {"categories": world.list_tree()}


@router.get("/api/world/file")
async def world_file(category: str, name: str) -> dict[str, Any]:
    try:
        return {"category": category, "name": name, "content": world.read_file(category, name)}
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=404, detail=str(exc) or "没有这个文件") from exc


@router.put("/api/world/file")
async def save_world_file(payload: WorldFileIn) -> dict[str, Any]:
    try:
        world.write_file(payload.category, payload.name, payload.content)
    except ValueError as exc:
        raise _bad(exc) from exc
    return {"ok": True, "categories": world.list_tree()}


@router.delete("/api/world/file")
async def delete_world_file(category: str, name: str) -> dict[str, Any]:
    try:
        world.delete_file(category, name)
    except ValueError as exc:
        raise _bad(exc) from exc
    return {"ok": True, "categories": world.list_tree()}


@router.post("/api/world/category")
async def create_world_category(payload: WorldCategoryIn) -> dict[str, Any]:
    try:
        world.create_category(payload.name)
    except ValueError as exc:
        raise _bad(exc) from exc
    return {"ok": True, "categories": world.list_tree()}


# ───────────────────────── 地点库（作者层，见 app/places.py） ─────────────────────────
class PlacesIn(BaseModel):
    places: list[dict[str, Any]]


def _places_view() -> dict[str, Any]:
    from . import prices

    rows = places.load()
    here = places.here()
    return {"places": rows, "kinds": places.KINDS, "price_places": {k: prices.label("place", k) for k in places.PRICE_PLACES},
            "here": here["id"], "here_label": places.label(here["id"]) or here["name"]}


@router.get("/api/places")
async def get_places() -> dict[str, Any]:
    return _places_view()


@router.put("/api/places")
async def save_places(payload: PlacesIn) -> dict[str, Any]:
    try:
        places.save(payload.places)
    except ValueError as exc:
        raise _bad(exc) from exc
    return _places_view()


# ───────────────────────── 她的房间 ─────────────────────────
class RoomItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    category: str = ""        # 留空 = 让模型归类；「食物」= 进食物表（她可能会吃掉）
    location: str = ""        # 留空 = 让模型决定摆哪（食物只能书桌 / 食物桌）
    source: str = "gift"
    got_on: str = ""
    note: str = ""
    kind: str | None = None                                   # 食物：种类（不给就按名字猜）
    expires_h: float | None = Field(default=None, gt=0, le=24 * 365)   # 食物：保质期（小时，不给就按种类随机）
    portions: int | None = Field(default=None, ge=1, le=20)  # 食物：分几次吃完（不给就随机）


class RoomItemPatch(BaseModel):
    name: str | None = None
    category: str | None = None
    location: str | None = None
    source: str | None = None
    got_on: str | None = None
    note: str | None = None


@router.get("/api/room")
async def get_room() -> dict[str, Any]:
    return room.snapshot()


@router.post("/api/room/items")
async def create_room_item(payload: RoomItemIn) -> dict[str, Any]:
    try:
        if payload.category.strip() == room.FOOD_CAT:
            item = room.add_food(payload.name, payload.source, payload.location.strip() or None, payload.kind, payload.expires_h, payload.portions)
        elif payload.category.strip() and payload.location.strip():
            item = room.add_item(payload.name, payload.category, payload.location, payload.source,
                                 payload.note, payload.got_on or None, strict=True)
        else:                                             # 缺的让模型补（模型没接入就按默认归类）
            if payload.category.strip() and not room.category_names().count(payload.category.strip()):
                raise ValueError(f"没有「{payload.category}」这个分类")
            if payload.location.strip() and payload.location.strip() not in room.areas():
                raise ValueError(f"没有「{payload.location}」这个区域")
            if payload.source not in room.SOURCES:
                raise ValueError("来源只能是：" + "、".join(room.SOURCES.values()))
            item = await room.add_auto(payload.name, payload.source, payload.note, payload.category,
                                       payload.location, payload.got_on or None, settings=db.get_settings())
    except ValueError as exc:
        raise _bad(exc) from exc
    return {"item": item, **room.snapshot()}


@router.patch("/api/room/items/{item_id}")
async def patch_room_item(item_id: int, payload: RoomItemPatch) -> dict[str, Any]:
    try:
        item = room.update_item(item_id, **payload.model_dump(exclude_none=True))
    except ValueError as exc:
        raise _bad(exc) from exc
    if not item:
        raise HTTPException(status_code=404, detail="没有这件东西")
    return {"item": item, **room.snapshot()}


class RoomBookIn(BaseModel):
    title: str = Field(min_length=1, max_length=30)
    author: str = ""


class RoomBookPatch(BaseModel):
    title: str | None = None
    author: str | None = None
    note: str | None = None
    place: str | None = None                 # shelf 书架 | storage 收纳篮
    look: dict[str, Any] | None = None       # 外观参数（主色、花纹、书脊宽高、书名排版……）
    reroll: bool = False                     # 换个外观：重新随机一组外观参数


class RoomBookSwap(BaseModel):
    a: int
    b: int


@router.post("/api/room/books")
async def create_room_book(payload: RoomBookIn) -> dict[str, Any]:
    """我手动加一本摆设书（不可阅读，外观随机）。"""
    try:
        book = room.add_phal_book(payload.title, payload.author, source="user")
    except ValueError as exc:
        raise _bad(exc) from exc
    return {"book": book, **room.snapshot()}


@router.patch("/api/room/books/{pid}")
async def patch_room_book(pid: int, payload: RoomBookPatch) -> dict[str, Any]:
    """改摆设书：书名 / 作者 / 外观参数，或者在书架和收纳篮之间挪。导入的书不在这里（在阅读器的书架里管理）。"""
    fields = payload.model_dump(exclude_none=True)
    if fields.pop("reroll", False):
        from . import booklook

        fields["look"] = booklook.random_look()
    try:
        book = room.update_book(pid, **fields)
    except ValueError as exc:
        raise _bad(exc) from exc
    if not book:
        raise HTTPException(status_code=404, detail="没有这本书")
    return {"book": book, **room.snapshot()}


@router.delete("/api/room/books/{pid}")
async def delete_room_book(pid: int) -> dict[str, Any]:
    if not room.delete_book(pid):
        raise HTTPException(status_code=404, detail="没有这本书")
    return {"ok": True, **room.snapshot()}


@router.post("/api/room/books/swap")
async def swap_room_books(payload: RoomBookSwap) -> dict[str, Any]:
    """交换书架上的一本摆设书和收纳篮里的一本。"""
    try:
        room.swap_books(payload.a, payload.b)
    except ValueError as exc:
        raise _bad(exc) from exc
    return {"ok": True, **room.snapshot()}


# ───────────────────────── 素材目录 ─────────────────────────
@router.get("/api/assets")
async def get_assets() -> dict[str, Any]:
    """素材清单（assets/manifest.json）+ 版本号（所有素材文件里最新的修改时间，页面用它给图片地址加 ?v=，你替换素材后刷新就能看到）。"""
    import json as _json

    from .config import ASSETS_DIR

    path = ASSETS_DIR / "manifest.json"
    if not path.exists():
        return {"available": False, "manifest": None, "version": 0}
    try:
        manifest = _json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"manifest.json 读不了：{exc}") from exc
    newest = path.stat().st_mtime
    for p in ASSETS_DIR.rglob("*"):
        if p.is_file() and p.suffix.lower() in (".png", ".webp", ".jpg", ".jpeg", ".gif", ".svg"):
            newest = max(newest, p.stat().st_mtime)
    return {"available": True, "manifest": manifest, "version": int(newest)}


class ClutterIn(BaseModel):
    kind: str = "snack"
    name: str = ""
    place: str | None = None                                  # desk 书桌杂物区 | table 食物桌；不给就按设置随机
    expires_h: float | None = Field(default=None, gt=0, le=24 * 365)   # 保质期（小时）；不给就按种类随机
    portions: int | None = Field(default=None, ge=1, le=20)
    force: bool = True                                        # 你手动放的：她睡着也能放
    source: str = "gift"


class ClutterPatch(BaseModel):
    name: str | None = Field(default=None, max_length=30)
    kind: str | None = None
    location: str | None = None                               # 书桌 / 食物桌
    source: str | None = None
    expires_h: float | None = Field(default=None, gt=0, le=24 * 365)   # 从现在起还有几小时过期
    portions_left: int | None = Field(default=None, ge=1, le=20)


@router.post("/api/room/clutter")
async def create_clutter(payload: ClutterIn) -> dict[str, Any]:
    """你往房间里放一份食物（房间页面「添加物品」选分类「食物」）：种类、名字、放哪、保质期、分几次吃。"""
    if payload.kind not in clutter.KINDS:
        raise HTTPException(status_code=400, detail="种类只能是：" + "、".join(f"{k}（{v}）" for k, v in clutter.KINDS.items()))
    if payload.place is not None and payload.place not in clutter.PLACES:
        raise HTTPException(status_code=400, detail="放哪只能是：desk（书桌）或 table（食物桌）")
    import time as _time

    exp = _time.time() + payload.expires_h * 3600 if payload.expires_h else None
    item = clutter.spawn(payload.kind, payload.name, force=payload.force, place=payload.place, expires_at=exp, portions=payload.portions,
                         source=payload.source if payload.source in clutter.SOURCE_LABEL else "gift")
    if item is None:
        raise HTTPException(status_code=400, detail="她睡着了，现在放不了")
    return {"item": item, **room.snapshot()}


@router.get("/api/room/pose-tuning")
async def get_pose_tuning() -> dict[str, Any]:
    """角色姿势调试器（房间页面 Ctrl+Shift+P）调过的偏移 / 旋转 / 大小。"""
    from . import posetune

    return {"tuning": posetune.load()}


@router.put("/api/room/pose-tuning")
async def put_pose_tuning(payload: dict[str, Any]) -> dict[str, Any]:
    from . import posetune

    try:
        return {"tuning": posetune.save(payload.get("tuning", payload))}
    except ValueError as exc:
        raise _bad(exc) from exc


@router.patch("/api/room/clutter/{clutter_id}")
async def patch_clutter(clutter_id: int, payload: ClutterPatch) -> dict[str, Any]:
    """改一份食物：名字、种类、位置（书桌 / 食物桌）、来源、保质期（还有几小时）、还剩几次。"""
    try:
        item = clutter.update(clutter_id, **payload.model_dump(exclude_none=True))
    except ValueError as exc:
        raise _bad(exc) from exc
    if not item:
        raise HTTPException(status_code=404, detail="这份已经吃完 / 收掉了")
    return {"item": item, **room.snapshot()}


@router.delete("/api/room/clutter/{clutter_id}")
async def delete_clutter(clutter_id: int) -> dict[str, Any]:
    if not clutter.remove(clutter_id):
        raise HTTPException(status_code=404, detail="这件东西已经收掉了")
    return {"ok": True, **room.snapshot()}


@router.get("/api/room/ambient")
async def get_ambient() -> dict[str, Any]:
    return weather.ambient()


@router.delete("/api/room/items/{item_id}")
async def delete_room_item(item_id: int) -> dict[str, Any]:
    if not room.delete_item(item_id):
        raise HTTPException(status_code=404, detail="没有这件东西")
    return {"ok": True, **room.snapshot()}


# ───────────────────────── 零花钱 ─────────────────────────
class MoneyAdjustIn(BaseModel):
    amount: float                    # 钱，正数给她加，负数扣掉
    note: str = ""


@router.get("/api/money")
async def get_money(month: str = "") -> dict[str, Any]:
    """账本页：余额、这个月的收入 / 支出 / 分类汇总、完整明细（包括她没告诉你的）。"""
    return money.snapshot(month or None)


@router.post("/api/money/adjust")
async def adjust_money(payload: MoneyAdjustIn) -> dict[str, Any]:
    try:
        entry = money.adjust(payload.amount, payload.note)
    except ValueError as exc:
        raise _bad(exc) from exc
    return {"entry": entry, **money.snapshot()}


# ───────────────────────── 生活事件 ─────────────────────────
@router.get("/api/events")
async def get_events() -> dict[str, Any]:
    """今天的事件计划 + 最近的事件（包括她没告诉你的）。"""
    return events.snapshot()
