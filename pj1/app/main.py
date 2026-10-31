"""FastAPI 应用：路由 + SSE 流式对话。

接口一览
--------
GET    /                      前端页面
GET    /api/bootstrap         启动快照（设置 / 当前时间 / 统计）
GET    /api/messages          当前会话聊天记录
POST   /api/chat              SSE 流式对话（核心）
POST   /api/settings          保存人设与配置（只动 settings 表）
GET    /api/search            搜索全部历史归档
GET    /api/memories          长期记忆列表
POST   /api/memories          手动新增一条记忆
DELETE /api/memories/{id}     删除一条记忆
POST   /api/memories/summarize 让模型整理长期记忆
POST   /api/session/new       开一段新对话（归档不动）
GET    /api/health            健康检查
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import date, timedelta
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import api_books, api_festivals, api_life, api_notebook, api_v3, api_writing, burst, context, db, festivals, guards, intents, life, llm, memory, moodsync, planner, registry, reply, room, style, usage, whylog, world, writing
from . import character as C
from .qq import api as qq_api, engine as qq_engine
from .config import API_KEY_MASK, APP_NAME, ASSETS_DIR, DEFAULT_PERSONA, PERSONA_NOTE, WEB_DIR, dump_words, parse_words
from .workdays import is_weekend, pending_for

log = logging.getLogger(__name__)

_background_tasks: set[asyncio.Task] = set()

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    db.init_db()
    world.ensure_dirs()
    # 禁词表单独存成文件；第一次把以前在界面里加过的禁词一起迁移过来
    guards.ensure_banned_file(parse_words(db.get_settings().get("banned_words")))
    qq_engine.get_engine().start()          # QQ 接入：设置里启用了才会去连 NapCat（没启用就只是空转）
    yield
    await qq_engine.get_engine().stop()
    await llm.close_client()


app = FastAPI(title=APP_NAME, lifespan=lifespan)
@app.middleware("http")
async def no_stale_frontend(request: Request, call_next):
    """页面和静态文件每次都向服务器确认有没有更新（没更新只回 304，几乎不花时间）。
    不然改过的前端可能被浏览器缓存住，看起来就像「什么都没变」。"""
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path == "/reader" or path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


app.include_router(api_books.router)
app.include_router(api_life.router)
app.include_router(api_writing.router)
app.include_router(api_festivals.router)
app.include_router(api_v3.router)
app.include_router(api_notebook.router)
app.include_router(qq_api.router)


# --------------------------------------------------------------------------
# 模型
# --------------------------------------------------------------------------
class ChatIn(BaseModel):
    content: str = Field(min_length=1)
    quote_id: int | None = None
    quote_text: str | None = None
    quote_role: str | None = None
    quote_meta: dict[str, Any] | None = None     # 引用的是书里的一段 / 一条批注时：书、章节、位置


class SettingsIn(BaseModel):
    persona_name: str | None = None
    system_prompt: str | None = None
    banned_words: list[str] | None = None
    common_words: list[str] | None = None
    recent_limit: int | None = Field(default=None, ge=1, le=200)
    memory_limit: int | None = Field(default=None, ge=0, le=100)
    enable_tools: bool | None = None
    split_replies: bool | None = None
    enable_auto_memory: bool | None = None
    auto_memory_every: int | None = Field(default=None, ge=5, le=200)
    api_base: str | None = None
    api_key: str | None = None
    clear_api_key: bool = False
    model: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=0, le=32000)
    context_char_budget: int | None = Field(default=None, ge=1000, le=200000)
    life_read_day_prob: float | None = Field(default=None, ge=0.0, le=1.0)      # 每天读书的概率
    life_read_cap_min: int | None = Field(default=None, ge=0, le=240)           # 每天累计读书上限（分钟）
    reply_delay: float | None = Field(default=None, ge=0.0, le=5.0)             # 条与条之间随机停顿的倍率，0 = 不停顿
    memory_extract_every: int | None = Field(default=None, ge=1, le=50)         # 每几轮对话提取一次记忆
    writing_p_revisit: float | None = Field(default=None, ge=0.0, le=1.0)       # 写作时回头续写之前内容的概率
    writing_p_new_doc: float | None = Field(default=None, ge=0.0, le=1.0)       # 不接着写时，先问我要新文档的概率（否则在已有文档里分隔另写）
    writing_p_self_delete: float | None = Field(default=None, ge=0.0, le=1.0)   # 心情低落写东西时，回头删掉写得不好的内容的概率
    writing_p_insist: float | None = Field(default=None, ge=0.0, le=1.0)        # 我改了她的文档后，她坚持己见、把改动撤回的概率
    life_read_refuse_prob: float | None = Field(default=None, ge=0.0, le=1.0)   # 今天没读满时，她拒绝「去读书」的概率
    life_interrupt_prob: float | None = Field(default=None, ge=0.0, le=1.0)     # 打游戏 / 刷视频被打断时，先撒娇问「干嘛打扰我」的概率
    enable_style_learning: bool | None = None
    style_notes: str | None = None


class MessageEdit(BaseModel):
    content: str = Field(min_length=1)


class MemoryIn(BaseModel):
    content: str = Field(min_length=1)
    kind: str = "fact"
    category: str | None = None        # about_user / phal_fact / shared


class HistoryClearIn(BaseModel):
    scope: str = "current"          # current = 当前这段对话, all = 全部历史
    with_memories: bool = False     # 是否连长期记忆一起清掉


# --------------------------------------------------------------------------
# 工具函数
# --------------------------------------------------------------------------
def public_settings(settings: dict[str, str]) -> dict[str, Any]:
    key = (settings.get("api_key") or "").strip()
    return {
        "persona_name": C.display_name(settings),
        "system_prompt": settings.get("system_prompt") or "",
        "banned_words": guards.load_banned(),
        "common_words": parse_words(settings.get("common_words")),
        "recent_limit": int(settings.get("recent_limit") or 24),
        "memory_limit": int(settings.get("memory_limit") or 12),
        "enable_tools": settings.get("enable_tools") == "1",
        "split_replies": settings.get("split_replies") == "1",
        "enable_auto_memory": settings.get("enable_auto_memory") == "1",
        "auto_memory_every": int(settings.get("auto_memory_every") or 20),
        "api_base": settings.get("api_base") or "",
        "model": settings.get("model") or "",
        "temperature": float(settings.get("temperature") or 0.9),
        "max_tokens": int(settings.get("max_tokens") or 0),
        "context_char_budget": int(settings.get("context_char_budget") or 12000),
        "life_read_day_prob": float(settings.get("life_read_day_prob") or life.DEFAULT_READ_DAY_PROB),
        "life_read_cap_min": int(float(settings.get("life_read_cap_min") or life.DEFAULT_READ_CAP_MIN)),
        "reply_delay": float(settings.get("reply_delay") if settings.get("reply_delay") not in (None, "") else 1),
        "memory_extract_every": int(settings.get("memory_extract_every") or 1),
        "writing_p_revisit": float(settings.get("writing_p_revisit") if settings.get("writing_p_revisit") not in (None, "") else writing.P_REVISIT),
        "writing_p_new_doc": float(settings.get("writing_p_new_doc") if settings.get("writing_p_new_doc") not in (None, "") else writing.P_NEW_DOC),
        "writing_p_self_delete": float(settings.get("writing_p_self_delete") if settings.get("writing_p_self_delete") not in (None, "") else writing.P_SELF_DELETE),
        "writing_p_insist": float(settings.get("writing_p_insist") if settings.get("writing_p_insist") not in (None, "") else writing.P_INSIST),
        "life_read_refuse_prob": float(settings.get("life_read_refuse_prob") if settings.get("life_read_refuse_prob") not in (None, "") else life.DEFAULT_REFUSE_READ_PROB),
        "life_interrupt_prob": float(settings.get("life_interrupt_prob") if settings.get("life_interrupt_prob") not in (None, "") else life.DEFAULT_INTERRUPT_PROB),
        "enable_style_learning": settings.get("enable_style_learning") == "1",
        "style_notes": settings.get("style_notes") or "",
        "edits_count": db.count_edits(),
        "api_key_masked": (API_KEY_MASK if key else ""),
        "api_key_set": bool(key),
        "echo_mode": llm.is_echo(settings),
        "persona_note": PERSONA_NOTE,
    }


def _parse_meta(raw: Any) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def sse(event: dict[str, Any]) -> str:
    return "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"


async def run_tool(name: str, args: dict[str, Any], session_id: str) -> str:
    if name == "search_history":
        query = str(args.get("query") or "").strip()
        try:
            limit = int(args.get("limit") or 6)
        except (TypeError, ValueError):
            limit = 6
        limit = max(1, min(limit, 20))
        rows, total = db.search_archive(query, limit=limit, exclude_session=session_id)
        if not rows:
            return f"在历史归档中没有找到与「{query}」相关的记录。请如实告诉用户你想不起来了，不要编造。"
        body = db.archive_context_lines(rows)
        return f"在历史归档中找到 {total} 条相关记录，以下是最近的 {len(rows)} 条（时间从近到远）：\n{body}"

    if name == "save_memory":
        content = str(args.get("content") or "").strip()
        category = str(args.get("category") or "").strip()
        saved = db.add_memory(content, category=category if category in db.MEMORY_CATEGORIES else "shared",
                              session_id=session_id, source="model")
        if saved is None:
            return "这条记忆已经存在了，无需重复记录。"
        return f"已写入长期记忆：{content}"

    if name == "read_doc":
        return writing.read_doc_tool(
            str(args.get("title") or ""), args.get("kind") if args.get("kind") in ("mine", "phal") else None,
            int(args.get("part") or 1) if str(args.get("part") or "1").lstrip("-").isdigit() else 1,
        )

    if name == "add_task":
        title = str(args.get("title") or "").strip()
        if not title:
            return "任务内容不能为空。"
        day = str(args.get("day") or "").strip() or context.now_info()["date"]
        try:
            remind = int(args.get("remind_after_days") or 3)
        except (TypeError, ValueError):
            remind = 3
        try:
            every = int(args.get("repeat_every_days") or 0)
        except (TypeError, ValueError):
            every = 0
        if every > 0:
            task = db.create_series(title, day, min(every, 365), max(0, remind), source="chat",
                                    priority=str(args.get("priority") or "minor"))
            return f"已经加到日历：{task['title']}（{task['day']} 起，每 {every} 天重复，休息日自动变可选）。"
        task = db.add_task(title, day, remind_after_days=max(0, remind), source="chat",
                           priority="main" if str(args.get("priority")) == "main" else "minor")
        return f"已经加到日历：{task['title']}（{task['day']}，拖满 {task['remind_after_days']} 天提醒）。"

    return f"没有名为 {name} 的工具。"


async def summarize_memories(session_id: str | None = None, limit: int = 40) -> list[dict[str, Any]]:
    """手动「整理当前会话」：把最近的对话过一遍，提取三类记忆条目（去重 / 冲突以新的为准）。"""
    settings = db.get_settings()
    if llm.is_echo(settings):
        raise HTTPException(status_code=400, detail="离线演示模式下无法整理记忆，请先配置 API Key。")
    session_id = session_id or db.current_session_id()
    rows = db.session_messages(session_id, limit)
    if not rows:
        return []
    created = await memory.extract_rows(settings, rows, session_id)
    db.save_settings({"auto_memory_marker": str(rows[-1]["id"])})
    return created


def schedule_style_learning() -> None:
    settings = db.get_settings()
    if not style.should_learn(settings):
        return

    async def runner() -> None:
        try:
            await style.learn_style(settings)
        except Exception as exc:  # noqa: BLE001
            log.warning("学习说话风格失败：%s: %s", type(exc).__name__, exc)

    task = asyncio.create_task(runner())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


# --------------------------------------------------------------------------
# 页面
# --------------------------------------------------------------------------
@app.get("/")
async def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/reader")
async def reader_page() -> FileResponse:
    """阅读器已经并进聊天页：/reader 还是能直接打开，只是加载的是同一个网页，并自动展开书架 / 那本书。"""
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/world")
async def world_info(q: str = "") -> dict[str, Any]:
    """世界设定目录一览；带 ?q=某句话 可以看这句话会带进哪些条目（调试用）。"""
    data = world.summary()
    if q:
        data["matched"] = [{"category": m["category"], "title": m["title"], "matched": m["matched"]} for m in world.match(q)]
    return data


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "app": APP_NAME, "now": context.now_info()}


@app.get("/api/bootstrap")
async def bootstrap() -> dict[str, Any]:
    settings = db.get_settings()
    db.init_db()
    return {
        "settings": public_settings(settings),
        "now": context.now_info(),
        "stats": db.stats(),
        "session_id": db.current_session_id(),
        "default_persona": DEFAULT_PERSONA,
    }


# --------------------------------------------------------------------------
# 聊天
# --------------------------------------------------------------------------
@app.get("/api/messages")
async def get_messages(limit: int = Query(default=200, ge=1, le=1000)) -> dict[str, Any]:
    return {"messages": db.session_messages(db.current_session_id(), limit)}


SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _retry_messages(messages: list[dict[str, Any]], hits: list[str]) -> list[dict[str, Any]]:
    """禁词命中后重新生成：在最后一条用户消息后面加一句提示（不提它们，换说法）。"""
    msgs = [dict(m) for m in messages]
    for m in reversed(msgs):
        if m["role"] == "user":
            m["content"] = m["content"] + (
                f"\n\n【系统提示：你上一版回复里出现了不该用的说法（{'、'.join(hits[:5])}），"
                "换一种说法把这一轮重新回复一遍，不要提这件事。】"
            )
            break
    return msgs


def typing_scale(settings: dict[str, Any], ooc: bool = False, outside: bool = False) -> float:
    """条与条之间「正在输入」停顿的倍率：设置里的总倍率 × 在外面 × 她这会儿闲 / 一般 / 忙（停顿本身跟字数挂钩，见 guards.delay_for）。"""
    try:
        scale = float(settings.get("reply_delay") or 1)
    except (TypeError, ValueError):
        scale = 1.0
    if outside:
        scale *= registry.num("outside_delay_mult")
    if not ooc and life.enabled():
        scale *= registry.num(f"type_k_{burst.current_tier()}")      # 闲着打得快、忙的时候慢
    return scale


async def reply_events(
    session_id: str,
    messages: list[dict[str, Any]],
    meta: dict[str, Any],
    prelude: list[dict[str, Any]],
    prepare: Any = None,
    ooc: bool = False,
    after: Any = None,
    hooks: dict[str, Any] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """一轮回复的全过程，产出事件 dict（/api/chat、/api/chat/regenerate 包成 SSE；连发队列 burst.py 广播给页面）。

    角色模式（默认）：先在后端把整条回复生成完，再做代码层兜底，然后才一条条发出去——
      剥掉模型附带的动作 → 感叹号换成句号 / 删掉 → 禁词检查（命中就重新生成，最多 2 次）
      → 按换行和句号切成多条 → 每条之间随机停顿，像真人在打字。
    出戏（消息以【OOC】开头）和离线演示模式：直通，不切分、不检查，也不写入长期记忆。
    hooks（连发用）：interrupt() → True 时不再发剩下的条（产出 discarded）；quotes = {id: 消息} 时解析引用格式；
    skip_memory = True 时不提取记忆（先插的那一句）。
    """
    hooks = hooks or {}
    settings = db.get_settings()
    tools = context.TOOL_SCHEMAS if settings.get("enable_tools") == "1" else None
    pipeline = not ooc and not llm.is_echo(settings)
    typing = {"type": "tool", "name": "typing", "label": "正在输入…"}
    outside = (not ooc) and life.enabled() and life.outside_now()      # 她在外面：回复更慢、更短
    delay_scale = typing_scale(settings, ooc, outside)

    current_messages = messages
    for event in prelude:
        yield (event)

    # 聊天里有明确的「写 / 改」命令：先真的去写 / 改文档，再让她回复（回复里自然说一下）
    if prepare is not None:
        yield ({"type": "tool", "name": "write_doc", "label": "想想怎么写……"})
        try:
            current_messages, extra_events = await prepare()
            for ev in extra_events:
                yield (ev)
        except Exception as exc:  # noqa: BLE001
            log.warning("执行写作命令出错：%s: %s", type(exc).__name__, exc)

    final_text = ""
    tools_used: list[str] = []
    parts: list[dict[str, Any]] = []   # 已经落库的条
    calls: list[dict[str, Any]] = []   # 模型附带的结构化动作（读书指令、add_item ……）

    if outside and pipeline:
        await asyncio.sleep(life.outside_wait())                   # 在外面：先等一会儿才看到手机
    if pipeline:
        yield (typing)
        msgs = current_messages
        attempts = 0
        clean = ""
        failed = False
        while True:
            full = ""
            try:
                async for event in llm.stream_turn(
                    settings, msgs, tools=tools,
                    tool_executor=lambda name, args: run_tool(name, args, session_id),
                ):
                    if event["type"] == "final":
                        full = event["text"]
                        tools_used += event.get("tools_used") or []
                    elif event["type"] in ("tool", "tool_result", "notice"):
                        yield (event)
                    # delta / reasoning：整条生成完之前不给用户看
            except Exception as exc:  # noqa: BLE001
                yield ({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
                failed = True
                break
            clean, calls = guards.extract_calls(full)
            clean = guards.strip_exclaim(clean)
            hits = guards.find_banned(clean)
            if hits and attempts < guards.MAX_RETRIES:
                attempts += 1
                log.info("回复命中禁词 %s，重新生成（第 %d 次）", hits, attempts)
                msgs = _retry_messages(current_messages, hits)
                yield (typing)
                continue
            break
        if not failed:
            # 连发：模型可能按 [{"quote": 消息id, "text": ...}, ...] 输出，引用这一轮里的某条；不是这个格式就当普通文本
            items = burst.parse_quoted(clean, hooks.get("quotes")) if hooks.get("quotes") is not None else [(None, clean)]
            split = settings.get("split_replies") != "0"
            bubbles: list[tuple[dict[str, Any] | None, str]] = []
            for q, text_ in items:
                for k, part in enumerate(guards.split_messages(text_) if split else ([text_] if text_ else [])):
                    bubbles.append((q if k == 0 else None, part))
            # 设置里关掉「把回复拆成多条短消息」时，整条一起发（感叹号 / 禁词 / 动作的处理照做）
            stop = hooks.get("interrupt")
            for i, (q, part) in enumerate(bubbles):
                if stop is not None and stop():
                    yield ({"type": "discarded", "texts": [b for _q, b in bubbles[i:]], "sent": len(parts)})
                    calls = []                       # 丢掉的这一半回复：附带的动作也不执行
                    break
                if i > 0:
                    yield (typing)
                    await asyncio.sleep(guards.delay_for(part, delay_scale))
                    if stop is not None and stop():
                        yield ({"type": "discarded", "texts": [b for _q, b in bubbles[i:]], "sent": len(parts)})
                        calls = []
                        break
                saved = db.add_message(session_id, "assistant", part,
                                       quote_id=q["id"] if q else None, quote_text=q["content"] if q else None, quote_role="user" if q else None)
                parts.append(saved)
                yield ({"type": "delta", "text": part})
                yield ({"type": "break", "message": saved})
            added = 0
            for call in calls:                       # 读书指令、收下东西等：后端执行，对用户隐藏
                act = str(call.get("action"))
                try:
                    if act == "add_item":
                        if added >= room.MAX_PER_REPLY:
                            continue
                        item = await room.add_from_chat(call, settings)
                        log.info("执行动作 add_item -> %s", item["name"] if item else "没放")
                        if item:
                            added += 1
                            yield ({"type": "room", "item": item})
                    else:
                        log.info("执行动作 %s -> %s", act, life.apply_action(act))
                except Exception as exc:  # noqa: BLE001
                    log.warning("执行动作 %s 失败：%s", act, exc)
    else:
        try:
            async for event in llm.stream_turn(
                settings, current_messages, tools=tools,
                tool_executor=lambda name, args: run_tool(name, args, session_id),
            ):
                if event["type"] == "final":
                    final_text = event["text"]
                    tools_used = event.get("tools_used") or []
                    continue
                yield (event)
        except Exception as exc:  # noqa: BLE001
            yield ({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        if final_text.strip():
            parts.append(db.add_message(session_id, "assistant", final_text.strip(), meta={"ooc": True} if ooc else None))

    if parts:
        yield ({
            "type": "done",
            "message": parts[-1],
            "messages": parts,
            "stats": db.stats(),
            "tools_used": tools_used,
        })
        if not ooc and not hooks.get("skip_memory"):
            memory.schedule(session_id)          # 后台提取这一轮的记忆（出戏的不记；连发里先插的那一句不算一轮）
        schedule_style_learning()
        if after is not None:                    # 回复发完了：再做「回复之后」的事（比如切到写作状态、开始写）
            try:
                if after():
                    yield ({"type": "life"})           # 前端据此马上刷新状态栏 / 卡片
            except Exception as exc:  # noqa: BLE001
                log.warning("回复之后的动作出错：%s: %s", type(exc).__name__, exc)
    else:
        yield ({"type": "done", "message": None, "messages": [], "stats": db.stats(), "tools_used": tools_used})


def stream_reply(
    session_id: str,
    messages: list[dict[str, Any]],
    meta: dict[str, Any],
    prelude: list[dict[str, Any]],
    prepare: Any = None,
    ooc: bool = False,
    after: Any = None,
) -> StreamingResponse:
    """把一轮回复以 SSE 流回去（/api/chat 和 /api/chat/regenerate 共用）。"""

    async def event_stream() -> AsyncIterator[str]:
        async for event in reply_events(session_id, messages, meta, prelude, prepare=prepare, ooc=ooc, after=after):
            yield sse(event)

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=SSE_HEADERS)


@app.post("/api/chat")
async def chat(payload: ChatIn) -> StreamingResponse:
    text = payload.content.strip()
    if not text:
        raise HTTPException(status_code=400, detail="消息内容不能为空")

    session_id = db.current_session_id()
    usage.start_round()                   # 运行开销：这一轮（含回复后的记忆提取）的调用归在一起
    ooc = guards.is_ooc(text)             # 【OOC】开头：跳出角色，这条不做角色化后处理、不进长期记忆
    msg_meta = dict(payload.quote_meta or {})
    if ooc:
        msg_meta["ooc"] = True
    user_msg = db.add_message(
        session_id,
        "user",
        text,
        quote_id=payload.quote_id,
        quote_text=payload.quote_text,
        quote_role=payload.quote_role,
        meta=msg_meta or None,
    )
    if not ooc:                           # 你在分享心情：她可能被感染（先关键词筛，筛中的才多调一次模型；最多等 3 秒，等不到就照常回复）
        try:
            await asyncio.wait_for(moodsync.process(db.get_settings(), text), timeout=3.0)
        except Exception as exc:  # noqa: BLE001
            log.info("心情同步跳过：%s", type(exc).__name__)
    messages, meta = context.build_context(
        session_id,
        text,
        quote_text=user_msg.get("quote_text"),
        quote_role=user_msg.get("quote_role"),
        quote_meta=payload.quote_meta,
        # 这条刚入库，拼上下文时要从历史里排除，否则模型会收到两遍
        exclude_id=user_msg["id"],
        ooc=ooc,
    )
    prelude = [{"type": "user", "message": user_msg, "meta": meta}]
    intent, hint = writing.detect_command(text) if not ooc else (None, None)
    if intent:
        settings = db.get_settings()

        planned: dict[str, Any] = {}

        async def prepare() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
            res = await writing.handle_command(settings, intent, text, hint)
            if res.get("plan"):
                planned["plan"] = res["plan"]
            msgs, _ = context.build_context(
                session_id, text,
                quote_text=user_msg.get("quote_text"), quote_role=user_msg.get("quote_role"),
                quote_meta=payload.quote_meta, exclude_id=user_msg["id"], extra_note=res["note"],
            )
            return msgs, res["events"]

        after = (lambda: planned.get("plan") and life.begin_writing_now(planned["plan"], text)) if intent == "write" else None
        return stream_reply(session_id, messages, meta, prelude, prepare=prepare, after=after)
    return stream_reply(session_id, messages, meta, prelude, ooc=ooc)


# --------------------------------------------------------------------------
# 连发（见 docs/已完成的计划/连发输入.md、app/burst.py）：发送立刻返回，回复由 /api/stream 推过来
# --------------------------------------------------------------------------
burst.configure(reply_events)


class TypingIn(BaseModel):
    typing: bool = False


@app.post("/api/chat/send")
async def chat_send(payload: ChatIn) -> dict[str, Any]:
    """收下一条消息就返回（任何时候都能发，包括她正在回复的时候）；什么时候回、怎么回由连发队列决定。"""
    text = payload.content.strip()
    if not text:
        raise HTTPException(status_code=400, detail="消息内容不能为空")
    session_id = db.current_session_id()
    msg_meta = dict(payload.quote_meta or {})
    if guards.is_ooc(text):
        msg_meta["ooc"] = True
    user_msg = db.add_message(session_id, "user", text, quote_id=payload.quote_id, quote_text=payload.quote_text,
                              quote_role=payload.quote_role, meta=msg_meta or None)
    burst.receive(user_msg, session_id)
    return {"message": user_msg}


@app.post("/api/typing")
async def chat_typing(payload: TypingIn) -> dict[str, Any]:
    """我正在输入（输入框里有字、或者 1.5 秒内有按键）：前端每 0.7 秒左右报一次，停下来报一声 false。"""
    burst.set_typing(payload.typing)
    return {"ok": True}


@app.get("/api/stream")
async def chat_stream(request: Request) -> StreamingResponse:
    """常开的推送连接：她的「正在输入」、一条条气泡（带引用）、丢掉的、这一轮结束……"""
    q = burst.subscribe()

    async def gen() -> AsyncIterator[str]:
        try:
            yield ": hello\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"                    # 保活
                    continue
                yield sse(ev)
        finally:
            burst.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


@app.get("/api/burst/ratios")
async def burst_ratios() -> dict[str, Any]:
    """设置页用：按当前参数算出来的「先回一句 / 合并回复 / 没注意到直接回」比例（闲 / 一般 / 忙）。"""
    return {"ratios": burst.ratios(), "labels": burst.TIER_LABEL}


@app.get("/api/usage")
async def usage_summary() -> dict[str, Any]:
    """设置页「模型与记忆」：最近几轮对话的调用次数、耗时、token，今天后台的开销。"""
    return usage.summary(int(registry.num("usage_rounds_shown")))


@app.delete("/api/usage")
async def usage_clear() -> dict[str, Any]:
    return {"deleted": usage.clear()}


@app.get("/api/why")
async def why_list(kind: str | None = None, limit: int = Query(default=30, ge=1, le=300)) -> dict[str, Any]:
    """「为什么」：最近的随机决定和原因（kind = state / meal / event / spend）。"""
    return {"items": whylog.recent(kind, limit), "kinds": whylog.KINDS}


@app.get("/api/intents")
async def list_intents(limit: int = Query(default=60, ge=1, le=300)) -> dict[str, Any]:
    """设置页「说过的打算」：当前的和历史（内容、强度、时间范围、结果、原因）。"""
    now = time.time()
    rows = []
    for it in intents.list_all(limit):
        rows.append({
            "id": it["id"], "kind": it["kind"], "kind_label": intents.KINDS.get(it["kind"], it["kind"]), "text": it["text"],
            "strength": it["strength"], "strength_label": intents.STRENGTHS.get(it["strength"], ""),
            "range": f"{intents._hm(it['start_at'], now)} ~ {intents._hm(it['end_at'], now)}",
            "status": it["status"], "status_label": _intent_status(it, now), "decision": it["decision"], "reason": it["reason"],
            "mentioned": bool(it["mentioned"]), "created_at": it["created_at"],
        })
    return {"intents": rows}


def _intent_status(it: dict[str, Any], now: float) -> str:
    if it["status"] == "pending":
        return "生效中" if it["start_at"] <= now else "还没到时间"
    return intents.STATUS_LABEL.get(it["status"], it["status"])


@app.delete("/api/intents")
async def clear_intents() -> dict[str, Any]:
    return {"deleted": intents.clear()}


@app.post("/api/chat/regenerate")
async def regenerate() -> StreamingResponse:
    """重说：删掉最后一条 AI 回复，用同一轮用户消息重新生成一次。"""
    session_id = db.current_session_id()
    rows = db.session_messages(session_id, 500)
    if not rows:
        raise HTTPException(status_code=400, detail="当前会话还没有消息")

    # 末尾可能是连续一串 AI 消息（开了拆分回复之后一条回复会存成好几条），全部删掉
    while rows and rows[-1]["role"] == "assistant":
        db.delete_message(rows[-1]["id"])
        rows = rows[:-1]
    if not rows or rows[-1]["role"] != "user":
        raise HTTPException(status_code=400, detail="上一条不是你的消息，没法重说")

    last_user = rows[-1]
    messages, meta = context.build_context(
        session_id,
        last_user["content"],
        quote_text=last_user.get("quote_text"),
        quote_role=last_user.get("quote_role"),
        quote_meta=_parse_meta(last_user.get("meta")),
        exclude_id=last_user["id"],
        ooc=guards.is_ooc(last_user["content"]),
    )
    return stream_reply(session_id, messages, meta, [{"type": "regenerate", "meta": meta}],
                        ooc=guards.is_ooc(last_user["content"]))


# --------------------------------------------------------------------------
# 设置（人设）
# --------------------------------------------------------------------------
@app.get("/api/settings")
async def get_settings() -> dict[str, Any]:
    return public_settings(db.get_settings())


@app.post("/api/settings")
async def update_settings(payload: SettingsIn) -> dict[str, Any]:
    patch: dict[str, Any] = {}
    if payload.persona_name is not None:
        patch["persona_name"] = payload.persona_name.strip() or "AI"
    if payload.system_prompt is not None:
        patch["system_prompt"] = payload.system_prompt
    if payload.banned_words is not None:
        guards.save_banned(payload.banned_words)          # 禁词表存在 data/banned_words.txt
    if payload.common_words is not None:
        patch["common_words"] = dump_words(payload.common_words)
    if payload.recent_limit is not None:
        patch["recent_limit"] = str(payload.recent_limit)
    if payload.memory_limit is not None:
        patch["memory_limit"] = str(payload.memory_limit)
    if payload.enable_tools is not None:
        patch["enable_tools"] = "1" if payload.enable_tools else "0"
    if payload.split_replies is not None:
        patch["split_replies"] = "1" if payload.split_replies else "0"
    if payload.enable_auto_memory is not None:
        patch["enable_auto_memory"] = "1" if payload.enable_auto_memory else "0"
    if payload.auto_memory_every is not None:
        patch["auto_memory_every"] = str(payload.auto_memory_every)
    if payload.api_base is not None:
        patch["api_base"] = payload.api_base.strip()
    if payload.model is not None:
        patch["model"] = payload.model.strip()
    if payload.temperature is not None:
        patch["temperature"] = str(payload.temperature)
    if payload.max_tokens is not None:
        patch["max_tokens"] = str(payload.max_tokens)
    if payload.context_char_budget is not None:
        patch["context_char_budget"] = str(payload.context_char_budget)
    for key in ("life_read_day_prob", "life_read_cap_min", "reply_delay", "memory_extract_every",
                "writing_p_revisit", "writing_p_new_doc", "writing_p_self_delete",
                "writing_p_insist", "life_read_refuse_prob", "life_interrupt_prob"):
        value = getattr(payload, key)
        if value is not None:
            patch[key] = str(value)
    if payload.enable_style_learning is not None:
        patch["enable_style_learning"] = "1" if payload.enable_style_learning else "0"
    if payload.style_notes is not None:
        patch["style_notes"] = payload.style_notes.strip()
    if payload.clear_api_key:
        patch["api_key"] = ""
    elif payload.api_key is not None and payload.api_key.strip() and payload.api_key.strip() != API_KEY_MASK:
        patch["api_key"] = payload.api_key.strip()

    # 只写 settings 表：人设变更不影响任何聊天记录
    settings = db.save_settings(patch)
    return {"settings": public_settings(settings), "stats": db.stats()}


# --------------------------------------------------------------------------
# 统一设置页（设置注册表，见 registry.py）
# --------------------------------------------------------------------------
class ConfigIn(BaseModel):
    values: dict[str, Any]


class ConfigReset(BaseModel):
    keys: list[str] | None = None
    group: str | None = None


@app.get("/api/config")
async def get_config() -> dict[str, Any]:
    return registry.view()


@app.post("/api/config")
async def set_config(payload: ConfigIn) -> dict[str, Any]:
    saved, errors = registry.apply(payload.values)
    return {"saved": saved, "errors": errors, "settings": public_settings(db.get_settings())}


@app.post("/api/config/reset")
async def reset_config(payload: ConfigReset) -> dict[str, Any]:
    keys = list(payload.keys or [])
    if payload.group:
        keys += [it.key for it in registry.ITEMS if it.group == payload.group]
    done = registry.reset(keys)
    return {"reset": done, "settings": public_settings(db.get_settings())}


# --------------------------------------------------------------------------
# 历史检索 / 记忆
# --------------------------------------------------------------------------
@app.get("/api/search")
async def search(q: str = "", limit: int = Query(default=20, ge=1, le=100)) -> dict[str, Any]:
    rows, total = db.search_archive(q, limit=limit)
    return {"query": q, "total": total, "results": rows}


@app.get("/api/memories")
async def get_memories(limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
    return {"memories": db.list_memories(limit)}


@app.post("/api/memories")
async def create_memory(payload: MemoryIn) -> dict[str, Any]:
    saved = db.add_memory(payload.content, kind=payload.kind, category=payload.category,
                          session_id=db.current_session_id(), source="manual")
    if saved is None:
        raise HTTPException(status_code=409, detail="这条记忆已经存在")
    return {"memory": saved}


@app.delete("/api/memories/{memory_id}")
async def remove_memory(memory_id: int) -> dict[str, Any]:
    if not db.delete_memory(memory_id):
        raise HTTPException(status_code=404, detail="记忆不存在")
    return {"ok": True}


@app.post("/api/memories/summarize")
async def trigger_summarize() -> dict[str, Any]:
    try:
        created = await summarize_memories()
    except llm.LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"created": created, "memories": db.list_memories(100)}


@app.delete("/api/memories")
async def clear_all_memories() -> dict[str, Any]:
    removed = db.clear_memories()
    return {"removed": removed, "stats": db.stats()}


# --------------------------------------------------------------------------
# 删除聊天记录
#
# 所有删除都是「窗口 + 归档」一次删干净，避免出现
# 网页上看不见了、本地库里还留着（或者反过来）的情况。
# --------------------------------------------------------------------------
@app.patch("/api/messages/{message_id}")
async def edit_message(message_id: int, payload: MessageEdit) -> dict[str, Any]:
    """改一条消息（主要是改 AI 的回复）。窗口和历史归档同步改；AI 回复的改动会当作风格范例。"""
    row = db.edit_message(message_id, payload.content)
    if not row:
        raise HTTPException(status_code=404, detail="这条消息不存在，或内容是空的")
    return {"message": row, "edits_count": db.count_edits()}


@app.post("/api/style/learn")
async def learn_style_now() -> dict[str, Any]:
    settings = db.get_settings()
    if llm.is_echo(settings):
        raise HTTPException(status_code=400, detail="离线演示模式下无法学习，请先配置 API Key。")
    try:
        notes = await style.learn_style(settings)
    except llm.LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"style_notes": notes, "edits_count": db.count_edits()}


@app.delete("/api/messages/{message_id}")
async def delete_message(message_id: int) -> dict[str, Any]:
    result = db.delete_message(message_id)
    if not (result["window"] or result["archive"]):
        raise HTTPException(status_code=404, detail="这条消息不存在")
    return {"deleted": result, "stats": db.stats()}


@app.delete("/api/archive/{archive_id}")
async def delete_archive_entry(archive_id: int) -> dict[str, Any]:
    result = db.delete_archive_entry(archive_id)
    if not (result["window"] or result["archive"]):
        raise HTTPException(status_code=404, detail="这条记录不存在")
    return {"deleted": result, "stats": db.stats()}


@app.post("/api/history/clear")
async def clear_history(payload: HistoryClearIn) -> dict[str, Any]:
    if payload.scope == "all":
        deleted = db.clear_all_history(with_memories=payload.with_memories)
        db.save_settings({"auto_memory_marker": "0"})
    else:
        deleted = db.clear_conversation(db.current_session_id())
        if payload.with_memories:
            deleted["memories"] = db.clear_memories()
    return {"deleted": deleted, "stats": db.stats()}


# --------------------------------------------------------------------------
# 关闭服务（网页左下角那个按钮调它）
# --------------------------------------------------------------------------
@app.post("/api/shutdown")
async def shutdown(request: Request) -> dict[str, Any]:
    server = getattr(request.app.state, "uvicorn_server", None)

    async def stopper() -> None:
        # 先让这个 HTTP 响应发出去，再关
        await asyncio.sleep(0.5)
        try:
            from .config import DATA_DIR

            state_file = DATA_DIR / "server.json"
            if state_file.exists():
                state_file.unlink()
        except OSError:
            pass
        if server is not None:
            server.should_exit = True
        else:
            os._exit(0)  # 开发模式（--reload）下拿不到 server，直接退出进程

    asyncio.create_task(stopper())
    return {"ok": True}


# --------------------------------------------------------------------------
# 日历：任务 + 每天的心情
# --------------------------------------------------------------------------
class TaskIn(BaseModel):
    title: str = Field(min_length=1)
    day: str | None = None
    remind_after_days: int = Field(default=3, ge=0, le=365)
    repeat_every: int = Field(default=0, ge=0, le=365)      # 每几天重复；0 = 不重复
    priority: str = "minor"                                 # main 主要 | minor 次要
    auto_adjust: bool = False                               # 重复任务：拖延后完成，下次从完成那天重新起算


class TaskPatch(BaseModel):
    auto_adjust: bool | None = None
    title: str | None = None
    status: str | None = None
    stamp_label: str | None = None
    stamp_tone: str | None = None
    remind_after_days: int | None = Field(default=None, ge=0, le=365)
    repeat_every: int | None = Field(default=None, ge=0, le=365)
    day: str | None = None
    priority: str | None = None


class MoodsIn(BaseModel):
    days: list[str]


def day_label(offset: int, day: str) -> str:
    if offset == 0:
        return "今天"
    if offset == -1:
        return "昨天"
    if offset == 1:
        return "明天"
    if offset == 2:
        return "后天"
    if offset == -2:
        return "前天"
    d = date.fromisoformat(day)
    return f"{d.month} 月 {d.day} 日"


# 一次显示 4 天：昨天、今天、明天、后天
CAL_BEFORE, CAL_AFTER = 1, 2


def calendar_payload(offset: int) -> dict[str, Any]:
    db.topup_all_series()   # 重复任务的未来实例随时补齐
    today = date.today()
    today_iso = today.isoformat()
    center = today + timedelta(days=max(-365, min(365, offset)))
    days = [(center + timedelta(days=i)).isoformat() for i in range(-CAL_BEFORE, CAL_AFTER + 1)]
    tasks = db.tasks_between(days[0], days[-1])

    by_day: dict[str, list[dict[str, Any]]] = {d: [] for d in days}
    for t in tasks:
        by_day.setdefault(t["day"], []).append(t)

    active = db.active_series()
    series_every = {r["id"]: r["repeat_every"] for r in active}
    series_auto = {r["id"]: bool(r.get("auto_adjust")) for r in active}

    def decorate(t: dict[str, Any], rest: bool) -> None:
        t["repeat_every"] = series_every.get(t.get("series_id"), 0)
        t["auto_adjust"] = series_auto.get(t.get("series_id"), False)
        main = t.get("priority") == "main"
        # 拖延天数：主要任务每天都算；次要任务只数工作日
        t["pending_days"] = pending_for(t.get("priority"), t["day"], today)
        t["optional"] = rest and not main                       # 休息日：次要任务可选，主要任务照旧

    first_day = db.first_message_day() or today_iso
    today_in_window = today_iso in days

    # ── 顺延规则 ──────────────────────────────────────────────
    # 之前拖着没干的任务，今天会出现在今天这一格（带红色拖延天数）。
    # 同名的最多同时出现一个：只留最早的那条；它还没做时，今天自己的同名任务先不显示。
    # 今天刚盖章的拖延任务今天还留在今天这一格，隔天就不再往后顺延，回到原来的日期。
    overdue = db.overdue_open(today_iso)
    carried_open = db.collapse_series(overdue)
    blocked_titles = {t["title"] for t in carried_open}
    stamped_today = db.overdue_stamped_today(today_iso)
    shown_stamped: dict[str, dict[str, Any]] = {}
    for t in stamped_today:                      # 已按时间新→旧排好，同名只留最新的
        shown_stamped.setdefault(t["title"], t)
    carried_ids = {t["id"] for t in overdue} | {t["id"] for t in stamped_today}

    out_days = []
    missing = []
    for d in days:
        dd = date.fromisoformat(d)
        diff = (dd - today).days
        rest = is_weekend(dd)
        tasks_here = by_day.get(d, [])
        if today_in_window and diff < 0:
            tasks_here = [t for t in tasks_here if t["id"] not in carried_ids]   # 它们现在住在今天那格
        if d == today_iso:
            # 拖着的同名任务已经住在今天这一格了（没做的 / 今天刚盖章的）：今天自己那条并进去，只显示一条
            merged_titles = blocked_titles | set(shown_stamped)
            tasks_here = [t for t in tasks_here if t["title"] not in merged_titles]
        for t in tasks_here:
            decorate(t, rest)

        # 心情：情绪词马上随机定好；那句话让模型补（见 /api/calendar/moods）
        mood = None
        mood_view = None
        if diff <= 0 and d >= first_day:
            mood = planner.ensure_mood_word(d)
            if d == today_iso and mood.get("kind") != "summary" and life.enabled():
                # 今天：显示她「此刻」的心情（被今天发生的事影响着），晚上九点总结之后才变成总结
                word, why = life.live_mood()
                mood_view = {"mood": word, "line": why, "recap": "", "summary": False, "live": True}
            elif d != today_iso and not mood["line"] and mood.get("kind") != "summary":
                missing.append(d)
        else:
            mood = db.get_mood(d)
        if mood_view is None and mood:
            mood_view = {"mood": festivals.floor_mood(mood["mood"], d), "line": mood["line"], "recap": mood.get("recap") or "",
                         "summary": mood.get("kind") == "summary", "live": False}
        out_days.append({
            "day": d,
            "weekday": context.WEEKDAYS[dd.weekday()],
            "label": day_label(diff, d),
            "is_today": d == today_iso,
            "is_past": diff < 0,
            "is_future": diff > 0,
            "is_rest": rest,
            "festivals": [{"id": f["id"], "name": f["name"], "note": f.get("note") or ""} for f in festivals.on_date(dd)],
            "tasks": tasks_here,
            "mood": mood_view,
        })

    today_cell = next((d for d in out_days if d["is_today"]), None)
    if today_cell is not None:
        carried = []
        for t in carried_open + [t for t in shown_stamped.values() if t["title"] not in blocked_titles]:
            item = dict(t)
            decorate(item, today_cell["is_rest"])
            item["carried"] = True
            carried.append(item)
        today_cell["tasks"] = carried + today_cell["tasks"]

    return {
        "today": today_iso,
        "center": center.isoformat(),
        "offset": offset,
        "days": out_days,
        "mood_missing": missing,
    }


@app.get("/api/calendar/month")
async def calendar_month(ym: str = "") -> dict[str, Any]:
    """月历：某个月每一天的概况（是不是休息日、任务有几个、几个没做）。ym = YYYY-MM，默认本月。"""
    today = date.today()
    try:
        y, m = (int(x) for x in (ym or today.strftime("%Y-%m")).split("-")[:2])
        first = date(y, m, 1)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="ym 应为 YYYY-MM") from exc
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    last = nxt - timedelta(days=1)
    db.topup_all_series()
    info = db.month_overview(first.isoformat(), last.isoformat())
    days = []
    for i in range((last - first).days + 1):
        d = first + timedelta(days=i)
        iso = d.isoformat()
        days.append({
            "day": iso,
            "n": d.day,
            "weekday": d.weekday(),
            "is_rest": is_weekend(d),
            "is_today": d == today,
            "offset": (d - today).days,
            "festivals": [f["name"] for f in festivals.on_date(d)],
            **info.get(iso, {"total": 0, "open": 0, "done": 0, "skipped": 0}),
        })
    return {"ym": first.strftime("%Y-%m"), "year": y, "month": m, "lead": first.weekday(), "days": days,
            "today": today.isoformat()}


@app.get("/api/calendar")
async def calendar(offset: int = Query(default=0, ge=-365, le=365)) -> dict[str, Any]:
    return calendar_payload(offset)


@app.post("/api/calendar/moods")
async def calendar_moods(payload: MoodsIn) -> dict[str, Any]:
    """补生成缺失的心情（由前端在日历渲染完之后单独来调，不拖慢打开速度）。"""
    settings = db.get_settings()
    if llm.is_echo(settings):
        return {"moods": {}}
    days = [d for d in payload.days if d][:7]
    try:
        moods = await planner.make_moods(settings, days)
    except llm.LLMError as exc:
        return {"moods": {}, "error": str(exc)}
    return {"moods": {d: {"mood": m["mood"], "line": m["line"]} for d, m in moods.items()}}


@app.post("/api/calendar/remind")
async def calendar_remind() -> dict[str, Any]:
    """启动时检查：拖太久的任务，让 Phal 主动发一条。"""
    settings = db.get_settings()
    today = date.today()
    rest_day = is_weekend(today)
    due = []
    for t in db.collapse_series(db.open_tasks_upto(today.isoformat())):
        if rest_day and t.get("priority") != "main":
            continue    # 休息日：次要任务是可选的，不催；主要任务照常
        try:
            pending = pending_for(t.get("priority"), t["day"], today)
        except ValueError:
            continue
        if pending >= int(t["remind_after_days"] or 3) and t.get("reminded_on") != today.isoformat():
            due.append(t)
    if not due or llm.is_echo(settings):
        return {"messages": [], "due": len(due), "rest_day": rest_day}
    try:
        bubbles = await planner.remind_overdue(settings, due, today.isoformat())
    except Exception as exc:  # noqa: BLE001
        return {"messages": [], "due": len(due), "error": str(exc)}
    session_id = db.current_session_id()
    saved = [db.add_message(session_id, "assistant", b) for b in bubbles if b.strip()]
    db.mark_reminded([t["id"] for t in due], today.isoformat())
    return {"messages": saved, "due": len(due)}


async def react_to_calendar(action: str, task: dict[str, Any]) -> list[dict[str, Any]]:
    """日历上的改动（加任务、盖章）不再引起 Phal 主动开口追问：这是你自己的事。"""
    return []


@app.post("/api/tasks")
async def create_task(payload: TaskIn) -> dict[str, Any]:
    day = (payload.day or date.today().isoformat()).strip()
    if payload.repeat_every > 0:
        task = db.create_series(payload.title, day, payload.repeat_every, payload.remind_after_days,
                                priority=payload.priority, auto_adjust=payload.auto_adjust)
        what = f"新加了一个每 {payload.repeat_every} 天重复的任务「{task['title']}」"
    else:
        task = db.add_task(payload.title, day, remind_after_days=payload.remind_after_days, source="manual",
                           priority=payload.priority)
        what = f"新加了一个任务「{task['title']}」"
    messages = await react_to_calendar(what, task)
    return {"task": task, "messages": messages}


def apply_repeat(task: dict[str, Any], repeat: int, auto_adjust: bool = False) -> dict[str, Any]:
    """改一条任务的重复设置：先清掉它之后还没做的旧实例，再按新的间隔重新生成。"""
    if task.get("series_id"):
        # 旧系列停掉，当前这条之后（不含当前）没做的实例删掉
        next_day = (date.fromisoformat(task["day"]) + timedelta(days=1)).isoformat()
        db.stop_series(task["series_id"], from_day=next_day)
        if repeat <= 0:
            return db.update_task_series(task["id"], None) or task
    if repeat <= 0:
        return task
    every = repeat
    sid = db.start_series_from(task, every, auto_adjust=auto_adjust)
    return db.update_task_series(task["id"], sid) or task


@app.patch("/api/tasks/{task_id}")
async def patch_task(task_id: int, payload: TaskPatch) -> dict[str, Any]:
    before = db.get_task(task_id)
    if not before:
        raise HTTPException(status_code=404, detail="任务不存在")
    fields = payload.model_dump(exclude_none=True)
    repeat = fields.pop("repeat_every", None)
    auto_adjust = fields.pop("auto_adjust", None)

    if "title" in fields:
        fields["title"] = fields["title"].strip()
        if not fields["title"]:
            raise HTTPException(status_code=400, detail="任务内容不能为空")
    if "priority" in fields and fields["priority"] not in ("main", "minor"):
        raise HTTPException(status_code=400, detail="priority 只能是 main / minor")
    if "day" in fields:
        try:
            date.fromisoformat(fields["day"])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="日期格式应为 YYYY-MM-DD") from exc

    if "status" in fields:
        status = fields["status"]
        if status not in db.TASK_STATUS:
            raise HTTPException(status_code=400, detail="status 只能是 open / done / skipped")
        if fields.get("stamp_label") is None and status != before["status"]:
            fields["stamp_label"] = {"done": "完成", "skipped": "不干", "open": None}.get(status)
            fields["stamp_tone"] = {"done": "green", "skipped": "red", "open": None}.get(status)

    task = db.update_task(task_id, **fields)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    # 重复任务改了名字 / 提醒天数：系列和这一天起还没做的实例一起改
    if task.get("series_id") and ("title" in fields or "remind_after_days" in fields or "priority" in fields):
        db.update_series(
            task["series_id"], task["day"],
            title=fields.get("title"), remind=fields.get("remind_after_days"),
            priority=fields.get("priority"),
        )

    # 改重复间隔（和现在不一样才动）
    if repeat is not None:
        current = 0
        if task.get("series_id"):
            sr = db.get_series(task["series_id"])
            current = int(sr["repeat_every"]) if sr and sr["active"] else 0
        if repeat != current:
            if auto_adjust is None:       # 改了间隔但没提「自动调整」：沿用原来的设置
                old = db.get_series(before["series_id"]) if before.get("series_id") else None
                auto_adjust = bool(old and old.get("auto_adjust"))
            task = apply_repeat(task, repeat, bool(auto_adjust))
            auto_adjust = None            # 新系列已经带着设置建好了
    if auto_adjust is not None and task.get("series_id"):
        db.set_series_auto_adjust(task["series_id"], bool(auto_adjust))

    # 标完成 / 不干：之前漏掉的同名任务一起标掉，隔天不会再冒出来
    if fields.get("status") in ("done", "skipped") and fields["status"] != before["status"]:
        today_iso = date.today().isoformat()
        db.close_open_siblings(task, fields["status"], today_iso)
        # 自动调整：重复任务拖延了才完成，下一次从今天（完成那天）重新起算
        if fields["status"] == "done" and task.get("series_id") and before["day"] < today_iso:
            sr = db.get_series(task["series_id"])
            if sr and sr["active"] and sr.get("auto_adjust"):
                db.rebase_series(task["series_id"], today_iso)

    return {"task": db.get_task(task_id), "messages": []}


@app.post("/api/tasks/{task_id}/cancel-delay")
async def cancel_task_delay(task_id: int) -> dict[str, Any]:
    """取消拖延：任务挪到今天、拖延天数清零。不会取消重复任务本身，也不影响它之后的安排。"""
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    today_iso = date.today().isoformat()
    if task["status"] != "open" or task["day"] >= today_iso:
        raise HTTPException(status_code=400, detail="这条任务没有在拖延")
    return {"task": db.reset_delay(task, today_iso), "messages": []}


@app.delete("/api/tasks/{task_id}")
async def remove_task(task_id: int, series: bool = False) -> dict[str, Any]:
    """series=true：连这条之后所有重复的未做实例一起删，并停止这个重复。"""
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    removed = 0
    if series and task.get("series_id"):
        removed = db.stop_series(task["series_id"], from_day=task["day"])
    else:
        db.delete_task(task_id)
        removed = 1
    return {"ok": True, "removed": removed}


# --------------------------------------------------------------------------
# 会话
# --------------------------------------------------------------------------
@app.post("/api/session/new")
async def new_session() -> dict[str, Any]:
    db.clear_current_session()
    return {"session_id": db.current_session_id(), "stats": db.stats()}


@app.get("/api/sessions")
async def sessions() -> dict[str, Any]:
    return {"sessions": db.list_sessions(), "current": db.current_session_id()}


app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR), check_dir=False), name="assets")       # 素材目录（目录不存在时只是 404，房间里的角色不显示）
