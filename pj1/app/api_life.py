"""Phal 的生活：状态 tick、草稿本。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import db, life, library as lib

router = APIRouter()


class TickIn(BaseModel):
    after_id: int = Field(default=0, ge=0)     # 页面已经看到的最大消息 id；之后 Phal 主动发来的会一并带回


class LifeToggle(BaseModel):
    enabled: bool


@router.post("/api/life/tick")
async def life_tick(payload: TickIn) -> dict[str, Any]:
    """页面开着时每 30 秒调一次：推进她的生活，并带回她主动发来的新消息。"""
    if not life.enabled():
        return {"status": {"enabled": False}, "messages": []}
    status = life.tick()
    messages = db.messages_after_id(payload.after_id) if payload.after_id > 0 else []
    return {"status": status, "messages": messages}


@router.get("/api/life")
async def life_status() -> dict[str, Any]:
    if not life.enabled():
        return {"status": {"enabled": False}}
    st = life.load_state()
    if st is None:
        return {"status": life.tick()}
    return {"status": life.status_view(st)}


@router.post("/api/life/enabled")
async def life_set_enabled(payload: LifeToggle) -> dict[str, Any]:
    db.save_settings({"life_enabled": "1" if payload.enabled else "0"})
    return {"enabled": payload.enabled}


@router.get("/api/drafts")
async def list_drafts() -> dict[str, Any]:
    return {"drafts": lib.list_drafts(), "enabled": life.enabled()}


@router.delete("/api/drafts/{draft_id}")
async def delete_draft(draft_id: int) -> dict[str, Any]:
    if not lib.delete_draft(draft_id):
        raise HTTPException(status_code=404, detail="草稿不存在")
    return {"ok": True}
