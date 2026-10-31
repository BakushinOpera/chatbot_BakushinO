"""吃饭：午饭 11:30~12:30 之间随机开始，晚饭 18:00 前后；当天的吃法由代码按权重决定。

吃法：自己做 / 点外卖 / 一个人出去吃 / 和朋友出去吃 / 和家人一起吃。
* 权重受这些因素影响：工作日还是周末、心情、天气、季节、零花钱余额、有没有生病。
* 自己做饭：提前一小时进入「做饭」状态，做完进入「吃饭」；其它不需要提前准备。
* 平时在家吃饭（自己做 / 外卖 / 和家人一起）都是在客厅和家人一起吃。家人出差的那几天（family.py）：不会「和家人一起吃」，
  自己做或外卖的饭菜摆到房间的食物桌上，她一个人在房间里吃（状态 eat_alone）。
* 出去吃的那段时间她「在外面」（回复更慢、更短）。
* 菜名由模型生成：提示词里要求不带现实地名，再用现实地名检查表兜底（命中就重新生成）；吃过的记下来，几天内不重复。
* 刻意模糊学校生活：工作日午饭也不提学校，选项和周末一样（只是权重略有不同）。
* 时间只在页面开着时流动：饭点到了页面没开、错过太久就不补了。
"""

from __future__ import annotations

import json
import random
import time
from datetime import datetime, timedelta
from typing import Any

from . import character as C
from . import db, registry, whylog

METHODS = ["self", "takeout", "alone_out", "friend_out", "family"]
METHOD_LABEL = {"self": "自己做", "takeout": "点外卖", "alone_out": "一个人出去吃", "friend_out": "和朋友出去吃", "family": "和家人一起吃",
                "skip": "不吃", "food": "吃房间里的东西"}         # 后两种只来自她说过的打算（intents.py），不参与随机
MEAL_LABEL = {"lunch": "午饭", "dinner": "晚饭"}

LOCAL_DISHES = {
    "self": ["番茄炒蛋", "青菜面", "蛋炒饭", "葱油拌面", "煮了一锅粥", "土豆丝和米饭", "紫菜蛋花汤配馒头"],
    "takeout": ["黄焖鸡饭", "麻辣烫", "一份盖浇饭", "炸鸡套餐", "小馄饨", "酸辣粉", "卤肉饭"],
    "alone_out": ["一碗拉面", "小店的煲仔饭", "馄饨店的小笼", "一份简餐", "米线", "麻酱凉皮"],
    "friend_out": ["火锅", "一家新开的小餐馆", "烤肉", "自助小火锅", "炸物拼盘"],
    "family": ["妈妈做的家常菜", "一桌子热菜", "爸爸难得下厨做的红烧肉", "餐桌上很丰盛"],
}


def _away() -> bool:
    from . import family

    try:
        return family.away()
    except Exception:  # noqa: BLE001
        return False


# ───────────────────────── 当天的饭点计划 ─────────────────────────
def _load() -> dict[str, Any]:
    try:
        data = json.loads(db.get_settings().get("meal_plan") or "{}")
    except json.JSONDecodeError:
        data = {}
    return data if isinstance(data, dict) else {}


def _save(plan: dict[str, Any]) -> None:
    db.save_settings({"meal_plan": json.dumps(plan, ensure_ascii=False)})


def _day_start(ts: float) -> float:
    d = datetime.fromtimestamp(ts)
    return d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _wall(now: float, minutes: float) -> float:
    """当天墙上时钟的第 minutes 分钟对应的时间戳（夏令时切换那天也不错位）。"""
    d = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
    return (d + timedelta(minutes=minutes)).timestamp() if minutes < 0 or minutes >= 1440 else d.replace(hour=int(minutes // 60), minute=int(minutes % 60), second=int((minutes % 1) * 60)).timestamp()


def plan_for(now: float, rng: random.Random | None = None) -> dict[str, Any]:
    """今天的饭点计划：没有就现编（午饭、晚饭各一个随机时刻）。"""
    rng = rng or random
    plan = _load()
    day = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
    if plan.get("day") == day:
        return plan
    j = registry.num("meal_dinner_jitter_min")
    lunch_t = _wall(now, registry.minutes_of(registry.get("meal_lunch_start")) + rng.uniform(0, registry.num("meal_lunch_window_min")))
    dinner_t = _wall(now, registry.minutes_of(registry.get("meal_dinner_center")) + rng.uniform(-j, j))
    plan = {"day": day, "active": "",
            "lunch": {"t": lunch_t, "method": None, "stage": "pending", "dish": "", "friend": ""},
            "dinner": {"t": dinner_t, "method": None, "stage": "pending", "dish": "", "friend": ""}}
    _save(plan)
    return plan


# ───────────────────────── 怎么吃 ─────────────────────────
def method_weights(meal: str, ctx: dict[str, Any]) -> dict[str, float]:
    w = {m: registry.num(f"meal_w_{m}") for m in METHODS}
    weekend = bool(ctx.get("weekend"))
    # 爸爸很忙：工作日午饭几乎不会和家人吃；晚上和周末更多
    if meal == "lunch":
        w["family"] *= 1.5 if weekend else 0.15
    else:
        w["family"] *= 2.0 if weekend else 0.8
    if weekend:                                   # 周末：更愿意出门、和朋友 / 家人一起
        w["friend_out"] *= 1.5
        w["alone_out"] *= 1.3
    else:                                         # 工作日：随手解决
        w["self"] *= 1.1
        w["takeout"] *= 1.1
    mood = ctx.get("mood") or ""
    if ctx.get("negative"):                       # 心情差：懒得动手、不太想出门
        w["takeout"] *= 1.5
        w["self"] *= 0.5
        w["alone_out"] *= 0.8
    elif mood in ("开心", "雀跃", "兴奋", "小得意", "兴致勃勃", "元气满满", "期待", "跃跃欲试", "嘴馋"):
        w["friend_out"] *= 1.3
        w["alone_out"] *= 1.2
    if ctx.get("raining"):                        # 下雨：不想出门
        for m in ("alone_out", "friend_out"):
            w[m] *= 0.4
        w["takeout"] *= 1.3
    if ctx.get("snowing"):
        for m in ("alone_out", "friend_out"):
            w[m] *= 0.6
    if ctx.get("hot"):
        w["self"] *= 0.6
        w["takeout"] *= 1.3
    if ctx.get("cold"):
        w["alone_out"] *= 0.6
        w["friend_out"] *= 0.6
        w["self"] *= 1.1
    season = ctx.get("season")
    if season == "冬":
        w["self"] *= 1.1
    elif season == "夏":
        w["takeout"] *= 1.1
    money = float(ctx.get("money_factor", 1.0))   # 零花钱紧：少点外卖、少出去吃
    for m in ("takeout", "alone_out", "friend_out"):
        w[m] *= money
    if ctx.get("sick"):                           # 生病：没力气，家里人照顾
        w["alone_out"] = w["friend_out"] = 0.0
        w["self"] *= 0.1
        w["family"] *= 4
        w["takeout"] *= 1.5
    if not ctx.get("has_friend"):
        w["friend_out"] = 0.0
    if ctx.get("family_away"):                     # 爸妈出差：没法一起吃
        w["family"] = 0.0
    if ctx.get("home_intent"):                     # 她说过这段时间在家吃 / 吃房间里的东西：出去吃的概率低一些（还是可能被拉出去）
        for m in ("alone_out", "friend_out"):
            w[m] *= registry.num("intent_conflict_mult")
    if ctx.get("exclude") in w:                    # 说过的吃法这次没照做：换一种
        w[ctx["exclude"]] = 0.0
    if ctx.get("far_trip"):                        # 特地出门去了远的地方（见 places.py）：就在那边吃，和朋友一起来的就和朋友吃
        friends = bool(ctx.get("with_friends") and ctx.get("has_friend"))
        w = {m: 0.0 for m in w}
        w["friend_out" if friends else "alone_out"] = 1.0
    return w


def choose_method(meal: str, ctx: dict[str, Any], rng: random.Random | None = None) -> str:
    rng = rng or random
    w = method_weights(meal, ctx)
    if sum(w.values()) <= 0:
        return "self"
    m = rng.choices(METHODS, [w[m] for m in METHODS])[0]
    facts = {"weekend": "周末", "negative": "心情差", "raining": "下雨", "snowing": "下雪", "hot": "天热", "cold": "降温", "sick": "生病",
             "family_away": "爸妈出差", "far_trip": "特地出门在外面", "with_friends": "和朋友在一起", "home_intent": "说过这阵子在家"}
    on = [lab for k, lab in facts.items() if ctx.get(k)]
    whylog.record("meal", m, [MEAL_LABEL.get(meal, meal) + "；" + ("、".join(on) if on else "没有特别的情况"),
                              "各吃法的权重在设置页「吃饭」，按上面的情况乘系数"], weights=w, labels=METHOD_LABEL)
    return m


# ───────────────────────── 要经过哪些状态 ─────────────────────────
def _mins(lo: int, hi: int, rng) -> int:
    return rng.randint(lo, hi) * 60


def states_for(method: str, friend: str = "", family_names: str = "爸爸妈妈", rng: random.Random | None = None, away: bool = False) -> list[dict[str, Any]]:
    """这顿饭要经过的状态链：[{key, dur(秒), blurb}]。away：家人出差，在房间的食物桌上一个人吃。"""
    rng = rng or random
    lead = int(registry.num("meal_cook_lead_min")) * 60
    if method == "self":
        if away:
            return [{"key": "cook", "dur": lead, "blurb": "一个人在厨房做饭"},
                    {"key": "eat_alone", "dur": _mins(20, 40, rng), "blurb": "端回房间，在食物桌边吃自己做的饭"}]
        return [{"key": "cook", "dur": lead, "blurb": "在厨房忙活"},
                {"key": "eat", "dur": _mins(20, 40, rng), "blurb": f"在客厅和{family_names}一起吃自己做的饭"}]
    if method == "takeout":
        if away:
            return [{"key": "eat_alone", "dur": _mins(20, 35, rng), "blurb": "在房间的食物桌边吃外卖"}]
        return [{"key": "eat", "dur": _mins(20, 35, rng), "blurb": "在客厅吃外卖"}]
    if method == "alone_out":
        return [{"key": "dine_out", "dur": _mins(45, 90, rng), "blurb": "一个人在外面吃饭"}]
    if method == "friend_out":
        return [{"key": "dine_out", "dur": _mins(60, 120, rng), "blurb": f"和{friend or '朋友'}在外面吃饭"}]
    if method == "food":                           # 她说过拿房间里的东西当这顿饭
        return [{"key": "eat_alone", "dur": _mins(15, 30, rng), "blurb": "在食物桌边吃房间里的东西，当这顿饭"}]
    if method == "skip":
        return []
    # family：一半在家吃，一半出门
    if rng.random() < 0.6:
        return [{"key": "eat", "dur": _mins(35, 60, rng), "blurb": f"在客厅和{family_names}一起吃饭"}]
    return [{"key": "dine_out", "dur": _mins(60, 100, rng), "blurb": f"和{family_names}出门吃饭"}]


def lead_seconds(method: str) -> int:
    return int(registry.num("meal_cook_lead_min")) * 60 if method == "self" else 0


# ───────────────────────── 到点了没有 ─────────────────────────
def due(now: float, ctx_fn, friends: list[str] | None = None, family_names: str = "爸爸妈妈",
        rng: random.Random | None = None) -> dict[str, Any] | None:
    """有一顿饭该开始了就返回 {meal, method, friend, states}（并记成已开始），否则 None。
    ctx_fn(meal) 返回影响吃法的各种情况（工作日 / 心情 / 天气 / 余额…），只在真的要决定时才调用。"""
    rng = rng or random
    if not registry.flag("meals_enabled"):
        return None
    plan = plan_for(now, rng)
    grace = registry.num("meal_late_grace_min") * 60
    changed = False
    out = None
    for meal in ("lunch", "dinner"):
        m = plan[meal]
        if m["stage"] != "pending":
            continue
        if m["method"] is None:
            if now < m["t"] - int(registry.num("meal_cook_lead_min")) * 60:
                continue
            ctx = dict(ctx_fn(meal))
            ctx["has_friend"] = bool(friends)
            ctx["family_away"] = _away()
            m["away"] = ctx["family_away"]
            _decide(meal, m, ctx, friends, rng)
            changed = True
        start_at = m["t"] - lead_seconds(m["method"])
        if now < start_at:
            continue
        if now > m["t"] + grace:
            m["stage"] = "skipped"                    # 页面没开着，错过了
            changed = True
            continue
        if out is not None:
            continue
        if m["method"] == "skip":                     # 她说过这顿不吃：到点就过去了
            m["stage"] = "skipped"
            changed = True
            _intent_done(m)
            continue
        m["stage"] = "started"
        plan["active"] = meal
        changed = True
        out = {"meal": meal, "method": m["method"], "friend": m["friend"], "intent": m.get("intent"), "foods": m.get("foods") or [],
               "broken": m.get("broken") or [],
               "away": bool(m.get("away")), "states": states_for(m["method"], m["friend"], family_names, rng, away=bool(m.get("away")))}
    if changed:
        _save(plan)
    return out


def _feasible(method: str, ctx: dict[str, Any]) -> bool:
    if method in ("alone_out", "friend_out") and ctx.get("sick"):
        return False
    if method == "friend_out" and not ctx.get("has_friend"):
        return False
    return not (method == "family" and ctx.get("family_away"))


def _decide(meal: str, m: dict[str, Any], ctx: dict[str, Any], friends: list[str] | None, rng) -> None:
    """决定这顿怎么吃。她说过这顿怎么吃（intents）：按遵守概率照做；没照做就换一种（记下原因）。
    说过这段时间要在家做的事（吃可颂……）：出去吃的概率乘 intent_conflict_mult；真出去了，那件事算被打破。"""
    from . import intents

    dec = intents.meal_decision(meal, m["t"], ctx, rng)
    m["intent"], m["broken"], m["foods"], m["dish_hint"] = None, [], [], ""
    if dec and dec["keep"] and _feasible(dec["method"], ctx):
        m["method"] = dec["method"]
        whylog.record("meal", m["method"], [f"她说过「{dec['intent']['text']}」，照做了（照做概率按强度和心情、精力、天气算）"], prob=1.0, labels=METHOD_LABEL)
        m["intent"] = dec["intent"]["id"]
        m["foods"] = dec["foods"]
        m["dish_hint"] = dec["intent"]["content"].get("dish") or ""
        if m["method"] == "friend_out" and friends:
            m["friend"] = rng.choice(friends)
        return
    c = dict(ctx)
    home = [it for it in (intents.home_bound(m["t"] - 2 * 3600, m["t"] + 3600) if intents.enabled() else [])
            if not (dec and it["id"] == dec["intent"]["id"])]
    if home or (dec and dec["method"] not in intents.OUT_METHODS):
        c["home_intent"] = True
    if dec:
        c["exclude"] = dec["method"]
    m["method"] = choose_method(meal, c, rng)
    if m["method"] == "friend_out" and friends:
        m["friend"] = rng.choice(friends)
    why = break_reason(m["method"], m.get("friend") or "")
    if dec:
        if dec["keep"]:                            # 想照做，但这会儿做不到（生病不能出门、没有朋友、爸妈不在）
            why_dec = "临时去不了，" + break_reason(m["method"], m.get("friend") or "")
        elif m["method"] in intents.OUT_METHODS:
            why_dec = why
        else:
            why_dec = intents._why_not(dec["intent"], rng)
        intents.resolve(dec["intent"]["id"], "broken", why_dec, notify=False)
        m["broken"].append(dec["intent"]["id"])
    if m["method"] in intents.OUT_METHODS:
        for it in home:
            if it["kind"] == "item" and float(it.get("exec_at") or 0) < m["t"] - 1800:
                continue                           # 本来打算出门前就吃掉的：还来得及，不算打破
            intents.resolve(it["id"], "broken", why, notify=False)
            m["broken"].append(it["id"])


def break_reason(method: str, friend: str = "") -> str:
    return {"friend_out": f"被{friend or '朋友'}拉出去吃饭了", "alone_out": "临时一个人出去吃饭了", "takeout": "最后点了外卖",
            "self": "最后自己做了饭", "family": "和家里人一起吃了"}.get(method, "临时改了主意")


def _intent_done(m: dict[str, Any]) -> None:
    from . import intents

    if m.get("intent"):
        intents.resolve(m["intent"], "done")


# ───────────────────────── 吃了什么 ─────────────────────────
def recent_dishes(days: int | None = None) -> list[str]:
    days = int(registry.num("meal_repeat_days")) if days is None else days
    if days <= 0:
        return []                                # 0 = 不限制，想吃几次吃几次
    try:
        hist = json.loads(db.get_settings().get("meal_history") or "[]")
    except json.JSONDecodeError:
        hist = []
    cut = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    return [h["dish"] for h in hist if h.get("day", "") >= cut and h.get("dish")]


def record(meal: str, method: str, dish: str) -> None:
    """记下这顿吃了什么（避免连续几天吃同样的），也更新当前计划里的菜名。"""
    plan = _load()
    if plan.get(meal):
        plan[meal]["dish"] = dish
        _save(plan)
    try:
        hist = json.loads(db.get_settings().get("meal_history") or "[]")
    except json.JSONDecodeError:
        hist = []
    hist.append({"day": datetime.now().strftime("%Y-%m-%d"), "meal": meal, "method": method, "dish": dish})
    db.save_settings({"meal_history": json.dumps(hist[-40:], ensure_ascii=False)})


def local_dish(method: str, rng: random.Random | None = None) -> str:
    rng = rng or random
    avoid = set(recent_dishes())
    pool = [d for d in LOCAL_DISHES.get(method, LOCAL_DISHES["self"]) if d not in avoid] or LOCAL_DISHES.get(method, LOCAL_DISHES["self"])
    return rng.choice(pool)


def current_note(state_key: str) -> str:
    """她现在这顿饭的一句话（给状态栏 / 上下文）：比如「做番茄炒蛋」。没有就空。"""
    plan = _load()
    m = plan.get(plan.get("active") or "") if plan.get("day") == datetime.now().strftime("%Y-%m-%d") else None
    if not m or not m.get("dish") or state_key not in ("cook", "eat", "eat_alone", "dine_out"):
        return ""
    return {"cook": "在做", "eat": "吃的是", "eat_alone": "吃的是", "dine_out": "吃的是"}[state_key] + m["dish"]


def current_meal() -> dict[str, Any] | None:
    """正在进行的这顿饭（今天的计划里 active 那顿）：{method, dish, away, ...}。"""
    plan = _load()
    if plan.get("day") != datetime.now().strftime("%Y-%m-%d"):
        return None
    return plan.get(plan.get("active") or "")


def snapshot() -> dict[str, Any]:
    """给设置页 / 调试看今天的计划。"""
    plan = _load()
    out = {"day": plan.get("day")}
    for meal in ("lunch", "dinner"):
        m = plan.get(meal)
        if m:
            out[meal] = {**m, "time": datetime.fromtimestamp(m["t"]).strftime("%H:%M"),
                         "method_label": METHOD_LABEL.get(m.get("method") or "", "")}
    return out


# ───────────────────────── 让模型想吃什么 ─────────────────────────
DISH_PROMPT = """你是「{name}」。你的口吻（人设里的说话方式）：
{voice}

现在是{season}季，{weather}。到{meal}了，你今天的吃法是：{method}。你现在的心情：{mood}。
最近几天吃过：{recent}（别重复）。{note}

决定具体吃什么，输出 JSON，不要别的：
{{"dish": "吃的东西，12 字以内，朴素的叫法", "line": "饭前想主动跟她说的一句话，20 字以内，口语，自然带出想吃什么或准备做什么；不想说就留空"{price_fields}}}

要求：
- {no_real_dish}外卖 / 餐厅的牌子可以虚构；
- 符合季节和天气（冬天偏爱热食热饮，天热想吃凉的，下雨想吃热乎的），也符合你的心情；
- 不要提学校，不要提上课下课。"""
DISH_PROMPT = DISH_PROMPT.replace("{no_real_dish}", C.pick("fictional_world", "菜名**不要带现实里的地名**（比如不要写某某拉面、某地烤鸭、某地小吃）：去掉地名只留做法，或者虚构；"))

METHOD_DESC = {
    "self": "自己在家做", "takeout": "点外卖", "alone_out": "一个人出去吃", "friend_out": "和朋友出去吃", "family": "和家人一起吃",
    "food": "拿房间里的东西当这顿饭", "skip": "不吃",
}


def local_line(meal: str, method: str, dish: str) -> str:
    return {"self": f"{MEAL_LABEL[meal]}自己做，{dish}", "takeout": f"{MEAL_LABEL[meal]}点个{dish}",
            "alone_out": f"出去吃{dish}", "friend_out": f"和朋友去吃{dish}", "family": f"{MEAL_LABEL[meal]}一家人一起吃",
            "food": f"{MEAL_LABEL[meal]}就吃{dish}了", "skip": f"{MEAL_LABEL[meal]}不吃了"}.get(method, dish)


async def make_dish(settings: dict[str, Any], meal: str, method: str, ctx: dict[str, Any]) -> tuple[str, str]:
    """返回 (菜名, 饭前想说的一句话)。模型不可用 / 失败时用本地菜单。"""
    from . import guards, llm, reading, weather
    from . import persona as persona_lib
    from .planner import _json_object

    from . import money, prices

    fixed = ctx.get("fixed_dish") or ""             # 拿房间里的东西当饭：吃的就是那几样
    cat = None if fixed else money.meal_cat(method, bool(ctx.get("went_out")))
    pay = ctx.get("pay") or "aa"
    if llm.is_echo(settings):
        dish = fixed or ctx.get("dish_hint") or local_dish(method)
        line = local_line(meal, method, dish)
        if ctx.get("broken_text"):
            line = f"本来说{ctx['broken_text']}的，{ctx['broken_reason']}"
        return dish, line
    recent = "、".join(recent_dishes()) or "（没有记录）"
    note = ""
    if fixed:
        note += f"\n这顿你吃的就是：{fixed}（房间里的，dish 就写这个）。"
    elif ctx.get("dish_hint"):
        note += f"\n你之前说过想吃「{ctx['dish_hint']}」，就吃这个（dish 写它，可以写得具体一点）。"
    if ctx.get("broken_text"):
        note += (f"\n你之前跟她说过要「{ctx['broken_text']}」，现在没做成（{ctx['broken_reason']}）："
                 "line 里自然地提一句（比如「本来要吃可颂的 被朋友拉出去了」），这时 line 不能留空。")
    price_fields = ""
    if cat:                                           # 花她的钱：在哪家店吃、多少钱（一个人一份），按价格带 × 档次 × 噱头 × 地点
        budget = money.balance() / money.UNIT
        note += "\n\n" + prices.prompt_block(cat, round(budget / (2 if pay == "treat" else 1), 1) if money.enabled() else None, ctx.get("here"),
                                              pick=True)      # 这一带去过的店：去老店还是试新店（见 notebook.shops_near）
        note += {"aa": "\n和朋友 AA：price 填你自己那份。" if method == "friend_out" else "",
                 "treated": "\n这顿朋友请客，你不用付钱（price 照样填一个人一份大概多少）；line 里可以自然提一句。",
                 "treat": "\n这顿你请朋友，你付两份（price 填一个人一份）；line 里可以自然提一句。"}[pay]
        note += f"\n比平时贵的（商场里的、高档店、网红店、限定、被坑了、在{prices.pricey_places()}吃的），line 可以自然地带一句为什么贵。"
        price_fields = ", " + prices.FIELDS.replace(f"（数字，单位{C.unit()}）", f"（数字，单位{C.unit()}，一个人一份）")
    from . import notebook

    nb_keys = [prices.BAND[b][1] for b in (prices.CAT_BANDS.get(cat) or ())] if cat else []
    nb = notebook.life_block(" ".join([METHOD_DESC.get(method, method), ctx.get("dish_hint") or "", fixed, *nb_keys]), ctx.get("here"),
                             chapters=("food",))                 # 小本子：这一带的地方 / 店、吃过的东西
    if nb:
        note += "\n\n" + nb
    system = DISH_PROMPT.format(
        name=C.display_name(settings), voice=persona_lib.voice_excerpt(settings.get("system_prompt") or "", 900),
        season=weather.season(), weather=weather.describe("her") or "天气一般", meal=MEAL_LABEL[meal],
        method=METHOD_DESC.get(method, method), mood=ctx.get("mood") or "平静", recent=recent, note=note, price_fields=price_fields)
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": "决定吧。"}]
    retried = False
    for _ in range(3):
        try:
            raw = await llm.complete(settings, msgs, temperature=1.0)
        except Exception:  # noqa: BLE001
            break
        data = _json_object(raw)
        dish = fixed or str(data.get("dish") or "").strip()[:20]
        line = guards.strip_exclaim(str(data.get("line") or "").strip())[:40]
        if not dish or (dish in recent_dishes() and not fixed and dish != ctx.get("dish_hint")):
            continue
        impression = guards.strip_exclaim(str(data.get("impression") or "").strip())[:60]
        text = dish + line + str(data.get("shop") or "") + impression
        if reading.mentions_real_place(text) or guards.find_banned(text):
            continue                                   # 命中现实地名 / 禁词：重新生成
        if cat:
            info = prices.normalize(data, cat)
            if ctx.get("here"):
                from . import places

                info["place"] = places.price_place(ctx["here"])      # 地点由她现在在哪决定（外卖 = 家里）
            if (info["price"] <= 0 or not prices.in_range(info)) and not retried:
                lo, hi = prices.allowed(info)
                retried = True                         # 价格不合理（比如一碗羊肉面 200）：告诉她范围，重想一次；还不行就夹回范围内
                msgs = msgs[:2] + [{"role": "assistant", "content": raw},
                                   {"role": "user", "content": f"「{dish}」{info['price']:g} {C.unit()}不合理：{prices.BAND[info['band']][1]}"
                                    f"（{prices.label('tier', info['tier'])}、{prices.label('place', info['place'])}"
                                    f"{'、' + prices.label('gimmick', info['gimmick']) if info['gimmick'] != 'none' else ''}）一个人一份应该在 {lo:g}~{hi:g} {C.unit()}之间。"
                                    "重新想一下，吃的、店和价格要对得上，还是输出同样的 JSON。"}]
                continue
            final, info = money.settle_price(cat, 0, None, info, dish)
            prices.remember(info.get("shop", ""), dish, info, final / money.UNIT, impression=impression)   # 店和价格记进小本子，第一次来的印象写进那一条
            ctx["priced"] = dict(info, pay=pay)
        return dish, line
    dish = fixed or ctx.get("dish_hint") or local_dish(method)
    if ctx.get("broken_text"):
        return dish, f"本来说{ctx['broken_text']}的，{ctx['broken_reason']}"
    return dish, local_line(meal, method, dish)
