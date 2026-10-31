"""生活事件：她的日子里偶尔发生的小事（神秘学、和家人、和朋友、和路人、生病）。

怎么发生（第 9 节）：
1. 每天早上起床后第一次 tick：代码先决定今天有几件事（默认 0 件 40%、1 件 40%、2 件 15%、3 件 5%），不是每天都有事；
2. 再给每件事抽一个发生时间（按时段权重；21 点以后到起床是 0），并抽一个类型；
3. 每种事件有自己的条件：允许的时段、天气、是否需要出门（睡觉 / 生病时不发生外出的）；
4. 到时间检查条件：满足就触发；不满足就在当天的允许时段里往后顺延一次，还不行就作废。
生病单独判定：季节交替或气温骤降时概率更高，每天只判定一次，持续 2~4 天。

发生之后：内容由模型生成（带现实地名 / 禁词检查，只能用人物档案里的人），写进事件日志、
「你自己说过的事」类记忆、相关人物的「最近发生过的事」；按概率（默认 50%）主动告诉我，
没告诉我的事不写进日总结，但我问起「今天怎么样」时她可能会提到。
神秘学的事记进「神秘学经验」，攒够几次她会有一条自己的小心得，也可能来问我（在她眼里我是神秘学家）。
"""

from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timedelta
from typing import Any

from . import character as C
from . import db, llm, people, registry, whylog
from .event_defs import DEFAULTS, LAST_HOUR, LOCAL, LOCAL_TONE, SICK, SLOTS, TONE_LABEL, TYPES  # noqa: F401

log = logging.getLogger(__name__)

# ───────────────────────── 当天的计划 ─────────────────────────
def _load() -> dict[str, Any]:
    try:
        data = json.loads(db.get_settings().get("event_plan") or "{}")
    except json.JSONDecodeError:
        data = {}
    return data if isinstance(data, dict) else {}


def _save(plan: dict[str, Any]) -> None:
    db.save_settings({"event_plan": json.dumps(plan, ensure_ascii=False)})


def cond(etype: str) -> dict[str, Any]:
    return {"hours": (int(registry.num(f"ev_from_{etype}")), int(registry.num(f"ev_to_{etype}"))),
            "weather": str(registry.get(f"ev_weather_{etype}")), "out": bool(registry.get(f"ev_out_{etype}"))}


def _pick_count(rng) -> int:
    w = [registry.num(f"ev_count_{i}") for i in range(4)]
    n = rng.choices(range(4), w)[0] if sum(w) > 0 else 0
    whylog.record("event", str(n), ["今天有几件事（起床后定一次）"], weights={str(i): x for i, x in enumerate(w)}, labels={str(i): f"{i} 件" for i in range(4)})
    return n


def _pick_time(now: float, rng) -> float | None:
    """按时段权重抽一个还没过去的时刻。"""
    base = datetime.fromtimestamp(now).replace(minute=0, second=0, microsecond=0)
    cands = []
    for sid, lo, hi, _ in SLOTS:
        start, end = max(base.replace(hour=lo).timestamp(), now), base.replace(hour=hi).timestamp()      # 按墙上时钟算，夏令时切换那天也不错位
        w = registry.num(f"ev_slot_{sid}")
        if end > start + 60 and w > 0:
            cands.append((start, end, w * (end - start) / ((hi - lo) * 3600)))
    if not cands:
        return None
    s, e, _ = rng.choices(cands, [c[2] for c in cands])[0]
    return rng.uniform(s, e)


def _pick_type(hour: int, rng, friends: bool, have: set[str]) -> str | None:
    w: dict[str, float] = {}
    for t in TYPES:
        lo, hi = int(registry.num(f"ev_from_{t}")), int(registry.num(f"ev_to_{t}"))
        x = registry.num(f"ev_w_{t}") * (1.0 if lo <= hour < hi else 0.1)     # 时间窗不合适的也留一点机会（之后靠顺延 / 作废）
        if (t == "friend" and not friends) or (t in ("mom", "dad", "parents") and not (TYPES[t][2] in have or (t == "parents" and {"mom", "dad"} & have))):
            x = 0.0
        w[t] = x
    if sum(w.values()) <= 0:
        return None
    t = rng.choices(list(w), list(w.values()))[0]
    whylog.record("event", t, [f"{hour} 点那件事的类型（不在它的时段里的 ×0.1）"], weights=w, labels={k: TYPES[k][0] for k in TYPES})
    return t


def plan_day(now: float, rng: random.Random | None = None, roll_sick=None) -> dict[str, Any]:
    """今天的事件计划：没有就现编。roll_sick(now) -> 生病天数（0 = 没病），每天只判定一次。"""
    rng = rng or random
    plan = _load()
    day = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
    if plan.get("day") == day:
        return plan
    friends = bool([p for p in people.list_people(drafts=False) if p["kind"] == "friend"])
    names = {p["name"] for p in people.list_people(drafts=False) if p["kind"] == "family"}
    have = {"mom"} if "妈妈" in names else set()
    if "爸爸" in names:
        have.add("dad")
    events: list[dict[str, Any]] = []
    if registry.flag("events_enabled"):
        for _ in range(_pick_count(rng)):
            t = _pick_time(now, rng)
            if t is None:
                break
            etype = _pick_type(datetime.fromtimestamp(t).hour, rng, friends, have)
            if etype:
                events.append({"t": t, "type": etype, "state": "pending", "retried": False})
    events.sort(key=lambda e: e["t"])
    plan = {"day": day, "events": events, "sick_days": int(roll_sick(now)) if roll_sick else 0, "sick_started": False}
    _save(plan)
    return plan


def mark_sick_started() -> None:
    plan = _load()
    plan["sick_started"] = True
    _save(plan)


def due(now: float, ok_fn, rng: random.Random | None = None) -> str | None:
    """有一件事到点了就返回它的类型（并记成已发生）。ok_fn(type, hour) 判断此刻满足不满足条件。"""
    rng = rng or random
    plan = _load()
    if plan.get("day") != datetime.fromtimestamp(now).strftime("%Y-%m-%d"):
        return None
    base = datetime.fromtimestamp(now).replace(minute=0, second=0, microsecond=0)
    out = None
    changed = False
    for ev in plan.get("events", []):
        if ev["state"] != "pending" or ev["t"] > now or out is not None:
            continue
        hour = datetime.fromtimestamp(now).hour
        if ok_fn(ev["type"], hour):
            ev["state"] = "fired"
            out = ev["type"]
        elif not ev["retried"]:
            hi = min(cond(ev["type"])["hours"][1], LAST_HOUR)
            nt = now + rng.uniform(1800, 5400)                  # 顺延一次：半小时到一个半小时之后
            if nt < base.replace(hour=min(hi, 23)).timestamp() + (3600 if hi >= 24 else 0):
                ev["t"], ev["retried"] = nt, True
            else:
                ev["state"] = "void"
        else:
            ev["state"] = "void"
        changed = True
    if changed:
        _save(plan)
    return out


# ───────────────────────── 生病判定 ─────────────────────────
def sick_probability(now: float | None = None) -> float:
    from . import weather

    d = datetime.fromtimestamp(now) if now else datetime.now()
    p = registry.num("sick_p_base")
    if d.month in (3, 6, 9, 12) and d.day <= registry.num("sick_season_window_days"):
        p = max(p, registry.num("sick_p_season"))                # 季节交替
    if weather.temp_drop_24h() >= registry.num("sick_drop_c") or weather.is_cold_snap():
        p = max(p, registry.num("sick_p_drop"))                  # 气温骤降 / 寒潮
    return p


def roll_sick(now: float, rng: random.Random | None = None, already_sick: bool = False) -> int:
    rng = rng or random
    if already_sick or not registry.flag("events_enabled"):
        return 0
    if rng.random() < sick_probability(now):
        lo, hi = int(registry.num("sick_days_min")), int(registry.num("sick_days_max"))
        return rng.randint(min(lo, hi), max(lo, hi))
    return 0


# ───────────────────────── 生成内容 ─────────────────────────
EVENT_PROMPT = """你是「{name}」。你的口吻（人设里的说话方式）：
{voice}

现在是{season}季，{when}，你这边{weather}。你在{state}，心情{mood}。
刚刚发生了一件小事，类型是「{label}」（比如：{desc}）。这件事的基调：**{tone}**——照着这个基调写。
{people}
最近发生过的（别重复）：{recent}
{notes}
写这件事，输出 JSON，不要别的：
{{"text": "发生了什么，第一人称，口语，40 字以内，具体一点（人、物、动作）", "share_line": "{share_rule}", "result": "{result_rule}"}}

要求：
- 小事就是小事，别写得戏剧化、别升华；
- 只能用上面给出的人物；路人不起名字；不要现实里的国家、城市、学校名；刻意不提学校和班级；
{occult_rules}"""
EVENT_PROMPT = EVENT_PROMPT.replace("不要现实里的国家、城市、学校名；", C.pick("fictional_world", "不要现实里的国家、城市、学校名；"))
OCCULT_RULES = ("- 神秘学的事：你只是新人，写成「好像有点感觉」「可能是巧合」这种；\n"
                "- 神秘学的用具和材料在这个世界里很贵（最普通的也要 800 {unit}以上），你没有，只能凑合：不要写成你买了或拥有了神秘学的用具，除非上面的信息里已经有；")


def local_event(etype: str, friend: str = "", tone: str = "neutral") -> str:
    """没有模型时的本地句子：按基调挑（没有这个基调的句子就随便挑一句）。"""
    pool = (LOCAL_TONE.get(etype) or {}).get(tone) or [t for ts in (LOCAL_TONE.get(etype) or {}).values() for t in ts] or LOCAL.get(etype) or ["今天没什么特别的"]
    return random.choice(pool).format(friend=friend or "朋友")


async def make_event(settings: dict[str, Any], etype: str, ctx: dict[str, Any], ask: bool = False, tone: str = "neutral") -> dict[str, Any]:
    """返回 {text, mood（空：心情由代码按基调定）, share_line, result, people: [名字]}。tone：基调（代码抽好的）。模型不可用 / 失败时用本地句子。"""
    from . import guards, reading
    from . import persona as persona_lib
    from .planner import _json_object

    label, desc, who = SICK if etype == "sick" else TYPES[etype]
    all_people = people.list_people(drafts=False)
    involved: list[dict[str, Any]] = []
    if who == "mom":
        involved = [p for p in all_people if p["name"] == "妈妈"]
    elif who == "dad":
        involved = [p for p in all_people if p["name"] == "爸爸"]
    elif who == "parents":
        involved = [p for p in all_people if p["name"] in ("爸爸", "妈妈")]
    elif who == "friend":
        friends = [p for p in all_people if p["kind"] == "friend"]
        involved = [random.choice(friends)] if friends else []
    friend_name = involved[0]["name"] if who == "friend" and involved else ""
    local = {"text": local_event(etype, friend_name, tone), "mood": "", "share_line": "", "result": "", "people": [p["name"] for p in involved]}
    local["share_line"] = local["text"][:30]
    if llm.is_echo(settings):
        return local
    people_txt = ("涉及的人（只能用他们）：" + "；".join(f"{p['name']}（{p['relation']}）{p['personality']}{p['how']}" for p in involved)) if involved else "不涉及固定的人物。"
    recent = "；".join(e["text"] for e in recent_events(5)) or "（没有）"
    share_rule = ("如果要告诉她，你会怎么说，25 字以内，口语。这次是想**问她一件事**（在你眼里她懂神秘学），写成一个问句"
                  if ask else "如果要告诉她，你会怎么说，25 字以内，口语，像随手发的消息")
    result_rule = "success 有点成果 / fail 失败了 / weird 有点怪 / nothing 没什么，四选一" if etype == "occult" else ""
    system = EVENT_PROMPT.format(unit=C.unit(), occult_rules=C.pick("occult", "\n" + OCCULT_RULES.replace("{unit}", C.unit())), 
        name=C.display_name(settings), voice=persona_lib.voice_excerpt(settings.get("system_prompt") or "", 900),
        season=ctx.get("season", ""), when=ctx.get("when", ""), weather=ctx.get("weather") or "天气一般", state=ctx.get("state", "在家"),
        mood=ctx.get("mood") or "平静", label=label, desc=desc, people=people_txt, recent=recent,
        tone=TONE_LABEL.get(tone, TONE_LABEL["neutral"]), share_rule=share_rule, result_rule=result_rule, notes=_notes(etype, label, desc, ctx))
    for _ in range(3):
        try:
            raw = await llm.complete(settings, [{"role": "system", "content": system}, {"role": "user", "content": "写吧。"}], temperature=1.0)
        except Exception:  # noqa: BLE001
            break
        data = _json_object(raw)
        text = guards.strip_exclaim(str(data.get("text") or "").strip())[:80]
        line = guards.strip_exclaim(str(data.get("share_line") or "").strip())[:60]
        if not text:
            continue
        if reading.mentions_real_place(text + line) or guards.find_banned(text + line):
            continue
        return {"text": text, "mood": "", "share_line": line or text[:30],
                "result": str(data.get("result") or "").strip(), "people": [p["name"] for p in involved]}
    return local


def _notes(etype: str, label: str, desc: str, ctx: dict[str, Any]) -> str:
    """小本子：当时所在的地方 + 事件类型（神秘学的事带上神秘学章节，路人 / 网络的事带上听说的事）。"""
    from . import notebook, places

    try:
        here = places.here()["id"]
    except Exception:  # noqa: BLE001
        here = None
    chs = {"occult": ("occult",), "stranger": ("heard", "place"), "net": ("heard",)}.get(etype, ())
    nb = notebook.life_block(f"{label} {desc} {ctx.get('state', '')}", here, chapters=chs)
    return (nb + "\n") if nb else ""


# ───────────────────────── 记下来 ─────────────────────────
def recent_events(n: int = 8) -> list[dict[str, Any]]:
    with db._lock:
        rows = db.connect().execute("SELECT * FROM life_events WHERE etype IS NOT NULL AND etype <> '' ORDER BY id DESC LIMIT ?", (int(n),)).fetchall()
    return [dict(r) for r in rows]


def record(etype: str, text: str, mood: str, people_names: list[str], result: str = "", tone: str | None = None) -> int:
    """写进事件日志（先记成「没告诉她」，真的发出去了再改）、你自己说过的事（记忆）、相关人物的档案。返回事件 id。
    mood：这件事带来的心情词（没有情绪反应就空）；tone：基调（good / neutral / bad）。"""
    day = datetime.now().strftime("%Y-%m-%d")
    eid = db.add_event(day, "event", text, mood or None, shared=False, etype=etype, people=json.dumps(people_names, ensure_ascii=False), tone=tone)
    db.add_memory(f"{day[5:]} {text}", kind="event", category="phal_fact", source="event")
    for name in people_names:
        for p in people.list_people(drafts=False):
            if p["name"] == name:
                people.add_recent(p["id"], text, day)
    if etype == "occult":
        log = _occult_log()
        log.append({"day": day, "text": text, "result": result or "nothing"})
        db.save_settings({"occult_log": json.dumps(log[-60:], ensure_ascii=False)})
    return eid


# ───────────────────────── 神秘学经验 ─────────────────────────
def _occult_log() -> list[dict[str, Any]]:
    try:
        data = json.loads(db.get_settings().get("occult_log") or "[]")
    except json.JSONDecodeError:
        data = []
    return data if isinstance(data, list) else []


def occult_insights() -> list[str]:
    try:
        data = json.loads(db.get_settings().get("occult_insights") or "[]")
    except json.JSONDecodeError:
        data = []
    return [str(x) for x in data] if isinstance(data, list) else []


INSIGHT_PROMPT = """你是「{name}」，在悄悄学神秘学的新人。下面是你最近的神秘学经历（按时间）：
{log}
你已有的小心得：{old}

请写一条新的小心得，一句话，25 字以内，是你自己摸索出来的体会，不确定的语气（「好像」「我觉得」），不要写成教科书，不要和已有的重复。
只输出这一句。"""


async def maybe_insight(settings: dict[str, Any]) -> str | None:
    """每攒够几次神秘学经历，她有一条自己的小心得。返回新心得或 None。"""
    log = _occult_log()
    every = int(registry.num("occult_insight_every"))
    if every <= 0 or not log or len(log) % every != 0:
        return None
    old = occult_insights()
    if len(old) >= 12 and len(log) // every <= len(old):
        return None
    if llm.is_echo(settings):
        new = "好像心静下来的时候更容易有感觉"
    else:
        try:
            raw = await llm.complete(settings, [{"role": "system", "content": INSIGHT_PROMPT.format(
                name=C.display_name(settings), log="\n".join(f"- {e['day'][5:]} {e['text']}（{e['result']}）" for e in log[-8:]),
                old="；".join(old[-5:]) or "（还没有）")}, {"role": "user", "content": "写吧。"}], temperature=0.9)
            new = raw.strip().strip("「」\"'")[:40]
        except Exception:  # noqa: BLE001
            return None
    if not new or new in old:
        return None
    db.save_settings({"occult_insights": json.dumps((old + [new])[-12:], ensure_ascii=False)})
    return new


def occult_block(user_text: str = "") -> str:
    """她的神秘学经验（聊到神秘学相关时才带进上下文）。角色没开神秘学就没有。"""
    if not C.feature("occult"):
        return ""
    import re

    if not re.search(r"神秘|占卜|塔罗|感应|玄学|灵|符|梦|星|仪式|冥想|直觉", user_text or ""):
        return ""
    log = _occult_log()
    if not log:
        return ""
    ok = sum(1 for e in log if e.get("result") == "success")
    lines = [f"【你的神秘学经验】你悄悄练了 {len(log)} 次，其中有点成果的 {ok} 次。最近：" + "；".join(e["text"] for e in log[-3:]) + "。"]
    ins = occult_insights()
    if ins:
        lines.append("你自己摸出来的小心得：" + "；".join(ins[-3:]) + "。")
    lines.append("她（用户）在你眼里是懂神秘学的人，你可以请教，但你只是新人，别装懂。")
    return "\n".join(lines)


# ───────────────────────── 给上下文 ─────────────────────────
def unshared_today() -> list[dict[str, Any]]:
    day = datetime.now().strftime("%Y-%m-%d")
    return [e for e in db.events_on(day) if e.get("etype") and not e.get("shared")]


def context_block() -> str:
    """今天发生了、但还没主动告诉她的事：她问起「今天怎么样」之类时，可以自然地提到。"""
    items = unshared_today()[-3:]
    if not items:
        return ""
    return ("【你今天经历过、还没跟她说的事】" + "；".join(e["text"] for e in items)
            + "。她问起今天怎么样、最近有什么事时可以自然提一两件；没问就不用硬说。")


def snapshot() -> dict[str, Any]:
    """给设置页看：今天的计划 + 最近的事件（包括没告诉她的）。"""
    plan = _load()
    out_plan = [{"time": datetime.fromtimestamp(e["t"]).strftime("%H:%M"), "type": e["type"], "label": TYPES[e["type"]][0], "state": e["state"]}
                for e in plan.get("events", [])]
    recent = [{"day": e["day"], "time": e["created_at"][11:16], "type": e["etype"], "text": e["text"], "shared": bool(e["shared"])} for e in recent_events(12)]
    return {"day": plan.get("day"), "plan": out_plan, "sick_days": plan.get("sick_days", 0), "recent": recent,
            "occult": {"count": len(_occult_log()), "insights": occult_insights()}}
