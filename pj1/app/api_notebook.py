"""小本子页面的接口（见 app/notebook.py）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import db, notebook, notebook_write, places
from . import character as C

router = APIRouter()


def _bad(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


def _view_entry(e: dict[str, Any]) -> dict[str, Any]:
    out = dict(e)
    out.pop("norm", None)
    out["place_label"] = places.label(e["place"]) if e.get("place") else ""
    out["auto_keywords"] = notebook.keywords_of(e)[:1]
    return out


def _view() -> dict[str, Any]:
    by = places.by_id()
    return {
        "chapters": notebook.chapters(),
        "unit": C.unit(),
        "entries": [_view_entry(e) for e in notebook.list_entries(status=None)],
        "sources": notebook.SOURCES, "trusts": notebook.TRUSTS, "accuracy": notebook.ACCURACY,
        "places": [{"id": p["id"], "label": places.label(p["id"]) or p["name"]} for p in by.values() if p["kind"] not in ("world", "home")],
        "character": notebook.config.CHARACTER_ID,
        "copy": notebook_write.copy_status(),
        "chat_ready": notebook.chat_ready(),
        "flags": [dict(f, title=(notebook.get(f["entry_id"]) or {}).get("title", "")) for f in notebook_write.flags()],
    }


class EntryIn(BaseModel):
    chapter: str | None = None
    title: str | None = None
    keywords: list[str] | str | None = None
    body: str | None = None
    addenda: list[dict[str, Any]] | None = None
    info: dict[str, Any] | None = None
    trust: str | None = None
    source: str | None = None
    place: str | None = None
    locked: bool | None = None


class LockIn(BaseModel):
    locked: bool


class RestoreIn(BaseModel):
    history_id: int


class ApproveIn(BaseModel):
    ids: list[int] | None = None


class FlagIn(BaseModel):
    delete: bool


class ChaptersIn(BaseModel):
    chapters: list[dict[str, Any]]


def _fields(p: EntryIn) -> dict[str, Any]:
    return {k: v for k, v in p.model_dump().items() if v is not None}


@router.get("/api/notebook")
async def get_notebook() -> dict[str, Any]:
    return _view()


@router.post("/api/notebook/entry")
async def create_entry(payload: EntryIn) -> dict[str, Any]:
    try:
        e = notebook.create(_fields(payload), by="user")
    except ValueError as exc:
        raise _bad(exc) from exc
    return {"entry": _view_entry(e), **_view()}


@router.put("/api/notebook/entry/{eid}")
async def update_entry(eid: int, payload: EntryIn) -> dict[str, Any]:
    old = notebook.get(eid)
    try:
        e = notebook.update(eid, _fields(payload), by="user")
    except ValueError as exc:
        raise _bad(exc) from exc
    if old:
        notebook_write.maybe_notice(old, e)            # 开着「她察觉到修改」时：按概率记下来，下次聊天她可能提一句
    return {"entry": _view_entry(e), **_view()}


@router.delete("/api/notebook/entry/{eid}")
async def delete_entry(eid: int) -> dict[str, Any]:
    notebook.delete(eid, by="user")
    return _view()


@router.post("/api/notebook/entry/{eid}/lock")
async def lock_entry(eid: int, payload: LockIn) -> dict[str, Any]:
    try:
        notebook.set_locked(eid, payload.locked)
    except ValueError as exc:
        raise _bad(exc) from exc
    return _view()


@router.get("/api/notebook/entry/{eid}/history")
async def entry_history(eid: int) -> dict[str, Any]:
    return {"history": notebook.history(eid)}


@router.post("/api/notebook/entry/{eid}/restore")
async def restore_entry(eid: int, payload: RestoreIn) -> dict[str, Any]:
    try:
        e = notebook.restore(eid, payload.history_id, by="user")
    except ValueError as exc:
        raise _bad(exc) from exc
    return {"entry": _view_entry(e), **_view()}


@router.post("/api/notebook/copy")
async def start_copy() -> dict[str, Any]:
    """初始抄写：每个世界设定文件一次调用，在后台跑；抄出来的条目先待审。"""
    st = notebook_write.copy_status()
    if st["state"] in ("copying", "pending"):
        raise HTTPException(status_code=400, detail="已经在抄 / 还有待审的条目")
    if not st["sources"]:
        raise HTTPException(status_code=400, detail="世界设定里没有可以抄的文件")
    notebook_write.start_copy(db.get_settings())
    return _view()


@router.post("/api/notebook/copy/skip")
async def skip_copy() -> dict[str, Any]:
    notebook_write.skip_copy()
    return _view()


@router.post("/api/notebook/approve")
async def approve(payload: ApproveIn) -> dict[str, Any]:
    notebook_write.approve(payload.ids)
    return _view()


@router.post("/api/notebook/flag/{fid}")
async def resolve_flag(fid: int, payload: FlagIn) -> dict[str, Any]:
    notebook_write.resolve_flag(fid, payload.delete)
    return _view()


@router.put("/api/notebook/chapters")
async def save_chapters(payload: ChaptersIn) -> dict[str, Any]:
    try:
        notebook.save_chapters(payload.chapters)
    except ValueError as exc:
        raise _bad(exc) from exc
    return _view()
