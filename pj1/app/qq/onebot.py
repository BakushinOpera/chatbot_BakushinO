"""OneBot v11 正向 WebSocket 客户端：连本机 NapCat。

* 我们是 WebSocket 的客户端（NapCat 开「WebSocket 服务器」，默认端口 3001）；token 同时放在 ``Authorization: Bearer`` 头和 ``?access_token=`` 里；
* 断线 / 连不上：指数退避自动重连（设置里的最小 / 最大间隔）；改了连接设置、开关，立刻按新的重连；
* 事件不在读循环里处理（处理里可能还要调动作等回包，会把读循环堵死），每个事件单独起任务；
* 动作用 echo 配对回包（``call``）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Awaitable, Callable
from urllib.parse import quote

import websockets

log = logging.getLogger(__name__)

STATE_LABEL = {
    "stopped": "未启动", "disabled": "没有启用", "connecting": "正在连接…", "connected": "已连接",
    "error": "没连上（等一会儿自动重试）",
}


class OneBotError(RuntimeError):
    def __init__(self, message: str, retcode: int | None = None) -> None:
        super().__init__(message)
        self.retcode = retcode


def build_uri(cfg: dict[str, Any]) -> str:
    uri = f"ws://{cfg['host']}:{int(cfg['port'])}/"
    if cfg.get("token"):
        uri += "?access_token=" + quote(str(cfg["token"]), safe="")
    return uri


def backoff(attempt: int, lo: float, hi: float) -> float:
    """第 attempt 次失败后等多久：lo, 2lo, 4lo … 不超过 hi。"""
    return float(min(hi, lo * (2 ** max(0, attempt))))


def parse_event(ev: dict[str, Any]) -> dict[str, Any] | None:
    """OneBot 的消息事件 → 规整后的一条；不是消息（心跳、生命周期、通知……）返回 None。"""
    if ev.get("post_type") != "message":
        return None
    mt = ev.get("message_type")
    if mt not in ("private", "group"):
        return None
    sender = ev.get("sender") or {}
    return {
        "kind": mt,
        "user_id": str(ev.get("user_id") or sender.get("user_id") or ""),
        "group_id": str(ev.get("group_id") or "") if mt == "group" else "",
        "nickname": str(sender.get("nickname") or ""),
        "card": str(sender.get("card") or ""),
        "role": str(sender.get("role") or ""),
        "message": ev.get("message") if ev.get("message") is not None else ev.get("raw_message", ""),
        "msg_id": str(ev.get("message_id")) if ev.get("message_id") is not None else None,
        "ts": float(ev.get("time") or time.time()),
        "self_id": str(ev.get("self_id") or ""),
    }


class OneBotClient:
    def __init__(self, get_cfg: Callable[[], dict[str, Any]], on_event: Callable[[dict[str, Any]], Awaitable[None]],
                 on_connected: Callable[[], Awaitable[None]] | None = None) -> None:
        self.get_cfg = get_cfg
        self.on_event = on_event
        self.on_connected = on_connected
        self._ws: Any = None
        self._pending: dict[str, asyncio.Future] = {}
        self._tasks: set[asyncio.Task] = set()
        self._run_task: asyncio.Task | None = None
        self._stopping = False
        self._gen = 0                      # 设置改动的次数：等待中发现它变了就提前醒来
        self.status: dict[str, Any] = {"state": "stopped", "error": "", "retry_in": 0, "self_id": "", "nickname": "", "connected_at": 0.0,
                                       "last_event_at": 0.0, "attempts": 0, "events": 0}

    # ───────────────────────── 状态 ─────────────────────────
    def _set(self, state: str, **kw: Any) -> None:
        self.status.update(state=state, **kw)

    def view(self) -> dict[str, Any]:
        s = dict(self.status)
        s["label"] = STATE_LABEL.get(s["state"], s["state"])
        return s

    @property
    def connected(self) -> bool:
        return self._ws is not None and self.status["state"] == "connected"

    # ───────────────────────── 生命周期 ─────────────────────────
    def start(self) -> None:
        if self._run_task is None or self._run_task.done():
            self._stopping = False
            self._run_task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        self._stopping = True
        self._gen += 1
        ws = self._ws
        if ws is not None:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass
        if self._run_task is not None:
            self._run_task.cancel()
            try:
                await self._run_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        for t in list(self._tasks):
            t.cancel()
        self._set("stopped")

    def reload(self) -> None:
        """设置改了：断开当前连接，马上按新设置重连（或者停下）。"""
        self._gen += 1
        ws = self._ws
        if ws is not None:
            task = asyncio.get_running_loop().create_task(self._close_quietly(ws))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    @staticmethod
    async def _close_quietly(ws: Any) -> None:
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass

    async def _sleep(self, seconds: float, gen: int) -> bool:
        """等 seconds 秒；中途设置变了（reload）就提前醒来。返回是不是被打断的。"""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self._gen != gen or self._stopping:
                return True
            await asyncio.sleep(min(0.2, max(0.01, end - time.monotonic())))
        return self._gen != gen

    # ───────────────────────── 主循环 ─────────────────────────
    async def _run(self) -> None:
        attempt = 0
        while not self._stopping:
            gen = self._gen
            cfg = self.get_cfg()
            if not cfg.get("enabled") or not cfg.get("host"):
                self._set("disabled", error="", retry_in=0)
                await self._sleep(2.0, gen)
                attempt = 0
                continue
            uri = build_uri(cfg)
            headers = {"Authorization": f"Bearer {cfg['token']}"} if cfg.get("token") else None
            err = ""
            try:
                self._set("connecting", error="", retry_in=0, attempts=attempt)
                async with websockets.connect(uri, additional_headers=headers, open_timeout=10, ping_interval=20, ping_timeout=20,
                                              max_size=16 * 1024 * 1024) as ws:
                    self._ws = ws
                    attempt = 0
                    self._set("connected", error="", retry_in=0, connected_at=time.time(), attempts=0)
                    log.info("QQ：已连上 NapCat（%s:%s）", cfg["host"], cfg["port"])
                    if self.on_connected:
                        self._spawn(self.on_connected())
                    async for raw in ws:
                        self._dispatch(raw)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                err = f"{type(exc).__name__}: {exc}"
            finally:
                self._ws = None
                for fut in self._pending.values():
                    if not fut.done():
                        fut.set_exception(ConnectionError("连接断了"))
                        fut.exception()                    # 标成「已取走」：没人在等它（比如刚连上就被关了）时，不会报「从没取走的异常」
                self._pending.clear()
            if self._stopping:
                break
            cfg = self.get_cfg()
            if not cfg.get("enabled"):
                continue
            delay = backoff(attempt, float(cfg["reconnect_min_s"]), float(cfg["reconnect_max_s"]))
            attempt += 1
            self._set("error", error=err or "连接断开", retry_in=delay, attempts=attempt)
            log.info("QQ：和 NapCat 的连接断了（%s），%.0f 秒后重连", err or "断开", delay)
            if await self._sleep(delay, gen):
                attempt = 0                                   # 设置变了：别再按之前的退避等，马上重连

    def _spawn(self, coro: Awaitable[Any]) -> None:
        task = asyncio.get_running_loop().create_task(self._guard(coro))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @staticmethod
    async def _guard(coro: Awaitable[Any]) -> None:
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("QQ：处理事件出错")

    def _dispatch(self, raw: Any) -> None:
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return
        if not isinstance(data, dict):
            return
        echo = data.get("echo")
        if echo is not None and str(echo) in self._pending:
            fut = self._pending.pop(str(echo))
            if not fut.done():
                fut.set_result(data)
            return
        if data.get("post_type"):
            self.status["last_event_at"] = time.time()
            self.status["events"] = int(self.status.get("events", 0)) + 1
            if data.get("self_id") and not self.status.get("self_id"):
                self.status["self_id"] = str(data["self_id"])
            self._spawn(self.on_event(data))

    # ───────────────────────── 调动作 ─────────────────────────
    async def call(self, action: str, params: dict[str, Any] | None = None, timeout: float = 15.0) -> dict[str, Any]:
        ws = self._ws
        if ws is None:
            raise ConnectionError("还没连上 NapCat")
        echo = uuid.uuid4().hex
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[echo] = fut
        try:
            await ws.send(json.dumps({"action": action, "params": params or {}, "echo": echo}, ensure_ascii=False))
            resp = await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(echo, None)
        if str(resp.get("status")) not in ("ok", "async") and resp.get("retcode") not in (0, None):
            raise OneBotError(str(resp.get("wording") or resp.get("message") or f"{action} 失败"), resp.get("retcode"))
        return resp

    async def hello(self) -> None:
        """连上之后问一下自己是谁（self_id、昵称）。"""
        try:
            resp = await self.call("get_login_info", timeout=8)
            d = resp.get("data") or {}
            self.status["self_id"] = str(d.get("user_id") or self.status.get("self_id") or "")
            self.status["nickname"] = str(d.get("nickname") or "")
        except Exception as exc:  # noqa: BLE001
            log.info("QQ：get_login_info 失败：%s", exc)
