"""Phal 的「生活」：状态机。

* 状态切换、持续时长、要不要偷懒、要不要分享，**全部由这里的代码随机决定**（带权重），不交给模型。
* 时间只在网页开着时流动：页面每 30 秒来 tick 一次，后端按「两次 tick 之间过去的时间」推进，
  单次最多算 120 秒 —— 网页关掉就冻结，再打开接着来，不补算离线时间。
  例外：心情和精力（mood.py）按真实时间走，关着应用也在回落 / 按钟点变。
* 我和她聊天时（最近 2 分钟我发过消息）活动暂停，聊完再接着。
* 氛围文案用本地文案池；只有看书、创作、主动消息才会调用模型（见 reading.py）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import re
import time
from datetime import datetime
from typing import Any

from . import books as bk
from . import character as C
from . import daily, db, events, festivals, guards, letters, library as lib, llm, money, planner, meals, people, reading, registry, states, weather, writing
from . import behavior as B
from . import clutter, family
from . import intents, notebook, notebook_write, places, whylog
from . import mood as M

log = logging.getLogger(__name__)

TICK_CAP_S = 120          # 单次 tick 最多推进多少秒（网页关着就冻结）
CHAT_PAUSE_S = 120        # 我发消息之后这么久没动静，才算聊完
CHARS_PER_MIN = (300, 500)
CHUNK_CHARS = (2200, 3400)
SLACK_CHANCE = 0.10       # 每读完一块，走神 / 偷懒的概率
QUIT_EARLY_CHANCE = 0.18  # 一开始就打算「看一会儿就不看了」的概率
POST_READ_THOUGHT = 0.40  # 看完一轮后发读后感的概率
SHARE_DRAFT_DONE = 0.35
SHARE_DRAFT_ABANDONED = 0.10

# 状态定义：以后想加状态，在这里加一项 + 文案池就行
STATES: dict[str, dict[str, Any]] = {
    d.key: {"label": d.label, "dur": (d.dur[0] * 60, d.dur[1] * 60), "weight": float(d.weight) if d.random else 0.0}
    for d in states.DEFS                  # 所有状态的定义在 states.py（默认值），设置页里每项都能改
}
STATES["sleep"] = {"label": "睡觉", "dur": (0, 0), "weight": 0}      # 不参与随机，由作息（daily.py）决定

NO_DOC_BLURBS = ["想写点什么，可手边没有本子", "对着空气构思", "在脑子里写了一段，没法落到纸上"]
SLEEP_BLURBS = ["睡着了", "睡得很沉", "翻了个身，没醒"]
WAKE_BLURBS = ["刚醒，还有点迷糊", "醒了，先去泡点茶", "窗帘缝里漏进来一点光"]

# 读书时的「感受」→ 心情（只有这几种会真的影响心情，平淡不算）
FEELING_MOOD = {"兴奋": "兴奋", "有意思": "雀跃", "气人": "烦躁", "难过": "忧郁", "困惑": "心不在焉"}
FEELING_TEXT = {
    "兴奋": "读《{t}》读到特别精彩的地方，一直兴奋着",
    "有意思": "读《{t}》读到有意思的地方，心情不错",
    "气人": "读《{t}》读到气人的情节，有点烦",
    "难过": "读《{t}》读到难过的段落，心里发沉",
    "困惑": "《{t}》里有处没看懂，一直在琢磨",
}

BLURBS: dict[str, list[str]] = {
    "tea": ["泡了一壶茶，还烫", "茶凉了一半", "在看茶叶慢慢沉下去", "第二泡了", "茶有点涩，没管它", "杯子捂在手里"],
    "daze": ["在看窗外", "盯着天花板，脑子里空空的", "想到什么，又忘了", "在数地板的纹路", "发呆。别叫我", "刚才在想什么来着", "在听很远的地方有什么声音"],
    # 刷视频 / 打游戏：只写「在做什么」，具体内容留给她被问到时现编（编出来的会进长期记忆，之后保持一致）
    "video": ["在刷视频", "刷着刷着停不下来", "手指在划，脑子没跟上", "又刷到一个，再看一个就睡"],
    "game": ["在打游戏", "打得有点投入", "在打游戏，别叫我", "手游，随便玩玩"],
    "create": ["对着空白的纸坐着", "写了一行，划掉了", "在找一个合适的词", "握着笔，还没想好", "试图写点什么", "改了三遍，又改回第一遍"],
}
for _d in states.DEFS:
    if _d.blurbs and _d.key not in BLURBS:
        BLURBS[_d.key] = list(_d.blurbs)
SLACK_BLURBS = ["看着看着走神了", "书扣在膝盖上，人不知道去哪了", "这段有点看不进去"]
WISHES_GENERIC = [
    "最近没什么可看的了。你那有没有书借我看看",
    "想看点短一点的，你那有没有",
    "书架快见底了，有推荐的吗",
]

# --------------------------------------------------------------------------
# 读书：每天累计上限 + 每天随机决定读不读（按本地日期重置）
# --------------------------------------------------------------------------
DEFAULT_READ_CAP_MIN = 30        # 每天累计最多读多少分钟（settings: life_read_cap_min）
DEFAULT_READ_DAY_PROB = 0.7      # 每天开始时「今天读不读」的概率（settings: life_read_day_prob）


def _read_cap_s() -> float:
    try:
        return max(0.0, registry.num("life_read_cap_min")) * 60
    except (TypeError, ValueError):
        return DEFAULT_READ_CAP_MIN * 60.0


def _read_prob() -> float:
    try:
        return min(1.0, max(0.0, registry.num("life_read_day_prob")))
    except (TypeError, ValueError):
        return DEFAULT_READ_DAY_PROB


def read_day(now: float | None = None) -> dict[str, Any]:
    """今天的读书账：{date, reads（今天读不读）, seconds（已读多少秒）}。新的一天（本地日期）自动重置并重新随机。"""
    today = daily.today_str(now)
    try:
        data = json.loads(db.get_settings().get("life_read_day") or "{}")
    except json.JSONDecodeError:
        data = {}
    if data.get("date") != today:
        data = {"date": today, "reads": random.random() < _read_prob(), "seconds": 0.0}
        db.save_settings({"life_read_day": json.dumps(data)})
    return data


def read_left(now: float | None = None) -> float:
    return max(0.0, _read_cap_s() - float(read_day(now).get("seconds", 0.0)))


def add_read_seconds(dt: float, now: float | None = None) -> None:
    data = read_day(now)
    data["seconds"] = float(data.get("seconds", 0.0)) + dt
    db.save_settings({"life_read_day": json.dumps(data)})


_lock = asyncio.Lock()
_busy = False                      # 有一次模型调用在后台跑，别叠着开新的
_background: set[asyncio.Task] = set()


# --------------------------------------------------------------------------
# 持久化
# --------------------------------------------------------------------------
def load_state() -> dict[str, Any] | None:
    raw = db.get_settings().get("life_state") or ""
    try:
        data = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) and data.get("key") in STATES else None


def save_state(st: dict[str, Any]) -> None:
    db.save_settings({"life_state": json.dumps(st, ensure_ascii=False)})


def enabled() -> bool:
    return db.get_settings().get("life_enabled", "1") != "0"


# --------------------------------------------------------------------------
# 心情和精力（v8，见 mood.py）：事件推一下情绪 / 精力，之后按真实时间慢慢回落到今天的基线
# --------------------------------------------------------------------------
_ticking = False


def register_event(kind: str, text: str, mood: str | None = None, minutes: int | None = None) -> None:
    """记一件事；带 mood（心情词）的话，按这个词推一下她的情绪和精力（之后慢慢回落）。minutes 以前用来定情绪持续多久，现在不用了。"""
    db.add_event(daily.today_str(), kind, text, mood)
    apply_mood(mood, text, src=kind)


def apply_mood(word: str | None, why: str, minutes: int | None = None, src: str = "", scale: float = 1.0) -> None:
    """按心情词推一下（事件本身已经记过了）。同一类事（src + 词）几天内反复发生，推得越来越轻。"""
    if not word:
        return
    p = M.params()
    dv, de, flavor = M.word_impulse(word, p, scale)
    st = M.load()
    M.push(st, time.time(), p, dv, de, f"{src}:{word}" if src else word, why, flavor)
    M.save(st)


def push_energy(de: float, src: str, now: float | None = None) -> None:
    """做完一件事：精力加减一点（之后慢慢回落）。"""
    if not de:
        return
    p = M.params()
    st = M.load()
    M.push(st, now or time.time(), p, 0.0, de, "", "")
    M.save(st)


def live_mood(st: dict[str, Any] | None = None) -> tuple[str, str]:
    """她此刻的心情词和原因（原因 = 影响还很大的那件事，否则是今天心情的原因）。st 参数留着兼容，不用了。"""
    v = mood_view()
    return v["word"], v["why"]


def mood_view(now: float | None = None) -> dict[str, Any]:
    """此刻的 v（情绪）、e（精力）、心情词、原因、精力的说法。顺手记下心情词（下次换词时的滞回要用）。"""
    now = now or time.time()
    p = M.params()
    mst = M.load()
    if not mst.get("day"):                                   # 还没过过一天（刚升级）：先按今天现定
        _mood_new_day(mst, now, p)
        M.save(mst)
    elif mst["day"] != M.day_of(now, p) and not daily.is_sleeping(now):
        _mood_new_day(mst, now, p)                           # 已经是新的一天、后台还没推进（比如刚打开就聊天）：先换天，免得心情原因和小打算还是昨天的
        M.save(mst)
    elif "intents" not in mst:                               # 刚升级到 v9（今天已经换过天）：今天的小打算现在补上
        mst["intents"] = B.make_intents([], _intent_ctx(), p, random)
        M.save(mst)
    # 节日当天情绪有下限（当天新加的节日也立刻算）
    mst["festival_floor"] = p["mood_festival_floor"] if festivals.on_date(daily.today_str(now)) else None
    before = json.dumps(mst.get("qword"), ensure_ascii=False)
    out = M.view(mst, now, p)
    if json.dumps(mst.get("qword"), ensure_ascii=False) != before:        # 换了区域、换了词：记下来（下次接着用这个词）
        M.save(mst)
    return out


def _mood_ctx(mst: dict[str, Any], now: float) -> dict[str, Any]:
    """算今天基线用的外部情况：天气（和连着阴雨几天）、季节、星期几、今天过不过节。"""
    d = datetime.fromtimestamp(now)
    kind = weather.kind("her") or "clear"
    hist = [h for h in mst.get("wx_hist", []) if isinstance(h, list) and len(h) == 2][-6:]
    streak = 1
    for _day, k in reversed(hist):
        if k in ("rain", "thunder", "overcast", "fog") and kind in ("rain", "thunder", "overcast", "fog"):
            streak += 1
        else:
            break
    mst["wx_hist"] = (hist + [[d.date().isoformat(), kind]])[-7:]
    return {"kind": kind, "rain_streak": streak, "season": weather.season(d.month), "weekday": d.weekday(),
            "festival": bool(festivals.on_date(d.date().isoformat()))}


def _mood_new_day(mst: dict[str, Any], now: float, p: dict[str, Any]) -> None:
    """换到新的一天：结算昨天存进日历、算今天的基线、让模型把原因写成一句话。"""
    y = M.new_day(mst, now, _mood_ctx(mst, now), p, random)
    # v9：今天的小打算（接着昨天没做完的）；下雨 / 下雪的一天多一点灵感
    mst["intents"] = B.make_intents(mst.get("intents") or [], _intent_ctx(), p, random)
    if weather.kind("her") in ("rain", "thunder", "snow"):
        B.add_insp(mst, now, float(p["insp_weather"]), p)
    if y.get("day") and y.get("avg_v") is not None:
        db.set_mood_numbers(y["day"], v=y["avg_v"], e=y["avg_e"], base=y.get("base_v"), sleep=y.get("sleep_q"))
    day = mst["day"]
    word = M.label(mst.get("base_v", 0.0), M.rhythm(10.0, p), None, p)       # 日历上的「底色」：基线 + 上午的精力
    row = db.get_mood(day)
    if not row or row.get("kind") != "summary":
        db.upsert_mood(day, word, "")
    db.set_mood_numbers(day, base=mst.get("base_v"), sleep=mst.get("sleep_q"))
    if not llm.is_echo(db.get_settings()):
        _spawn(_reason_job(day, list(mst.get("factors") or []), word))


def _intent_ctx() -> dict[str, Any]:
    """定小打算用的：手上能写的文档、在看的书、一个朋友的名字、今天能不能出门。"""
    docs = [d for d in writing.list_docs("phal") if not d.get("full")]
    books = readable_books()
    reading_now = [b for b in books if b["phal_status"] == "reading"] or books
    friends = _friend_names()
    return {"doc": max(docs, key=lambda d: d["updated_at"])["title"] if docs else "", "book": reading_now[0]["title"] if reading_now else "",
            "friend": random.choice(friends) if friends else "", "out_ok": not weather.is_raining() and not weather.is_snowing()}


def intents_block() -> str:
    """给上下文：今天的小打算（做了没）。"""
    its = M.load().get("intents") or []
    if not its:
        return ""
    return "【你今天的小打算】" + "；".join(f"{it['text']}（{'做了' if it.get('done') else '还没做'}{'，昨天就想做了' if it.get('carried') else ''}）" for it in its) + "。（只是随手的打算，被问起或者自然聊到时可以提，别硬说。）"


REASON_PROMPT = """你是「{name}」。你的口吻（人设里的说话方式）：
{voice}

今天早上你的心情是「{word}」。下面是影响今天心情的几件事（按影响从大到小，正数让心情变好、负数让心情变差）：
{factors}

用第一人称写一句话，说说今天心情为什么是这样，像随手写的日记，25 字以内；只挑影响最大的一两件说，别全列出来，
别写成分析，不要 emoji。只输出这句话。"""


async def _reason_job(day: str, factors: list[list[Any]], word: str) -> None:
    """「今天心情的原因」：模型润色成一句话，存进心情状态（给上下文）和日历（当天那一句）。"""
    try:
        settings = db.get_settings()
        from . import persona as persona_lib

        lines = "\n".join(f"- {t}（{c:+.2f}）" for _n, c, t in sorted(factors, key=lambda x: -abs(float(x[1]))) if abs(float(c)) >= 0.02) or "- 没什么特别的"
        raw = await llm.complete(settings, [{"role": "system", "content": REASON_PROMPT.format(
            name=C.display_name(settings), voice=persona_lib.voice_excerpt(settings.get("system_prompt") or "", 800),
            word=word, factors=lines)}], temperature=0.8)
        line = guards.strip_exclaim(str(raw or "")).strip().strip("「」\"' ").splitlines()[0][:40] if raw else ""
    except Exception as exc:  # noqa: BLE001 —— 写不出来就用本地拼的那句
        log.info("写今天心情的原因失败：%s", exc)
        return
    if not line:
        return
    mst = M.load()
    if mst.get("day") == day:
        mst["reason"], mst["reason_src"] = line, "model"
        M.save(mst)
    row = db.get_mood(day)
    if row and row.get("kind") != "summary":
        db.upsert_mood(day, row["mood"], line)
        db.set_mood_numbers(day, base=mst.get("base_v"), sleep=mst.get("sleep_q"))


def mood_tick(now: float, awake: bool) -> None:
    """每次 tick：到了新的一天（起床之后）就换天；记一笔当天的统计；看看要不要挂上「想念 / 孤单 / 嘴馋」。"""
    p = M.params()
    mst = M.load()
    if awake and mst.get("day") != M.day_of(now, p):
        _mood_new_day(mst, now, p)
    M.sample(mst, now, p, awake)
    last_user = db.last_user_message_ts()
    if last_user and last_user > float(mst.get("chat_seen") or 0):            # 和你聊了：加一点灵感（隔一阵才算新的一次）
        if last_user - float(mst.get("chat_insp_at") or 0) > float(p["insp_chat_gap_min"]) * 60:
            B.add_insp(mst, now, float(p["insp_chat"]), p)
            mst["chat_insp_at"] = last_user
        mst["chat_seen"] = last_user
    if awake:
        fl = mst.get("flavor") or {}
        free = not fl or now >= float(fl.get("until", 0))
        if free and p["mood_miss_hours"] > 0:
            if M.miss_due(mst, db.last_user_message_ts(), now, p):
                v, _e = M.value(mst, now, p)
                M.push(mst, now, p, *M.FLAVORS["想念" if v > -0.05 else "孤单"][:2], "miss", "好久没和她说话了", "想念" if v > -0.05 else "孤单")
                mst["miss_at"] = now
                free = False
        if free and p["mood_snack_min"] > 0:
            plan = meals._load()                                    # noqa: SLF001 —— 只读今天的饭点计划
            for m in (plan.get("lunch"), plan.get("dinner")):
                if m and m.get("stage") == "pending" and 0 < float(m.get("t", 0)) - now < p["mood_snack_min"] * 60 and M.value(mst, now, p)[1] > 0.35:
                    if mst.get("snack_for") != m.get("t"):
                        mst["snack_for"] = m.get("t")
                        M.push(mst, now, p, *M.FLAVORS["嘴馋"][:2], "", "快到饭点了", "嘴馋")
                    break
    M.save(mst)


# --------------------------------------------------------------------------
# 选状态 / 选书
# --------------------------------------------------------------------------
def readable_books() -> list[dict[str, Any]]:
    return [b for b in lib.list_books() if b["phal_status"] in ("unread", "reading", "paused") and b["chars"] > 0]


SICK_ALLOWED = {"sick": 60.0, "tea": 8.0, "daze": 8.0, "video": 8.0, "music": 6.0, "anime": 6.0}      # 生病时只会做这些


def _friend_names() -> list[str]:
    return [p["name"] for p in people.list_people(drafts=False) if p["kind"] == "friend"]


def state_allowed(key: str, hour: int, raining: bool, snowing: bool, has_friend: bool) -> bool:
    """这个状态现在能不能出现：不参与随机的不行；时间段、天气条件不满足的不行；和朋友出去得先有朋友。"""
    d = states.BY_KEY.get(key)
    if d is None or not d.random:
        return False
    lo, hi = registry.state_window(key)
    if not lo <= hour < (hi if hi > 0 else 24):
        return False
    if not states.weather_ok(str(registry.get(f"state_weather_{key}")), raining, snowing):
        return False
    if key in family.AWAY_STATES and family.away():
        return False                                       # 爸妈出差：没人一起拼图，也没人叫她跑腿
    return has_friend or not d.needs_friend


def pick_state_key(prev: str | None, forced: str | None = None, soft: bool = False, avoid: tuple[str, ...] = ()) -> str:
    """forced：指定状态。soft = True 时只是「顺手」的指定（醒来泡茶、合上书歇一会儿……）：
    设置里这个状态现在不允许（时间段 / 天气）就不用它，按设置重新抽（avoid 里的不抽）。"""
    hour = datetime.now().hour
    raining, snowing = weather.is_raining(), weather.is_snowing()
    has_friend = bool(_friend_names())
    if forced and (not soft or state_allowed(forced, hour, raining, snowing, has_friend)):
        return forced
    weights = {k: (registry.state_weight(k) if registry.state_weight(k) is not None else float(v["weight"])) for k, v in STATES.items()}
    for k in avoid:
        weights[k] = 0.0
    for k in weights:
        if not state_allowed(k, hour, raining, snowing, has_friend):
            weights[k] = 0.0
    if sick_active():
        weights = {k: (SICK_ALLOWED.get(k, 0.0) if k in SICK_ALLOWED and (weights.get(k, 0) > 0 or k == "sick") else 0.0) for k in weights}
    rd = read_day()
    if not readable_books() or not rd["reads"] or read_left() <= 60:
        weights["read"] = 0.0          # 没书 / 今天随机到不读 / 今天已经读满
    if writing.writing_blocked():
        weights["create"] *= 0.25     # 没有能写的文档：不太会想去写
    p = M.params()
    mst = M.load()
    now = time.time()
    if now < float(mst.get("read_pause_until", 0)):
        weights["read"] = 0.0          # 连着读不进去：歇几个小时再读
    if mst.get("force_next") and weights.get(mst["force_next"], 0) > 0:          # 写作连着不顺：先去看会儿书
        k = mst.pop("force_next")
        M.save(mst)
        whylog.record("state", k, ["写作连着不顺，换个事做"], prob=1.0, labels=_state_labels())
        return k
    v, e = M.value(mst, now, p) if mst.get("day") else (p["base_mean"], 0.6)
    cr = mst.get("create") or {}
    base = dict(weights)
    weights = M.adjust_weights(weights, prev, raining, v, e, int(p["create_max_per_day"]) - int(cr.get("done", 0)), p,
                               write_mult=B.write_mult(mst, now, p), intents=mst.get("intents"))
    said = intents.state_pick(weights, now)            # 她在聊天里说过要做 / 不做的（按遵守概率；不打断正在做的，只影响这一次挑选）
    if said:
        whylog.record("state", said, ["她在聊天里说过要做，按照做概率照做了"], prob=1.0, labels=_state_labels())
        return said
    home = intents.enabled() and bool(intents.home_bound(now, now + 3600))
    if home:
        for k in weights:                              # 说过这阵子在家做的事：出门的状态少一点
            if _outside_key(k):
                weights[k] *= registry.num("intent_conflict_mult")
    keys = [k for k in weights if weights[k] > 0] or ["daze"]
    key = random.choices(keys, [weights.get(k, 1.0) for k in keys])[0]
    f = [f"情绪 {v:+.2f}、精力 {e:.2f}", f"它的基础权重 {whylog.fmt(base.get(key, 0))} → 调整后 {whylog.fmt(weights.get(key, 0))}（心情、精力、灵感、小打算、刚做过的）"]
    if raining or snowing:
        f.append("下雨 / 下雪")
    if home:
        f.append(f"说过这阵子在家：出门的 ×{whylog.fmt(registry.num('intent_conflict_mult'))}")
    if sick_active():
        f.append("生病中，只在几个低消耗的事里挑")
    whylog.record("state", key, f, weights={k: weights[k] for k in keys}, labels=_state_labels())
    intents.state_started(key, now)                    # 说过不做、还是抽到了：算没做到
    return key


def _state_labels() -> dict[str, str]:
    return {k: str(v.get("label") or k) for k, v in STATES.items()}


def _count_create() -> None:
    """今天又写了一次（每天早上定好打算写几次，写够了就不再自己去写；你叫她写的也算一次）。"""
    mst = M.load()
    cr = mst.setdefault("create", {"target": 2, "done": 0})
    cr["done"] = int(cr.get("done", 0)) + 1
    M.save(mst)


def pick_book() -> dict[str, Any] | None:
    """优先接着读在读的；偶尔开新书；偶尔捡起搁置的；偶尔搁置 / 弃书。"""
    books = readable_books()
    if not books:
        return None
    reading_now = [b for b in books if b["phal_status"] == "reading"]
    unread = [b for b in books if b["phal_status"] == "unread"]
    paused = [b for b in books if b["phal_status"] == "paused"]

    # 手上这本，偶尔搁置、偶尔弃掉
    for b in list(reading_now):
        roll = random.random()
        if roll < 0.03 and b["phal_percent"] < 40:
            lib.set_phal_status(b["id"], "dropped")
            reading_now.remove(b)
        elif roll < 0.08:
            lib.set_phal_status(b["id"], "paused")
            reading_now.remove(b)
            paused.append(b)

    r = random.random()
    if reading_now and r < 0.70:
        return random.choice(reading_now)
    if unread and r < 0.92:
        return random.choice(unread)
    if paused and r < 0.97:
        return random.choice(paused)
    for pool in (reading_now, unread, paused):
        if pool:
            return random.choice(pool)
    return None


# --------------------------------------------------------------------------
# 开始一个状态
# --------------------------------------------------------------------------
def _rand_blurb(key: str, avoid: str | None = None) -> str:
    pool = [b for b in BLURBS.get(key, ["……"]) if b != avoid] or BLURBS.get(key, ["……"])
    return random.choice(pool)


def read_blurb(book: dict[str, Any], chapter_title: str) -> str:
    return f"在看《{book['title']}》{chapter_title}".strip()


def start_state(prev: str | None = None, forced: str | None = None, blurb: str | None = None, duration: int | None = None,
                soft: bool = False, avoid: tuple[str, ...] = (), brew: bool = True) -> dict[str, Any]:
    """开始一个状态。brew：喝茶前先去客厅泡茶（测试里直接要「在喝茶」时传 False）。"""
    key = pick_state_key(prev, forced, soft, avoid)
    if forced and soft and key != forced:
        blurb = None                       # 换成了别的状态：原来那句文案（「先去泡点茶」）对不上了，用新状态自己的
    if key == "tea" and prev != "tea_brew" and brew:
        # 喝茶有前摇：先去客厅泡 1~3 分钟（不在房间里），再端回房间喝
        lo_b, hi_b = registry.state_duration("tea_brew") or (60, 180)
        brew = start_state(prev, forced="tea_brew", duration=random.randint(lo_b, hi_b))
        lo_t, hi_t = registry.state_duration("tea") or STATES["tea"]["dur"]
        brew["chain"] = [{"key": "tea", "blurb": blurb or _rand_blurb("tea"), "dur": duration or random.randint(lo_t, hi_t)}]
        return brew
    if key == "create":
        _count_create()
    lo, hi = registry.state_duration(key) or STATES[key]["dur"]
    planned = duration or random.randint(lo, hi)
    if key == "read":
        planned = max(60, min(planned, int(read_left())))      # 一次读到上限为止，不会超过今天剩下的额度
    st: dict[str, Any] = {
        "key": key, "elapsed": 0.0, "planned": planned, "quit_at": None, "blurb": "", "blurb_at": 0.0,
        "last_tick": time.time(), "prev": prev, "book_id": None, "sess": None,
    }
    if key == "read":
        book = pick_book()
        if not book:
            return start_state(prev, forced="daze", soft=True, avoid=("read",))
        chapters = bk.load_chapters(book["id"])
        if not chapters:
            return start_state(prev, forced="tea", soft=True, avoid=("read",))
        ch = min(book["phal_chapter"], len(chapters) - 1)
        off = book["phal_offset"] if book["phal_status"] != "unread" else 0
        if off >= len(chapters[ch]["text"]) - 20 and ch + 1 < len(chapters):
            ch, off = ch + 1, 0
        lib.save_phal_progress(book["id"], ch, off, "reading")
        st["book_id"] = book["id"]
        tier = _pick_act("read")
        speed_k, quit_frac = B.READ_EFFECT[tier]
        st["tier"] = tier
        st["sess"] = {
            "chapter": ch, "pos": float(off), "proc_pos": off,
            "speed": random.uniform(*CHARS_PER_MIN) / 60.0 * speed_k,
            "chunk": random.randint(*CHUNK_CHARS),
            "shares": 0, "slack": False, "chunks": 0,
        }
        st["blurb"] = blurb or read_blurb(book, chapters[ch]["title"])
        if quit_frac:
            st["quit_at"] = planned * random.uniform(*quit_frac)          # 读不进去：早早放下
        elif tier != "absorbed" and random.random() < registry.num("quit_early_chance"):
            st["quit_at"] = planned * random.uniform(0.3, 0.6)
    elif key == "create":
        st["blurb"] = random.choice(NO_DOC_BLURBS) if writing.writing_blocked() else _rand_blurb(key)
    elif key == "friends":
        names = _friend_names()
        st["blurb"] = blurb or (f"和{random.choice(names)}出去玩了" if names else _rand_blurb(key))
    else:
        st["blurb"] = blurb or _rand_blurb(key)
    _place_for(st, prev)
    return st


def _place_for(st: dict[str, Any], prev: str | None) -> None:
    """出门的状态：定去哪（见 places.py）。接着上一个在外面的状态就留在原地；特地出门去远的地方，这一趟拉长到够来回。
    不是出门的状态：回到家了。"""
    key = st["key"]
    try:
        if not _outside_key(key):
            if places.away_from_home():
                places.set_here(None)
            return
        if prev and _outside_key(prev) and places.away_from_home():
            st["place"] = places.here()["id"]
            if places.far_trip():
                st["far"] = True                 # 特地出门途中换状态（吃饭 / 吃完接着玩）：还是特地出门
            return
        ctx = places._ctx_now()
        ctx["with_friends"] = key in ("friends", "boardgame_friends")
        dest = places.pick_destination(key, st["planned"] / 60, ctx)
        if not dest:
            return
        st["place"] = dest["place"]
        if dest["far"]:
            st["planned"] = int(dest["planned_min"] * 60)
            st["far"] = True
        places.set_here(dest["place"], far=dest["far"])
        notebook.visit_place(dest["place"])          # 第一次去的地方：小本子「地方」里建一条；记进今天的见闻
    except Exception:  # noqa: BLE001
        log.exception("定目的地出错")


# --------------------------------------------------------------------------
# 后台任务（只有这里会调模型）
# --------------------------------------------------------------------------
_said_tail: asyncio.Task | None = None       # 会主动发消息的后台任务排成一队（见 _spawn 的 ordered）
SAY_WAIT_S = 120                              # 排队时最多等前一个多久（模型卡住了也不至于一直不发）


def _spawn(coro, ordered: bool = False) -> None:
    """起一个后台任务。ordered=True：会主动发消息的任务，按触发的先后排队跑——前一个跑完（最多等 SAY_WAIT_S 秒）才开始下一个。
    这样「前一个状态」的消息（比如写不下去来问你）一定在「后一个状态」的消息（比如出门了）之前发出来；
    不排队的话，不用调模型的那条会先到，顺序就乱了（隔很久再打开程序、一次 tick 结束好几个状态时最明显）。"""
    global _said_tail
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:          # 不在事件循环里（脚本 / 测试直接调用）：就地跑完
        asyncio.run(coro)
        return
    if ordered:
        prev = _said_tail

        async def run_after() -> None:
            if prev is not None and not prev.done():
                try:
                    await asyncio.wait_for(asyncio.shield(prev), SAY_WAIT_S)
                except Exception:  # noqa: BLE001 —— 前一个出错 / 超时 / 不在同一个事件循环：不管它，照常发
                    pass
            await coro

        task = loop.create_task(run_after())
        _said_tail = task
    else:
        task = loop.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)


async def _chunk_job(book_id: int, chapter: int, start: int, text: str, allow_share: bool, st_ref: dict[str, Any]) -> None:
    global _busy
    _busy = True
    try:
        book = lib.get_book(book_id)
        chapters = bk.load_chapters(book_id)
        if not book or not chapters:
            return
        res = await reading.process_chunk(
            db.get_settings(), book, chapter, chapters[chapter]["title"], start, text, allow_share)
        mood = FEELING_MOOD.get(res.get("feeling", ""))
        if mood and (res["shared"] or random.random() < 0.7):
            react = random.random() < registry.num("react_p_act")          # v10：读到的感受也只有一部分真的影响心情
            register_event("read", FEELING_TEXT[res["feeling"]].format(t=book["title"]), mood if react else None)
        elif res["shared"]:
            register_event("read", f"读《{book['title']}》时忍不住告诉了她一处", "雀跃")
        if res["shared"]:
            sess = (load_state() or {}).get("sess")
            if sess is not None:
                cur = load_state()
                cur["sess"]["shares"] = cur["sess"].get("shares", 0) + 1
                save_state(cur)
    except Exception:  # noqa: BLE001
        log.exception("读书会话出错")
    finally:
        _busy = False


async def _after_read_job(book_id: int) -> None:
    try:
        book = lib.get_book(book_id)
        if book:
            await reading.read_done_thought(db.get_settings(), book)
    except Exception:  # noqa: BLE001
        log.exception("读后感出错")


SHARE_SHY = ["随便写的，别笑", "写得不好，先放在这", "不知道对不对，你看看", "还没改，凑合看", "写完就觉得有点丑"]


_writing_busy = False


def _writing_context() -> tuple[str, str]:
    """写之前想一想用的：现在的心情 + 一点灵感（最近的读书笔记、今天经历）。"""
    word, _why = live_mood()
    bits = []
    notes = lib.recent_notes(2)
    if notes:
        bits.append("最近的读书笔记：" + "；".join(f"《{n['book_title']}》：{n['text']}" for n in notes) + "。")
    events = db.events_on(daily.today_str(), 3)
    if events:
        bits.append("今天经历过：" + "；".join(e["text"] for e in events) + "。")
    return word, "".join(bits)


def _say(lines: list[str], meta: dict[str, Any], counted: bool, source: str = "create") -> bool:
    """她对用户说几句话（来自创作）。counted=True 受睡觉 / 每小时频率限制（自己主动开口）；
    用户正跟她聊着的时候（counted=False）不受限。几句话算一次主动消息。"""
    lines = [guards.strip_exclaim(ln) for ln in lines if ln.strip()]
    if not lines:
        return False
    if counted:
        first = reading.post_proactive(source, lines[0], meta)
        if first is None:
            return False
    else:
        db.add_message(db.current_session_id(), "assistant", lines[0], source=source, meta=meta)
    for extra in lines[1:]:
        db.add_message(db.current_session_id(), "assistant", extra, source=source, meta=meta)
    return True


def _announce(plan: dict[str, Any], user_triggered: bool) -> bool:
    """写之前先说一声：是因为想到了什么才写的，还是只是想写了。"""
    doc = writing.get_doc(plan["doc_id"]) if plan.get("doc_id") else None
    title = doc["title"] if doc else ""
    meta = {"kind": "writing_intent", "doc_id": plan.get("doc_id"), "title": title, "branch": plan.get("branch")}
    ok = _say(writing.announce_lines(plan, title), meta, counted=not user_triggered)
    if ok and doc:
        writing.set_focus(doc["id"], title, plan.get("idea") or "")
    return ok


def _save_pending(plan: dict[str, Any], instruction: str | None = None) -> None:
    """她问我要新文档、在等：记下她的想法，我建好之后她就接着写。"""
    db.save_settings({
        "writing_pending": json.dumps({"idea": plan.get("idea", ""), "motive": plan.get("motive", "whim"),
                                       "instruction": instruction or ""}, ensure_ascii=False),
        "writing_waiting": "1", "writing_ask_day": datetime.now().strftime("%Y-%m-%d"),
    })


async def _run_plan(plan: dict[str, Any], instruction: str | None, user_triggered: bool,
                    announced: bool = False, tier: str | None = None) -> dict[str, Any] | None:
    """按 plan 动笔：先说一声 → 按这次的结果分档写（v9）：
    顺利 = 正常写一大段；写了一点 = 只写一小段；卡住 = 一个字没写；不满意 = 写了几行划掉（进草稿本）；删了重写 = 先删掉不满意的再写。
    tier 不给就按此刻的心情、精力、灵感抽（你叫她写的，只在顺利 / 写了一点 / 删了重写里抽）。"""
    global _writing_busy
    if _writing_busy:
        return None
    _writing_busy = True
    try:
        settings = db.get_settings()
        branch = plan.get("branch")
        if branch == "no_doc":
            writing.maybe_ask_new_doc()
            return None
        if branch == "new_doc":
            if not announced and _announce(plan, user_triggered):
                _save_pending(plan, instruction)
            return None
        if not announced and not _announce(plan, user_triggered) and not user_triggered:
            return None                                    # 想说都说不出口（睡着 / 频率到上限）：这次先不写
        doc = writing.get_doc(plan["doc_id"])
        if not doc or doc.get("full"):
            return None
        tier = tier or pick_write_tier(user_triggered)
        word, _ctx = _writing_context()
        title = doc["title"]
        res = None
        if tier == "stuck":
            pass                                           # 坐了半天，一个字没写出来
        elif tier == "unhappy":
            await reading.create_piece(settings, False, False)        # 写了几行又划掉：不进文档，只留在草稿本里
        else:
            if tier == "rewrite":
                cut = await writing.self_delete(settings, doc, word)
                if cut:
                    _say(writing.delete_lines(len(cut["deleted"]), cut["why"]),
                         {"kind": "doc_cut", "doc_id": doc["id"], "title": title}, counted=not user_triggered)
                    doc = writing.get_doc(doc["id"])
                else:
                    tier = "some"                          # 回头看了一遍，没有要删的：就算「写了一点」，免得说删了其实没删
            ins = instruction
            if not ins and plan.get("idea"):
                ins = f"你动笔前脑子里冒出来的是「{plan['idea']}」（你也这样跟她说了），就从这个写起。"
            if tier == "some":
                ins = ((instruction + "\n") if instruction else "") + "这次只写一小段（两三句就停），写到哪算哪。"
            res = await writing.write_piece(settings, doc, instruction=ins,
                                            mode=branch if branch in ("revisit", "new_section") else "continue",
                                            section_index=plan.get("section"))
            if not res and llm.is_echo(settings):
                return None                                # 没有模型：什么都没写成，不记结果
            if res:
                writing.mark_focus_written(doc["id"])
        writing_outcome(tier, title, user_triggered)
        return res
    except Exception:  # noqa: BLE001
        log.exception("创作出错")
        return None
    finally:
        _writing_busy = False


def pick_write_tier(user_triggered: bool = False, now: float | None = None) -> str:
    """这次写作的结果分档：按此刻的心情、精力、灵感抽（见 behavior.WRITE_TIERS）。你叫她写的，只在顺利 / 写了一点 / 删了重写里抽。"""
    now = now or time.time()
    p = M.params()
    mst = M.load()
    v, e = M.value(mst, now, p)
    w = B.tier_weights(v, e, B.insp(mst, now, p), p)
    keys = [k for k, _ in B.WRITE_TIERS]
    if user_triggered:
        w = [x if k in ("smooth", "some", "rewrite") else 0.0 for k, x in zip(keys, w)]
    return random.choices(keys, w)[0] if sum(w) > 0 else "some"


def writing_outcome(tier: str, title: str, user_triggered: bool = False, now: float | None = None) -> dict[str, Any]:
    """记下这次写作的结果：事件文字按分档写；约 40% 带情绪反应（按分档的范围抽）；扣灵感、加满足感；
    连着不顺就换个做法（歇一会 / 换题材 / 先看书 / 来问你）；和今天的小打算对上就记成做了。"""
    now = now or time.time()
    p = M.params()
    mst = M.load()
    row = B.tier_row(tier, p)
    text = B.write_text(tier, title, random)
    word = None
    if B.reacts("writing", p, random):
        dv, de = B.reaction(row, random)
        M.push(mst, now, p, dv, de, f"create:{tier}", text)
        word = M.repick(mst, now, p)
    B.add_insp(mst, now, -float(row["insp"]), p)
    if row["sat"]:
        B.add_sat(mst, now, float(row["sat"]), p)
    asks = [t for t in mst.get("asks", []) if now - t < 7 * 86400]
    adapt = None if user_triggered else B.after_write(mst, tier, now, p, random, ask_ok=len(asks) < int(p["ask_per_week"]))
    if adapt == "read":
        mst["force_next"] = "read"
    if adapt == "ask":
        mst["asks"] = asks + [now]
    if tier in ("smooth", "some", "rewrite"):
        it = B.mark_intent(mst.get("intents") or [], "create")
        if it:
            M.push(mst, now, p, 0.06, 0.0, "", f"做完了今天打算的事：{it['text']}")
    M.save(mst)
    db.add_event(daily.today_str(), "create", text, word, etype=f"write_{tier}")
    if adapt == "ask":
        _ask_now(title)                                     # 不排队：它属于这次写作，要在下一个状态的消息（比如出门了）之前发
    return {"tier": tier, "text": text, "word": word, "adapt": adapt}


ASK_LINES = [["{t}写不下去了", "你觉得接下来该怎么走"], ["卡在{t}这儿了", "你帮我想想"], ["{t}写了好几次都不对", "要是你，你会怎么写"]]


def _ask_now(title: str) -> None:
    """写作连着卡住：来问你（每周有上限）。你没及时回也没关系，她过一阵可能自己接着写。"""
    lines = [ln.format(t=f"《{title}》") for ln in random.choice(ASK_LINES)]
    _say(lines, {"kind": "writing_ask", "title": title}, counted=True, source="create")


async def _ask_job(title: str) -> None:
    _ask_now(title)


async def _intent_job() -> None:
    """进入「试图创作」时：先想好这次怎么写，并马上跟她说一声（想到了什么 / 只是想写）；计划留到这段时间结束时执行。"""
    try:
        settings = db.get_settings()
        word, ctx = _writing_context()
        plan = await writing.plan_writing(settings, mood=word, inspiration=ctx)
        cur = load_state() or {}
        if cur.get("key") != "create" or cur.get("written_now"):
            return                                         # 想好的时候她已经不在写了：这句「想写了」过时了，不发
        if plan["branch"] == "no_doc":
            writing.maybe_ask_new_doc()
            return
        if plan["branch"] == "new_doc":
            if _announce(plan, user_triggered=False):
                _save_pending(plan)
            return
        plan["announced"] = _announce(plan, user_triggered=False)
        plan["at"] = time.time()
        db.save_settings({"writing_plan": json.dumps(plan, ensure_ascii=False)})
    except Exception:  # noqa: BLE001
        log.exception("决定怎么写出错")


def _take_plan() -> dict[str, Any] | None:
    raw = db.get_settings().get("writing_plan") or ""
    if not raw:
        return None
    db.save_settings({"writing_plan": ""})
    try:
        plan = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return plan if time.time() - float(plan.get("at") or 0) < 3 * 3600 else None


async def _create_job(tier: str | None = None) -> None:
    """试图创作结束：按进入这个状态时想好的计划动笔（结果分档在 _run_plan 里抽）；没有计划（比如页面中途关过）就现在想一个。
    没文档（或写满了）就先向她要一份。写成了按概率拿给你看。"""
    try:
        plan = _take_plan()
        announced = bool(plan and plan.get("announced"))
        if plan is None:
            word, ctx = _writing_context()
            plan = await writing.plan_writing(db.get_settings(), mood=word, inspiration=ctx)
        mst = M.load()
        if mst.pop("write_switch", False) and plan.get("branch") == "continue":
            plan["branch"] = "new_section"                                       # 连着不顺：换个题材 / 写法（另起一段写别的）
            M.save(mst)
        tier = tier or pick_write_tier()
        res = await _run_plan(plan, None, user_triggered=False, announced=announced, tier=tier)
        share = random.random() < (registry.num("share_draft_done") if tier in ("smooth", "some", "rewrite") else registry.num("share_draft_abandoned"))
        if res and share and reading.can_send_proactive():
            excerpt = res["text"][:180] + ("…" if len(res["text"]) > 180 else "")
            reading.post_proactive("create", f"{random.choice(SHARE_SHY)}\n\n{excerpt}",
                                   {"kind": "doc", "doc_id": res["doc"]["id"], "title": res["doc"]["title"]})
    except Exception:  # noqa: BLE001
        log.exception("创作出错")


# --------------------------------------------------------------------------
# 她被我要求写作：先回复 → 切到写作状态 → 才尝试动笔
# --------------------------------------------------------------------------
def set_create_state(blurb: str = "去写东西了") -> None:
    st = load_state() or {}
    new = start_state(prev=st.get("key"), forced="create", blurb=blurb)
    new["intent_started"] = True                      # 计划已经有了（用户触发的），不用再想
    new["written_now"] = True                         # 这段创作已经按她的要求写过了，时间到了不再自己另写一次
    if st:
        _carry(st, new)
    new["last_tick"] = time.time()
    save_state(new)


def begin_writing_now(plan: dict[str, Any], instruction: str | None = None) -> bool:
    """在她回复完之后调用：把状态切成「试图创作」，然后开始写。返回是不是真的开始了。"""
    if not plan or plan.get("branch") in (None, "no_doc", "new_doc"):
        return False
    set_create_state()
    _spawn(_run_plan(plan, instruction, user_triggered=True))
    return True


def on_new_doc(doc: dict[str, Any]) -> bool:
    """我给她建了新文档：如果她正等着（之前问我要过），就接着把想写的写进去。"""
    raw = db.get_settings().get("writing_pending") or ""
    if not raw or doc.get("kind") != "phal":
        return False
    db.save_settings({"writing_pending": ""})
    try:
        pend = json.loads(raw)
    except json.JSONDecodeError:
        return False
    plan = {"branch": "continue", "doc_id": doc["id"], "idea": pend.get("idea", ""), "motive": pend.get("motive", "whim")}
    _say(random.choice([["收到", "我去写了"], ["谢啦", "那我去写了"], ["嗯 好", "这就写"]]),
         {"kind": "writing_intent", "doc_id": doc["id"], "title": doc["title"]}, counted=False)
    set_create_state("在新文档里写东西")
    _spawn(_run_plan(plan, pend.get("instruction") or None, user_triggered=True, announced=True))
    return True


# --------------------------------------------------------------------------
# 结束一个状态
# --------------------------------------------------------------------------
_edit_bursts: dict[int, dict[str, Any]] = {}      # doc_id -> {old: 这一轮修改之前的内容, new: 最近保存的内容, gen: 第几次保存}


def on_user_edit(doc: dict[str, Any], old: str, new: str) -> None:
    """我在网页里改了她的文档并保存：过一小会儿，她读完修改记录再作反应。"""
    if doc.get("kind") != "phal" or old == new or not enabled():
        return
    burst = _edit_bursts.setdefault(doc["id"], {"old": old, "gen": 0})
    burst["new"] = new
    burst["gen"] += 1
    _spawn(_edit_react_job(doc["id"], burst["gen"]))


async def _edit_react_job(doc_id: int, gen: int) -> None:
    try:
        await asyncio.sleep(registry.num("edit_react_delay_s"))
        burst = _edit_bursts.get(doc_id)
        if not burst or burst["gen"] != gen:
            return                                         # 期间又保存过：留给最后那次的任务
        _edit_bursts.pop(doc_id, None)
        if daily.is_sleeping():
            return                                         # 她睡着了，没看见
        doc, cur = writing.read_doc(doc_id)
        diff, changed = writing.describe_edit(burst["old"], cur)
        if changed < registry.num("edit_min_change"):
            return
        settings = db.get_settings()
        # 要坚持自己的版本，前提是文档现在还正好是我保存的样子（她期间又写了东西的话，不能整份退回去）
        insist = (cur == burst["new"].replace("\r\n", "\n")
                  and random.random() < writing._setting_float(settings, "writing_p_insist", writing.P_INSIST))
        lines = await writing.react_to_edit(settings, doc, diff, insist)
        if not lines:
            return
        if insist:
            writing.save_content(doc_id, burst["old"], reason="Phal 撤回了你的修改")
        meta = {"kind": "doc_edit", "doc_id": doc_id, "title": doc["title"], "reverted": insist}
        sid = db.current_session_id()
        first = db.add_message(sid, "assistant", lines[0], source="doc_edit", meta=meta)
        lib.log_proactive("doc_edit", first["id"])
        for extra in lines[1:]:
            db.add_message(sid, "assistant", extra, source="doc_edit", meta=meta)
        if insist:
            register_event("doc_edit", f"她改了《{doc['title']}》，我不同意，改回去了", "倔强")
        else:
            register_event("doc_edit", f"她改了《{doc['title']}》，我看了修改记录", None)
    except Exception:  # noqa: BLE001
        log.exception("读修改记录出错")


def end_state(st: dict[str, Any], reason: str = "time", finished_book: bool = False) -> dict[str, Any]:
    """收尾（读后感 / 写作成果），再开下一个状态。"""
    key = st["key"]
    if f"state_de_{key}" in registry.BY_KEY:
        push_energy(registry.num(f"state_de_{key}"), key)          # 做完这件事：累了一点 / 歇过来一点
    try:
        _after_state(key, float(st.get("elapsed", 0)), tier=st.get("tier"), book_id=st.get("book_id"))
    except Exception:  # noqa: BLE001
        log.exception("状态收尾出错")
    chain = list(st.get("chain") or [])
    if chain:                                   # 一顿饭的下一个状态（做饭 → 吃饭）
        spec = chain.pop(0)
        nxt = start_state(prev=key, forced=spec["key"], blurb=spec["blurb"], duration=spec["dur"])
        nxt["chain"], nxt["meal"] = chain, st.get("meal")
        return nxt
    settings = db.get_settings()
    not_echo = not llm.is_echo(settings)
    forced = None
    blurb = None
    if key == "read" and st.get("book_id"):
        if finished_book:
            lib.set_phal_status(st["book_id"], "finished")
            book = lib.get_book(st["book_id"])
            register_event("read", f"把《{book['title'] if book else '一本书'}》读完了，怅然若失", "怅然")
        if not_echo and st["elapsed"] >= 600 and random.random() < registry.num("post_read_thought"):
            _spawn(_after_read_job(st["book_id"]))
        if reason == "slack":
            forced, blurb = "daze", random.choice(SLACK_BLURBS)
    elif key == "create" and not_echo and st["elapsed"] >= 300 and not st.get("written_now"):
        _spawn(_create_job(), ordered=True)
    nxt = start_state(prev=key, forced=forced, blurb=blurb, soft=True)
    # 书架上真的没书时，偶尔跟我说想看点什么（本地文案，不调模型，每天最多一次）
    if key == "read" and nxt["key"] != "read":
        maybe_wish()
    return nxt


DESSERTS = ["小蛋糕", "曲奇", "布丁", "玛芬", "蛋挞", "司康", "奶油泡芙"]


def _after_state(key: str, elapsed: float, now: float | None = None, tier: str | None = None, book_id: int | None = None) -> None:
    """一件事做完之后：和今天的小打算对上就记成做了；看完书加灵感；（v10）读书 / 做饭 / 出门按档记结果、带一部分情绪；
    外出回来按概率发生一件事（基调跟着出门的档）；做饭（爸妈出差时）和烘焙的成果摆上食物桌 / 书桌；一个人吃饭吃掉一份饭菜。"""
    now = now or time.time()
    p = M.params()
    mst = M.load()
    changed = False
    if key != "create" and elapsed >= float(p["intent_done_min"]) * 60:
        it = B.mark_intent(mst.get("intents") or [], key)
        if it:
            M.push(mst, now, p, 0.06, 0.0, "", f"做完了今天打算的事：{it['text']}")
            changed = True
    if key == "read" and elapsed >= 300:
        B.add_insp(mst, now, float(p["insp_read"]), p)
        changed = True
    if changed:
        M.save(mst)
    label = STATES.get(key, {}).get("label", "")
    if key == "read" and elapsed >= 300 and tier:
        title = (lib.get_book(book_id) or {}).get("title", "书") if book_id else "书"
        act_outcome("read", tier, title, now)
    if key == "cook" and elapsed >= 600:
        m = meals.current_meal() or {}
        dish = m.get("dish") or "饭"
        act_outcome("cook", _pick_act("cook"), dish, now)
        if m.get("away"):                                  # 爸妈出差：做好的饭菜端回房间，摆在食物桌上
            _food("meal", dish, source="cook", place="table", portions=random.randint(1, 2))
    if key == "eat_alone":
        meal = next((f for f in reversed(clutter.available(now, ("table",))) if f["kind"] == "meal"), None)
        if meal:
            clutter.eat_one(meal["id"])                    # 吃掉一份（做多了就剩下，之后也可能当夜宵）
    if key in B.OUTING_EVENTS and elapsed >= 600:
        out_tier = act_outcome("out", _pick_act("out"), f"{label}回来", now)
        if registry.flag("events_enabled") and random.random() < float(p["outing_event_p"]):
            etype = random.choice(B.OUTING_EVENTS[key])
            if etype != "friend" or _friend_names():
                _spawn(_event_job(etype, after=label, tone=B.OUT_TONE[out_tier]), ordered=True)
    if key == "bake" and elapsed >= 900:
        _food("dessert", random.choice(DESSERTS), source="bake")


def _pick_act(kind: str, now: float | None = None) -> str:
    """读书 / 做饭 / 出门这一次的结果分档：按此刻的心情、精力抽。"""
    now = now or time.time()
    p = M.params()
    mst = M.load()
    v, e = M.value(mst, now, p) if mst.get("day") else (p["base_mean"], 0.6)
    return B.sample_act(kind, v, e, p, random)


def act_outcome(kind: str, tier: str, title: str, now: float | None = None) -> str:
    """记下读书 / 做饭 / 出门的结果：事件文字按档写；一部分（react_p_act）带情绪反应；读书连着读不进去就歇一阵。返回档。"""
    now = now or time.time()
    p = M.params()
    mst = M.load()
    text = B.act_text(kind, tier, title, random)
    word = None
    if random.random() < registry.num("react_p_act"):
        dv, de = B.reaction(B.act_row(kind, tier, p), random)
        M.push(mst, now, p, dv, de, f"{kind}:{tier}", text)
        word = M.repick(mst, now, p)
    B.after_act(mst, kind, tier, now, p)
    M.save(mst)
    db.add_event(daily.today_str(), "read" if kind == "read" else "life", text, word, etype=f"{kind}_{tier}")
    return tier


def _food(kind: str, name: str, **kw: Any) -> dict[str, Any] | None:
    try:
        return clutter.spawn(kind, name, **kw)
    except Exception:  # noqa: BLE001
        log.exception("把食物摆上桌出错")
        return None


# --------------------------------------------------------------------------
# 边做事边吃东西（v10）：不是单独的状态
# --------------------------------------------------------------------------
SNACK_STATES = ("tea", "read", "daze", "video", "game", "create", "homework", "music", "anime", "draw", "rain_watch", "sick",
                "bake", "call", "boardgame_online", "laze")


def snack_rate(v: float, e: float) -> float:
    """想吃东西的频率（每小时几次）：心情越差、精力越不足越想吃（和保质期无关）。"""
    return registry.num("food_eat_rate") * math.exp(-registry.num("food_eat_kv") * v + registry.num("food_eat_ke") * (0.5 - e) * 2)


def _maybe_snack(st: dict[str, Any], now: float, dt: float) -> dict[str, Any]:
    """在能边吃边做的状态里：正在吃的吃完了就收尾；没在吃就按频率掷骰子，从食物桌 / 书桌上拿一份吃（最多吃 food_eat_max_min 分钟）。"""
    eating = st.get("eating")
    if eating and (now >= float(eating.get("until", 0)) or st["key"] not in SNACK_STATES):
        st.pop("eating", None)
        eating = None
    if eating or st["key"] not in SNACK_STATES or dt <= 0:
        return st
    foods = clutter.available(now)
    if not foods:
        return st
    p = M.params()
    mst = M.load()
    v, e = M.value(mst, now, p) if mst.get("day") else (p["base_mean"], 0.6)
    if random.random() >= 1 - math.exp(-snack_rate(v, e) * dt / 3600):
        return st
    near = [f for f in foods if f["place"] == "desk"] if st["key"] in ("create", "homework", "draw", "game", "boardgame_online") else []
    food = random.choice(near or foods)                     # 坐在书桌边时先拿书桌上的
    got = clutter.eat_one(food["id"])
    if not got:
        return st
    lo, hi = int(registry.num("food_eat_min_min")), int(registry.num("food_eat_max_min"))
    mins = random.uniform(min(lo, hi), max(lo, hi))
    st["eating"] = {"id": food["id"], "name": food["name"], "kind": food["kind"], "place": food["place"], "until": now + mins * 60,
                    "finished": bool(got.get("finished"))}
    if got.get("finished"):
        db.add_event(daily.today_str(), "food", f"把{food['name']}吃完了", None, etype="food")
        intents.food_eaten(food["id"], True, now)        # 说好要吃掉的那份：吃完了就算做到
    return st


def maybe_wish() -> None:
    if random.random() > 0.25:
        return
    today = time.strftime("%Y-%m-%d")
    if db.get_settings().get("life_wish_day") == today:
        return
    shelf = lib.list_books()
    finished = [b for b in shelf if b["phal_status"] == "finished"]
    unread = [b for b in shelf if b["phal_status"] in ("unread", "reading", "paused")]
    if len(unread) >= 2:
        return           # 还有的是书可看，不用跟我要
    if finished and random.random() < 0.6:
        text = f"想看点类似《{random.choice(finished)['title']}》的，你那有没有"
    else:
        text = random.choice(WISHES_GENERIC)
    if reading.post_proactive("wish", text, {"kind": "wish"}):
        db.save_settings({"life_wish_day": today})


# --------------------------------------------------------------------------
# tick：每 30 秒由页面调一次
# --------------------------------------------------------------------------
def _advance_read(st: dict[str, Any], dt: float) -> tuple[dict[str, Any], str | None, bool]:
    """推进阅读进度。返回 (state, 结束原因 or None, 是否读完全书)。"""
    sess = st["sess"]
    book_id = st["book_id"]
    chapters = bk.load_chapters(book_id)
    if not chapters:
        return st, "time", False
    ch = sess["chapter"]
    text = chapters[ch]["text"]
    sess["pos"] += sess["speed"] * dt
    ended: str | None = None
    finished = False

    def flush(chapter: int, upto: int) -> None:
        nonlocal ended
        start = sess["proc_pos"]
        if upto - start < 300:
            sess["proc_pos"] = upto
            return
        chunk_text = chapters[chapter]["text"][start:upto]
        sess["proc_pos"] = upto
        sess["chunks"] += 1
        sess["chunk"] = random.randint(*CHUNK_CHARS)
        if not llm.is_echo(db.get_settings()) and not _busy:
            allow = sess["shares"] < reading.MAX_SHARES_PER_SESSION and reading.can_send_proactive()
            _spawn(_chunk_job(book_id, chapter, start, chunk_text, allow, st))
        if random.random() < registry.num("slack_chance") and ended is None:
            ended = "slack"

    while sess["pos"] >= len(text):
        flush(ch, len(text))
        if ch + 1 >= len(chapters):
            sess["pos"] = float(len(text))
            finished = True
            ended = "finished"
            break
        ch += 1
        sess["chapter"] = ch
        sess["pos"] = 0.0
        sess["proc_pos"] = 0
        text = chapters[ch]["text"]
        st["blurb"] = read_blurb(lib.get_book(book_id) or {"title": "书"}, chapters[ch]["title"])

    pos = int(sess["pos"])
    if pos - sess["proc_pos"] >= sess["chunk"] and ended is None:
        flush(ch, pos)
    lib.save_phal_progress(book_id, ch, pos, "reading" if not finished else "finished")
    return st, ended, finished


def _carry(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """换状态时带过去的东西：累计时钟（心情和精力存在别处，见 mood.py）。"""
    new["clock"] = old.get("clock", 0.0)
    return new


def _go_to_sleep(st: dict[str, Any]) -> dict[str, Any]:
    new = {
        "key": "sleep", "elapsed": 0.0, "planned": 0, "quit_at": None, "blurb": random.choice(SLEEP_BLURBS),
        "blurb_at": 0.0, "last_tick": st["last_tick"], "prev": st["key"], "book_id": None, "sess": None,
        "clock": st.get("clock", 0.0),
    }
    try:
        if places.away_from_home():
            places.set_here(None)                      # 在外面到了睡觉点：算回家了（不然夜里还以为她在外面）
    except Exception:  # noqa: BLE001
        log.exception("睡觉时清地点出错")
    p = M.params()
    mst = M.load()
    M.note_bedtime(mst, time.time(), p)                # 睡前的心情：影响今晚睡得好不好
    M.save(mst)
    return new


def _hour_cn(h: int) -> str:
    """22 → 晚上10点，7 → 早上7点（作息时间可在设置里改，提示词里不能写死）。"""
    h = int(h) % 24
    if h < 5:
        return f"凌晨{h}点"
    if h < 9:
        return f"早上{h}点"
    if h < 12:
        return f"上午{h}点"
    if h == 12:
        return "中午12点"
    if h < 18:
        return f"下午{h - 12}点"
    return f"晚上{h - 12}点"


GOODNIGHT_PROMPT = """你是「{name}」。你的口吻（人设里的说话方式）：
{voice}

现在是{clock}，你要睡了。向她道晚安，像平时发消息一样：1 到 2 条，每条 15 字以内，用换行分开，
不要太肉麻，可以带一点今天的事或者催她也早点睡。只输出消息本身。"""
GOODNIGHT_LOCAL = [["我先睡了", "你也早点睡，晚安"], ["困了，晚安"], ["不早了，我去睡了", "晚安"], ["睡了睡了，晚安"]]
_goodnight_busy = False


async def _goodnight_job(day: str) -> None:
    global _goodnight_busy
    _goodnight_busy = True
    try:
        settings = db.get_settings()
        parts: list[str] = []
        if not llm.is_echo(settings):
            try:
                from . import persona as persona_lib

                raw = await llm.complete(
                    settings,
                    [{"role": "system", "content": GOODNIGHT_PROMPT.format(
                        name=C.display_name(settings),
                        clock=_hour_cn(daily.sleep_hour()),
                        voice=persona_lib.voice_excerpt(settings.get("system_prompt") or "", 1200))},
                     {"role": "user", "content": "发吧。"}],
                    temperature=0.9,
                )
                parts = guards.split_messages(guards.strip_exclaim(raw))[:2]
            except Exception as exc:  # noqa: BLE001
                log.warning("生成晚安失败，用本地文案：%s", exc)
        if not parts:
            parts = random.choice(GOODNIGHT_LOCAL)
        meta = {"kind": "goodnight"}
        # 临睡前说的话，不受「睡着了不主动开口」的限制；但计入主动消息，别和别的叠在一起轰炸
        sid = db.current_session_id()
        first = db.add_message(sid, "assistant", parts[0], source="goodnight", meta=meta)
        lib.log_proactive("goodnight", first["id"])
        for extra in parts[1:]:
            db.add_message(sid, "assistant", extra, source="goodnight", meta=meta)
        register_event("goodnight", "睡前跟她道了晚安", None)
    except Exception:  # noqa: BLE001
        log.exception("道晚安出错")
    finally:
        _goodnight_busy = False


def _maybe_goodnight(now: float) -> None:
    if _goodnight_busy or not daily.sleep_enabled():
        return
    h = datetime.fromtimestamp(now)
    # 睡觉那个点的头半小时（goodnight_window_min）才算「临睡前」；凌晨 / 早上打开网页时她本来就在睡，不补
    if not (h.hour == daily.sleep_hour() and h.minute * 60 + h.second < registry.num("goodnight_window_min") * 60):
        return
    day = daily.today_str(now)
    if db.get_settings().get("life_goodnight_day") == day:
        return
    db.save_settings({"life_goodnight_day": day})
    _spawn(_goodnight_job(day), ordered=True)


# --------------------------------------------------------------------------
# 天气：定时更新；下雪时心情变好，并且（按概率）主动发一条开心的消息，每场雪最多一条
# --------------------------------------------------------------------------
_weather_busy = False

SNOW_PROMPT = """你是「{name}」。你的口吻（人设里的说话方式）：
{voice}

你这边刚刚下雪了（{temp}°C），你很开心。主动给她发 1 到 2 条消息，每条 20 字以内，用换行分开，
像随手发的，别写成描写。只输出消息本身。"""
SNOW_LOCAL = [["下雪了"], ["外面在下雪", "好久没见了"], ["下雪了，你那边呢"]]


async def _snow_job() -> None:
    register_event("weather", "下雪了，心情很好", "雀跃")
    p = M.params()
    mst = M.load()
    first = not mst.get("snow_seen_year") or mst["snow_seen_year"] != datetime.now().year - (1 if datetime.now().month < 7 else 0)
    B.add_insp(mst, time.time(), float(p["insp_snow_first"] if first else p["insp_weather"]), p)      # 第一场雪：灵感多一些
    mst["snow_seen_year"] = datetime.now().year - (1 if datetime.now().month < 7 else 0)
    M.save(mst)
    if random.random() >= registry.num("weather_snow_message_prob"):
        return
    settings = db.get_settings()
    parts: list[str] = []
    if not llm.is_echo(settings):
        try:
            from . import persona as persona_lib

            w = weather.current("her") or {}
            raw = await llm.complete(
                settings,
                [{"role": "system", "content": SNOW_PROMPT.format(
                    name=C.display_name(settings), temp=w.get("temp", ""),
                    voice=persona_lib.voice_excerpt(settings.get("system_prompt") or "", 1200))},
                 {"role": "user", "content": "发吧。"}],
                temperature=0.9,
            )
            parts = guards.split_messages(guards.strip_exclaim(raw))[:2]
        except Exception as exc:  # noqa: BLE001
            log.warning("生成下雪消息失败，用本地文案：%s", exc)
    if not parts:
        parts = random.choice(SNOW_LOCAL)
    _say(parts, {"kind": "snow"}, counted=True, source="weather")


async def _weather_job() -> None:
    global _weather_busy
    _weather_busy = True
    try:
        await weather.refresh()
        if weather.snow_started():
            await _snow_job()
    except Exception:  # noqa: BLE001
        log.exception("更新天气出错")
    finally:
        _weather_busy = False



# --------------------------------------------------------------------------
# 吃饭 / 生病 / 在外面
# --------------------------------------------------------------------------
def _money_factor() -> float:
    try:
        return float(money.meal_factor())          # 零花钱：余额低就少点外卖、少出去吃
    except Exception:  # noqa: BLE001
        return 1.0


def _meal_ctx(meal: str) -> dict[str, Any]:
    from .workdays import is_weekend

    word, _why = live_mood()
    return {
        "weekend": is_weekend(datetime.now().date()), "mood": word, "negative": M.is_negative(word),
        "raining": weather.is_raining(), "snowing": weather.is_snowing(), "hot": weather.is_hot(), "cold": weather.is_cold_snap(),
        "season": weather.season(), "sick": sick_active(), "money_factor": _money_factor(), "here": places.here()["id"],
        **_far_trip_ctx(),
    }


def _far_trip_ctx() -> dict[str, Any]:
    """特地出门在外（离家远）：饭就在那边吃。"""
    st = load_state() or {}
    if not (st.get("far") and places.away_from_home()):
        return {}
    return {"far_trip": True, "with_friends": st.get("key") in ("friends", "boardgame_friends")}


def _family_names() -> str:
    names = [p["name"] for p in people.list_people(drafts=False) if p["kind"] == "family"]
    return "".join(names) if names else "家人"


def _maybe_meal(st: dict[str, Any], now: float, paused: bool) -> dict[str, Any]:
    if paused or st["key"] in states.MEAL_STATES or not enabled():
        return st
    spec = meals.due(now, _meal_ctx, _friend_names(), _family_names())
    if not spec:
        return st
    first, rest = spec["states"][0], spec["states"][1:]
    left = st.get("planned", 0) - st.get("elapsed", 0)
    if st.get("far") and places.away_from_home() and left > 600:
        rest = rest + [{"key": st["key"], "blurb": st.get("blurb") or _rand_blurb(st["key"]), "dur": int(left)}]   # 特地出门：吃完接着玩，不是吃完就回家
    blurb = first["blurb"]
    eaten = _eat_meal_foods(spec) if spec["method"] == "food" else []
    if eaten:
        blurb = f"在食物桌边吃{'和'.join(eaten)}，当{meals.MEAL_LABEL[spec['meal']]}"
    new = start_state(prev=st["key"], forced=first["key"], blurb=blurb, duration=first["dur"])
    new["chain"], new["meal"] = rest, spec["meal"]
    _carry(st, new)
    new["last_tick"] = now
    register_event("meal", f"{meals.MEAL_LABEL[spec['meal']]}：{meals.METHOD_LABEL[spec['method']]}", None)
    info = {"fixed_dish": "和".join(eaten), "broken": spec.get("broken") or []}
    _spawn(_meal_job(spec["meal"], spec["method"], any(s["key"] == "dine_out" for s in spec["states"]), info), ordered=True)
    return new


def _eat_meal_foods(spec: dict[str, Any]) -> list[str]:
    """她说过拿房间里的东西当这顿饭：把那几样吃掉（每样吃掉说好的次数）。一样都没了就算没做到（这顿还是在食物桌边随便吃点）。"""
    names = []
    for fd in spec.get("foods") or []:
        f = clutter.get(fd["id"])
        if not f or f["expired"] or f["portions_left"] <= 0:
            continue
        for _ in range(max(1, min(int(fd.get("portions") or f["portions_left"]), f["portions_left"]))):
            got = clutter.eat_one(f["id"])
            if not got:
                break
        names.append(f["name"])
    if spec.get("intent"):
        if names:
            intents.resolve(spec["intent"], "done")
        else:
            intents.resolve(spec["intent"], "broken", "那几样已经没了 / 放坏了")
    return names


async def _meal_job(meal: str, method: str, went_out: bool = False, info: dict[str, Any] | None = None) -> None:
    """想好吃什么（模型 + 现实地名检查），记下来；外卖 / 出去吃扣钱；饭前按概率主动跟我说一句。
    info：拿房间里的东西当饭时吃的是什么（fixed_dish）；这顿饭打破了她说过的打算（broken：意图 id）时，这句话要提一下，而且更可能说。"""
    info = info or {}
    try:
        settings = db.get_settings()
        ctx = _meal_ctx(meal)
        cur = meals.current_meal() or {}
        ctx["dish_hint"] = cur.get("dish_hint") or ""
        ctx["fixed_dish"] = info.get("fixed_dish") or ""
        broken = [it for it in (intents.get(i) for i in info.get("broken") or []) if it and not it.get("mentioned")]
        if broken:
            ctx["broken_text"], ctx["broken_reason"] = broken[0]["text"], broken[0]["reason"]
        ctx["went_out"] = went_out
        ctx["pay"] = money.roll_pay(method)                 # 和朋友吃：AA / 朋友请 / 她请（先定好，饭前那句话可以提）
        dish, line = await meals.make_dish(settings, meal, method, ctx)
        meals.record(meal, method, dish)
        try:
            notebook.log_day("food", f"{meals.MEAL_LABEL[meal]}：{meals.METHOD_DESC.get(method, method)}，吃了{dish}")    # 今天的见闻（晚上整理小本子用）
        except Exception:  # noqa: BLE001
            log.exception("记今天的见闻出错")
        if method == "takeout" and cur.get("away"):
            _food("meal", dish, source="takeout", place="table", portions=random.randint(1, 2))
        paid = money.charge_meal(meal, method, dish, went_out, priced=dict(ctx.get("priced") or {}, pay=ctx["pay"]) if ctx.get("priced") else {"pay": ctx["pay"]})
        posted = False
        p = registry.num("meal_announce_prob")
        if broken:
            p = max(p, registry.num("intent_p_tell"))
        if line and random.random() < p and reading.can_send_proactive():
            posted = _say([line], {"kind": "meal", "meal": meal, "dish": dish, "intents": [it["id"] for it in broken]}, counted=True, source="meal")
        if posted and broken:
            intents.mark_mentioned([it["id"] for it in broken])
        if broken:
            for it in broken:
                _maybe_followup(it)
        if paid:
            money.mark_shared(paid["id"], bool(posted))
    except Exception:  # noqa: BLE001
        log.exception("决定吃什么出错")


# --------------------------------------------------------------------------
# 她说过的打算（对话意图，见 intents.py）
# --------------------------------------------------------------------------
def _intent_ctx_now() -> dict[str, Any]:
    now = time.time()
    try:
        p = M.params()
        mst = M.load()
        v, e = M.value(mst, now, p) if mst.get("day") else (p["base_mean"], 0.6)
    except Exception:  # noqa: BLE001
        e = 0.6
    word, _why = live_mood()
    return {"negative": M.is_negative(word), "energy": e, "raining": weather.is_raining() or weather.is_snowing()}


def _on_intent_broken(it: dict[str, Any]) -> None:
    """说过的事没做到（不是饭点那里打破的——那边由饭前那句话来提）：按概率主动提一句；没吃成的东西可能留到之后。"""
    follow = _maybe_followup(it)
    if random.random() < registry.num("intent_p_tell"):
        _spawn(_intent_tell_job(it["id"], follow["text"] if follow else ""), ordered=True)


def _maybe_followup(it: dict[str, Any]) -> dict[str, Any] | None:
    """没吃成的东西（还没坏）：按概率变成新的打算——留着当夜宵 / 明天再吃。"""
    if it["kind"] not in ("item", "meal") or random.random() >= registry.num("intent_p_followup"):
        return None
    c = it["content"]
    foods = [{"id": c["food_id"], "name": c.get("food", "")}] if it["kind"] == "item" else (c.get("foods") or [])
    now = time.time()
    for fd in foods:
        f = clutter.get(fd["id"])
        if not f or f["expired"] or f["portions_left"] <= 0:
            continue
        bed = intents._clock(f"{daily.sleep_hour():02d}:00", now, now) or now + 4 * 3600
        start, end = (now + 3600, bed - 600) if bed - now > 2.5 * 3600 else (now + 10 * 3600, now + 20 * 3600)
        end = min(end, float(f.get("expires_at") or end))
        if end - start < 1800:
            continue
        night = start < bed
        new = {"kind": "item", "content": {"food_id": f["id"], "food": f["name"], "portions": 1},
               "text": f"{f['name']}留着{'当夜宵' if night else '明天吃'}", "start_at": start, "end_at": end, "strength": "casual"}
        return intents.add(new, now)
    return None


INTENT_TELL_PROMPT = """你是「{name}」。你的口吻（人设里的说话方式）：
{voice}

你之前跟她说过要「{text}」，后来没做到：{reason}。{follow}
现在主动跟她提一句，20 字以内，口语，像随手发的消息，自然地带出原因（比如「本来要吃可颂的 被朋友拉出去了」）。
只输出这句话。"""


async def _intent_tell_job(intent_id: int, follow: str = "") -> None:
    """说过的事没做到：主动提一句（受主动消息的频率限制）。"""
    from . import persona as persona_lib

    try:
        it = intents.get(intent_id)
        if not it or it.get("mentioned") or not reading.can_send_proactive():
            return
        settings = db.get_settings()
        line = f"本来说要{it['text']}的，{it['reason']}"
        if not llm.is_echo(settings):
            try:
                raw = await llm.complete(settings, [{"role": "system", "content": INTENT_TELL_PROMPT.format(
                    name=C.display_name(settings), voice=persona_lib.voice_excerpt(settings.get("system_prompt") or "", 900),
                    text=it["text"], reason=it["reason"], follow=f"（你打算{follow}）" if follow else "")},
                    {"role": "user", "content": "说吧。"}], temperature=0.9)
                cand = guards.strip_exclaim((raw or "").strip().strip("「」\"'"))[:40]
                if cand and not guards.find_banned(cand):
                    line = cand
            except Exception:  # noqa: BLE001
                pass
        if _say([line], {"kind": "intent_broken", "intent_id": intent_id}, counted=True, source="intent"):
            intents.mark_mentioned([intent_id])
    except Exception:  # noqa: BLE001
        log.exception("提一句没做到的事出错")


def _maybe_intent_items(st: dict[str, Any], now: float) -> dict[str, Any]:
    """说过要吃掉房间里的 xx：到了挑好的那个时候、她在家又不在吃饭，就去吃（扣掉说好的次数）；没照做的记下原因。"""
    if not intents.enabled():
        return st
    intents.expire_old(now)
    for it in intents.items_due(now):
        c = it["content"]
        if it.get("decision") == "break":
            intents.resolve(it["id"], "broken", intents._why_not(it), now)
            continue
        if outside_now() or st["key"] in states.MEAL_STATES or st["key"] in ("sleep", "shower"):
            continue                                       # 在外面 / 正在吃饭：等会儿再说（过了时间范围还没吃，就算没做到）
        f = clutter.get(c["food_id"])
        if not f or f["portions_left"] <= 0:
            intents.resolve(it["id"], "broken", f"{c.get('food') or '那份'}已经没了", now)
            continue
        if f["expired"]:
            intents.resolve(it["id"], "broken", f"{f['name']}已经放坏了", now)
            continue
        finished = False
        for _ in range(max(1, min(int(c.get("portions") or 1), f["portions_left"]))):
            got = clutter.eat_one(f["id"])
            if not got:
                break
            finished = bool(got.get("finished"))
        if st["key"] in SNACK_STATES:
            lo, hi = int(registry.num("food_eat_min_min")), int(registry.num("food_eat_max_min"))
            st["eating"] = {"id": f["id"], "name": f["name"], "kind": f["kind"], "place": f["place"],
                            "until": now + random.uniform(min(lo, hi), max(lo, hi)) * 60, "finished": finished}
        db.add_event(daily.today_str(), "food", f"照说的吃了{f['name']}" + ("，吃完了" if finished else ""), None, etype="food")
        intents.resolve(it["id"], "done", "", now)
    return st


intents.configure(ctx_fn=_intent_ctx_now, on_broken=_on_intent_broken)


# --------------------------------------------------------------------------
# 零花钱：买东西
# --------------------------------------------------------------------------
_spend_busy = 0


def _spend_ctx(st: dict[str, Any]) -> dict[str, Any]:
    d = datetime.now()
    word, _why = live_mood()
    key = st.get("key", "tea")
    return {"state": key, "state_label": _state_where(st),
            "spends": bool(registry.BY_KEY.get(f"state_spends_{key}") and registry.flag(f"state_spends_{key}")),
            "negative": M.is_negative(word), "hot": weather.is_hot(), "cold": weather.is_cold_snap(),
            "season": weather.season(), "weather": weather.describe("her"), "mood": word, "when": f"{events_weekday(d)}{d.hour}点",
            "here": places.here()["id"]}


def _maybe_spend(st: dict[str, Any], now: float, paused: bool) -> dict[str, Any]:
    """每隔一阵掷一次骰子：要不要买东西（按状态、心情、余额、季节、天气）。"""
    if paused or not enabled() or not money.enabled() or sick_active(now):
        return st
    if st["key"] in money.NO_SPEND_STATES:
        return st
    said = intents.spend_decision(now)                 # 她说过要买 / 不买的
    if said and said["buy"]:
        ctx = _spend_ctx(st)
        spec = money.intent_spec(said["cat"], said.get("max_price"))
        if spec:
            spec.update(intent=said["intent"]["id"], item_hint=said.get("item") or "")
            _spawn(_spend_job(spec, ctx), ordered=True)
        else:
            intents.resolve(said["intent"]["id"], "broken", "钱不太够了", now)
        return st
    if not money.check_due(now):
        return st
    ctx = _spend_ctx(st)
    spec = money.roll(now, ctx)
    if spec and said and not said["buy"] and (not said.get("cat") or said["cat"] == spec["cat"]):
        if said.get("keep"):
            spec = None                                 # 说好了不买这一类
        else:
            intents.resolve(said["intent"]["id"], "broken", "还是没忍住买了", now)
    if spec:
        _spawn(_spend_job(spec, ctx), ordered=True)
    return st


async def _spend_job(spec: dict[str, Any], ctx: dict[str, Any]) -> None:
    """写买了什么 → 记账本 / 放进房间 / 记忆 → 按概率主动告诉我。"""
    global _spend_busy
    _spend_busy += 1
    try:
        if spec.get("item_hint"):
            ctx = dict(ctx, item_hint=spec["item_hint"])
        res = await money.purchase(db.get_settings(), spec, ctx)
        if spec.get("intent"):
            intents.resolve(spec["intent"], "done")
        p = registry.num("spend_share_prob_big" if res["big"] else "spend_share_prob")
        if res["share_line"] and random.random() < p and reading.can_send_proactive():
            posted = _say([res["share_line"]], {"kind": "spend", "entry_id": res["entry"]["id"]}, counted=True, source="money")
            money.mark_shared(res["entry"]["id"], bool(posted))
    except Exception:  # noqa: BLE001
        log.exception("买东西出错")
    finally:
        _spend_busy -= 1


def sick_until() -> float:
    try:
        return float(db.get_settings().get("life_sick_until") or 0)
    except ValueError:
        return 0.0


def sick_active(now: float | None = None) -> bool:
    return sick_until() > (now or time.time())


def start_illness(days: int, now: float | None = None) -> None:
    """生病 days 天（含今天）：期间她大多躺着，不出门。"""
    now = now or time.time()
    end = datetime.fromtimestamp(now).replace(hour=23, minute=59, second=0, microsecond=0).timestamp() + max(0, days - 1) * 86400
    db.save_settings({"life_sick_until": str(end)})
    register_event("sick", "生病了，躺着没力气", "恍惚")
    push_energy(-0.3, "sick", now)                     # 生病：没力气


# --------------------------------------------------------------------------
# 生活事件
# --------------------------------------------------------------------------
_event_busy = 0


def _event_ctx() -> dict[str, Any]:
    d = datetime.now()
    word, _why = live_mood()
    st = load_state() or {}
    return {"season": weather.season(), "weather": weather.describe("her"), "mood": word,
            "when": f"{events_weekday(d)}{d.hour}点", "state": _state_where(st)}


def _state_where(st: dict[str, Any]) -> str:
    """给事件 / 消费提示词的「你在做什么」：出门在外时带上地点（「逛街（在杭州）」）。"""
    label = STATES.get(st.get("key", "tea"), {}).get("label", "在家")
    if st.get("place") and _outside_key(st.get("key")):
        where = places.label(st["place"])
        return f"{label}（在{where}）" if where else label
    return label


def events_weekday(d: datetime) -> str:
    return "周" + "一二三四五六日"[d.weekday()]


def _event_ok(etype: str, hour: int) -> bool:
    c = events.cond(etype)
    lo, hi = c["hours"]
    if not lo <= hour < (hi if hi > 0 else 24):
        return False
    if not states.weather_ok(c["weather"], weather.is_raining(), weather.is_snowing()):
        return False
    if c["out"] and sick_active():
        return False                                       # 生病时不发生外出的事
    if etype == "friend" and not _friend_names():
        return False
    if etype in family.AWAY_EVENTS and family.away():
        return False                                       # 爸妈出差中
    return True


def _maybe_event(st: dict[str, Any], now: float, paused: bool) -> dict[str, Any]:
    if paused or not enabled() or not registry.flag("events_enabled"):
        return st
    plan = events.plan_day(now, roll_sick=lambda t: events.roll_sick(t, already_sick=sick_active(t)))
    if plan.get("sick_days") and not plan.get("sick_started"):
        events.mark_sick_started()                         # 今天判定生病了：开始生病 + 发生一件「生病」的事
        start_illness(int(plan["sick_days"]), now)
        _spawn(_event_job("sick"), ordered=True)
    etype = events.due(now, _event_ok)
    if etype:
        _spawn(_event_job(etype), ordered=True)
    return st


async def _event_job(etype: str, after: str = "", tone: str | None = None) -> None:
    """（v9）先抽这件事的基调（好事 / 平常 / 不顺：洗牌袋 + 同类不顺的冷却）→ 模型照着基调写 → 记进事件日志 / 记忆 / 人物档案
    → 加灵感；约 40% 带情绪反应（按基调的范围抽，心情词从情绪落到的那个区域里取）→ 按概率主动告诉我。
    after：外出回来时那件事的名字（「和朋友出去」……），写进提示词。"""
    global _event_busy
    _event_busy += 1
    try:
        settings = db.get_settings()
        now = time.time()
        p = M.params()
        mst = M.load()
        if tone is None:
            tone = "bad" if etype == "sick" else B.draw_tone(mst, etype, now, p, random)
        M.save(mst)
        ctx = _event_ctx()
        if after:
            ctx["state"] = f"刚{after}回来"
        ev = await events.make_event(settings, etype, ctx, tone=tone)
        word = None
        if etype != "sick":                                  # 生病的心情和精力在 start_illness 里已经推过了
            mst = M.load()
            B.add_insp(mst, now, float(p["insp_event"]) + (float(p["insp_event_good"]) if tone == "good" else 0.0), p)
            if B.reacts("event", p, random):
                dv, de = B.reaction(B.tone_row(tone, p), random)
                M.push(mst, now, p, dv, de, f"event_{etype}:{tone}", ev["text"])
                word = M.repick(mst, now, p)
            M.save(mst)
        eid = events.record(etype, ev["text"], word or "", ev["people"], ev["result"], tone=tone)
        if random.random() < registry.num("event_share_prob") and reading.can_send_proactive():
            posted = _say([ev["share_line"]], {"kind": "event", "etype": etype, "event_id": eid}, counted=True, source="event")
            db.mark_event_shared(eid, bool(posted))
    except Exception:  # noqa: BLE001
        log.exception("生活事件出错")
    finally:
        _event_busy -= 1


def _maybe_sick(st: dict[str, Any], now: float) -> dict[str, Any]:
    if not sick_active(now) or st["key"] in ("sick", "sleep") + tuple(states.MEAL_STATES):
        return st
    new = start_state(prev=st["key"], forced="sick")
    _carry(st, new)
    new["last_tick"] = now
    return new


def _outside_key(key: str | None) -> bool:
    """某个状态键算不算「在外面」。"""
    return bool(key and f"state_outside_{key}" in registry.BY_KEY and registry.flag(f"state_outside_{key}"))


def outside_now() -> bool:
    """她现在是不是在外面（散步、逛街、出门吃饭……）：回复会更慢、更短。"""
    st = load_state()
    key = st.get("key") if st else None
    return _outside_key(key)


def outside_wait() -> float:
    """她在外面时，看到消息到开始回之前要等多久（秒）。"""
    lo, hi = registry.num("outside_reply_min_s"), registry.num("outside_reply_max_s")
    return random.uniform(min(lo, hi), max(lo, hi))


# --------------------------------------------------------------------------
# 出门 / 回家：状态在「外面」和「屋里」之间切换时，主动告诉她一声
# --------------------------------------------------------------------------
OUT_HOME_PROMPT = """你是「{name}」。你的口吻（人设里的说话方式）：
{voice}

{scene}主动给她发 1 到 2 条消息，每条 18 字以内，用换行分开，像随手发的，别写成描写。只输出消息本身。"""


async def _out_home_job(going_out: bool, from_label: str, to_label: str, place: str | None = None) -> None:
    """生成并发送一条出门 / 回家的消息（不占主动消息上限，每次都发）。"""
    settings = db.get_settings()
    if llm.is_echo(settings):
        return      # 离线演示模式没有模型，跳过（正常在线模式下每次都发）
    where = places.label(place) if place else ""
    far = bool(place) and (places.get(place) or {}).get("travel_min", 0) >= 45
    if going_out:
        scene = f"你刚刚出门了，要去「{to_label}」" + (f"，去{where}" if where else "") + ("（特地出门，路上要挺久）" if far else "") + "。告诉她自己出门了。"
    else:
        scene = f"你刚刚回家了（之前在外面「{from_label}」" + (f"，在{where}" if where else "") + "）。告诉她你回来了。"
    note, told = intents.proactive_note()               # 她说过的打算：别说出相反的；刚没做到的可以顺口提
    scene += note
    if place and going_out:
        nb = notebook.life_block(where, place, n=2)    # 小本子：要去的地方
        if nb:
            scene += "\n" + nb
    try:
        from . import persona as persona_lib

        raw = await llm.complete(
            settings,
            [{"role": "system", "content": OUT_HOME_PROMPT.format(
                name=C.display_name(settings),
                voice=persona_lib.voice_excerpt(settings.get("system_prompt") or "", 1200),
                scene=scene)}],
            temperature=0.9,
        )
        parts = guards.split_messages(guards.strip_exclaim(raw))[:2]
    except Exception as exc:  # noqa: BLE001
        log.warning("生成出门/回家消息失败：%s", exc)
        return
    parts = [p for p in parts if p.strip()]
    if not parts:
        return
    sid = db.current_session_id()
    meta = {"kind": "out_home", "going_out": going_out}
    for p in parts:
        db.add_message(sid, "assistant", p, source="out_home", meta=meta)
    if told:
        intents.mark_mentioned(told)


def _wake_up(st: dict[str, Any]) -> dict[str, Any]:
    # 醒来顺手泡茶：设置里喝茶的时间段不包括现在（比如 9 点以后才喝），就按设置抽一个现在允许的
    new = start_state(prev=None, forced="tea", blurb=random.choice(WAKE_BLURBS), soft=True)
    if new["key"] not in ("tea", "tea_brew"):
        new["blurb"] = random.choice([b for b in WAKE_BLURBS if "茶" not in b])
    new["clock"] = st.get("clock", 0.0)
    new["last_tick"] = st["last_tick"]
    return new


async def _summary_job(day: str) -> None:
    try:
        await planner.make_summary(db.get_settings(), day)
    except Exception:  # noqa: BLE001
        log.exception("晚上总结出错")


async def _notebook_weekly_job(day: str) -> None:
    try:
        await notebook_write.weekly(db.get_settings(), day)
    except Exception:  # noqa: BLE001
        log.exception("每周整理小本子出错")


async def _notebook_job(day: str) -> None:
    try:
        await notebook_write.nightly(db.get_settings(), day)
    except Exception:  # noqa: BLE001
        log.exception("整理小本子出错")


# --------------------------------------------------------------------------
# 月末的信
# --------------------------------------------------------------------------
_letter_busy = False


def _maybe_letter(st: dict[str, Any], now: float, paused: bool) -> dict[str, Any]:
    """月末那天晚上日总结之后写信；页面没开着错过了的月份，下次打开时补写。"""
    global _letter_busy
    if paused or _letter_busy or not enabled() or not letters.enabled():
        return st
    ym = letters.due(now)
    if ym:
        _letter_busy = True
        _spawn(_letter_job(ym), ordered=True)
    return st


async def _letter_job(ym: str) -> None:
    """汇总这个月 → 写信 → 存进「信」分类 → 主动告诉我一声。"""
    global _letter_busy
    try:
        res = await letters.write(db.get_settings(), ym)
        if res and registry.flag("letter_notify"):
            _say([random.choice(letters.NOTICE_LINES)], {"kind": "letter", "doc_id": res["doc"]["id"], "title": res["title"]}, counted=False, source="letter")
    except Exception:  # noqa: BLE001
        log.exception("写月末的信出错")
    finally:
        _letter_busy = False


def tick(now: float | None = None) -> dict[str, Any]:
    """推进一次时间，返回当前状态（不含新消息，路由层再补）。"""
    global _ticking
    now = now or time.time()
    _ticking = True
    try:
        try:
            money.ensure_grants(now)                    # 每月发零花钱（没开页面漏掉的月份在这里补发）
        except Exception:  # noqa: BLE001
            log.exception("发零花钱出错")
        try:
            mood_tick(now, awake=not daily.is_sleeping(now))      # 心情和精力：换天（算今天的基线）、记统计、想念 / 嘴馋
        except Exception:  # noqa: BLE001
            log.exception("心情更新出错")
        st = load_state()
        if st is None:
            st = start_state()
            st["clock"] = 0.0
        start_key = st["key"]
        was_outside = _outside_key(start_key)
        last = float(st.get("last_tick") or now)
        raw_dt = max(0.0, min(TICK_CAP_S, now - last))
        st["last_tick"] = now
        st["clock"] = float(st.get("clock", 0.0)) + raw_dt

        if daily.is_sleeping(now):
            # 晚上十点到早上七点：睡着了，什么都不推进，也不会主动来找你
            if st["key"] != "sleep":
                _maybe_goodnight(now)           # 临睡前道一声晚安（只在刚到睡觉点那阵子，且网页开着）
                st = _go_to_sleep(st)
        else:
            if st["key"] == "sleep":
                st = _wake_up(st)
            dt = raw_dt
            last_user = db.last_user_message_ts()
            paused = last_user > 0 and (now - last_user) < registry.num("chat_pause_s")
            if paused:
                dt = 0.0
            if dt > 0:
                st["elapsed"] = float(st["elapsed"]) + dt
                ended: str | None = None
                finished = False
                if st["key"] == "read" and st.get("sess"):
                    st, ended, finished = _advance_read(st, dt)
                    add_read_seconds(dt, now)
                    if ended is None and read_left(now) <= 0:
                        ended = "cap"                       # 今天读满上限了
                elif st["key"] in BLURBS and st["key"] not in ("read", "cook", "eat", "eat_alone", "dine_out", "sick"):
                    # 氛围文案：偶尔换一句（本地池，不调模型）
                    if st["elapsed"] - st.get("blurb_at", 0) > random.uniform(240, 420):
                        st["blurb"] = _rand_blurb(st["key"], st.get("blurb"))
                        st["blurb_at"] = st["elapsed"]
                if ended is None:
                    if st["quit_at"] is not None and st["elapsed"] >= st["quit_at"]:
                        ended = "quit"
                    elif st["elapsed"] >= st["planned"]:
                        ended = "time"
                if ended:
                    st = _carry(st, end_state(st, ended if ended != "quit" else "time", finished_book=finished))
                    st["last_tick"] = now
            st["_paused"] = paused

        if st["key"] != "sleep" and not daily.is_sleeping(now):
            st = _maybe_meal(st, now, bool(st.get("_paused")))      # 饭点到了：做饭 / 吃饭 / 出门吃饭
            st = _maybe_event(st, now, bool(st.get("_paused")))      # 今天的生活事件 / 生病判定
            st = _maybe_sick(st, now)                               # 生病的这几天：躺着
            st = _maybe_spend(st, now, bool(st.get("_paused")))      # 要不要买点什么（零花钱）
            st = _maybe_letter(st, now, bool(st.get("_paused")))     # 月末写信
            st = _maybe_snack(st, now, 0.0 if st.get("_paused") else raw_dt)   # 边做事边吃点东西
            st = _maybe_intent_items(st, now)                        # 她说过要吃掉房间里的 xx
        if not _weather_busy and weather.due():
            _spawn(_weather_job())                      # 启动时拉一次，之后每隔几小时拉一次（联网 / 模拟）
        if st["key"] == "create" and not st.get("intent_started") and not daily.is_sleeping(now):
            st["intent_started"] = True                 # 刚进入「试图创作」：先想怎么写，并跟她说一声
            _spawn(_intent_job(), ordered=True)
        if not daily.is_sleeping(now):
            _maybe_festival_greeting()
        if daily.summary_due(now):
            # 晚上九点：总结今天的状态变化和经历，结果显示在日历里
            db.save_settings({"life_summary_day": daily.today_str(now), "life_summary_at": str(now)})
            _spawn(_summary_job(daily.today_str(now)))
            if notebook_write.enabled():
                _spawn(_notebook_job(daily.today_str(now)))     # 紧接着把今天的见闻整理进小本子
        if notebook_write.weekly_due(now) and not daily.is_sleeping(now):
            notebook.meta_set("weekly_done", daily.today_str(now))   # 先占上，免得下一次 tick 又起一个
            _spawn(_notebook_weekly_job(daily.today_str(now)))
        now_outside = _outside_key(st["key"])
        if now_outside != was_outside and registry.flag("out_home_notify"):
            # 状态刚跨过了「外面 / 屋里」的边界：出门或回家，主动说一声
            _spawn(_out_home_job(now_outside, STATES[start_key]["label"], STATES[st["key"]]["label"],
                                 st.get("place") if now_outside else places.last_place()), ordered=True)
        paused_flag = bool(st.pop("_paused", False))
        save_state(st)
        return status_view(st, paused_flag)
    finally:
        _ticking = False


def status_view(st: dict[str, Any], paused: bool = False) -> dict[str, Any]:
    book = lib.get_book(st["book_id"]) if st.get("book_id") else None
    mv = mood_view()
    word, why = mv["word"], mv["why"]
    return {
        "enabled": True,
        "mood_v": mv["v"], "mood_e": mv["e"], "energy": mv["energy"],
        "key": st["key"],
        "label": STATES[st["key"]]["label"],
        "blurb": st["blurb"] + (f"（{meals.current_note(st['key'])}）" if meals.current_note(st["key"]) else "")
                 + (f"（边吃{st['eating']['name']}）" if st.get("eating") else ""),
        "eating": ({k: st["eating"][k] for k in ("name", "kind", "place")} if st.get("eating") else None),
        "family_away": family.away(),
        "elapsed": int(st["elapsed"]),
        "paused": paused,
        "book": ({"id": book["id"], "title": book["title"]} if book else None),
        "mood": word,
        "mood_why": why,
        "sleeping": st["key"] == "sleep",
    }


# --------------------------------------------------------------------------
# 给聊天用的上下文
# --------------------------------------------------------------------------
INTERRUPT_STATES = ("game", "video", "anime")      # 在做这类「正玩着」的事时被打断，会先撒娇问一句
DEFAULT_INTERRUPT_PROB = 0.5               # settings: life_interrupt_prob


def _rolls_interrupt(st: dict[str, Any]) -> bool:
    """她正在打游戏 / 刷视频，我发来了新消息：有概率先撒娇问我干嘛打扰她。
    一段对话只掷一次（距离上一次掷骰子不到 CHAT_PAUSE_S 算同一段对话），免得每句都问。"""
    if st.get("key") not in INTERRUPT_STATES:
        return False
    now = time.time()
    if now - float(st.get("chat_roll_at") or 0) < registry.num("chat_pause_s"):
        st["chat_roll_at"] = now
        save_state(st)
        return False
    st["chat_roll_at"] = now
    save_state(st)
    try:
        p = min(1.0, max(0.0, registry.num("life_interrupt_prob")))
    except (TypeError, ValueError):
        p = DEFAULT_INTERRUPT_PROB
    return random.random() < p


def context_block() -> str:
    """当前在做什么 + 在读的书 + 最近的读书笔记，放进聊天上下文（只放最近、相关的）。"""
    if not enabled():
        return ""
    st = load_state()
    if not st:
        return ""
    lines = ["【你的生活】（这一段写的都是你自己的事，不是她的）你平时有自己的事做，网页开着的时候它们一直在进行（她和你说话时暂停）。"
             f"你和她一样，{_hour_cn(daily.sleep_hour())}睡、{_hour_cn(daily.wake_hour())}起。"]
    if st["key"] == "sleep":
        lines.append(
            "- 现在是深夜，你本来已经睡着了，是被她的消息叫醒的：迷迷糊糊、话很短、可以嘟囔一句，"
            "别精神抖擞地聊起来；可以劝她早点睡。"
        )
    else:
        mins = int(st["elapsed"] // 60)
        lines.append(f"- 刚才 / 现在你在：{STATES[st['key']]['label']}——{st['blurb']}（已经 {mins} 分钟；这是你在做的事，她没有和你一起）")
        note = meals.current_note(st["key"])
        if note:
            lines.append(f"  这顿饭：{note}。被问到吃什么时就据此说，前后一致。")
        if st.get("eating"):
            lines.append(f"  你正边吃{st['eating']['name']}边做这件事（从{clutter.PLACES.get(st['eating']['place'], '桌')}上拿的）。")
        if st["key"] == "sick":
            lines.append("  你生病了：躺着、没精神、话少、有点蔫，可以抱怨难受，别硬撑着聊很多。")
        elif sick_active():
            lines.append("  你这几天不太舒服（生病中）：没什么力气，嘴上可以提一句。")
        if outside_now():
            lines.append(
                f"  你现在在外面（{STATES[st['key']]['label']}）：回复要短（一两句），可能隔一会儿才看手机，"
                "可以说在外面不方便多打字，别长篇大论。"
            )
        if st["key"] not in ("read", "sleep", "sick", "cook", "eat", "eat_alone", "dine_out"):
            lines.append(
                "  她问你在干嘛时，就当前这件事像真的在做一样，说出**具体内容**"
                "（刷到了什么视频、游戏打到哪了、发呆时想了什么……），随口编，但编出来的要记住，之后前后一致。"
            )
        if _rolls_interrupt(st):
            lines.append(
                f"- 她这条消息打断了你（你正在{STATES[st['key']]['label']}）：先带点撒娇地问一句她干嘛打扰你"
                "（一句就好，比如「干嘛啦，正玩着呢」「又来，我这局快完了」），再正常回应她说的事；别真的生气。"
            )
    if family.away():
        lines.append("- 这几天爸妈出差了，家里只有你一个人：一个人吃饭（自己做或点外卖，端回房间吃），家里有点安静。")
    foods = clutter.available()
    if foods:
        lines.append("- 你房间里放着能吃的：" + "、".join(f"{f['name']}（{f['area']}）" for f in foods[:6]))
    lines += [x for x in [places.context_line()] if x]   # 出门在外：现在在哪
    lines += intents.context_lines()                    # 她说过的打算：生效中的 / 没做到的 / 做到了的
    lines += intents.limits_lines()                     # 现在的限制：做不到的别答应
    lines.append(reading_status_line())
    lines += writing.context_lines()
    today_events = [e for e in db.events_on(daily.today_str(), 12) if e.get("shared", 1)][-6:]
    if today_events:
        lines.append("- 你今天经历过：" + "；".join(e["text"] for e in today_events[-4:]))
    shelf = lib.list_books()
    current = [b for b in shelf if b["phal_status"] == "reading"]
    if st.get("book_id"):
        cur = lib.get_book(st["book_id"])
        if cur and cur not in current:
            current.insert(0, cur)
    for b in current[:2]:
        chapters = bk.load_chapters(b["id"])
        ct = chapters[min(b["phal_chapter"], len(chapters) - 1)]["title"] if chapters else ""
        lines.append(f"- 在读《{b['title']}》，读到「{ct}」，大约 {lib.percent(b, 'phal')}%")
    done = [b for b in shelf if b["phal_status"] == "finished"][:3]
    if done:
        lines.append("- 读完过：" + "、".join(f"《{b['title']}》" for b in done))
    paused = [b for b in shelf if b["phal_status"] in ("paused", "dropped")][:2]
    if paused:
        lines.append("- 搁下 / 没读下去：" + "、".join(f"《{b['title']}》" for b in paused))
    notes = lib.recent_notes(3)
    if notes:
        lines.append("- 你最近的读书笔记（只是你自己记的，别背给她听）：")
        lines += [f"  · 《{n['book_title']}》：{n['text']}" for n in notes]
    lines.append(
        "被问到最近在做什么 / 看什么书 / 那本怎么样时，据此如实回答，前后一致；只能谈上面写到的，不要编造书里的内容。"
        "平时回复不用每次都提自己在干嘛，自然的时候提一句就好。"
    )
    return "\n".join(lines)


def share_note(meta: dict[str, Any] | None) -> str:
    """用户从书里选了一段 / 点了一条批注发给她：告诉她她读没读过这里，以及该怎么反应。"""
    if not meta or meta.get("kind") not in ("selection", "annotation"):
        return ""
    book = lib.get_book(int(meta.get("book_id") or 0))
    if not book:
        return ""
    ch, off = int(meta.get("chapter") or 0), int(meta.get("offset") or 0)
    chapters = bk.load_chapters(book["id"])
    ch_title = chapters[ch]["title"] if 0 <= ch < len(chapters) else ""
    where = f"《{book['title']}》「{ch_title}」"
    if meta["kind"] == "annotation":
        return f"【这条引用的是你自己在{where}页边写的批注，是你读过的地方，可以顺着它聊。】"

    read = lib.phal_has_read(book, ch, off)
    if read:
        extra = ""
        near = lib.annotations_near(book["id"], ch, off)
        notes = lib.notes_in_chapter(book["id"], ch, 2)
        if near:
            extra += "你当时在这附近写过批注：" + "；".join(a["note"] for a in near) + "。"
        if notes:
            extra += "你这一章的笔记：" + "；".join(n["text"] for n in notes) + "。"
        return (
            f"【她从{where}里选了一段发给你。你读过这一段了（状态：{lib.STATUS_LABEL[book['phal_status']]}）。{extra}"
            "像读过的人那样回应，但你记得的程度和真人一样：细节可能模糊，别背书。】"
        )
    state = lib.STATUS_LABEL.get(book["phal_status"], "")
    return (
        f"【她从{where}里选了一段发给你。你还没读到这里（这本书你的状态：{state}），所以不知道前后文。"
        "如果是小说 / 故事类：别装懂，可以说你还没看到这儿、问她是不是剧透，也可以只就这一小段的字面说点感觉；"
        "如果是知识 / 说明类：你可以凭自己的印象讲讲，但要像真人：记不全、可能记错，"
        "用「好像」「我记得大概是」「不太确定」，别写成百科条目，更别说得斩钉截铁。】"
    )


# --------------------------------------------------------------------------
# 她说「休息一下」/ 你说「读会儿书」：由模型在回复末尾附结构化动作，后端执行
# --------------------------------------------------------------------------
def reading_status() -> dict[str, Any]:
    rd = read_day()
    cap = _read_cap_s()
    st = load_state() or {}
    return {
        "reads_today": bool(rd["reads"]), "used_s": float(rd["seconds"]), "cap_s": cap,
        "left_s": max(0.0, cap - float(rd["seconds"])), "full": float(rd["seconds"]) >= cap - 1,
        "reading_now": st.get("key") == "read",
    }


def reading_status_line() -> str:
    """给聊天上下文：今天读了多少 / 满没满 / 现在是不是在读。"""
    r = reading_status()
    used, cap = int(r["used_s"] // 60), int(r["cap_s"] // 60)
    if r["full"]:
        return f"- 今天看书的份已经看完了（累计 {used}/{cap} 分钟）。今天已经读满了。"
    line = f"- 今天看书累计 {used}/{cap} 分钟"
    if r["reading_now"]:
        line += "，你现在正在看书"
    elif not r["reads_today"]:
        line += "；今天你本来没打算看书"
    return line + "。"


_READ_ASK_RE = re.compile(r"读|看.{0,3}书|看会|看一会|看点|书")      # 这一句像不像在叫她去看书（像才掷骰子）


def reading_action_block(user_text: str = "") -> str:
    """她让 Phal 读书 / 休息时怎么回应（只在角色模式下注入）。
    对话意图开着时：答应了就是一条「读书」意图，提取完按概率执行（不用附动作）；关着时用回复末尾的结构化动作，回复时立刻执行。"""
    if intents.enabled():
        return _reading_intent_block(user_text)
    r = reading_status()
    lines = [
        "【读书指令】如果她明确让你休息 / 别看书了（比如「休息一下吧」「别看了」），按她的意思回应；"
        "如果她让你去读书（比如「读会儿书」「看会儿书呗」），见下面的规则。",
        "需要执行时，在回复**最后另起一行**附上动作（系统会执行并对她隐藏，不要解释它）：",
        '停止读书：{"action":"stop_reading"}　　开始读书：{"action":"start_reading"}',
        "只有她真的这么要求时才附，平时不要附。附动作的话，正文照常用你的口吻回应。",
    ]
    global _refuse_read
    _refuse_read = False
    if r["full"]:
        lines.append("今天已经读满了：她让你读书时，不要附 start_reading，用撒娇耍赖的口气拒绝（比如「今天的份看完了」「眼睛要瞎了」）。")
    elif not r["reading_now"]:
        if _READ_ASK_RE.search(user_text or "") and _roll_refuse_read():
            _refuse_read = True
            lines.append(
                f"今天还没读满（还能读 {int(r['left_s'] // 60)} 分钟），但**这会儿你不想看**——"
                "心情不对 / 手头有别的事 / 就是懒。她这次让你读书，你拒绝，**不要附 start_reading**；"
                "用你自己的口气找个理由推掉（比如「现在不想看」「等会儿」「字太多了」），别说成是因为今天读满了。"
                "她要是又磨一磨，之后你可以松口。"
            )
        else:
            lines.append(f"今天还没读满（还能读 {int(r['left_s'] // 60)} 分钟）：她让你读书，你就去读，附 start_reading，回应里说一声（比如「那我看会儿」）。")
    if r["reading_now"]:
        lines.append("你现在正在看书：她让你休息，你就停下，附 stop_reading。")
    else:
        lines.append("你现在没在看书：不用附 stop_reading。")
    return "\n".join(lines)


def _reading_intent_block(user_text: str) -> str:
    r = reading_status()
    lines = ["【读书】她让你去读书（比如「读会儿书」「看会儿书呗」）或者让你休息 / 别看了（比如「休息一下吧」），按下面的情况用你的口吻回应。"
             "答应了的话，系统之后会照你说的去做（不用附任何动作，也别说「已经开始看了」，说「那我去看会儿」这种就行）。"]
    global _refuse_read
    _refuse_read = False
    if r["full"]:
        lines.append("今天已经读满了：她让你读书时，用撒娇耍赖的口气拒绝（比如「今天的份看完了」「眼睛要瞎了」）。")
    elif not r["reading_now"]:
        if _READ_ASK_RE.search(user_text or "") and _roll_refuse_read():
            _refuse_read = True
            lines.append(
                f"今天还没读满（还能读 {int(r['left_s'] // 60)} 分钟），但**这会儿你不想看**——"
                "心情不对 / 手头有别的事 / 就是懒。她这次让你读书，你拒绝；"
                "用你自己的口气找个理由推掉（比如「现在不想看」「等会儿」「字太多了」），别说成是因为今天读满了。"
                "她要是又磨一磨，之后你可以松口。"
            )
        else:
            lines.append(f"今天还没读满（还能读 {int(r['left_s'] // 60)} 分钟）：她让你读书，一般就答应。")
    if r["reading_now"]:
        lines.append("你现在正在看书：她让你休息，一般就答应停下。")
    return "\n".join(lines)


def refused_read_this_turn() -> bool:
    """这一轮的上下文是不是已经决定了「拒绝去读书」（意图那边用：模型要是照样抽出了「去看书」，不算数）。"""
    return _refuse_read


DEFAULT_REFUSE_READ_PROB = 0.3     # 没读满时，她拒绝「去读书」的概率（settings: life_read_refuse_prob）
REFUSE_SOFTEN_S = 600              # 刚拒绝过，这么久内她再被磨一次，拒绝的概率减半
_refuse_read = False               # 这一轮的上下文是不是已经决定了「拒绝」（模型要是照样附了 start_reading，也不执行）


def _roll_refuse_read(now: float | None = None) -> bool:
    now = now or time.time()
    try:
        p = min(1.0, max(0.0, registry.num("life_read_refuse_prob")))
    except (TypeError, ValueError):
        p = DEFAULT_REFUSE_READ_PROB
    if now - float(db.get_settings().get("life_read_refused_at") or 0) < REFUSE_SOFTEN_S:
        p /= 2
    if random.random() < p:
        db.save_settings({"life_read_refused_at": str(now)})
        return True
    return False


def apply_action(action: str) -> str:
    """执行模型附带的动作。返回结果标记（测试 / 日志用）：started / stopped / full / no_book / already / not_reading / refused / ignored。"""
    if action in ("start_reading", "stop_reading") and intents.enabled():
        return "ignored"                   # 对话意图开着：读书走意图（提取完按概率执行），回复里的动作不算
    if action == "start_reading":
        if _refuse_read:
            return "refused"
        return start_reading_now()
    if action == "stop_reading":
        return stop_reading_now()
    return "ignored"


def start_reading_now(now: float | None = None) -> str:
    if read_left(now) <= 60:
        return "full"
    if not readable_books():
        return "no_book"
    st = load_state() or {}
    if st.get("key") == "read":
        return "already"
    rd = read_day(now)
    rd["reads"] = True                     # 她明确要求读：不受「今天随机读不读」限制，只受每天上限限制
    db.save_settings({"life_read_day": json.dumps(rd)})
    new = start_state(prev=st.get("key"), forced="read")
    if st:
        _carry(st, new)
    new["last_tick"] = time.time()
    save_state(new)
    return "started"


def stop_reading_now() -> str:
    """立刻结束当前阅读会话，保存进度（阅读进度本来就随时写进书架，这里只是收尾换状态）。"""
    st = load_state()
    if not st or st.get("key") != "read":
        return "not_reading"
    if st.get("book_id") and st.get("sess"):
        lib.save_phal_progress(st["book_id"], st["sess"]["chapter"], int(st["sess"]["pos"]), "reading")
    new = start_state(prev="read", forced=random.choice(["tea", "daze"]), blurb="书合上了，歇一会儿", soft=True, avoid=("read",))
    _carry(st, new)
    new["last_tick"] = time.time()
    save_state(new)
    return "stopped"


# --------------------------------------------------------------------------
# 节日祝福：节日当天打开网页后，主动发一条；每个节日每年只发一次
# --------------------------------------------------------------------------
_greeting_busy = False

GREETING_PROMPT = """你是「{name}」。你的口吻（人设里的说话方式）：
{voice}

今天是{fname}。{note}主动给她发节日祝福，像平时发消息一样：1 到 2 条，每条 20 字以内，用换行分开，
不要太正式，不要说教。只输出消息本身。"""


async def _festival_greeting_job(fid: int, fname: str, note: str, year: int) -> None:
    global _greeting_busy
    _greeting_busy = True
    try:
        settings = db.get_settings()
        parts: list[str] = []
        if not llm.is_echo(settings):
            try:
                from . import persona as persona_lib

                raw = await llm.complete(
                    settings,
                    [{"role": "system", "content": GREETING_PROMPT.format(
                        name=C.display_name(settings),
                        voice=persona_lib.voice_excerpt(settings.get("system_prompt") or "", 1200),
                        fname=fname, note=(f"备注：{note}。" if note else ""))},
                     {"role": "user", "content": "发吧。"}],
                    temperature=0.9,
                )
                parts = guards.split_messages(guards.strip_exclaim(raw))[:2]
            except Exception as exc:  # noqa: BLE001
                log.warning("生成节日祝福失败，用本地文案：%s", exc)
        if not parts:
            parts = festivals.local_greeting(fname)
        meta = {"kind": "festival", "festival_id": fid, "title": fname}
        first = reading.post_proactive("festival", parts[0], meta)        # 一次主动消息：受睡眠 / 频率限制
        if first is None:
            return                                                         # 这会儿发不了（比如频率到上限），下次 tick 再试
        for extra in parts[1:]:
            db.add_message(db.current_session_id(), "assistant", extra, source="festival", meta=meta)
        festivals.mark_greeted(fid, year)
        register_event("festival", f"今天{fname}，给她发了祝福", None)
    except Exception:  # noqa: BLE001
        log.exception("节日祝福出错")
    finally:
        _greeting_busy = False


def _maybe_festival_greeting() -> None:
    if _greeting_busy:
        return
    due = festivals.greeting_due()
    if not due:
        return
    f = due[0]
    _spawn(_festival_greeting_job(f["id"], f["name"], f.get("note") or "", datetime.now().year), ordered=True)
