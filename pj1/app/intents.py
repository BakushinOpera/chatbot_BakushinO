"""对话意图（见 docs/已完成的计划/对话意图.md）：她在聊天里说的打算、答应我的事，按概率真的影响她之后在后台的随机决定。

* 提取：每轮对话后和记忆提取合并成一次调用（memory.extract_rows），从她的回复里抽出打算 / 承诺，
  ``clean`` 做合理性检查（时间范围不能早于现在、最长几天；状态键、食物名要对得上），存进 ``chat_intents`` 表。
* 影响：吃饭方式（meals.due）、下一个状态（life.pick_state_key）、买东西（life._maybe_spend）、吃房间里的东西（tick）、
  作息（daily.is_sleeping 的「今晚早点睡 / 明天晚点起」）。**不打断她正在做的事**，只影响接下来的随机决定。
* 每个意图在时间范围里只判定一次遵守还是打破（``decide``，结果记在 decision 里）：
  随口 60% / 答应 85% / 坚定 95%，心情差、很累、下雨还要出门时低一点（系数在设置页）。
* 打破时记下原因，写进「她自己说过的事」类记忆、聊天上下文；按概率主动提一句（life 里登记的回调），没做完的可能留到之后（新的意图）。
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from datetime import datetime, timedelta
from typing import Any, Callable

from . import clutter, db, registry, states
from . import character as C

log = logging.getLogger(__name__)

KINDS = {"meal": "吃饭", "state": "做事", "spend": "买东西", "item": "吃房间里的东西", "sleep": "作息", "read": "读书"}
READ_SPAN_S = 30 * 60              # 「现在去看书 / 别看了」：提取完马上判定执行，记录上的时间范围就是接下来半小时
STRENGTHS = {"casual": "随口一说", "agreed": "答应了", "firm": "很坚定"}
STATUS_LABEL = {"pending": "等待中", "done": "做到了", "broken": "没做到", "expired": "过期了"}
MEAL_METHODS = ("self", "takeout", "family", "alone_out", "friend_out", "skip", "food")
OUT_METHODS = ("alone_out", "friend_out")
MAX_PER_TURN = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_intents (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT    NOT NULL,                 -- meal / state / spend / item / sleep
    content       TEXT    NOT NULL,                 -- JSON
    text          TEXT    NOT NULL,                 -- 原话的意思（「吃掉可颂」）
    start_at      REAL    NOT NULL,
    end_at        REAL    NOT NULL,
    exec_at       REAL,                             -- 吃东西这类：大概什么时候去做（时间范围里随机一个点）
    strength      TEXT    NOT NULL DEFAULT 'casual',
    source_msg_id INTEGER,
    status        TEXT    NOT NULL DEFAULT 'pending',
    decision      TEXT    NOT NULL DEFAULT '',      -- '' 还没判定 / keep / break
    reason        TEXT    NOT NULL DEFAULT '',
    mentioned     INTEGER NOT NULL DEFAULT 0,       -- 打破了以后，她跟我提过没有
    created_at    TEXT    NOT NULL,
    resolved_at   TEXT,
    ts            REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_intents_status ON chat_intents (status, end_at);
"""

_ready = False
# life 启动时登记：现在的心情 / 精力 / 天气（判定遵守率用）、打破时要做的事（主动提一句）
_ctx_fn: Callable[[], dict[str, Any]] | None = None
_on_broken: Callable[[dict[str, Any]], None] | None = None


def configure(ctx_fn: Callable[[], dict[str, Any]] | None = None, on_broken: Callable[[dict[str, Any]], None] | None = None) -> None:
    global _ctx_fn, _on_broken
    if ctx_fn is not None:
        _ctx_fn = ctx_fn
    if on_broken is not None:
        _on_broken = on_broken


def enabled() -> bool:
    return registry.flag("intent_enabled")


# ───────────────────────── 存取 ─────────────────────────
def _conn():
    global _ready
    conn = db.connect()
    if not _ready:
        with db._lock:
            conn.executescript(SCHEMA)
            conn.commit()
        _ready = True
    return conn


def _view(r: Any) -> dict[str, Any]:
    d = dict(r)
    try:
        d["content"] = json.loads(d.get("content") or "{}")
    except json.JSONDecodeError:
        d["content"] = {}
    return d


def _rows(sql: str, args: tuple = ()) -> list[dict[str, Any]]:
    conn = _conn()
    with db._lock:
        return [_view(r) for r in conn.execute(sql, args).fetchall()]


def _exec(sql: str, args: tuple = ()) -> int:
    conn = _conn()
    with db._lock:
        cur = conn.execute(sql, args)
        conn.commit()
        return cur.lastrowid if sql.lstrip().upper().startswith("INSERT") else cur.rowcount


def get(intent_id: int) -> dict[str, Any] | None:
    rows = _rows("SELECT * FROM chat_intents WHERE id = ?", (int(intent_id),))
    return rows[0] if rows else None


def list_all(limit: int = 60) -> list[dict[str, Any]]:
    return _rows("SELECT * FROM chat_intents ORDER BY id DESC LIMIT ?", (int(limit),))


def pending(kind: str | None = None) -> list[dict[str, Any]]:
    if kind:
        return _rows("SELECT * FROM chat_intents WHERE status = 'pending' AND kind = ? ORDER BY id", (kind,))
    return _rows("SELECT * FROM chat_intents WHERE status = 'pending' ORDER BY id")


def active(kind: str, now: float | None = None) -> list[dict[str, Any]]:
    """生效中的（在时间范围里、还没有结果的）。"""
    now = now if now is not None else time.time()
    return [it for it in pending(kind) if it["start_at"] <= now <= it["end_at"]]


def _key(kind: str, c: dict[str, Any]) -> str:
    """同一件事（新说的替换旧的）：同一顿饭 / 同一个状态 / 同一样食物 / 同一类东西 / 作息。"""
    if kind == "meal":
        return f"meal:{c.get('day')}:{c.get('meal')}"
    if kind == "state":
        return f"state:{c.get('state')}"
    if kind == "item":
        return f"item:{c.get('food_id')}"
    if kind == "spend":
        return f"spend:{c.get('cat') or c.get('item')}"
    return kind


def add(it: dict[str, Any], now: float | None = None, source_msg_id: int | None = None, rng: random.Random | None = None) -> dict[str, Any]:
    """存一条（已经 clean 过的）。同一件事之前还有没结果的：旧的作废（改主意了）。"""
    now = now if now is not None else time.time()
    rng = rng or random
    k = _key(it["kind"], it["content"])
    for old in pending(it["kind"]):
        if _key(old["kind"], old["content"]) == k:
            resolve(old["id"], "expired", "后来又改了主意", now=now)
    exec_at = None
    if it["kind"] == "item":                                   # 吃东西：时间范围的前半段里挑个时候
        span = max(60.0, it["end_at"] - it["start_at"])
        exec_at = it["start_at"] + rng.uniform(0, min(span * 0.5, 90 * 60))
    iid = _exec(
        "INSERT INTO chat_intents (kind, content, text, start_at, end_at, exec_at, strength, source_msg_id, created_at, ts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (it["kind"], json.dumps(it["content"], ensure_ascii=False), it["text"], it["start_at"], it["end_at"], exec_at,
         it["strength"], source_msg_id, datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S"), now))
    return get(iid) or {}


def resolve(intent_id: int, status: str, reason: str = "", now: float | None = None, notify: bool = True) -> dict[str, Any] | None:
    """记下结果。打破的：写进「她自己说过的事」，并交给 life 决定要不要主动提一句。"""
    it = get(intent_id)
    if not it or it["status"] != "pending":
        return None
    now = now if now is not None else time.time()
    _exec("UPDATE chat_intents SET status = ?, reason = ?, resolved_at = ? WHERE id = ?",
          (status, reason[:60], datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S"), int(intent_id)))
    it.update(status=status, reason=reason[:60])
    if status == "broken":
        try:
            db.add_memory(f"Phal 说过要{it['text']}，后来没做到：{reason}"[:80], category="phal_fact", source="intent")
        except Exception:  # noqa: BLE001
            log.exception("记下没做到的事出错")
        if notify and _on_broken is not None:
            try:
                _on_broken(it)
            except Exception:  # noqa: BLE001
                log.exception("打破意图的回调出错")
    return it


def mark_mentioned(ids: list[int]) -> None:
    for i in ids:
        _exec("UPDATE chat_intents SET mentioned = 1 WHERE id = ?", (int(i),))


def unmentioned_broken(hours: float = 12, now: float | None = None) -> list[dict[str, Any]]:
    now = now if now is not None else time.time()
    return [it for it in _rows("SELECT * FROM chat_intents WHERE status = 'broken' AND mentioned = 0 ORDER BY id DESC LIMIT 20")
            if _resolved_ts(it) > now - hours * 3600][::-1]


def _resolved_ts(it: dict[str, Any]) -> float:
    try:
        return datetime.strptime(it.get("resolved_at") or "", "%Y-%m-%d %H:%M:%S").timestamp()
    except ValueError:
        return 0.0


def clear() -> int:
    return _exec("DELETE FROM chat_intents")


# ───────────────────────── 判定：遵守还是打破 ─────────────────────────
def is_out(it: dict[str, Any]) -> bool:
    c = it["content"]
    if it["kind"] == "meal":
        return c.get("method") in OUT_METHODS
    if it["kind"] == "state":
        k = f"state_outside_{c.get('state')}"
        return bool(c.get("do")) and k in registry.BY_KEY and registry.flag(k)
    return False


def keep_prob(it: dict[str, Any], ctx: dict[str, Any] | None = None) -> float:
    ctx = ctx if ctx is not None else (_ctx_fn() if _ctx_fn else {})
    p = registry.num(f"intent_p_{it.get('strength') if it.get('strength') in STRENGTHS else 'casual'}")
    if ctx.get("negative"):
        p *= registry.num("intent_k_bad_mood")
    if float(ctx.get("energy", 0.6)) < 0.3:
        p *= registry.num("intent_k_tired")
    if ctx.get("raining") and is_out(it):
        p *= registry.num("intent_k_rain_out")
    return max(0.0, min(1.0, p))


def decide(it: dict[str, Any], ctx: dict[str, Any] | None = None, rng: random.Random | None = None) -> str:
    """遵守 keep / 打破 break：每个意图只掷一次，结果记下来。"""
    if it.get("decision"):
        return it["decision"]
    rng = rng or random
    d = "keep" if rng.random() < keep_prob(it, ctx) else "break"
    _exec("UPDATE chat_intents SET decision = ? WHERE id = ? AND decision = ''", (d, int(it["id"])))
    it["decision"] = d
    return d


def _why_not(it: dict[str, Any], rng: random.Random | None = None) -> str:
    """没做到的原因（不是被别的事打断的时候）：按心情 / 精力挑一句。"""
    rng = rng or random
    ctx = _ctx_fn() if _ctx_fn else {}
    if float(ctx.get("energy", 0.6)) < 0.3:
        return "太累了，没力气"
    if ctx.get("negative"):
        return "心情不太好，没心思"
    if ctx.get("raining") and is_out(it):
        return "下雨了，懒得出门"
    pool = {
        "state": ["后来没那个心思了", "一拖就忘了", "懒得动了"],
        "item": ["后来不太饿", "一忙就忘了"],
        "spend": ["想了想还是没买", "没顾上买"],
        "sleep": ["又磨蹭了一会儿", "没睡着，刷了会儿手机"],
        "meal": ["临时改了主意"],
        "read": ["拿起书又放下了，没看进去", "后来又不想看了"] if it["content"].get("do") else ["又多看了一会儿", "正看到要紧的地方，没停下"],
    }.get(it["kind"], ["后来改了主意"])
    return rng.choice(pool)


def expire_old(now: float | None = None) -> None:
    """过了时间范围还没结果的：说好「不做」的、做到了；该做没做的，算没做到（她没机会做的算过期）。"""
    now = now if now is not None else time.time()
    for it in pending():
        if now <= it["end_at"]:
            continue
        c = it["content"]
        if it["kind"] == "state" and not c.get("do"):
            resolve(it["id"], "done" if it.get("decision") != "break" else "broken", "" if it.get("decision") != "break" else _why_not(it), now)
        elif it["kind"] == "spend" and not c.get("do"):
            resolve(it["id"], "done", "", now)                 # 说了不买、这段时间也真没买（买了的话当时就记成没做到了）
        elif it["kind"] == "item" and it.get("decision") == "keep":
            resolve(it["id"], "broken", "一直没顾上", now)
        elif it["kind"] == "sleep" and it.get("decision") == "keep":
            resolve(it["id"], "done", "", now)
        elif it.get("decision") == "break":
            resolve(it["id"], "broken", _why_not(it), now)
        else:
            resolve(it["id"], "expired", "没找到机会", now, notify=False)


# ───────────────────────── 各处的钩子 ─────────────────────────
def meal_decision(meal: str, t: float, ctx: dict[str, Any], rng: random.Random | None = None) -> dict[str, Any] | None:
    """到了决定这顿怎么吃的时候：有说过的就按遵守概率执行。
    返回 {"intent": it, "keep": bool, "method": …, "foods": [...]}；没有相关的意图返回 None。"""
    if not enabled():
        return None
    day = datetime.fromtimestamp(t).strftime("%Y-%m-%d")
    for it in pending("meal"):
        c = it["content"]
        if c.get("meal") != meal or c.get("day") not in (None, "", day):
            continue
        if not (it["start_at"] - 3600 <= t <= it["end_at"] + 3600):
            continue
        keep = decide(it, rng=rng) == "keep"
        return {"intent": it, "keep": keep, "method": c.get("method"), "foods": c.get("foods") or []}
    return None


def home_bound(now: float, until: float) -> list[dict[str, Any]]:
    """这段时间里她说过要在家做的事（吃房间里的东西、在家吃饭、在家做某件事）：外出的事件 / 出去吃饭要更少发生。"""
    out = []
    for it in pending():
        if it["end_at"] < now or it["start_at"] > until:
            continue
        c = it["content"]
        if it["kind"] == "item" or (it["kind"] == "meal" and c.get("method") not in OUT_METHODS) or \
                (it["kind"] == "state" and c.get("do") and not is_out(it)):
            out.append(it)
    return out


def state_pick(weights: dict[str, float], now: float | None = None, rng: random.Random | None = None) -> str | None:
    """挑下一个状态之前：说过要做的（遵守的话）就做它；说过不做的（遵守的话）权重归零。返回要直接做的状态键或 None。
    weights 原地改。"""
    if not enabled():
        return None
    now = now if now is not None else time.time()
    rng = rng or random
    for it in active("state", now):
        key = it["content"].get("state")
        if key not in weights:
            continue
        keep = decide(it, rng=rng) == "keep"
        if it["content"].get("do"):
            if keep and weights.get(key, 0) > 0:
                resolve(it["id"], "done", "", now)
                return key
            if not keep:
                resolve(it["id"], "broken", _why_not(it, rng), now)
        elif keep:
            weights[key] = 0.0
    return None


def state_started(key: str, now: float | None = None) -> None:
    """（随机抽到的）某个状态开始了：说过「不做」但还是做了的，算没做到。"""
    for it in active("state", now):
        c = it["content"]
        if c.get("state") == key and not c.get("do"):
            label = states.BY_KEY[key].label if key in states.BY_KEY else key
            resolve(it["id"], "broken", f"还是没忍住去{label}了", now)


def items_due(now: float | None = None) -> list[dict[str, Any]]:
    """到了该去吃的时候的「吃掉房间里的 xx」（已判定）。"""
    now = now if now is not None else time.time()
    out = []
    for it in active("item", now):
        if float(it.get("exec_at") or it["start_at"]) <= now:
            decide(it)
            out.append(it)
    return out


def food_eaten(food_id: int, finished: bool, now: float | None = None) -> None:
    """她边做事边吃的时候刚好吃的是说好要吃掉的那份：吃完了就算做到（不用等到点再去吃）。"""
    if not finished:
        return
    for it in pending("item"):
        if it["content"].get("food_id") == food_id:
            resolve(it["id"], "done", "", now)


def spend_decision(now: float | None = None, rng: random.Random | None = None) -> dict[str, Any] | None:
    """要不要买东西之前：说过要买的（遵守）→ 返回 {"intent", "buy": True, cat, item, max_price}；
    说过不买的（遵守）→ {"intent", "buy": False, cat}（这一类不买）；打破的直接记下（不买 → 照常随机；要买 → 没买）。"""
    if not enabled():
        return None
    now = now if now is not None else time.time()
    for it in active("spend", now):
        c = it["content"]
        keep = decide(it, rng=rng) == "keep"
        if c.get("do"):
            if not keep:
                resolve(it["id"], "broken", _why_not(it, rng), now)
                continue
            return {"intent": it, "buy": True, "cat": c.get("cat"), "item": c.get("item") or "", "max_price": c.get("max_price")}
        return {"intent": it, "buy": False, "cat": c.get("cat"), "keep": keep}
    return None


def sleep_window(now: float | None = None) -> tuple[float, float] | None:
    """「今晚早点睡 / 明天晚点起」遵守的话：多睡的那一段 (从, 到)。只会让她多睡，不会让她熬夜。"""
    if not enabled():
        return None
    now = now if now is not None else time.time()
    for it in pending("sleep"):
        if not (it["start_at"] - 6 * 3600 <= now <= it["end_at"]):
            continue
        if decide(it) != "keep":
            continue
        c = it["content"]
        if c.get("from") and c.get("to"):
            return float(c["from"]), float(c["to"])
    return None


# ───────────────────────── 提取：给模型的说明 + 解析 ─────────────────────────
def prompt_block(now: float | None = None) -> str:
    """附在记忆提取的提示词后面：现在几点、她能做的事、房间里能吃的、今天的饭、能买的类别。"""
    from . import meals, money

    now = now if now is not None else time.time()
    d = datetime.fromtimestamp(now)
    st_list = "、".join(f"{s.key}={s.label}" for s in states.DEFS if s.random)
    foods = clutter.available(now)
    food_txt = "、".join(f"{f['name']}（还能吃 {f['portions_left']} 次，{_left_txt(f, now)}）" for f in foods) or "（没有）"
    snap = meals.snapshot()
    meal_txt = []
    for m in ("lunch", "dinner"):
        x = snap.get(m)
        if x and snap.get("day") == d.strftime("%Y-%m-%d"):
            stage = {"pending": "还没吃", "started": "在吃 / 吃过了", "skipped": "没吃"}.get(x.get("stage"), "")
            meal_txt.append(f"{meals.MEAL_LABEL[m]} {x['time']} {stage}" + (f"（{x['method_label']}）" if x.get("method_label") and stage != "还没吃" else ""))
    cats = "、".join(f"{k}={money.LABEL[k]}" for k in money.CAT_KEYS)
    return INTENT_PROMPT.format(now=d.strftime("%Y-%m-%d %H:%M"), weekday="周" + "一二三四五六日"[d.weekday()], n=MAX_PER_TURN,
                                states=st_list, foods=food_txt, meals="；".join(meal_txt) or "（还没排）", cats=cats)


def _left_txt(f: dict[str, Any], now: float) -> str:
    h = (float(f.get("expires_at") or now) - now) / 3600
    return f"{h:.0f} 小时后过期" if h < 48 else f"{h / 24:.0f} 天后过期"


INTENT_PROMPT = """
另外，抽出 Phal 在这段对话里说的「打算 / 答应的事」（intents）：她自己说的（「待会儿吃可颂」「等下去看书」「今晚早点睡」「晚饭不吃了」），
也包括「她」让 Phal 做什么、Phal 答应了的（「好哦」「行吧」——要结合「她」上一句在说什么）。
只抽具体的、Phal 自己能做到的、接下来几天之内的；开玩笑、假设、已经做完的不算；一轮最多 {n} 条。
现在是 {now}（{weekday}）。

kind 和 content：
- meal：某一顿饭怎么吃。content = {{"meal": "lunch|dinner", "method": "self 自己做|takeout 外卖|family 和家人吃|alone_out 一个人出去吃|friend_out 和朋友出去吃|skip 不吃|food 拿房间里的东西当这顿饭", "dish": "想吃的，可空", "foods": ["method=food 时：房间里的食物名"]}}
- state：做 / 不做某件事。content = {{"state": "下面列表里的键", "do": true 或 false}}
- spend：买 / 不买东西。content = {{"cat": "类别键，可空", "item": "买什么", "max_price": 数字或 null, "do": true 或 false}}
- item：吃掉房间里的某样食物（不是当一顿饭）。content = {{"food": "房间里的食物名", "portions": 吃几次（全吃掉就写还剩的次数）}}
- sleep：早点睡 / 晚点起。content = {{"sleep_by": "HH:MM 或空", "wake_at": "HH:MM 或空"}}
- read：现在就去看书 / 现在别看了（「她」叫 Phal 读会儿书、休息一下，Phal 答应了；或者 Phal 自己说现在就去看 / 不看了）。content = {{"do": true 去看 | false 停下}}；start / end 不用写。Phal 拒绝了的不算。
start / end：生效的时间范围（"YYYY-MM-DD HH:MM"）：「待会儿」≈ 接下来一两个小时，「晚上」= 今晚，「明天」= 明天白天。
strength：casual 随口一说 / agreed 答应了她 / firm 很坚定。text：一句话概括（「吃掉可颂」「晚饭不吃了」），12 字以内。

Phal 能做的事（键=名字）：{states}
房间里能吃的：{foods}
今天的饭：{meals}
买东西的类别（键=名字）：{cats}

这时整体输出一个 JSON 对象，不要别的：
{{"memories": [上面说的记忆条目], "intents": [{{"kind": "…", "content": {{…}}, "start": "…", "end": "…", "strength": "…", "text": "…"}}]}}
没有就输出空数组。"""


def _parse_time(v: Any, now: float, default: float) -> float:
    if v in (None, ""):
        return default
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("T", " ").replace("/", "-")
    base = datetime.fromtimestamp(now)
    for fmt, n in (("%Y-%m-%d %H:%M", 16), ("%Y-%m-%d", 10)):
        try:
            return datetime.strptime(s[:n], fmt).timestamp()
        except ValueError:
            pass
    m = re.fullmatch(r"(\d{1,2})[:：](\d{2})", s)
    if m:
        t = base.replace(hour=min(23, int(m.group(1))), minute=min(59, int(m.group(2))), second=0, microsecond=0)
        if t.timestamp() < now - 3600:                       # 比现在早一个多小时：说的是明天
            t += timedelta(days=1)
        return t.timestamp()
    return default


def _clock(v: Any, now: float, after: float) -> float | None:
    """「HH:MM」→ after 之后最近的那个时刻。"""
    m = re.fullmatch(r"\s*(\d{1,2})[:：](\d{2})\s*", str(v or ""))
    if not m:
        return None
    t = datetime.fromtimestamp(after).replace(hour=min(23, int(m.group(1))), minute=min(59, int(m.group(2))), second=0, microsecond=0)
    if t.timestamp() < after - 60:
        t += timedelta(days=1)
    return t.timestamp()


def _match_food(name: Any, foods: list[dict[str, Any]]) -> dict[str, Any] | None:
    n = str(name or "").strip()
    if not n:
        return None
    for f in foods:
        if f["name"] == n:
            return f
    for f in foods:
        if n in f["name"] or f["name"] in n:
            return f
    return None


def clean(raw: dict[str, Any], now: float | None = None) -> dict[str, Any] | None:
    """模型给的一条 → 检查过的 {kind, content, text, start_at, end_at, strength}；对不上的返回 None。"""
    now = now if now is not None else time.time()
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("kind") or "").strip()
    c = raw.get("content") if isinstance(raw.get("content"), dict) else {}
    if kind not in KINDS:
        return None
    strength = str(raw.get("strength") or "casual").strip()
    strength = strength if strength in STRENGTHS else "casual"
    text = str(raw.get("text") or "").strip()[:24]
    max_s = registry.num("intent_max_days") * 86400
    start = _parse_time(raw.get("start"), now, now)
    end = _parse_time(raw.get("end"), now, start + 3 * 3600)
    start = max(start, now)
    if start > now + max_s:
        return None
    if end <= start:
        end = start + 2 * 3600
    end = min(end, start + max_s, now + max_s)
    foods = clutter.available(now)
    out: dict[str, Any]
    if kind == "meal":
        meal = str(c.get("meal") or "").strip()
        if meal not in ("lunch", "dinner"):
            meal = "lunch" if datetime.fromtimestamp(start).hour < 15 else "dinner"
        method = str(c.get("method") or "").strip()
        if method not in MEAL_METHODS:
            return None
        picked = []
        for nm in c.get("foods") or []:
            f = _match_food(nm, foods)
            if f and f["id"] not in [p["id"] for p in picked]:
                picked.append(f)
        if method == "food" and not picked:
            return None
        out = {"meal": meal, "method": method, "dish": str(c.get("dish") or "").strip()[:20],
               "foods": [{"id": f["id"], "name": f["name"], "portions": f["portions_left"]} for f in picked],
               "day": datetime.fromtimestamp(start).strftime("%Y-%m-%d")}
        text = text or {"skip": f"{_meal_label(meal)}不吃了", "food": f"拿{'和'.join(f['name'] for f in picked)}当{_meal_label(meal)}"}.get(method, f"{_meal_label(meal)}{method}")
    elif kind == "state":
        key = str(c.get("state") or "").strip()
        if key not in states.BY_KEY or not states.BY_KEY[key].random:
            return None
        do = c.get("do", True) not in (False, "false", 0, "0")
        out = {"state": key, "do": do}
        text = text or (("去" if do else "不") + states.BY_KEY[key].label)
    elif kind == "spend":
        from . import money

        cat = str(c.get("cat") or "").strip()
        cat = cat if cat in money.CAT_KEYS else ""
        item = str(c.get("item") or "").strip()[:20]
        if not cat and not item:
            return None
        try:
            mp = float(c["max_price"]) if c.get("max_price") not in (None, "", "null") else None
        except (TypeError, ValueError):
            mp = None
        do = c.get("do", True) not in (False, "false", 0, "0")
        if do and not cat:
            return None                                         # 要买的得知道是哪一类（记账、放进房间要用）
        out = {"cat": cat, "item": item, "max_price": mp, "do": do}
        text = text or (("买" if do else "不买") + (item or money.LABEL.get(cat, "")))
    elif kind == "item":
        f = _match_food(c.get("food"), foods)
        if not f:
            return None
        try:
            n = int(c.get("portions") or 1)
        except (TypeError, ValueError):
            n = 1
        out = {"food_id": f["id"], "food": f["name"], "portions": max(1, min(n, int(f["portions_left"])))}
        text = text or f"吃掉{f['name']}"
    elif kind == "read":
        do = c.get("do", True) not in (False, "false", 0, "0")
        out = {"do": do}
        start, end = now, now + READ_SPAN_S
        text = text or ("去看书" if do else "不看书了")
    else:  # sleep
        from . import daily

        night = _clock(f"{daily.sleep_hour():02d}:00", now, now - 6 * 3600) or now
        wake_reg = _clock(f"{daily.wake_hour():02d}:00", now, max(now, night)) or night + 9 * 3600
        frm = _clock(c.get("sleep_by"), now, now) if c.get("sleep_by") else None
        to = _clock(c.get("wake_at"), now, max(now, night)) if c.get("wake_at") else None
        if frm is not None and not (now + 300 <= frm < night and night - frm <= 3 * 3600):
            frm = None                                          # 只认「比平时早、最多早 3 小时」
        if to is not None and not (wake_reg < to <= wake_reg + 3 * 3600):
            to = None                                           # 只认「比平时晚、最多晚 3 小时」
        if frm is None and to is None:
            return None
        out = {"sleep_by": c.get("sleep_by") if frm else "", "wake_at": c.get("wake_at") if to else "",
               "from": frm or night, "to": to or wake_reg}
        start, end = now, (to or wake_reg)
        text = text or ("早点睡" if frm else "明天晚点起")
    return {"kind": kind, "content": out, "text": text or KINDS[kind], "start_at": start, "end_at": end, "strength": strength}


def _meal_label(meal: str) -> str:
    return {"lunch": "午饭", "dinner": "晚饭"}.get(meal, "饭")


def apply_raw(items: list[Any], now: float | None = None, source_msg_id: int | None = None) -> list[dict[str, Any]]:
    """模型抽出来的一串 → 检查 → 存。"""
    if not enabled():
        return []
    out = []
    for raw in (items or [])[:MAX_PER_TURN]:
        it = clean(raw, now)
        if it:
            saved = add(it, now, source_msg_id)
            if saved.get("kind") == "read":
                run_read(saved, now)
            out.append(saved)
    return out


def run_read(it: dict[str, Any], now: float | None = None, rng: random.Random | None = None) -> str:
    """「现在去看书 / 别看了」：提取完马上判定（照强度的概率），照做就开始 / 停下。返回结果标记（测试 / 日志用）。
    这一轮上下文已经让她拒绝了的（模型要是照样抽出了意图），不算数。"""
    from . import life

    now = now if now is not None else time.time()
    do = bool(it["content"].get("do"))
    if do and life.refused_read_this_turn():
        resolve(it["id"], "expired", "她当时没答应", now, notify=False)
        return "refused"
    if decide(it, rng=rng) != "keep":
        resolve(it["id"], "broken", _why_not(it, rng), now)
        return "broken"
    res = life.start_reading_now(now) if do else life.stop_reading_now()
    if res in ("started", "already", "stopped", "not_reading"):
        resolve(it["id"], "done", "", now)
    else:
        resolve(it["id"], "broken", {"full": "今天的书已经读满了", "no_book": "书架上没有能读的书"}.get(res, "没读成"), now)
    return res


# ───────────────────────── 上下文 ─────────────────────────
def _hm(ts: float, now: float) -> str:
    d, n = datetime.fromtimestamp(ts), datetime.fromtimestamp(now)
    day = "" if d.date() == n.date() else ("明天" if (d.date() - n.date()).days == 1 else f"{d.month}月{d.day}日")
    return f"{day}{d:%H:%M}"


def context_lines(now: float | None = None) -> list[str]:
    """聊天 / 主动消息的上下文：生效中的、最近没做到的、最近做到的。"""
    if not enabled():
        return []
    now = now if now is not None else time.time()
    lines = []
    act = [it for it in pending() if it["start_at"] - 6 * 3600 <= now]
    if act:
        lines.append("- 你说过的打算 / 答应她的事（还没到点或正在做；照着做，别马上说出相反的安排，比如说了在家吃又说出门了）：" +
                     "；".join(f"{it['text']}（{_hm(it['start_at'], now)}~{_hm(it['end_at'], now)}）" for it in act[:5]))
    recent = _rows("SELECT * FROM chat_intents WHERE status IN ('broken', 'done') ORDER BY id DESC LIMIT 12")
    broken = [it for it in recent if it["status"] == "broken" and _resolved_ts(it) > now - 12 * 3600][:3]
    if broken:
        lines.append("- 你说过但没做到的（被问起就自然解释原因，别装作没说过）：" + "；".join(f"本来说{it['text']}，{it['reason']}" for it in broken))
    done = [it for it in recent if it["status"] == "done" and _resolved_ts(it) > now - 6 * 3600][:3]
    if done:
        lines.append("- 你说到做到了的：" + "、".join(it["text"] for it in done))
    return lines


def proactive_note(now: float | None = None) -> tuple[str, list[int]]:
    """主动消息的提示词里附一句：她说过的、还在生效的打算（别说出相反的安排），以及刚没做到、还没跟她提过的（可以顺口提一句）。
    返回 (这句话, 里面提到的「没做到」的意图 id)。"""
    if not enabled():
        return "", []
    now = now if now is not None else time.time()
    act = [it for it in pending() if it["start_at"] - 3600 <= now][:3]
    broken = unmentioned_broken(12, now)[:2]
    bits = []
    if act:
        bits.append("你跟她说过的打算：" + "；".join(it["text"] for it in act) + "（别说出相反的安排；做不到了就说清楚为什么）")
    if broken:
        bits.append("你说过但没做到、还没跟她提的：" + "；".join(f"本来说{it['text']}，{it['reason']}" for it in broken) + "（可以顺口提一句）")
    return ("\n" + "\n".join(bits) + "\n") if bits else "", [it["id"] for it in broken]


def limits_lines(now: float | None = None) -> list[str]:
    """（防止乱许诺）现在有哪些限制：她让你做什么的时候，做不到的就别答应，或者说清楚。"""
    from . import daily, family, money

    if not enabled():
        return []
    now = now if now is not None else time.time()
    bits = []
    d = datetime.fromtimestamp(now)
    sh = daily.sleep_hour()
    if daily.sleep_enabled() and 0 <= (sh - d.hour - d.minute / 60) % 24 <= 1.0:
        bits.append(f"快到睡觉时间了（{sh} 点睡）")
    try:
        if money.enabled() and money.tier() != "ok":
            bits.append(f"这个月零花钱{money.TIER_LABEL[money.tier()]}（还剩 {money.fmt(money.balance())} {C.unit()}）")
    except Exception:  # noqa: BLE001
        pass
    if family.away():
        bits.append("爸妈出差不在家")
    try:
        if float(db.get_settings().get("life_sick_until") or 0) > now:
            bits.append("正在生病，不能出门")
    except ValueError:
        pass
    try:
        from . import life

        if life.read_left(now) <= 60:
            bits.append("今天的书已经读满了，不会再读")
    except Exception:  # noqa: BLE001
        pass
    gone = [f["name"] for f in clutter.active(now) if f["expired"]][:3]
    if gone:
        bits.append("这些东西已经过期了，不能吃：" + "、".join(gone))
    if not bits:
        return []
    return ["- 现在的限制（她让你做什么时，做不到的别答应，或者说清楚为什么做不到）：" + "；".join(bits)]
