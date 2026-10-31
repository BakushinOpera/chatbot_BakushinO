"""连发消息 + 「正在输入」（见 docs/已完成的计划/连发输入.md）：每个会话一个「这一轮」的管理器。

* 我随时可以发：``receive`` 收下一条就返回；每个会话同一时间只有一个生成任务（``Session.task``），新消息进待处理队列，不会重复回复。
* 等待：收到第一条后先等基础窗口（默认 2 秒，忙的时候更长）。她有没有注意到我在打字（默认 70%，按她当前状态乘系数）：
  - 没注意到：窗口一到就把已经发出来的回了，之后的算新的一轮；
  - 注意到了：窗口结束时我没在打字 → 一起回；还在打字 → 按概率先插一句短的（只回已经说的），
    或者继续等，直到我停下来超过 2 秒（最长等 15 秒）。
* 合并回复：一轮的几条按顺序、带编号一起交给模型；这一轮可以引用的话，让模型按 [{"quote": 编号, "text": ...}] 输出，
  气泡上方带被引用的那句（每轮最多引用 2 次，只有一条时不引用）。
* 她正在生成 / 一条条发的时候我又发了：按概率（默认 50%）丢掉还没发出去的、把新消息并进来重新生成；否则先发完，再回新的。
* 事件推给页面（``subscribe`` → /api/stream）：user / tool(正在输入) / break(一条气泡) / discarded / done / life / room / error。
* 记忆提取每轮只跑一次（插的那一句不算一轮）。【OOC】、读书指令、写作命令照常识别。
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from typing import Any, Awaitable, Callable

from . import context, db, guards, life, moodsync, registry, usage, writing

log = logging.getLogger(__name__)

# 她现在闲不闲：闲着更容易注意到我在打字、更爱插话；忙的时候窗口更长、更少插话（像没盯着手机）
IDLE_STATES = {"tea", "tea_brew", "daze", "laze", "rain_watch", "music", "unbox", "netshop", "album", "video", "sick"}
BUSY_STATES = {"walk", "shop", "friends", "dine_out", "photo", "errand", "boardgame_friends", "game", "anime", "create", "homework",
               "draw", "read", "cook", "eat", "eat_alone", "shower", "nap", "call", "boardgame_online", "craft", "calligraphy",
               "bake", "sfx", "exercise", "puzzle_family", "synth", "tidy", "shelf", "tryon"}
TIERS = ("idle", "normal", "busy")
TIER_LABEL = {"idle": "闲着", "normal": "一般", "busy": "忙"}

_reply_events: Callable[..., Any] | None = None      # main.reply_events（启动时 configure 进来，免得循环引用）


def configure(reply_events: Callable[..., Any]) -> None:
    global _reply_events
    _reply_events = reply_events


def cfg(key: str) -> float:
    return registry.num(key)


def tier_of(key: str | None) -> str:
    if key in IDLE_STATES:
        return "idle"
    if key in BUSY_STATES or life.outside_now():
        return "busy"
    return "normal"


def current_tier() -> str:
    try:
        st = life.load_state() or {}
        return tier_of(st.get("key"))
    except Exception:  # noqa: BLE001
        return "normal"


def tier_k(tier: str) -> float:
    return cfg(f"burst_k_{tier}")


def ratios(values: dict[str, float] | None = None) -> dict[str, dict[str, float]]:
    """按当前参数算：「我发完一条还在接着打字」时，先回一句 / 等我打完合并回复 / 没注意到直接回 各占多少（三档各一行）。"""
    g = (lambda k: float(values[k])) if values else cfg
    out = {}
    for t in TIERS:
        k = g(f"burst_k_{t}")
        notice = min(1.0, max(0.0, g("burst_p_notice") * k))
        inter = min(1.0, max(0.0, g("burst_p_interject") * k))
        out[t] = {"interject": round(notice * inter, 3), "merge": round(notice * (1 - inter), 3), "direct": round(1 - notice, 3)}
    return out


# ───────────────────────── 引用格式 ─────────────────────────
def parse_quoted(text: str, quotes: dict[int, dict[str, Any]] | None) -> list[tuple[dict[str, Any] | None, str]]:
    """模型按 [{"quote": 编号, "text": ...}, {"text": ...}] 输出时拆成 [(被引用的消息 or None, 文本)]；不是这个格式就当一段普通文本。
    只认这一轮里的编号；每轮最多引用 2 次；quotes 只有一条（或为空）时一律不引用。"""
    text = (text or "").strip()
    m = re.search(r"\[\s*\{.*\}\s*\]", text, flags=re.S)
    items: list[tuple[dict[str, Any] | None, str]] = []
    if m:
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            data = None
        if isinstance(data, list) and data and all(isinstance(x, dict) and str(x.get("text") or "").strip() for x in data):
            used = 0
            for x in data:
                q = None
                ref = x.get("quote")
                if ref not in (None, "") and quotes and len(quotes) > 1 and used < 2:
                    try:
                        q = quotes.get(int(str(ref).lstrip("#")))
                    except ValueError:
                        q = None
                    if q:
                        used += 1
                items.append((q, str(x["text"]).strip()))
            rest = (text[:m.start()] + text[m.end():]).strip()
            if rest:
                items.append((None, rest))
            return items
    return _plain_quoted(text, quotes)


# 模型有时不按 JSON 给，而是照着提示词写成一行「【引用】#852 回那一条的话」/「#852 …」：认出来当引用，标记本身不能发出去
_PLAIN_Q = re.compile(r"^\s*(?:【引用】\s*)?[#＃](\d+)\s*[:：]?\s*(.*)$")


def _plain_quoted(text: str, quotes: dict[int, dict[str, Any]] | None) -> list[tuple[dict[str, Any] | None, str]]:
    if not text:
        return []
    items: list[tuple[dict[str, Any] | None, str]] = []
    buf: list[str] = []
    used = 0
    for line in text.split("\n"):
        marked = bool(re.match(r"^\s*【引用】", line))
        line = re.sub(r"^\s*【引用】\s*", "", line)
        m = _PLAIN_Q.match(line)
        if m and (marked or int(m.group(1)) in (quotes or {})):     # 「#5号线」这种普通的话不动
            q = None
            if quotes and len(quotes) > 1 and used < 2:
                q = quotes.get(int(m.group(1)))
            body = m.group(2).strip()
            if body:
                if buf:
                    items.append((None, "\n".join(buf).strip()))
                    buf = []
                if q:
                    used += 1
                items.append((q, body))
            continue
        buf.append(line)
    if buf and "\n".join(buf).strip():
        items.append((None, "\n".join(buf).strip()))
    return items


# ───────────────────────── 会话 ─────────────────────────
class Session:
    def __init__(self, sid: str) -> None:
        self.sid = sid
        self.pending: list[int] = []            # 还没回的我的消息（按发送顺序）
        self.typing = False
        self.typing_at = 0.0                    # 最近一次「正在输入」心跳
        self.typing_off_at = 0.0                # 最近一次停下来
        self.task: asyncio.Task | None = None
        self.busy = False                       # 正在生成 / 一条条发
        self.discard = False                    # 这次发到一半来了新消息、决定丢掉剩下的
        self.round_start = 0.0
        self.notice: bool | None = None         # 这一轮她注意到我在打字没有（一轮掷一次）
        self.said: list[str] = []               # 这一轮她已经说过的（插的那句 / 被打断前发出去的）

    def is_typing(self, now: float | None = None) -> bool:
        now = now or time.time()
        return self.typing and now - self.typing_at < cfg("burst_typing_fresh_s")      # 心跳多久没来就当没在打（前端每 0.7 秒报一次）

    def idle_for(self, now: float | None = None) -> float:
        """我停下来多久了（还在打就是 0）。"""
        now = now or time.time()
        if self.is_typing(now):
            return 0.0
        last = max(self.typing_off_at, self.typing_at if not self.typing else self.typing_at + cfg("burst_typing_fresh_s"))
        return now - last if last else 1e9


_sessions: dict[str, Session] = {}


def session(sid: str | None = None) -> Session:
    sid = sid or db.current_session_id()
    if sid not in _sessions:
        _sessions[sid] = Session(sid)
    return _sessions[sid]


# 推送连接不绑会话：点「新对话」后会话 id 变了，网页上的 /api/stream 还是那一条，
# 所以订阅者放在一起，只推「当前会话」的事件（旧会话还没发完的那几条照样入库，只是不推到新页面上）
_subs: set[asyncio.Queue] = set()


def subscribe(sid: str | None = None) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue()
    _subs.add(q)
    return q


def unsubscribe(q: asyncio.Queue, sid: str | None = None) -> None:
    _subs.discard(q)


def broadcast(s: Session, event: dict[str, Any]) -> None:
    if s.sid != db.current_session_id():
        return
    for q in list(_subs):
        q.put_nowait(event)


def set_typing(typing: bool, sid: str | None = None) -> None:
    s = session(sid)
    now = time.time()
    if typing:
        s.typing, s.typing_at = True, now
    else:
        if s.typing:
            s.typing_off_at = now
        s.typing = False


# ───────────────────────── 收消息 ─────────────────────────
def receive(msg: dict[str, Any], sid: str | None = None) -> None:
    """收下一条我的消息（已经入库）：进队列；没有在跑的就开一个。她正在生成 / 发的时候，按概率决定丢不丢剩下的。"""
    s = session(sid)
    s.pending.append(int(msg["id"]))
    if not s.round_start:
        s.round_start = time.time()
    broadcast(s, {"type": "user", "message": msg})
    if not guards.is_ooc(msg.get("content") or ""):
        _bg(_moodsync(msg.get("content") or ""))           # 心情同步：不挡着队列
    if s.busy and not s.discard and random.random() < cfg("burst_p_discard"):
        s.discard = True                                    # 还没发出去的丢掉，把新消息并进来重新生成
    if s.task is None or s.task.done():
        s.task = asyncio.get_running_loop().create_task(_run(s))


def _bg(coro: Awaitable[Any]) -> None:
    try:
        asyncio.get_running_loop().create_task(coro)  # type: ignore[arg-type]
    except RuntimeError:
        pass


async def _moodsync(text: str) -> None:
    try:
        await asyncio.wait_for(moodsync.process(db.get_settings(), text), timeout=3.0)
    except Exception as exc:  # noqa: BLE001
        log.info("心情同步跳过：%s", type(exc).__name__)


async def _run(s: Session) -> None:
    """一直跑到队列空：等 → 回一轮 → 有新的再等。"""
    try:
        while s.pending:
            await _collect(s)
            ids = list(s.pending)
            s.pending.clear()
            s.discard = False
            await _generate(s, ids, interject=False)
            if s.discard:                                   # 发到一半被新消息打断、剩下的丢了：把这轮的消息放回去，和新的一起重新回
                s.pending[:0] = ids
                s.discard = False
                s.round_start = time.time()
                continue
            s.said.clear()
            s.notice = None
            s.round_start = time.time() if s.pending else 0.0
    except Exception:  # noqa: BLE001
        log.exception("连发队列出错")
        broadcast(s, {"type": "error", "message": "回复出错了，再说一句试试"})
        s.pending.clear()
    finally:
        s.busy = False
        s.round_start = 0.0 if not s.pending else s.round_start


async def _collect(s: Session) -> None:
    """等这一轮的消息到齐：基础窗口 →（注意到我在打字的话）插一句 / 等我打完 / 最长等待。"""
    tier = current_tier()
    if not registry.flag("burst_enabled"):
        return
    if s.notice is None:
        s.notice = random.random() < min(1.0, cfg("burst_p_notice") * tier_k(tier))
    base = cfg("burst_base_s") * (cfg("burst_busy_window_mult") if tier == "busy" else 1.0)
    start = s.round_start or time.time()
    while time.time() < start + base:
        await asyncio.sleep(0.05)
    if not s.notice or not s.is_typing():
        return
    interjected = False
    while True:
        now = time.time()
        if now - start >= cfg("burst_max_wait_s"):
            return                                          # 等太久了：先回已有的
        if not s.is_typing(now) and s.idle_for(now) >= cfg("burst_idle_after_s"):
            return                                          # 我停下来了
        if not interjected and s.is_typing(now):
            interjected = True
            if random.random() < min(1.0, cfg("burst_p_interject") * tier_k(tier)) and s.pending:
                await _generate(s, list(s.pending), interject=True)
                continue
        await asyncio.sleep(0.1)


def _round_note(rows: list[dict[str, Any]], said: list[str], interject: bool, allow_quote: bool) -> str:
    lines = []
    if len(rows) > 1:
        lines.append("【这一轮】她连着发了几条（按顺序，前面的编号是系统加的，不是她写的）：" + "；".join(f"#{r['id']}「{r['content'][:60]}」" for r in rows) + "。把它们当成一轮一起回应，别漏掉前面的。")
    if said:
        lines.append("你刚才已经回过：" + " / ".join(f"「{t}」" for t in said[-6:]) + "。别把已经回答过的再答一遍，接着往下说。")
    if interject:
        lines.append("【先插一句】她还在打字、话还没说完：只回应她已经发出来的内容，一两句就好，不要猜她还没发的话，也不用把话说完；不要引用。")
    elif allow_quote:
        lines.append("【引用】这一轮可以引用她的某一条来回（不是必须）：用 JSON 数组输出整条回复，"
                     "比如 [{\"quote\": 编号, \"text\": \"回那一条的话\"}, {\"text\": \"接着说的话\"}]；"
                     "quote 填上面的编号（不带 #），不引用的项就不写 quote；别每句都引用。"
                     "她要你回的不是最后一条、或者她说了好几件事的时候，引用一下会更清楚。")
    return "\n".join(lines)


def _turn_text(rows: list[dict[str, Any]]) -> str:
    if len(rows) == 1:
        return rows[0]["content"]
    return "\n".join(f"#{r['id']} {r['content']}" + (f"（引用了：{r['quote_text'][:40]}）" if r.get("quote_text") else "") for r in rows)


async def _generate(s: Session, ids: list[int], interject: bool) -> None:
    """回一轮（或者先插一句）。消息内容现读数据库：回复前我改了 / 撤回了，以改后的为准。"""
    rows = [r for r in (db.get_message(i) for i in ids) if r and r.get("role") == "user"]
    if not rows:
        return
    s.busy = True
    usage.start_round()                         # 运行开销：这一轮（含回复后的记忆提取）的调用归在一起
    try:
        sid = s.sid
        text = _turn_text(rows)
        ooc = any(guards.is_ooc(r["content"]) for r in rows)
        many = len(rows) >= 3
        allow_quote = (not interject and not ooc and len(rows) >= 2
                       and random.random() < cfg("burst_p_quote_many" if many else "burst_p_quote"))
        quotes = {int(r["id"]): r for r in rows} if allow_quote else None
        note = _round_note(rows, s.said, interject, allow_quote)
        last = rows[-1]
        first_id = int(rows[0]["id"])
        messages, meta = context.build_context(
            sid, text, quote_text=last.get("quote_text") if len(rows) == 1 else None, quote_role=last.get("quote_role") if len(rows) == 1 else None,
            exclude_id=first_id, extra_note=note or None, ooc=ooc)
        prepare = after = None
        if not ooc and not interject:
            cmd = next(((r, writing.detect_command(r["content"])) for r in reversed(rows) if writing.detect_command(r["content"])[0]), None)
            if cmd:
                prepare, after = _writing_hooks(sid, cmd[0], cmd[1][0], cmd[1][1], text, first_id, note)
        hooks = {"interrupt": (lambda: s.discard), "quotes": quotes, "skip_memory": interject}
        assert _reply_events is not None, "burst.configure 没调"
        async for ev in _reply_events(sid, messages, meta, [], prepare=prepare, ooc=ooc, after=after, hooks=hooks):
            if ev.get("type") == "break" and ev.get("message"):
                s.said.append(ev["message"]["content"])
            if ev.get("type") == "done":
                ev = dict(ev, round=[int(r["id"]) for r in rows], interject=interject)
            broadcast(s, ev)
    finally:
        s.busy = False


def _writing_hooks(sid: str, row: dict[str, Any], intent: str, hint: Any, text: str, first_id: int, note: str):
    """这一轮里有「写一写 / 改一改」的命令：先真的去写 / 改，再回复（和 /api/chat 一样）。"""
    settings = db.get_settings()
    planned: dict[str, Any] = {}

    async def prepare():
        res = await writing.handle_command(settings, intent, row["content"], hint)
        if res.get("plan"):
            planned["plan"] = res["plan"]
        extra = "\n".join(x for x in (note, res["note"]) if x)
        msgs, _ = context.build_context(sid, text, exclude_id=first_id, extra_note=extra or None)
        return msgs, res["events"]

    after = (lambda: planned.get("plan") and life.begin_writing_now(planned["plan"], row["content"])) if intent == "write" else None
    return prepare, after
