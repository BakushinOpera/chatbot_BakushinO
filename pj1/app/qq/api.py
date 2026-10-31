"""/api/qq/*：QQ 页用的接口（状态、设置、群友印象、模拟群聊）。

这里**不返回任何真实的聊天记录**：桌面端不显示 QQ 数据。模拟群聊的记录是假的（存在 sim:main 里）；群友印象是她对每个人的印象，不是聊天内容。
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import ctx, engine as eng
from . import settings as qqset
from . import store
from .localview import LocalView

router = APIRouter()
CONNECTION_KEYS = {"enabled", "host", "port", "token", "reconnect_min_s", "reconnect_max_s"}


class ConfigIn(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


class ResetIn(BaseModel):
    keys: list[str] | None = None


class MemberPatch(BaseModel):
    chat: str
    user_id: str
    call_name: str | None = None
    impression: str | None = None
    familiarity: float | None = None


class SimMember(BaseModel):
    user_id: str
    name: str
    is_owner: bool = False


class SimMembersIn(BaseModel):
    members: list[SimMember]


class SimSendIn(BaseModel):
    user_id: str
    name: str
    text: str = ""
    is_owner: bool = False
    at_her: bool = False
    reply_to: int | None = None
    visual: bool = False
    instant: bool = True                 # 跳过 20~90 秒的等待窗口，马上处理
    force: bool = False                  # 跳过掷骰子（睡觉 / 静默 / 上限照样拦）
    state: str | None = None             # 假装她现在在这个状态
    busy: bool | None = None             # 假装主人刚在桌面里和她聊过
    mood: str | None = None


# ───────────────────────── 状态 / 设置 ─────────────────────────
@router.get("/api/qq/status")
async def qq_status() -> dict[str, Any]:
    return eng.get_engine().status()


@router.get("/api/qq/config")
async def qq_get_config() -> dict[str, Any]:
    return {"schema": qqset.schema(), "values": qqset.public()}


@router.put("/api/qq/config")
async def qq_put_config(payload: ConfigIn) -> dict[str, Any]:
    before = qqset.load()
    values = qqset.save(payload.values)
    if any(before.get(k) != values.get(k) for k in CONNECTION_KEYS):
        eng.get_engine().reload()
    return {"values": qqset.public(values)}


@router.post("/api/qq/config/reset")
async def qq_reset_config(payload: ResetIn) -> dict[str, Any]:
    values = qqset.reset(payload.keys)
    eng.get_engine().reload()
    return {"values": qqset.public(values)}


@router.post("/api/qq/reconnect")
async def qq_reconnect() -> dict[str, Any]:
    e = eng.get_engine()
    e.reload()
    return e.status()


@router.get("/api/qq/preview/persona")
async def qq_preview_persona() -> dict[str, Any]:
    """群聊里她看到的人设（隐藏了设置里列的小节、接上了群聊补充和主人的基础信息）。"""
    e = eng.get_engine()
    env = ctx.Env(local=e.local, settings=e.local.settings(), cfg=e.cfg(), now=e.clock())
    return {"group": ctx.persona_for(env, "group")}


# ───────────────────────── 群友印象 ─────────────────────────
def _member_view(m: dict[str, Any]) -> dict[str, Any]:
    return {**m, "label": ctx.familiarity_label(float(m["familiarity"])), "chat_label": "模拟群" if m["chat"].startswith("sim:") else f"群 {m['chat'][2:]}"}


@router.get("/api/qq/members")
async def qq_members() -> dict[str, Any]:
    return {"members": [_member_view(m) for m in store.list_members()]}


@router.patch("/api/qq/members")
async def qq_patch_member(p: MemberPatch) -> dict[str, Any]:
    m = store.update_member(p.chat, p.user_id, call_name=p.call_name, impression=p.impression, familiarity=p.familiarity)
    if not m:
        raise HTTPException(status_code=404, detail="没有这个人")
    return {"member": _member_view(m)}


@router.delete("/api/qq/members")
async def qq_delete_member(chat: str, user_id: str = "") -> dict[str, Any]:
    if user_id:
        if not store.delete_member(chat, user_id):
            raise HTTPException(status_code=404, detail="没有这个人")
        return {"ok": True}
    return {"ok": True, "deleted": store.clear_members(chat)}


# ───────────────────────── 模拟群聊 ─────────────────────────
DEFAULT_SIM_MEMBERS = [
    {"user_id": "10001", "name": "我（主人）", "is_owner": True},
    {"user_id": "20001", "name": "小明", "is_owner": False},
    {"user_id": "20002", "name": "阿雨", "is_owner": False},
]


def _sim_members() -> list[dict[str, Any]]:
    stored = store.kv_json("sim_members", None)
    return stored if isinstance(stored, list) and stored else [dict(m) for m in DEFAULT_SIM_MEMBERS]


def _sim_messages() -> list[dict[str, Any]]:
    out = []
    for r in store.recent(eng.SIM_CHAT, 80):
        out.append({"id": r["id"], "ts": r["ts"], "user_id": r["user_id"], "name": "Phal" if r["is_self"] else (r["card"] or r["nickname"]), "is_owner": bool(r["is_owner"]),
                    "is_self": bool(r["is_self"]), "text": r["text"], "quote": r["quote"]})
    return out


@router.get("/api/qq/sim")
async def qq_sim() -> dict[str, Any]:
    e = eng.get_engine()
    local = LocalView()
    settings = local.settings()
    now = e.clock()
    state = local.state_key(now, settings)
    mood, _ = local.mood(now, settings)
    cfg = e.cfg()
    last = local.last_desktop_user_ts()
    return {
        "members": _sim_members(), "messages": _sim_messages(), "trace": e.traces.get(eng.SIM_CHAT),
        "muted": e._mute_until(eng.SIM_CHAT) > now,
        "real": {"state": state, "state_label": qqset.state_label(state), "mood": mood,
                 "busy": bool(cfg["busy_minutes"] and last and now - last < float(cfg["busy_minutes"]) * 60)},
        "states": [{"key": k, "label": lb} for k, lb in qqset.STATE_KEYS],
    }


@router.put("/api/qq/sim/members")
async def qq_sim_members(p: SimMembersIn) -> dict[str, Any]:
    members = [m.model_dump() for m in p.members][:12]
    store.kv_set("sim_members", json.dumps(members, ensure_ascii=False))
    return {"members": members}


@router.post("/api/qq/sim/send")
async def qq_sim_send(p: SimSendIn) -> dict[str, Any]:
    if not p.text.strip() and not p.visual:
        raise HTTPException(status_code=400, detail="说点什么")
    e = eng.get_engine()
    overrides = {"state": p.state or None, "busy": p.busy, "mood": p.mood}
    if not p.instant:
        e.overrides[eng.SIM_CHAT] = overrides
    run = eng.Run(fast=p.instant, force=p.force, **overrides)
    trace = await e.sim_send(p.user_id, p.name, p.text, p.is_owner, p.at_her, p.reply_to, p.visual, run)
    return {"trace": trace, "messages": _sim_messages(), "muted": e._mute_until(eng.SIM_CHAT) > e.clock()}


@router.post("/api/qq/sim/proactive")
async def qq_sim_proactive(p: SimSendIn | None = None) -> dict[str, Any]:
    e = eng.get_engine()
    p = p or SimSendIn(user_id="0", name="")
    run = eng.Run(fast=True, force=True, state=p.state or None, busy=p.busy, mood=p.mood)
    trace = await e.proactive_tick(eng.SIM_CHAT, run)
    return {"trace": trace, "messages": _sim_messages()}


@router.post("/api/qq/sim/reset")
async def qq_sim_reset() -> dict[str, Any]:
    e = eng.get_engine()
    store.clear_chat(eng.SIM_CHAT)
    store.clear_members(eng.SIM_CHAT)
    e._set_mute(eng.SIM_CHAT, 0)
    e.traces.pop(eng.SIM_CHAT, None)
    e.overrides.pop(eng.SIM_CHAT, None)
    return {"ok": True}


@router.post("/api/qq/sim/unmute")
async def qq_sim_unmute() -> dict[str, Any]:
    eng.get_engine()._set_mute(eng.SIM_CHAT, 0)
    return {"ok": True}
