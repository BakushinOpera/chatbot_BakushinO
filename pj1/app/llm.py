"""OpenAI 兼容的模型客户端：SSE 流式输出 + 工具调用循环。

设计要点
--------
* 只用 httpx 直连 /chat/completions，任何 OpenAI 兼容端点都能接
  （DeepSeek、Moonshot、通义、智谱、Ollama、LM Studio、One-API…）。
* 工具调用（search_history / save_memory）在流式模式下用 delta 累积实现：
  先流式输出正文，模型如果要求调用工具，就把工具结果塞回对话再来一轮。
* 没配 API Key 时自动进入 echo 演示模式：把真实拼装出来的上下文原样打印出来，
  方便先验证界面和上下文拼装是否正确。
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncIterator, Awaitable, Callable

import httpx

from . import registry, usage
from . import character as C

ECHO_MODELS = {"echo", "mock", "demo", "offline"}

TOOL_LABELS = {
    "search_history": "正在翻找过去的聊天记录",
    "save_memory": "正在写下一条长期记忆",
    "add_task": "正在往日历里记一笔",
    "read_doc": "正在翻文档",
}


class LLMError(RuntimeError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------
# 共用的 HTTP 客户端：复用连接（省掉每轮对话重新握手 TCP/TLS 的时间）
# --------------------------------------------------------------------------
_client: httpx.AsyncClient | None = None
_client_loop: asyncio.AbstractEventLoop | None = None


def get_client() -> httpx.AsyncClient:
    """按事件循环缓存一个 AsyncClient；换了事件循环（测试 / 重载）就重建。"""
    global _client, _client_loop
    loop = asyncio.get_running_loop()
    if _client is None or _client.is_closed or _client_loop is not loop:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(180.0, connect=20.0),
            limits=httpx.Limits(max_keepalive_connections=4, keepalive_expiry=60.0),
        )
        _client_loop = loop
    return _client


async def close_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------
def is_echo(settings: dict[str, Any]) -> bool:
    model = str(settings.get("model") or "").strip().lower()
    key = str(settings.get("api_key") or "").strip()
    return model in ECHO_MODELS or not key


def endpoint(api_base: str) -> str:
    base = (api_base or "").strip().rstrip("/")
    if not base:
        base = "https://api.deepseek.com/v1"
    if base.endswith("/chat/completions"):
        return base
    # 只给了域名（没有路径）时补上 /v1
    tail = base.split("://", 1)[-1]
    if "/" not in tail:
        return base + "/v1/chat/completions"
    return base + "/chat/completions"


def _friendly_http_error(status: int, body: str) -> str:
    snippet = body.strip().replace("\n", " ")[:400]
    if status in (401, 403):
        return f"接口鉴权失败（HTTP {status}）：请到「设置」里检查 API Key。原始返回：{snippet}"
    if status == 404:
        return f"接口地址不存在（HTTP 404）：请检查 API Base，应形如 https://api.deepseek.com/v1。原始返回：{snippet}"
    if status == 429:
        return f"触发限流或余额不足（HTTP 429）：{snippet}"
    return f"模型接口返回 HTTP {status}：{snippet}"


def _payload(settings: dict[str, Any], messages: list[dict[str, Any]], tools: list[dict] | None, stream: bool) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": settings.get("model") or "deepseek-chat",
        "messages": [_localized(m) for m in messages],     # 名字、对用户的称呼、世界专有词按角色换（只换系统写的部分）
        "stream": stream,
    }
    try:
        body["temperature"] = float(settings.get("temperature") or 0.9)
    except (TypeError, ValueError):
        body["temperature"] = 0.9
    try:
        max_tokens = int(settings.get("max_tokens") or 0)
    except (TypeError, ValueError):
        max_tokens = 0
    if max_tokens > 0:
        body["max_tokens"] = max_tokens
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    if stream and registry.flag("usage_stream_tokens"):
        body["stream_options"] = {"include_usage": True}     # 最后一块带上用量（运行开销统计用）
    return body


USER_TURN_MARK = "【以上是系统注入的信息"


def _localized(m: dict[str, Any]) -> dict[str, Any]:
    """系统消息整条按角色换（character.localize）；聊天消息不动（你说的「她」就是「她」），只换最后一条里系统注入的那段。"""
    c = m.get("content")
    if not isinstance(c, str):
        return m
    if m.get("role") == "system":
        return {**m, "content": C.localize(c)}
    i = c.find(USER_TURN_MARK)
    return {**m, "content": C.localize(c[:i]) + c[i:]} if i > 0 else m


def _headers(settings: dict[str, Any]) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {str(settings.get('api_key') or '').strip()}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }


# --------------------------------------------------------------------------
# 流式对话（带工具调用循环）
# --------------------------------------------------------------------------
async def stream_turn(
    settings: dict[str, Any],
    messages: list[dict[str, Any]],
    tools: list[dict] | None = None,
    tool_executor: Callable[[str, dict], Awaitable[str]] | None = None,
    max_rounds: int = 3,
) -> AsyncIterator[dict[str, Any]]:
    """产出事件流：delta / reasoning / tool / tool_result / notice / final / error。"""
    if is_echo(settings):
        async for event in _echo_turn(messages):
            yield event
        return

    convo = list(messages)
    allowed_tools = tools if (tools and tool_executor) else None
    tools_disabled = False
    full_text: list[str] = []
    tools_used: list[str] = []

    for round_index in range(max(1, max_rounds)):
        last_round = round_index == max_rounds - 1
        try:
            stream = _stream_once(
                settings, convo,
                tools=None if (tools_disabled or last_round) else allowed_tools,
            )
            text_parts: list[str] = []
            reasoning_parts: list[str] = []
            tool_calls: dict[int, dict[str, Any]] = {}
            async for event in stream:
                if event["type"] == "delta":
                    text_parts.append(event["text"])
                    full_text.append(event["text"])
                    yield event
                elif event["type"] == "reasoning":
                    reasoning_parts.append(event["text"])
                    yield event
                elif event["type"] == "tool_delta":
                    slot = tool_calls.setdefault(
                        event["index"], {"id": "", "name": "", "arguments": ""}
                    )
                    if event.get("id"):
                        slot["id"] = event["id"]
                    if event.get("name"):
                        slot["name"] = event["name"]
                    slot["arguments"] += event.get("arguments") or ""
        except LLMError as exc:
            message = str(exc)
            # 有些端点 / 模型不支持 tools 参数，自动降级重试一次
            if (
                allowed_tools
                and not tools_disabled
                and not full_text
                and exc.status in (400, 422)
                and any(k in message.lower() for k in ("tool", "function"))
            ):
                tools_disabled = True
                yield {"type": "notice", "text": "该模型不支持工具调用，已自动关闭历史检索功能。"}
                continue
            yield {"type": "error", "message": message}
            return
        except Exception as exc:  # noqa: BLE001 —— 网络异常等
            yield {"type": "error", "message": f"请求模型失败：{type(exc).__name__}: {exc}"}
            return

        pending = [tc for tc in tool_calls.values() if tc.get("name")]
        if not pending:
            break

        # 把 assistant 的 tool_calls 追加回去，再执行工具
        assistant_turn: dict[str, Any] = {
            "role": "assistant",
            "content": "".join(text_parts) or None,
            "tool_calls": [
                {
                    "id": tc["id"] or f"call_{i}",
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": tc["arguments"] or "{}"},
                }
                for i, tc in enumerate(pending)
            ],
        }
        # 思考模式的模型（DeepSeek 等）要求：带工具调用的那一轮，思考内容要原样回传，
        # 否则下一轮会报 400。只有模型真的吐了思考内容才带这个字段，别的服务不受影响。
        if reasoning_parts:
            assistant_turn["reasoning_content"] = "".join(reasoning_parts)
        convo.append(assistant_turn)
        for i, tc in enumerate(pending):
            name = tc["name"]
            try:
                args = json.loads(tc["arguments"] or "{}")
                if not isinstance(args, dict):
                    args = {}
            except json.JSONDecodeError:
                args = {}
            tools_used.append(name)
            yield {"type": "tool", "name": name, "args": args, "label": TOOL_LABELS.get(name, f"正在调用 {name}")}
            try:
                result = await tool_executor(name, args)  # type: ignore[misc]
            except Exception as exc:  # noqa: BLE001
                result = f"工具执行失败：{exc}"
            yield {"type": "tool_result", "name": name, "preview": result[:200]}
            convo.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"] or f"call_{i}",
                    "content": result,
                }
            )
        if last_round:
            break

    yield {"type": "final", "text": "".join(full_text), "tools_used": tools_used}


async def _stream_once(
    settings: dict[str, Any],
    messages: list[dict[str, Any]],
    tools: list[dict] | None,
) -> AsyncIterator[dict[str, Any]]:
    url = endpoint(str(settings.get("api_base") or ""))
    body = _payload(settings, messages, tools, stream=True)
    client = get_client()
    purpose, t0, used, ok = usage.purpose_of_caller(), time.monotonic(), None, False
    try:
        async for ev in _stream_lines(client, url, body, settings):
            if ev["type"] == "usage":
                used = ev["usage"]
                continue
            yield ev
        ok = True
    finally:
        usage.record(purpose, (time.monotonic() - t0) * 1000, used, ok)


async def _stream_lines(client: httpx.AsyncClient, url: str, body: dict[str, Any], settings: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    async with client.stream("POST", url, json=body, headers=_headers(settings)) as resp:
        if resp.status_code >= 400:
            raw = (await resp.aread()).decode("utf-8", "replace")
            raise LLMError(_friendly_http_error(resp.status_code, raw), status=resp.status_code)
        async for line in resp.aiter_lines():
            if not line or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            if isinstance(chunk, dict) and chunk.get("error"):
                err = chunk["error"]
                raise LLMError(f"模型返回错误：{err.get('message') if isinstance(err, dict) else err}")
            if isinstance(chunk.get("usage"), dict):
                yield {"type": "usage", "usage": chunk["usage"]}
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            reasoning = delta.get("reasoning_content") or delta.get("reasoning")
            if reasoning:
                yield {"type": "reasoning", "text": reasoning}
            content = delta.get("content")
            if content:
                yield {"type": "delta", "text": content}
            for call in delta.get("tool_calls") or []:
                fn = call.get("function") or {}
                yield {
                    "type": "tool_delta",
                    "index": call.get("index", 0),
                    "id": call.get("id"),
                    "name": fn.get("name"),
                    "arguments": fn.get("arguments"),
                }


# --------------------------------------------------------------------------
# 非流式调用（整理长期记忆用）
# --------------------------------------------------------------------------
async def complete(settings: dict[str, Any], messages: list[dict[str, Any]], temperature: float = 0.3) -> str:
    url = endpoint(str(settings.get("api_base") or ""))
    local = dict(settings)
    local["temperature"] = temperature
    body = _payload(local, messages, None, stream=False)
    purpose, t0 = usage.purpose_of_caller(), time.monotonic()
    try:
        resp = await get_client().post(url, json=body, headers=_headers(settings))
    except Exception:
        usage.record(purpose, (time.monotonic() - t0) * 1000, None, False)
        raise
    if resp.status_code >= 400:
        usage.record(purpose, (time.monotonic() - t0) * 1000, None, False)
        raise LLMError(_friendly_http_error(resp.status_code, resp.text), status=resp.status_code)
    data = resp.json()
    usage.record(purpose, (time.monotonic() - t0) * 1000, data.get("usage") if isinstance(data, dict) else None)
    choices = data.get("choices") or []
    if not choices:
        raise LLMError("模型没有返回任何内容")
    return (choices[0].get("message") or {}).get("content") or ""


# --------------------------------------------------------------------------
# echo 演示模式
# --------------------------------------------------------------------------
async def _echo_turn(messages: list[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
    from .context import USER_TURN_MARK

    system_blocks = [m["content"] for m in messages if m["role"] == "system"]
    turns = [dict(m) for m in messages if m["role"] in ("user", "assistant")]
    # 最后一条用户消息开头带着每轮注入的易变信息（时间 / 任务 / 归档），拆出来单独展示
    injected = ""
    if turns and turns[-1]["role"] == "user" and USER_TURN_MARK in turns[-1]["content"]:
        injected, _, real = turns[-1]["content"].partition(USER_TURN_MARK)
        turns[-1]["content"] = real.lstrip("\n")
    section2 = "\n\n".join(system_blocks[1:] + ([injected.strip()] if injected.strip() else []))
    lines = [
        "⚠️ 当前是【离线演示模式】：还没有配置 API Key，模型没有被真正调用。",
        "下面这些是后端刚刚真实拼装出来的上下文，配好 Key 之后，它们就会按这个顺序发给模型。",
        "",
        "──────── 1. 人设 system prompt ────────",
        system_blocks[0] if system_blocks else "（空）",
        "",
        "──────── 2. 注入的运行时变量 + 记忆摘要 ────────",
        section2 or "（空）",
        "",
        f"──────── 3. 短期上下文（{len(turns) - 1} 条历史 + 1 条新消息）────────",
    ]
    for m in turns:
        who = "你" if m["role"] == "user" else "AI"
        body = m["content"]
        if len(body) > 400:
            body = body[:400] + "…"
        lines.append(f"〔{who}〕{body}")
    lines += [
        "",
        "──────── 说明 ────────",
        "打开右上角 ⚙ 设置，填入 API Base / API Key / 模型名即可切换到真实对话。",
    ]
    text = "\n".join(lines)
    for i in range(0, len(text), 16):
        yield {"type": "delta", "text": text[i : i + 16]}
        await asyncio.sleep(0.012)
    yield {"type": "final", "text": text, "tools_used": []}
