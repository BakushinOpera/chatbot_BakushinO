"""QQ 的核心：收消息 → 批量窗口 → 闸门 → 生成 → 后处理 → 逐条发出。

* 白名单在最前面：不是主人的私聊、不在名单里的群，消息**落库之前**就丢掉（不存储、不进记忆）；
* 私聊（只有主人）：合并连发的几条，一律回；群聊：每条消息过闸门（gate.py），等一个随机窗口把这段时间的消息一起看，
  过了闸门的里面挑最想接的 1~2 条，一次调用模型；
* 模型只读本地数据（ctx.py / localview.py），不开工具，生成的内容只写进 qq.db，不会写回本地应用；
* 后处理沿用桌面的：剥动作、去感叹号、禁词检查（命中重新生成）、切成多条、条间停顿；再加：去掉所有 Markdown；
* 「模拟群聊」走的是同一套代码，只是发送换成了假的（transport），可以跳过等待。
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable

from .. import character as C
from .. import guards, llm, registry
from . import ctx, gate, onebot, store
from . import settings as qqset
from . import text as qqtext
from .localview import LocalView

log = logging.getLogger(__name__)

SIM_CHAT = "sim:main"
SILENT = "silent"
_MARK = re.compile(r"(?m)^\s*[\[【]\s*回\s*(\d+)\s*[\]】][ \t:：]*")
ONE_CHARS = ["嗯", "哦", "嗯嗯", "？"]


# ───────────────────────── 数据 ─────────────────────────
@dataclass
class Incoming:
    chat: str
    kind: str                       # group | private
    user_id: str
    text: str
    nickname: str = ""
    card: str = ""
    group_id: str = ""
    msg_id: str | None = None
    ts: float = 0.0
    is_owner: bool = False
    at_self: bool = False
    reply_to: str | None = None
    reply_to_self: bool = False
    quote: str = ""
    has_visual: bool = False


@dataclass
class Run:
    """一次处理的运行参数。模拟群聊里 fast=True（不真的等，只记下该等多久）、可以假装状态 / 忙碌、可以跳过掷骰子。"""
    fast: bool = False
    force: bool = False
    state: str | None = None
    busy: bool | None = None
    mood: str | None = None
    trace: dict[str, Any] = field(default_factory=dict)


# ───────────────────────── 发送（真 / 假）─────────────────────────
class Transport:
    async def send_private(self, user_id: str, text: str) -> str | None:
        raise NotImplementedError

    async def send_group(self, group_id: str, text: str, reply_to: str | None = None) -> str | None:
        raise NotImplementedError


class OneBotTransport(Transport):
    def __init__(self, client: onebot.OneBotClient) -> None:
        self.client = client

    @staticmethod
    def _id(v: str) -> Any:
        return int(v) if str(v).isdigit() else v

    @staticmethod
    def _mid(resp: dict[str, Any]) -> str | None:
        mid = (resp.get("data") or {}).get("message_id")
        return str(mid) if mid is not None else None

    async def send_private(self, user_id: str, text: str) -> str | None:
        return self._mid(await self.client.call("send_private_msg", {"user_id": self._id(user_id), "message": qqtext.build_segments(text)}))

    async def send_group(self, group_id: str, text: str, reply_to: str | None = None) -> str | None:
        return self._mid(await self.client.call("send_group_msg", {"group_id": self._id(group_id), "message": qqtext.build_segments(text, reply_to)}))


class SimTransport(Transport):
    def __init__(self) -> None:
        self.n = 0

    async def send_private(self, user_id: str, text: str) -> str | None:
        self.n += 1
        return f"sim-{self.n}"

    async def send_group(self, group_id: str, text: str, reply_to: str | None = None) -> str | None:
        self.n += 1
        return f"sim-{self.n}"


# ───────────────────────── 小函数 ─────────────────────────
def retry_messages(messages: list[dict[str, Any]], hits: list[str]) -> list[dict[str, Any]]:
    msgs = [dict(m) for m in messages]
    for m in reversed(msgs):
        if m["role"] == "user":
            m["content"] = m["content"] + (f"\n\n【系统提示：你上一版回复里出现了不该用的说法（{'、'.join(hits[:5])}），换一种说法重新回复一遍，不要提这件事。】")
            break
    return msgs


def parse_blocks(clean: str, n_targets: int) -> dict[int, str]:
    """群聊的回复：按「[回N]」切成针对第 N 条的几段；没写标记就当是回第 1 条。"""
    pieces = _MARK.split(clean or "")
    blocks: dict[int, str] = {}
    for i in range(1, len(pieces) - 1, 2):
        idx = int(pieces[i])
        body = pieces[i + 1].strip()
        if 1 <= idx <= n_targets and body:
            blocks[idx] = (blocks.get(idx, "") + "\n" + body).strip()
    if not blocks and (clean or "").strip() and len(pieces) == 1:
        blocks[1] = clean.strip()
    return blocks


def trim_parts(per_target: dict[int, list[str]], limit: int) -> dict[int, list[str]]:
    """一次发言总共最多 limit 条：从最长的那段开始砍尾巴，每个目标至少留 1 条。"""
    out = {k: list(v) for k, v in per_target.items() if v}
    while sum(len(v) for v in out.values()) > limit:
        longest = max(out, key=lambda k: len(out[k]))
        if len(out[longest]) <= 1:
            break
        out[longest].pop()
    return out


_WAVE = re.compile(r"(?<!\d)[~～〜]+|[~～〜]+(?!\d)")


def strip_waves(text: str) -> str:
    """去掉波浪号（撒娇的口气只对主人）；「1~3」这种数字之间的不动。"""
    return _WAVE.sub("", text or "").strip()


def day_start(now: float) -> float:
    return datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _row_view(r: dict[str, Any]) -> dict[str, Any]:
    v = dict(r)
    try:
        meta = json.loads(r.get("meta") or "{}") or {}
    except json.JSONDecodeError:
        meta = {}
    v["reply_to_self"] = bool(meta.get("reply_to_self"))
    v["at_self"] = bool(r.get("at_self"))
    v["is_owner"] = bool(r.get("is_owner"))
    v["has_visual"] = bool(r.get("has_image"))
    return v


class Engine:
    def __init__(self, local: LocalView | None = None, llm_complete: Callable[..., Awaitable[str]] | None = None,
                 clock: Callable[[], float] = time.time, rng: random.Random | None = None,
                 sleep: Callable[[float], Awaitable[Any]] | None = None) -> None:
        self.local = local or LocalView()
        self.llm_complete = llm_complete
        self.clock = clock
        self.rng = rng or random.Random()
        self.sleep = sleep or asyncio.sleep
        self.client = onebot.OneBotClient(self.cfg, self.on_event, self._on_connected)
        self.transport: Transport = OneBotTransport(self.client)
        self.sim_transport = SimTransport()
        self._timers: dict[str, asyncio.Task] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._bg: set[asyncio.Task] = set()
        self._loop_task: asyncio.Task | None = None
        self.traces: dict[str, dict[str, Any]] = {}
        self.overrides: dict[str, dict[str, Any]] = {}      # 模拟群聊里「假装她在某个状态」，实时窗口（不是 instant）也要带上
        self.counters = {"in_private": 0, "in_group": 0, "ignored": 0, "replies": 0, "silent": 0, "errors": 0}
        self.last_error = ""

    # ───────────────────────── 设置 / 模型 ─────────────────────────
    def cfg(self) -> dict[str, Any]:
        return qqset.load()

    async def _complete(self, settings: dict[str, Any], messages: list[dict[str, Any]], temperature: float) -> str:
        fn = self.llm_complete or llm.complete
        return await fn(settings, messages, temperature=temperature)

    # ───────────────────────── 启停 ─────────────────────────
    def start(self) -> None:
        store.connect()
        self.client.start()
        if self._loop_task is None or self._loop_task.done():
            self._loop_task = asyncio.get_running_loop().create_task(self._maintenance())

    async def stop(self) -> None:
        if self._loop_task:
            self._loop_task.cancel()
        for t in list(self._timers.values()) + list(self._bg):
            t.cancel()
        await self.client.stop()

    def reload(self) -> None:
        """QQ 设置改了：连接参数 / 开关变了就重连。"""
        self.client.reload()

    async def _on_connected(self) -> None:
        await self.client.hello()

    def status(self) -> dict[str, Any]:
        s = self.client.view()
        cfg = self.cfg()
        s.update({
            "enabled": bool(cfg["enabled"]), "host": cfg["host"], "port": cfg["port"], "owner_set": bool(cfg["owner_qq"]), "groups": len(cfg["groups"]),
            "counters": dict(self.counters), "muted": self.muted_chats(), "last_error": self.last_error,
        })
        return s

    def muted_chats(self) -> list[str]:
        now = self.clock()
        return [c for c in [f"g:{g}" for g in self.cfg()["groups"]] + [SIM_CHAT] if self._mute_until(c) > now]

    # ───────────────────────── 静默 ─────────────────────────
    def _mute_until(self, chat: str) -> float:
        try:
            return float(store.kv_get(f"mute:{chat}", "0") or 0)
        except ValueError:
            return 0.0

    def _set_mute(self, chat: str, until: float) -> None:
        store.kv_set(f"mute:{chat}", str(until))

    # ───────────────────────── 收事件（OneBot）─────────────────────────
    async def on_event(self, ev: dict[str, Any]) -> None:
        spec = onebot.parse_event(ev)
        if spec is None:
            return
        cfg = self.cfg()
        self_id = spec["self_id"] or self.client.status.get("self_id") or ""
        owner = cfg["owner_qq"]
        if self_id and spec["user_id"] == str(self_id):
            return                                                          # 她自己发出去的（NapCat 回报）
        if spec["kind"] == "private":
            if not owner or spec["user_id"] != owner:
                self.counters["ignored"] += 1
                return                                                      # 不是主人的私聊：直接忽略，不存储
            chat = f"p:{owner}"
        else:
            if spec["group_id"] not in cfg["groups"]:
                self.counters["ignored"] += 1
                return                                                      # 不在名单里的群：直接忽略
            chat = f"g:{spec['group_id']}"
        if spec["msg_id"] and store.find_by_msg_id(spec["msg_id"], chat):
            return                                                          # 同一条消息收到两次（比如重连后重发）：只算一次
        parsed = qqtext.parse_message(spec["message"], self_id, name_of=lambda qq: self._name_of(chat, qq))
        if not parsed["text"]:
            return
        quote, reply_to_self = "", False
        if parsed["reply_id"]:
            quote, reply_to_self = await self._resolve_quote(chat, parsed["reply_id"], self_id)
        inc = Incoming(
            chat=chat, kind=spec["kind"], user_id=spec["user_id"], text=parsed["text"], nickname=spec["nickname"], card=spec["card"],
            group_id=spec["group_id"], msg_id=spec["msg_id"], ts=spec["ts"] or self.clock(), is_owner=bool(owner) and spec["user_id"] == owner,
            at_self=parsed["at_self"], reply_to=parsed["reply_id"], reply_to_self=reply_to_self, quote=quote, has_visual=parsed["has_visual"],
        )
        self.counters["in_private" if inc.kind == "private" else "in_group"] += 1
        await self.on_incoming(inc)

    def _name_of(self, chat: str, qq: str) -> str:
        m = store.get_member(chat, qq)
        return qqtext.sanitize_name(m["nickname"]) if m and m.get("nickname") else ""

    async def _resolve_quote(self, chat: str, reply_id: str, self_id: str) -> tuple[str, bool]:
        """引用了哪句话（还原成「谁：内容」）、引用的是不是她自己说的。"""
        row = store.find_by_msg_id(reply_id, chat)
        if row:
            who = "你" if row["is_self"] else ("她" if row["is_owner"] else ctx.name_of(row))
            return f"{who}：{row['text']}"[:80], bool(row["is_self"])
        if self.client.connected:
            try:
                resp = await self.client.call("get_msg", {"message_id": int(reply_id) if str(reply_id).lstrip("-").isdigit() else reply_id}, timeout=6)
                d = resp.get("data") or {}
                sender = d.get("sender") or {}
                uid = str(sender.get("user_id") or d.get("user_id") or "")
                body = qqtext.parse_message(d.get("message") if d.get("message") is not None else d.get("raw_message", ""), self_id)["text"]
                mine = bool(self_id) and uid == str(self_id)
                who = "你" if mine else qqtext.sanitize_name(sender.get("card") or sender.get("nickname") or "")
                return f"{who}：{body}"[:80], mine
            except Exception as exc:  # noqa: BLE001
                log.info("QQ：还原引用失败：%s", exc)
        return "", False

    # ───────────────────────── 收消息（OneBot 和模拟共用）─────────────────────────
    async def on_incoming(self, inc: Incoming, run: Run | None = None) -> dict[str, Any] | None:
        cfg = self.cfg()
        now = self.clock()
        store.add_message(inc.chat, inc.user_id, inc.text, ts=inc.ts or now, nickname=inc.nickname, card=inc.card, is_owner=inc.is_owner,
                          msg_id=inc.msg_id, reply_to=inc.reply_to, quote=inc.quote, at_self=inc.at_self, has_image=inc.has_visual,
                          meta={"reply_to_self": inc.reply_to_self})
        if inc.kind == "group" and not inc.is_owner:
            store.touch_member(inc.chat, inc.user_id, qqtext.sanitize_name(inc.card or inc.nickname), now)
        if inc.kind == "group" and inc.is_owner:
            if self._mute_until(inc.chat) > now and qqtext.is_command(inc.text, cfg["unmute_words"]):
                self._set_mute(inc.chat, 0)
            elif qqtext.is_command(inc.text, cfg["mute_words"]):
                self._set_mute(inc.chat, now + float(cfg["mute_minutes"]) * 60)
                store.mark_handled([r["id"] for r in store.unhandled(inc.chat)])
                ack = (cfg.get("mute_ack") or "").strip()
                run = run or Run()
                run.trace.update({"chat": inc.chat, "outcome": "muted_by_command", "mute_minutes": cfg["mute_minutes"]})
                if ack:
                    await self._deliver_units(inc.chat, [{"text": ack, "reply_to": None}], run, cfg, "ack")
                self.traces[inc.chat] = run.trace
                return run.trace
        if run is not None and run.fast:
            return await self.process(inc.chat, run)
        self._schedule(inc.chat)
        return None

    # ───────────────────────── 批量窗口 ─────────────────────────
    def _schedule(self, chat: str) -> None:
        t = self._timers.get(chat)
        if t is not None and not t.done():
            return                                      # 已经有窗口在等 / 在处理；新消息会在它这一轮或下一轮被看到
        self._timers[chat] = asyncio.get_running_loop().create_task(self._window_loop(chat))

    async def _window_loop(self, chat: str) -> None:
        try:
            while True:
                cfg = self.cfg()
                lo, hi = (cfg["private_window_min_s"], cfg["private_window_max_s"]) if chat.startswith("p:") else (cfg["window_min_s"], cfg["window_max_s"])
                await self.sleep(self.rng.uniform(float(lo), max(float(lo), float(hi))))
                await self.process(chat, Run(**self.overrides.get(chat, {})))
                if not store.unhandled(chat):
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.counters["errors"] += 1
            self.last_error = f"{type(exc).__name__}: {exc}"
            log.exception("QQ：处理 %s 的消息出错", chat)

    def _lock(self, chat: str) -> asyncio.Lock:
        return self._locks.setdefault(chat, asyncio.Lock())

    def _env(self, run: Run, cfg: dict[str, Any]) -> ctx.Env:
        return ctx.Env(local=self.local, settings=self.local.settings(), cfg=cfg, now=self.clock(), state_override=run.state, busy_override=run.busy, mood_override=run.mood)

    # ───────────────────────── 处理一个窗口 ─────────────────────────
    async def process(self, chat: str, run: Run | None = None) -> dict[str, Any]:
        run = run or Run()
        async with self._lock(chat):
            cfg = self.cfg()
            now = self.clock()
            run.trace = {"chat": chat, "at": now, "outcome": "nothing"}
            store.expire(chat, now - float(cfg["stale_minutes"]) * 60)
            rows = [_row_view(r) for r in store.unhandled(chat, now - float(cfg["stale_minutes"]) * 60)]
            if not rows:
                self.traces[chat] = run.trace
                return run.trace
            store.mark_handled([r["id"] for r in rows])
            if chat.startswith("p:"):
                await self._process_private(chat, rows, run, cfg)
            else:
                await self._process_group(chat, rows, run, cfg)
            self.traces[chat] = run.trace
            return run.trace

    # ───────────────────────── 私聊 ─────────────────────────
    async def _process_private(self, chat: str, rows: list[dict[str, Any]], run: Run, cfg: dict[str, Any]) -> None:
        env = self._env(run, cfg)
        ooc = any(guards.is_ooc(r["text"]) for r in rows)
        history = store.recent(chat, int(cfg["private_context_messages"]), upto_id=rows[0]["id"] - 1)
        messages, info = ctx.build_private(env, history, rows, ooc)
        run.trace.update({"kind": "private", "state": env.state_key(), "ooc": ooc, "prompt": messages, "info": info})
        gen = await self._generate(messages, ooc, run)
        if gen.get("error") or gen["silent"] or not gen["clean"].strip():
            run.trace["outcome"] = f"error:{gen['error']}" if gen.get("error") else "silent"
            self.counters["silent" if not gen.get("error") else "errors"] += 1
            return
        settings = env.settings
        if ooc:
            text = gen["clean"]
            parts = [text[i:i + 900] for i in range(0, len(text), 900)] or [text]
        elif settings.get("split_replies") != "0":
            parts = guards.split_messages(gen["clean"])
        else:
            parts = [gen["clean"]]
        units = [{"text": p, "reply_to": None} for p in parts if p.strip()]
        await self._deliver_units(chat, units, run, cfg, "reply", env=env, private=True)

    # ───────────────────────── 群聊 ─────────────────────────
    async def _process_group(self, chat: str, rows: list[dict[str, Any]], run: Run, cfg: dict[str, Any]) -> None:
        env = self._env(run, cfg)
        now = env.now
        state = env.state_key()
        mood, _ = env.mood()
        busy = env.busy()
        last = store.last_speak(chat)
        since = (now - last) if last else None
        sleeping = state == "sleep" or self.local.is_sleeping(now, env.settings)
        muted = self._mute_until(chat) > now
        s10, s60 = store.count_speaks(chat, now - 600), store.count_speaks(chat, now - 3600)
        caps_ok, caps_why = gate.caps_ok(cfg, s10, s60)
        decisions: list[dict[str, Any]] = []
        short = False
        for r in rows:
            c = gate.classify(r, cfg)
            cls = c["cls"]
            r["cls"], r["trigger"] = cls, c["trigger"]
            f = gate.factors(cfg, state, busy, mood, since, direct=(cls == "direct"))
            short = short or f["short"]
            if cls == "forced":                       # 主人 @ 她：不走概率，每句都回，也不计入频率
                f = {**gate.NEUTRAL, "short": f["short"]}
                p, roll, passed = 1.0, 0.0, True
            else:
                p = gate.probability(c["base"], f)
                roll = self.rng.random()
                passed = bool(roll < p or (run.force and p > 0))               # force = 跳过掷骰子（概率是 0 的照样不回）
            decisions.append({"id": r["id"], "name": ctx.name_of(r) if not r["is_owner"] else f"【{C.pronoun()}】" + ctx.name_of(r), "text": r["text"][:60], "cls": cls,
                              "cls_label": c["label"], "base": c["base"], "factors": f, "p": round(p, 4), "roll": round(roll, 4), "passed": passed,
                              "counted": cls != "forced"})
        run.trace.update({"kind": "group", "state": state, "state_label": qqset.state_label(state), "mood": mood, "busy": busy, "sleeping": sleeping, "muted": muted,
                          "caps": {"ok": caps_ok, "why": caps_why, "speaks_10min": s10, "speaks_hour": s60}, "messages": decisions})
        owner_ooc = [r for r in rows if r["is_owner"] and guards.is_ooc(r["text"])]
        forced = [r for r in rows if r["cls"] == "forced"]
        block = "静默中" if muted else ("她在睡觉（深夜不发言）" if sleeping else ("" if caps_ok else caps_why))
        if block and not owner_ooc and not forced:
            if muted and any(r["at_self"] for r in rows) and cfg["mute_at_reply"] == "one_char":
                run.trace["outcome"] = "muted_one_char"
                target = [r for r in rows if r["at_self"]][-1]
                await self._deliver_units(chat, [{"text": self.rng.choice(ONE_CHARS), "reply_to": target.get("msg_id")}], run, cfg, "ack", env=env)
            else:
                run.trace["outcome"] = f"blocked:{block}"
            return
        by_id = {r["id"]: r for r in rows}
        if owner_ooc:
            targets = [owner_ooc[-1]]
        else:
            pool = forced if block else [by_id[d["id"]] for d in decisions if d["passed"]]          # 被拦住（静默 / 睡觉 / 上限）时，只有主人 @ 她的话还会回
            targets = gate.pick_targets(pool, int(cfg["max_targets"]), self.rng)
        if not targets:
            run.trace["outcome"] = "no_candidates"
            return
        ooc = bool(owner_ooc)
        counted = not all(t["cls"] == "forced" or ooc for t in targets)             # 只回了主人 @ 她的话：不计入发言频率（10 分钟 / 1 小时上限、冷却）
        members = {m["user_id"]: m for m in store.list_members(chat)}
        chat_rows = [_row_view(r) for r in store.recent(chat, int(cfg["context_messages"]))]
        for t in targets:
            t["has_visual"] = bool(t.get("has_image"))
        short_flag = short and counted and not ooc
        messages, info = ctx.build_group(env, chat_rows, targets, members, short_flag, ooc)
        run.trace.update({"targets": [t["id"] for t in targets], "detail": info["detail"], "short": short_flag, "ooc": ooc, "counted": counted,
                          "forced_through": block if block and forced else "", "prompt": messages, "info": info})
        avoid = [] if ooc or any(t["is_owner"] for t in targets) else list(cfg.get("intimate_words") or [])       # 亲昵的语气只对主人
        gen = await self._generate(messages, ooc, run, avoid)
        if gen.get("error"):
            run.trace["outcome"] = f"error:{gen['error']}"
            self.counters["errors"] += 1
            return
        if gen["silent"] or not gen["clean"].strip():
            run.trace["outcome"] = SILENT
            self.counters["silent"] += 1
            return
        blocks = parse_blocks(gen["clean"], len(targets))
        if not ooc:
            for idx in list(blocks):
                if not targets[idx - 1]["is_owner"]:
                    blocks[idx] = strip_waves(blocks[idx])
                    if not blocks[idx]:
                        del blocks[idx]
        if not blocks:
            run.trace["outcome"] = SILENT
            return
        if ooc:
            per = {1: [gen["clean"][i:i + 900] for i in range(0, len(gen["clean"]), 900)]}
        else:
            per = {i: guards.split_messages(b, guards.MAX_PART_CHARS, int(cfg["max_parts"])) for i, b in blocks.items()}
            per = trim_parts(per, int(cfg["max_parts"]))
        policy = cfg["quote_policy"]
        newest = max((r["id"] for r in chat_rows if not r.get("is_self")), default=0)
        units: list[dict[str, Any]] = []
        for idx in sorted(per):
            t = targets[idx - 1]
            quote = policy == "always" or (policy == "auto" and t["id"] != newest)
            for j, part in enumerate(per[idx]):
                units.append({"text": part, "reply_to": (t.get("msg_id") if quote and j == 0 else None)})
        replied = {targets[i - 1]["user_id"]: targets[i - 1] for i in per}
        await self._deliver_units(chat, units, run, cfg, "reply" if counted else "owner_at", env=env, short=short_flag)
        self._after_group_reply(chat, replied, cfg, chat_rows)

    # ───────────────────────── 生成 ─────────────────────────
    async def _generate(self, messages: list[dict[str, Any]], ooc: bool, run: Run, avoid: list[str] | tuple[str, ...] = ()) -> dict[str, Any]:
        settings = self.local.settings()
        if self.llm_complete is None and llm.is_echo(settings):
            return {"error": "桌面版里还没有配置模型（接口地址 / key），QQ 没法生成回复", "silent": False, "clean": ""}
        try:
            temp = float(settings.get("temperature") or 0.9)
        except ValueError:
            temp = 0.9
        msgs, attempts, raw, clean, silent = messages, 0, "", "", False
        while True:
            try:
                raw = await self._complete(settings, msgs, temp)
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"{type(exc).__name__}: {exc}"
                return {"error": self.last_error, "silent": False, "clean": ""}
            clean, calls = guards.extract_calls(raw)
            silent = any(str(c.get("action")) == SILENT for c in calls)
            clean = qqtext.strip_markdown(clean)
            if not ooc:
                clean = guards.strip_exclaim(clean)
            hits = [] if ooc else guards.find_banned(clean) + [w for w in avoid if w and w in clean]     # avoid：这次不该出现的口气（亲昵的语气只对主人）
            if hits and attempts < guards.MAX_RETRIES:
                attempts += 1
                msgs = retry_messages(messages, hits)
                continue
            break
        run.trace.update({"raw": raw, "retries": attempts, "chars_in": sum(len(m["content"]) for m in messages), "chars_out": len(raw)})
        return {"raw": raw, "clean": clean, "silent": silent and not clean.strip(), "retries": attempts}

    # ───────────────────────── 发出去 ─────────────────────────
    def _delay_scale(self, env: ctx.Env, cfg: dict[str, Any]) -> tuple[float, bool]:
        try:
            scale = float(env.settings.get("reply_delay") or 1.0)
        except ValueError:
            scale = 1.0
        scale *= float(cfg["typing_scale"])
        if env.busy():
            scale *= 1.5
        key = env.state_key()
        okey = f"state_outside_{key}"
        outside = okey in registry.BY_KEY and registry.flag(okey, env.settings)
        if outside:
            scale *= registry.num("outside_delay_mult", env.settings)
        return scale, outside

    async def _wait(self, seconds: float, run: Run) -> None:
        run.trace.setdefault("delays", []).append(round(seconds, 1))
        if not run.fast and seconds > 0:
            await self.sleep(seconds)

    async def _deliver_units(self, chat: str, units: list[dict[str, Any]], run: Run, cfg: dict[str, Any], kind: str,
                             env: ctx.Env | None = None, private: bool = False, short: bool = False) -> None:
        env = env or self._env(run, cfg)
        scale, outside = self._delay_scale(env, cfg)
        transport = self.sim_transport if chat.startswith("sim:") else self.transport
        sent: list[dict[str, Any]] = []
        self_id = str(self.client.status.get("self_id") or "self")
        for i, u in enumerate(units):
            if i == 0:
                think = self.rng.uniform(1.0, 3.0) if private else self.rng.uniform(2.0, 8.0)
                if short:
                    think *= 0.5
                if env.state_key() == "sleep":
                    think += self.rng.uniform(5.0, 15.0)
                if outside:
                    lo, hi = registry.num("outside_reply_min_s", env.settings), registry.num("outside_reply_max_s", env.settings)
                    think += self.rng.uniform(min(lo, hi), max(lo, hi))
                delay = think * scale
            else:
                delay = guards.delay_for(u["text"], scale)
            await self._wait(delay, run)
            try:
                if chat.startswith("p:"):
                    mid = await transport.send_private(chat[2:], u["text"])
                else:
                    mid = await transport.send_group(chat.split(":", 1)[1], u["text"], u.get("reply_to"))
            except Exception as exc:  # noqa: BLE001
                run.trace["send_error"] = f"{type(exc).__name__}: {exc}"
                self.last_error = run.trace["send_error"]
                self.counters["errors"] += 1
                log.warning("QQ：发送失败：%s", exc)
                break
            store.add_message(chat, self_id, u["text"], ts=self.clock(), nickname="Phal", is_self=True, msg_id=mid, reply_to=u.get("reply_to"), handled=True)
            sent.append({"text": u["text"], "reply_to": u.get("reply_to"), "delay": round(delay, 1)})
        if sent:
            store.add_speak(chat, kind, self.clock())
            self.counters["replies"] += 1
        run.trace["parts"] = sent
        if kind in ("reply", "owner_at"):
            run.trace["outcome"] = "replied" if sent else "send_failed"
        elif kind == "proactive":
            run.trace["outcome"] = "proactive" if sent else "send_failed"

    # ───────────────────────── 群友印象 ─────────────────────────
    def _after_group_reply(self, chat: str, replied: dict[str, dict[str, Any]], cfg: dict[str, Any], chat_rows: list[dict[str, Any]]) -> None:
        if not cfg["impression_enabled"]:
            return
        for uid, t in replied.items():
            if t.get("is_owner"):
                continue
            m = store.bump_interaction(chat, uid, float(cfg["familiarity_gain"]), float(cfg["familiarity_cap"]))
            if m and m["since_update"] >= int(cfg["impression_every"]):
                self._spawn(self._update_impression(chat, uid, chat_rows))

    def _spawn(self, coro: Awaitable[Any]) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    async def _update_impression(self, chat: str, user_id: str, chat_rows: list[dict[str, Any]]) -> None:
        try:
            m = store.get_member(chat, user_id)
            if not m:
                return
            lines = [ctx.line_of(_row_view(r)) for r in chat_rows if r.get("user_id") == user_id or r.get("is_self")][-12:]
            prompt = ("你在帮 Phal（一个住在虚构世界里的普通女高中生）记住 QQ 群里的一个人。下面是她现有的印象，和最近的几句对话（「你」= Phal 自己）。"
                      "请更新她对这个人的印象。只输出 JSON，不要别的：\n"
                      '{"call": "她平时怎么称呼这个人（一个称呼）", "impression": "不超过 60 字：这个人怎么样、聊过什么、对她好不好"}\n'
                      "没有新信息就保留原来的。")
            user = (f"这个人的昵称：{m['nickname']}\n现有印象：{m['impression'] or '（还没有）'}\n现有称呼：{m['call_name'] or '（还没有）'}\n\n最近的对话：\n" + "\n".join(lines))
            settings = self.local.settings()
            if self.llm_complete is None and llm.is_echo(settings):
                return
            raw = await self._complete(settings, [{"role": "system", "content": prompt}, {"role": "user", "content": user}], 0.3)
            a, b = raw.find("{"), raw.rfind("}")
            data = json.loads(raw[a:b + 1]) if a >= 0 and b > a else {}
            store.update_member(chat, user_id, call_name=str(data.get("call") or "")[:12] or None, impression=str(data.get("impression") or "")[:120] or None)
            store.reset_since_update(chat, user_id)
        except Exception as exc:  # noqa: BLE001
            log.info("QQ：更新群友印象失败：%s", exc)

    # ───────────────────────── 主动开口 / 维护 ─────────────────────────
    async def _maintenance(self) -> None:
        last_prune = 0.0
        while True:
            await self.sleep(60)
            try:
                if self.client.connected:
                    await self.proactive_tick()
                now = self.clock()
                if now - last_prune > 86400:
                    store.prune(int(self.cfg()["retention_days"]), now)
                    last_prune = now
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("QQ：维护任务出错")

    async def proactive_tick(self, chat: str | None = None, run: Run | None = None) -> dict[str, Any] | None:
        """极少在群里主动说句话：只在闲状态、没到每天上限、离上次主动够久、夜里不发，按很低的概率。"""
        cfg = self.cfg()
        if not cfg["proactive_enabled"]:
            return None
        chats = [chat] if chat else [f"g:{g}" for g in cfg["groups"]]
        result = None
        for c in chats:
            r = run or Run()
            async with self._lock(c):
                result = await self._proactive_one(c, r, cfg)
        return result

    async def _proactive_one(self, chat: str, run: Run, cfg: dict[str, Any]) -> dict[str, Any] | None:
        env = self._env(run, cfg)
        now = env.now
        state = env.state_key()
        run.trace = {"chat": chat, "at": now, "kind": "proactive", "state": state, "outcome": "no"}
        if state not in (cfg["proactive_states"] or []) or state == "sleep" or self.local.is_sleeping(now, env.settings) or self._mute_until(chat) > now:
            run.trace["outcome"] = "no:状态不合适 / 夜里 / 静默"
            return run.trace
        today = store.count_speaks(chat, day_start(now), ("proactive",))
        lastp = store.last_speak(chat, ("proactive",))
        s10, s60 = store.count_speaks(chat, now - 600), store.count_speaks(chat, now - 3600)
        ok, why = gate.caps_ok(cfg, s10, s60)
        if today >= int(cfg["proactive_daily_cap"]) or (lastp and now - lastp < float(cfg["proactive_min_gap_min"]) * 60) or not ok:
            run.trace["outcome"] = "no:今天的次数 / 间隔 / 上限"
            return run.trace
        if not run.force and self.rng.random() >= float(cfg["proactive_prob_per_min"]):
            run.trace["outcome"] = "no:这一分钟没轮到"
            return run.trace
        chat_rows = [_row_view(r) for r in store.recent(chat, int(cfg["context_messages"]))]
        messages = ctx.build_proactive(env, chat_rows)
        run.trace["prompt"] = messages
        gen = await self._generate(messages, False, run, list(cfg.get("intimate_words") or []))
        if gen.get("error") or gen["silent"] or not strip_waves(gen["clean"]):
            run.trace["outcome"] = f"error:{gen['error']}" if gen.get("error") else SILENT
            return run.trace
        parts = guards.split_messages(strip_waves(gen["clean"]), guards.MAX_PART_CHARS, min(2, int(cfg["max_parts"])))
        await self._deliver_units(chat, [{"text": p, "reply_to": None} for p in parts], run, cfg, "proactive", env=env)
        return run.trace

    # ───────────────────────── 模拟群聊 ─────────────────────────
    async def sim_send(self, user_id: str, name: str, text: str, is_owner: bool = False, at_her: bool = False, reply_to: int | None = None,
                       visual: bool = False, run: Run | None = None) -> dict[str, Any]:
        """假装某个群友在模拟群里说了句话；走的和真的群消息完全一样的路，只是发送是假的。"""
        run = run or Run(fast=True)
        quote, to_self, reply_msg_id = "", False, None
        if reply_to:
            row = store.get_message(int(reply_to))
            if row and row["chat"] == SIM_CHAT:
                who = "你" if row["is_self"] else ("她" if row["is_owner"] else ctx.name_of(row))
                quote, to_self, reply_msg_id = f"{who}：{row['text']}"[:80], bool(row["is_self"]), row["msg_id"] or str(row["id"])
        body = text.strip()
        if visual:
            body = (body + " [图片]").strip()
        if at_her:
            body = ("@你 " + body).strip()
        inc = Incoming(chat=SIM_CHAT, kind="group", user_id=str(user_id), text=body, nickname=name, group_id="sim", msg_id=f"simin-{time.time_ns()}", ts=self.clock(),
                       is_owner=bool(is_owner), at_self=at_her, reply_to=reply_msg_id, reply_to_self=to_self, quote=quote, has_visual=visual)
        trace = await self.on_incoming(inc, run)
        return trace or run.trace


_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = Engine()
    return _engine


def reset_engine() -> None:
    """测试用。"""
    global _engine
    _engine = None
