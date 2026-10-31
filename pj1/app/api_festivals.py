"""节日：在日历里添加 / 编辑 / 删除。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import festivals

router = APIRouter()


class FestivalIn(BaseModel):
    name: str = Field(min_length=1, max_length=30)
    date: str                       # YYYY-MM-DD（每年重复的只看月和日）
    yearly: bool = True             # 是否每年重复
    note: str = Field(default="", max_length=60)   # 一句备注


class FestivalPatch(BaseModel):
    name: str | None = None
    date: str | None = None
    yearly: bool | None = None
    note: str | None = None


@router.get("/api/festivals")
async def list_festivals() -> dict[str, Any]:
    return {"festivals": festivals.list_all(), "today": festivals.today()}


@router.post("/api/festivals")
async def add_festival(payload: FestivalIn) -> dict[str, Any]:
    try:
        return {"festival": festivals.add(payload.name, payload.date, payload.yearly, payload.note)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/api/festivals/{fid}")
async def patch_festival(fid: int, payload: FestivalPatch) -> dict[str, Any]:
    try:
        f = festivals.update(fid, payload.name, payload.date, payload.yearly, payload.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not f:
        raise HTTPException(status_code=404, detail="节日不存在")
    return {"festival": f}


@router.delete("/api/festivals/{fid}")
async def delete_festival(fid: int) -> dict[str, Any]:
    if not festivals.delete(fid):
        raise HTTPException(status_code=404, detail="节日不存在")
    return {"ok": True}
