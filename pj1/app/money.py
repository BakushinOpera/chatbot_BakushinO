"""零花钱：每月发、账本、消费流程。

* 单位：账本存整数的小单位（1 块 = 10 小单位），显示成「xx.x 块」（单位见角色配置）。每月 1 号发 1500 块（额度、日子都可配）；
  月底结余默认滚入下个月；页面没开着漏掉的月份，下次打开时补发。
* 余额紧会影响她的选择：少点外卖（`meal_factor()` 给饭点调度用）、少买东西，聊天里她嘴上可能抱怨。
* 消费由代码按概率触发（`roll`，结合状态、心情、余额、季节、天气：只定类别、大概想花多少、最多花得起多少），
  买什么 / 在哪家店 / 多少钱由模型生成（牌子可以虚构，现实地名检查 + 禁词检查，不合格重生成，最后用本地兜底）。
  价格要落在「价格带 × 档次 × 噱头 × 地点」里（v13，见 prices.py），同一家店的同一样东西价格保持一致。
  外卖、出去吃由饭点调度扣钱（`charge_meal`）：和朋友吃多数 AA，偶尔朋友请 / 她请。
* 每笔消费记进账本（时间、物品、金额、分类、她的理由、是否告诉过我），同时记成一条「没告诉你的事」；
  按概率主动告诉我；衣服、游戏、饰品这类会留下来的东西自动放进她的房间（`room.add_auto`）。
* 书架上的书是我给她买的，不扣她的钱。我能看到完整的账本，她不会觉得奇怪。
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from datetime import datetime, timedelta
from typing import Any

from . import booklook, clutter, db, llm, places, prices, registry, room, whylog
from . import character as C
from .money_defs import CATS, LABEL, LOCAL, NO_SPEND_STATES, ONLY_IN, RELEVANT, RESERVE_CATS  # noqa: F401

log = logging.getLogger(__name__)

UNIT = C.sub_per_unit()          # 1 大单位 = 多少小单位（块 → 小单位），见角色配置
CAT_KEYS = [c[0] for c in CATS]
TIER_LABEL = {"ok": "宽裕", "low": "有点紧", "tight": "很紧"}


def bi(qian: float) -> int:
    """大单位 → 小单位（整数）。"""
    return int(round(float(qian) * UNIT))


def fmt(b: int) -> str:
    """小单位 → 「xx.x」（大单位）。"""
    return f"{b / UNIT:.1f}"


def enabled() -> bool:
    return registry.flag("money_enabled")


def _q(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    with db._lock:
        return [dict(r) for r in db.connect().execute(sql, params).fetchall()]


def _exec(sql: str, params: tuple = ()) -> int:
    with db._lock:
        conn = db.connect()
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid if sql.lstrip().upper().startswith("INSERT") else cur.rowcount


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# ───────────────────────── 账本 ─────────────────────────
def _entry(r: dict[str, Any]) -> dict[str, Any]:
    cat = r["category"]
    return {"id": r["id"], "created_at": r["created_at"], "day": r["day"], "time": r["created_at"][11:16], "kind": r["kind"],
            "category": cat, "category_label": LABEL.get(cat, cat or LABEL.get(r["kind"], "")), "item": r["item"],
            "delta": r["delta"], "delta_text": f"{r['delta'] / UNIT:+.1f}", "reason": r["reason"], "shared": bool(r["shared"]),
            "room_item": r["room_item"], "room_book": r.get("room_book", 0), "event_id": r["event_id"]}


def _insert(kind: str, category: str, item: str, delta: int, reason: str = "", shared: bool = False, day: str | None = None,
            created: str | None = None, room_item: int = 0, event_id: int = 0) -> int:
    return _exec(
        "INSERT INTO money_ledger (created_at, day, kind, category, item, delta, reason, shared, room_item, event_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (created or _now_str(), day or _today(), kind, category, item, int(delta), reason, int(shared), room_item, event_id))


def get_entry(entry_id: int) -> dict[str, Any] | None:
    rows = _q("SELECT * FROM money_ledger WHERE id = ?", (int(entry_id),))
    return _entry(rows[0]) if rows else None


def balance() -> int:
    """余额（小单位）。"""
    return int(_q("SELECT COALESCE(SUM(delta), 0) AS b FROM money_ledger")[0]["b"])


def ensure_grants(now: float | None = None) -> int:
    """发零花钱：到了发放日就发这个月的；之前没开页面漏掉的月份补发。返回这次发了几个月。第一次使用时马上发当月的。"""
    if not enabled():
        return 0
    d = datetime.fromtimestamp(now or time.time())
    gd = int(registry.num("money_grant_day"))
    cur = f"{d.year:04d}-{d.month:02d}"
    last = db.get_settings().get("money_granted_through") or ""
    if last == cur:
        return 0
    months: list[str] = []
    first = not last
    if first:
        months = [cur]
    else:
        y, m = (int(x) for x in last.split("-"))
        while True:
            m += 1
            if m > 12:
                y, m = y + 1, 1
            ym = f"{y:04d}-{m:02d}"
            if ym > cur:
                break
            months.append(ym)
        if months and months[-1] == cur and d.day < gd:
            months.pop()                                   # 这个月还没到发放日
    amount = bi(registry.num("money_monthly"))
    for ym in months:
        day = d.strftime("%Y-%m-%d") if first else f"{ym}-{gd:02d}"
        created = _now_str() if first else f"{day} 00:00:00"
        bal = balance()
        if not registry.flag("money_carry") and bal > 0:
            _insert("expire", "", "上个月的结余清零", -bal, "不滚入下个月", True, day, created)
        _insert("grant", "grant", f"{int(ym[5:])} 月零花钱", amount, "", True, day, created)
    if months:
        db.save_settings({"money_granted_through": months[-1]})
    return len(months)


def tier(bal: int | None = None) -> str:
    b = balance() if bal is None else bal
    if b < bi(registry.num("money_tight")):
        return "tight"
    if b < bi(registry.num("money_low")):
        return "low"
    return "ok"


def factor(bal: int | None = None) -> float:
    """余额对「想花钱」的影响：宽裕 1.0，紧 / 很紧时变小。"""
    if not enabled():
        return 1.0
    t = tier(bal)
    return 1.0 if t == "ok" else registry.num("money_factor_low" if t == "low" else "money_factor_tight")


def meal_factor() -> float:
    """给饭点调度用：余额紧就少点外卖、少出去吃。"""
    try:
        return factor()
    except Exception:  # noqa: BLE001
        return 1.0


# ───────────────────────── 价格 ─────────────────────────
def price_range(cat: str) -> tuple[float, float]:
    """这一类东西（普通店、在家所在的地方）最低 / 最高多少钱：取它能落进的那几个价格带的并集。"""
    bands = [prices.band(k) for k in prices.CAT_BANDS.get(cat, ())] or [{"lo": 1.0, "hi": 1.0}]
    return min(b["lo"] for b in bands), max(b["hi"] for b in bands)


def common_range(cat: str) -> tuple[float, float]:
    b = prices.band(prices.CAT_BANDS[cat][0])
    return b["c_lo"], b["c_hi"]


def _skewed(lo: float, hi: float, rng) -> int:
    return bi(lo + (hi - lo) * rng.random() ** 1.6)


# ───────────────────────── 什么时候买什么 ─────────────────────────
def check_due(now: float) -> bool:
    """多久考虑一次要不要买东西。刚启动的第一次只记下时间，不马上买。"""
    last = float(db.get_settings().get("money_last_check") or 0)
    if now - last < registry.num("spend_check_min") * 60:
        return False
    db.save_settings({"money_last_check": str(now)})
    return last > 0


def _today_count(day: str) -> int:
    marks = ",".join("?" * len(CAT_KEYS))
    return int(_q(f"SELECT COUNT(*) AS n FROM money_ledger WHERE kind = 'spend' AND day = ? AND category IN ({marks})", (day, *CAT_KEYS))[0]["n"])


def _big_blocked(now: float) -> bool:
    days = int(registry.num("spend_big_cooldown_days"))
    if days <= 0:
        return False
    marks = ",".join("?" * len(CAT_KEYS))
    rows = _q(f"SELECT day FROM money_ledger WHERE kind = 'spend' AND category IN ({marks}) AND delta <= ? ORDER BY id DESC LIMIT 1",
              (*CAT_KEYS, -bi(registry.num("spend_big_price"))))
    if not rows:
        return False
    last = datetime.strptime(rows[0]["day"], "%Y-%m-%d").date()
    return (datetime.fromtimestamp(now).date() - last).days < days


def roll(now: float, ctx: dict[str, Any], rng: random.Random | None = None) -> dict[str, Any] | None:
    """这会儿要不要买东西、买哪一类、大概花多少。ctx：state（状态键）、spends（这个状态可能花钱）、negative（心情差）、hot、cold。
    返回 {cat, price（大概想花多少，小单位）, cap（最多花得起多少，小单位）, big, impulse}，不买就 None。最终价格由模型按价格带定（purchase）。"""
    rng = rng or random
    if not enabled() or ctx.get("state") in NO_SPEND_STATES:
        return None
    if _today_count(datetime.fromtimestamp(now).strftime("%Y-%m-%d")) >= int(registry.num("spend_max_per_day")):
        return None
    bal = balance()
    p = registry.num("spend_prob_spend" if ctx.get("spends") else "spend_prob_idle") * factor(bal)
    if rng.random() >= p:
        return None
    t = tier(bal)
    big = bi(registry.num("spend_big_price"))
    blocked = _big_blocked(now)
    boost, mood_boost, wboost = registry.num("spend_state_boost"), registry.num("spend_mood_boost"), registry.num("spend_weather_boost")
    state = ctx.get("state")
    w: dict[str, float] = {}
    for k in CAT_KEYS:
        x = registry.num(f"spend_w_{k}")
        lo, _hi = price_range(k)
        if x <= 0 or (k in ONLY_IN and state not in ONLY_IN[k]) or bi(lo) > bal - _reserve(k):
            continue
        if bi(common_range(k)[0]) >= big and (t == "tight" or blocked):
            continue                                               # 很紧 / 刚买过大件：平时就是大件价位的（衣服、游戏……）不买
        if k in RELEVANT.get(state, ()):
            x *= boost
        if ctx.get("negative") and k in ("milktea", "snack"):
            x *= mood_boost
        if (ctx.get("hot") or ctx.get("season") == "夏") and k == "milktea":
            x *= wboost                                            # 天热 / 夏天：冷饮
        if (ctx.get("cold") or ctx.get("season") == "冬") and k in ("milktea", "clothes"):
            x *= wboost                                            # 降温 / 冬天：热饮、添衣服
        w[k] = x
    if not w:
        return None
    cat = rng.choices(list(w), list(w.values()))[0]
    whylog.record("spend", cat, [f"这会儿买东西的概率 {p:.0%}（{'这个状态常花钱' if ctx.get('spends') else '平时'} × 余额系数 {whylog.fmt(factor(bal))}），掷中了",
                                f"在做：{state or '没在做什么'}" + ("；心情差" if ctx.get("negative") else "")], weights=w, labels=LABEL)
    no_big = t == "tight" or blocked
    price, impulse = _pick_price(cat, bal, no_big, rng)
    if price <= 0:
        return None
    return {"cat": cat, "price": price, "cap": _cap(cat, bal, no_big), "big": price >= big, "impulse": impulse}


def intent_spec(cat: str, max_price: float | None = None, rng: random.Random | None = None) -> dict[str, Any] | None:
    """她说过要买的（对话意图）：照这一类定个价（说了最多多少钱就不超过），买得起才返回 {cat, price, big}。"""
    rng = rng or random
    if cat not in CAT_KEYS or not enabled():
        return None
    bal = balance()
    price, _imp = _pick_price(cat, bal, False, rng)
    cap = _cap(cat, bal, False)
    if max_price:
        cap = min(cap, bi(float(max_price)))
        price = min(price, cap) if price > 0 else cap
    if price <= 0 or price > bal - _reserve(cat):
        return None
    return {"cat": cat, "price": price, "cap": cap, "big": price >= bi(registry.num("spend_big_price"))}


def _reserve(cat: str) -> int:
    """买这类东西时，买完至少还要剩下多少（小单位）：贵的东西她舍得，但不会把自己花到见底。"""
    return bi(registry.num("money_low")) if cat in RESERVE_CATS else 0


def _cap(cat: str, bal: int, no_big: bool) -> int:
    cap = bal - _reserve(cat)
    if no_big:
        cap = min(cap, bi(registry.num("spend_big_price")) - 1)
    return cap


def _pick_price(cat: str, bal: int, no_big: bool, rng) -> tuple[int, bool]:
    """大概想花多少（给模型的参考）：多数落在「常见」区间，偏便宜；游戏内购偶尔冲动，超过平时的上限。"""
    lo, hi = price_range(cat)
    clo, chi = common_range(cat)
    impulse = False
    if cat == "iap" and rng.random() < registry.num("spend_iap_big_prob"):
        p = bi(rng.uniform(hi, max(hi, registry.num("spend_iap_big_max"))))        # 偶尔冲动：超过平时的上限
        impulse = True
    elif rng.random() < 0.85:
        p = _skewed(clo, chi, rng)
    else:
        p = _skewed(lo, hi, rng)
    p = min(p, _cap(cat, bal, no_big))
    return (p if p >= bi(lo) else 0), impulse


# ───────────────────────── 买完记下来 ─────────────────────────
def record_spend(cat: str, item: str, price: int, reason: str) -> dict[str, Any]:
    """记进账本，同时记成一条「没告诉你的事」（告诉了再改）。"""
    day = _today()
    eid = db.add_event(day, "spend", f"买了{item}，花了 {fmt(price)} {C.unit()}", None, shared=False, etype="spend", people="[]")
    lid = _insert("spend", cat, item[:30], -int(price), reason[:60], False, day, event_id=eid)
    return get_entry(lid)  # type: ignore[return-value]


def mark_shared(entry_id: int, shared: bool = True) -> None:
    e = get_entry(entry_id)
    if not e:
        return
    _exec("UPDATE money_ledger SET shared = ? WHERE id = ?", (int(shared), int(entry_id)))
    if e["event_id"]:
        db.mark_event_shared(e["event_id"], shared)


def meal_cat(method: str, went_out: bool = False) -> str | None:
    """这顿饭花不花她的钱：外卖 → takeout；一个人 / 和朋友出去吃 → dine；和家人出门吃按设置；自己做、在家吃不花钱。"""
    if method == "takeout":
        return "takeout"
    if method in ("alone_out", "friend_out"):
        return "dine"
    if method == "family" and went_out and not registry.flag("meal_family_pays"):
        return "dine"
    return None


PAY_LABEL = {"aa": "AA", "treated": "朋友请客", "treat": "请朋友"}


def roll_pay(method: str, rng: random.Random | None = None) -> str:
    """和朋友出去吃：多数 AA（只付自己那份），偶尔朋友请（不花钱）/ 她请（付两份）。别的吃法都是自己付（aa）。"""
    if method != "friend_out":
        return "aa"
    r = (rng or random).random()
    p_treated, p_treat = registry.num("meal_p_treated"), registry.num("meal_p_treat")
    if r < p_treated:
        return "treated"
    if r < p_treated + p_treat:
        return "treat"
    return "aa"


def fallback_meal_price(cat: str, rng: random.Random | None = None) -> dict[str, Any]:
    """模型没给价格（没接入 / 生成失败）：外卖按「外卖」价格带，出去吃按「快餐盖饭」，在常见区间里取，尾数照样偏 .9。"""
    rng = rng or random
    info = prices.normalize({"band": "delivery" if cat == "takeout" else "fastfood"}, cat)
    info["price"] = prices.pretty(prices.clamp(info, rng=rng), info, rng, *prices.allowed(info))
    return info


def charge_meal(meal: str, method: str, dish: str, went_out: bool = False, rng: random.Random | None = None,
                priced: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """外卖、出去吃：从余额里扣（自己做、在家吃不花钱；和家人出门吃饭按设置由家里付）。余额不够就只扣到 0。
    priced：make_dish 定好的 {price（一个人一份，大单位）, pay（aa / treated / treat）, shop, band…}；没有就按价格带兜底。"""
    from . import meals

    if not enabled():
        return None
    cat = meal_cat(method, went_out)
    if not cat:
        return None
    info = priced if priced and priced.get("price") else fallback_meal_price(cat, rng)
    pay = (priced or {}).get("pay") or "aa"
    if pay == "treated":
        return None                                              # 朋友请客：一分不花
    bal = balance()
    if bal <= 0:
        return None
    price = min(bi(float(info["price"]) * (2 if pay == "treat" else 1)), bal)
    if price <= 0:
        return None
    note = f"{meals.MEAL_LABEL[meal]}{meals.METHOD_LABEL[method]}" + (f" · {PAY_LABEL[pay]}" if method == "friend_out" else "")
    if info.get("shop"):
        note += f" · {info['shop']}"
    lid = _insert("spend", cat, dish[:30], -price, note[:60])
    return get_entry(lid)


# ───────────────────────── 让模型写买了什么 ─────────────────────────
PURCHASE_PROMPT = """你是「{name}」。你的口吻（人设里的说话方式）：
{voice}

现在是{season}季，{when}，你这边{weather}。你在{state}，心情{mood}。{money_note}
你刚刚买了东西，类别是「{label}」，这次大概花了 {price} {unit}上下{money_rule}。

{price_block}

{extra}输出 JSON，不要别的：
{{"item": "买的东西，具体的名字，20 字以内", {fields}, "reason": "为什么买，第一人称，口语，20 字以内，结合天气 / 心情 / 场合，比如「今天降温了 买了杯热的」", "share_line": "如果要告诉她，你会怎么说，25 字以内，口语，带上理由，像随手发的消息"{keep_field}}}

要求：
- 牌子、店名和商品名可以虚构，但别写得太怪；{no_real}
- 东西要和价格对得上，价格要在价格带 × 档次 × 噱头 × 地点的范围里；不提学校；
- 比平时贵的（商场里的、高档店、联名款、限定、被坑了、在{pricey}买的），share_line 可以自然地带一句为什么贵；
- 如果这个月零花钱很紧，可以带一点心疼或自嘲，但别哭穷。"""
PURCHASE_PROMPT = PURCHASE_PROMPT.replace("{no_real}", C.pick("fictional_world", "不要现实里的国家、城市、学校名；"))

# 买书：item 写书名；这本书买回来只是摆在书架上（不可阅读）
BOOK_EXTRA = (f"你买的是一本书：item 写成书名（虚构的，12 字以内，别用现实里真有的书名{C.pick('fictional_world', '，也别带现实里的地名')}）；"
              "你爱看法庭推理、游记、纪实文学、推理小说、诗集，reason 写为什么想买这本。\n")
BOOK_AUTHOR = ', "author": "这本书的作者，虚构的名字，2 到 4 个字"'


def local_purchase(cat: str, rng: random.Random | None = None) -> dict[str, Any]:
    item, reason, line = (rng or random).choice(LOCAL.get(cat) or LOCAL["trinket"])
    out = {"item": item, "reason": reason, "share_line": line, "keep": True, "info": None}
    if cat == "book":                                                  # 书名随机生成，免得总是那几个
        out["item"], out["author"] = booklook.random_title(rng), booklook.random_author(rng)
    return out


def _truthy(v: Any) -> bool:
    return v if isinstance(v, bool) else str(v).strip().lower() not in ("false", "0", "no", "否", "不", "")


def settle_price(cat: str, hint: int, cap: int | None, info: dict[str, Any] | None, item: str = "",
                 hi_override: float | None = None, rng: random.Random | None = None) -> tuple[int, dict[str, Any]]:
    """定下最终价格（小单位）：模型给的（已检查过范围）→ 同一家店同一样东西跟上次一致 → 尾数 → 不超过花得起的。
    info 为空（本地兜底）：按这一类默认的价格带，在大概想花的数附近取。"""
    rng = rng or random
    if info is None:
        info = prices.normalize({"price": hint / UNIT}, cat)
    lo, hi = prices.allowed(info, hi_override)
    prev = prices.recall(info.get("shop", ""), item) if item else None
    if prev and prev["band"] == info["band"]:
        p = prices.repeat_price(float(prev["price"]), info, rng)
    else:
        p = prices.pretty(prices.clamp(info, hi_override, rng), info, rng, lo, hi)
    out = bi(p)
    if cap is not None and out > cap:
        out = cap if cap >= bi(lo) else out
    info = dict(info, price=out / UNIT)
    return out, info


async def make_purchase(settings: dict[str, Any], cat: str, price: int, ctx: dict[str, Any], rng: random.Random | None = None,
                        cap: int | None = None, impulse: bool = False) -> dict[str, Any]:
    """返回 {item, reason, share_line, keep, price（小单位）, info}（keep：会不会一直留着，决定要不要放进房间）。
    price 参数是「大概想花多少」；最终价格由模型按价格带给出，超出允许范围就重想一次，还不行就夹回范围内（settle_price）。
    模型不可用 / 反复不合格时用本地兜底。"""
    out = await _make_purchase(settings, cat, price, ctx, rng, cap, impulse)
    hi_over = registry.num("spend_iap_big_max") if impulse and cat == "iap" else None
    final, info = settle_price(cat, price, cap, out.get("info"), out["item"], hi_over, rng)
    out["price"], out["info"] = final, info
    return out


async def _make_purchase(settings: dict[str, Any], cat: str, price: int, ctx: dict[str, Any], rng: random.Random | None,
                         cap: int | None, impulse: bool) -> dict[str, Any]:
    from . import guards, reading
    from . import persona as persona_lib
    from .planner import _json_object

    if llm.is_echo(settings):
        return local_purchase(cat, rng)
    t = tier()
    note = {"ok": "", "low": f"这个月零花钱有点紧了（还剩 {fmt(balance())} {C.unit()}）。", "tight": f"这个月零花钱很紧了（只剩 {fmt(balance())} {C.unit()}）。"}[t]
    hi_over = registry.num("spend_iap_big_max") if impulse and cat == "iap" else None
    from . import notebook

    nb = notebook.life_block(" ".join([LABEL[cat], ctx.get("item_hint") or "", *(prices.BAND[b][1] for b in (prices.CAT_BANDS.get(cat) or ()) if b in prices.BAND)]),
                             ctx.get("here"), chapters=("price",))   # 小本子：这一带的店、物价印象
    system = PURCHASE_PROMPT.format(
        name=C.display_name(settings), voice=persona_lib.voice_excerpt(settings.get("system_prompt") or "", 900),
        season=ctx.get("season", ""), when=ctx.get("when", ""), weather=ctx.get("weather") or "天气一般", state=ctx.get("state_label", "在家"),
        pricey=prices.pricey_places(), mood=ctx.get("mood") or "平静", money_note=note, label=LABEL[cat], price=fmt(price), unit=C.unit(), money_rule=(f"（{C.currency()['real']}）" if C.currency().get("real") else ""),
        price_block=prices.prompt_block(cat, round(cap / UNIT, 1) if cap is not None else None, ctx.get("here"), pick=True)
        + ("\n这次是一时冲动，花得比平时多（可以超过平时的最高价）。" if hi_over else ""),
        fields=prices.FIELDS,
        extra=(f"你之前跟她说过要买「{ctx['item_hint']}」，买的就是它（item 写它，可以写得更具体）。\n" if ctx.get("item_hint") else "")
        + {"book": BOOK_EXTRA}.get(cat, "") + (nb + "\n" if nb else ""), keep_field={"book": BOOK_AUTHOR}.get(cat, ""))
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": "写吧。"}]
    retried = False
    for _ in range(3):
        try:
            raw = await llm.complete(settings, msgs, temperature=1.0)
        except Exception:  # noqa: BLE001
            break
        data = _json_object(raw)
        item = guards.strip_exclaim(str(data.get("item") or "").strip())[:12 if cat == "book" else 20]
        reason = guards.strip_exclaim(str(data.get("reason") or "").strip())[:40]
        line = guards.strip_exclaim(str(data.get("share_line") or "").strip())[:60]
        if not item or not reason:
            continue
        impression = guards.strip_exclaim(str(data.get("impression") or "").strip())[:60]
        text = item + reason + line + str(data.get("shop") or "") + impression
        if reading.mentions_real_place(text) or guards.find_banned(text):
            continue
        info = prices.normalize(data, cat)
        if ctx.get("here") and info["band"] not in prices.NO_MOD:
            info["place"] = places.price_place(ctx["here"])          # 地点由她现在在哪决定，不由模型选
        if (info["price"] <= 0 or not prices.in_range(info, hi_over)) and not retried:
            lo, hi = prices.allowed(info, hi_over)
            retried = True                                        # 价格不合理：告诉她范围，重想一次；还不行就夹回范围内
            msgs = msgs[:1] + [{"role": "user", "content": "写吧。"}, {"role": "assistant", "content": raw},
                               {"role": "user", "content": f"「{item}」{info['price']:g} {C.unit()}不合理：{prices.BAND[info['band']][1]}"
                                f"（{prices.label('tier', info['tier'])}、{prices.label('place', info['place'])}"
                                f"{'、' + prices.label('gimmick', info['gimmick']) if info['gimmick'] != 'none' else ''}）应该在 {lo:g}~{hi:g} {C.unit()}之间。"
                                "重新想一下，东西、档次和价格要对得上，还是输出同样的 JSON。"}]
            continue
        out = {"item": item, "reason": reason, "share_line": line or f"买了{item}", "info": info, "impression": impression,
               "keep": True}
        if cat == "book":
            out["author"] = guards.strip_exclaim(str(data.get("author") or "").strip())[:12] or booklook.random_author(rng)
        return out
    return local_purchase(cat, rng)


async def purchase(settings: dict[str, Any], spec: dict[str, Any], ctx: dict[str, Any], rng: random.Random | None = None) -> dict[str, Any]:
    """一次消费的全过程：生成内容（含价格）→ 记账本 → 记下店和价格 → 会留下来的放进房间 → 值得记的写进记忆。
    返回 {entry, share_line, big, room_item}。"""
    cat = spec["cat"]
    data = await make_purchase(settings, cat, int(spec["price"]), ctx, rng, cap=spec.get("cap"), impulse=bool(spec.get("impulse")))
    price = int(data["price"])
    if cat == "book":
        data["item"] = f"《{data['item'].strip('《》')}》"
    entry = record_spend(cat, data["item"], price, data["reason"])
    try:
        from . import notebook

        notebook.log_day("buy", f"买了{data['item']}（{LABEL[cat]}，{fmt(price)} {C.unit()}）：{data['reason']}")     # 今天的见闻：晚上整理小本子用
    except Exception:  # noqa: BLE001
        log.exception("记今天的见闻出错")
    info = data.get("info") or {}
    if info.get("band"):
        prices.remember(info.get("shop", ""), data["item"], info, price / UNIT, impression=data.get("impression") or "")   # 记进小本子的店铺条目
    if cat in clutter.KINDS:                                                    # 吃的（奶茶 / 零食 / 水果）：摆上书桌或食物桌，按保质期，过期的睡前收掉
        try:
            clutter.spawn(cat, data["item"], rng=rng, ledger_id=entry["id"])
        except Exception:  # noqa: BLE001
            log.exception("把买的零食 / 奶茶摆上桌失败")
    room_item = 0
    if cat == "book" and registry.flag("spend_room_book"):                    # 买的书：随机外观的摆设书，放上书架（放满了堆地上 / 收纳）
        try:
            bk = room.add_phal_book(data["item"], data.get("author", ""), price=price, note=data["reason"], rng=rng)
            _exec("UPDATE money_ledger SET room_book = ? WHERE id = ?", (bk["pid"], entry["id"]))
            entry["room_book"] = bk["pid"]
            room_item = -bk["pid"]
        except Exception:  # noqa: BLE001
            log.exception("把买的书放上书架失败")
    elif registry.flag(f"spend_room_{cat}") and data.get("keep", True):          # 会留下来的才放进房间（用掉就没有的材料不放）
        try:
            it = await room.add_auto(data["item"], "bought", note=data["reason"], settings=settings)
            room_item = it["id"]
            _exec("UPDATE money_ledger SET room_item = ? WHERE id = ?", (room_item, entry["id"]))
            entry["room_item"] = room_item
        except Exception:  # noqa: BLE001
            log.exception("把买的东西放进房间失败")
    if room_item or price >= bi(registry.num("money_memory_min")):                 # 放进房间的（含书）总是写进记忆
        db.add_memory(f"{entry['day'][5:]} 买了{data['item']}，花了 {fmt(price)} {C.unit()}（{data['reason']}）", kind="event", category="phal_fact", source="money")
    big = price >= bi(registry.num("spend_big_price"))
    return {"entry": entry, "share_line": data["share_line"], "big": big, "room_item": max(0, room_item), "room_book": entry.get("room_book", 0)}


# ───────────────────────── 给聊天用的上下文 ─────────────────────────
_ASK = re.compile(r"钱|零花|余额|花了|买了|买什么|买的|贵|便宜|账本|消费|省着|奶茶|外卖|付款|剩多少")


def overview_block() -> str:
    """稳定部分：她有零花钱；我能看到账本，她正常接话。"""
    if not enabled():
        return ""
    return (f"【零花钱】你每月有 {registry.num('money_monthly'):.0f} {C.unit()}零花钱{('（' + C.currency()['real'] + '）') if C.currency().get('real') else ''}，买东西、点外卖、出去吃都从里面花；自己做饭、在家吃不花钱。"
            "你的消费账本用户随时看得到（包括你没跟她说过的），她提起你花了什么、还剩多少，你正常接话，不要惊讶，也不要觉得被偷看。"
            f"书架上的书是她给你买的，不花你的钱；你偶尔也会自己买本书（几十{C.unit()}）摆在书架上，那种只是摆设，不会读。")


def month_spent(ym: str | None = None) -> int:
    ym = ym or _today()[:7]
    return -int(_q("SELECT COALESCE(SUM(delta), 0) AS s FROM money_ledger WHERE kind = 'spend' AND day LIKE ?", (ym + "%",))[0]["s"])


def recent_spends(n: int = 5) -> list[dict[str, Any]]:
    return [_entry(r) for r in _q("SELECT * FROM money_ledger WHERE kind = 'spend' ORDER BY id DESC LIMIT ?", (int(n),))]


def context_block(user_text: str = "", rng: random.Random | None = None) -> str:
    """volatile 部分：聊到钱 / 买东西时带上余额和最近的消费；余额紧时偶尔提醒她可以抱怨一句。"""
    if not enabled():
        return ""
    rng = rng or random
    bal = balance()
    t = tier(bal)
    lines: list[str] = []
    if _ASK.search(user_text or ""):
        rec = recent_spends(5)
        line = f"【你的零花钱】你手里还有 {fmt(bal)} {C.unit()}，这个月已经花了 {fmt(month_spent())} {C.unit()}"
        if rec:
            line += "；最近买过：" + "、".join(f"{e['item']}（{fmt(-e['delta'])} {C.unit()}）" for e in rec)
        lines.append(line + "。只能说这些，别凭空多出消费。")
    if t != "ok" and rng.random() < registry.num("money_complain_prob"):
        lines.append(f"【零花钱{TIER_LABEL[t]}】这个月你的零花钱{TIER_LABEL[t]}（还剩 {fmt(bal)} {C.unit()}），最近在省着花。"
                     "聊到买东西、点外卖、出去玩时，可以自然地抱怨一句或者婉拒；别每次都提，也别哭穷。")
    return "\n".join(lines)


# ───────────────────────── 给账本页 ─────────────────────────
def months() -> list[str]:
    rows = _q("SELECT DISTINCT substr(day, 1, 7) AS m FROM money_ledger ORDER BY m DESC")
    return [r["m"] for r in rows]


def month_summary(ym: str) -> dict[str, Any]:
    rows = _q("SELECT * FROM money_ledger WHERE day LIKE ? ORDER BY id ASC", (ym + "%",))
    income = sum(r["delta"] for r in rows if r["delta"] > 0)
    by: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r["kind"] != "spend":
            continue
        c = by.setdefault(r["category"], {"key": r["category"], "label": LABEL.get(r["category"], r["category"]), "amount": 0, "count": 0})
        c["amount"] += -r["delta"]
        c["count"] += 1
    spent = sum(c["amount"] for c in by.values())
    cats = sorted(by.values(), key=lambda c: -c["amount"])
    for c in cats:
        c["percent"] = round(c["amount"] * 100 / spent) if spent else 0
    end = int(_q("SELECT COALESCE(SUM(delta), 0) AS b FROM money_ledger WHERE day <= ?", (ym + "-31",))[0]["b"])
    return {"month": ym, "income": income, "spent": spent, "count": sum(c["count"] for c in cats), "by_category": cats, "end_balance": end}


def next_grant() -> str:
    d = datetime.now()
    gd = int(registry.num("money_grant_day"))
    last = db.get_settings().get("money_granted_through") or ""
    cur = f"{d.year:04d}-{d.month:02d}"
    if last < cur and d.day < gd:
        return f"{cur}-{gd:02d}"
    y, m = (d.year + (d.month == 12), 1 if d.month == 12 else d.month + 1)
    return f"{y:04d}-{m:02d}-{gd:02d}"


def snapshot(ym: str | None = None) -> dict[str, Any]:
    ensure_grants()
    cur = _today()[:7]
    ym = ym if ym and re.fullmatch(r"\d{4}-\d{2}", ym) else cur
    bal = balance()
    ms = months()
    if cur not in ms:
        ms = [cur] + ms
    entries = [_entry(r) for r in _q("SELECT * FROM money_ledger WHERE day LIKE ? ORDER BY id DESC", (ym + "%",))]
    return {"enabled": enabled(), "unit": C.unit(), "balance": bal, "tier": tier(bal), "tier_label": TIER_LABEL[tier(bal)], "month": ym, "months": ms,
            "summary": month_summary(ym), "entries": entries, "next_grant": next_grant(), "monthly": registry.num("money_monthly"),
            "labels": LABEL}


def adjust(amount_qian: float, note: str = "") -> dict[str, Any]:
    """我手动给她加 / 扣一笔（比如额外给的零花钱）。"""
    amount = bi(amount_qian)
    if amount == 0:
        raise ValueError("金额不能是 0")
    note = (note or "").strip()[:40]
    lid = _insert("adjust", "adjust", note or ("额外给的零花钱" if amount > 0 else "扣掉一笔"), amount, "", True)
    return get_entry(lid)  # type: ignore[return-value]
