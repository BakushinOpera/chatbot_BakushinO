"""Phal 的心情和精力（v8）：两个连续的数。

* 情绪 v：−1（很低落）~ 1（很愉快）；精力 e：0（累到不行）~ 1（精神很好）。
* 情绪 = 今天的基线 + 各次事件的「推动」；每次推动按半衰期回落（``mood_half_v``）。
  精力 = 一天的节奏曲线（醒来 → 上午最高 → 午后低谷 → 傍晚回升 → 睡前）+ 昨晚睡眠带来的整体高低 + 活动的消耗 / 恢复（也按半衰期回落）。
* **时间按真实时间走**：应用关着，情绪照样回落、精力照样按钟点变（事件只在应用开着时发生，见 life.py）。
* 显示的心情词（v9）：按「情绪 × 精力」分四个区域（behavior.QUADS），每个区域一组词（设置页里维护）；
  进入一个区域、或者一件事带来的情绪落在某个区域时，从那组随机取一个词（``current_word`` / ``repick``）。
  ``LABELS`` 里的坐标只用来把事件给的心情词换算成推动。另有几个「附加词」（想念、嘴馋、怀旧……）由具体情境挂上一阵（``FLAVORS``）。
* 每天起床时算基线：前一天的平均情绪（延续）+ 天气 + 季节 + 星期几 + 昨晚睡眠 + 昨天的事 + 一点随机。
  每一项的贡献都记下来，影响最大的一两项拼成「今天心情的原因」（模型再润色成一句话，见 life.py）。
* 纯函数（rhythm / value / label / baseline …）不碰数据库；
  存取（load / save / push …）在下面，状态存在 settings 的 ``mood_state``。
"""

from __future__ import annotations

import json
import math
import random
import time
from datetime import date, datetime, timedelta
from typing import Any

from . import behavior as B
from . import db, registry

# ───────────────────────── 心情词的坐标 ─────────────────────────
# (词, 情绪, 精力)。显示时取离 (v, e) 最近的那个。
LABELS: list[tuple[str, float, float]] = [
    ("元气满满", 0.55, 0.95), ("雀跃", 0.7, 0.8), ("兴奋", 0.8, 0.92), ("兴致勃勃", 0.5, 0.78), ("跃跃欲试", 0.35, 0.86),
    ("开心", 0.6, 0.56), ("小得意", 0.45, 0.62), ("期待", 0.33, 0.68), ("充实", 0.4, 0.5), ("满足", 0.55, 0.4),
    ("惬意", 0.45, 0.28), ("暖洋洋", 0.35, 0.18), ("温柔", 0.3, 0.4), ("安心", 0.24, 0.3),
    ("专注", 0.15, 0.76), ("好奇", 0.2, 0.62), ("平静", 0.08, 0.5), ("淡然", -0.02, 0.42),
    ("放空", 0.0, 0.26), ("懒洋洋", 0.1, 0.15), ("犯困", -0.02, 0.05), ("无所事事", -0.08, 0.34),
    ("心不在焉", -0.2, 0.46), ("小纠结", -0.28, 0.6), ("闷闷的", -0.32, 0.35), ("恍惚", -0.25, 0.15),
    ("烦躁", -0.5, 0.75), ("焦虑", -0.6, 0.92), ("委屈", -0.5, 0.5), ("沮丧", -0.62, 0.42),
    ("忧郁", -0.55, 0.25), ("空落落", -0.45, 0.12),
]
COORD = {w: (v, e) for w, v, e in LABELS}
# 附加词：不占坐标（说不清是高兴还是低落），由情境挂上一阵。值 = (推一下情绪多少, 推一下精力多少, 情绪在哪个范围内才显示)
FLAVORS: dict[str, tuple[float, float, tuple[float, float]]] = {
    "想念": (-0.04, 0.0, (-0.25, 1.0)), "孤单": (-0.12, -0.02, (-1.0, -0.05)), "嘴馋": (0.02, 0.0, (-0.3, 1.0)),
    "怀旧": (-0.03, -0.02, (-0.5, 0.6)), "害羞": (0.05, 0.03, (-0.2, 1.0)), "倔强": (-0.08, 0.05, (-0.7, 0.4)),
    "感激": (0.2, 0.02, (0.0, 1.0)), "心软": (0.08, 0.0, (-0.3, 1.0)), "感动": (0.25, 0.04, (0.0, 1.0)),
    "怅然": (-0.2, -0.05, (-0.8, 0.2)),
}
NEGATIVE_FLAVORS = {"孤单", "怅然"}


def is_negative(word: str, p: dict[str, Any] | None = None) -> bool:
    """算不算低落：在「低落」那两组词里（设置页的心情词表），或者是低落的附加词。"""
    if word in NEGATIVE_FLAVORS:
        return True
    q = B.all_words(p if p is not None else params()).get(word)
    if q:
        return q.startswith("lv")
    return word in COORD and COORD[word][0] <= -0.2


# ───────────────────────── 参数 ─────────────────────────
def params(settings: dict[str, str] | None = None) -> dict[str, Any]:
    """心情与精力分区的所有参数（一次读完）。"""
    s = settings if settings is not None else db.get_settings()
    return {it.key: registry.get(it.key, s) for it in registry.ITEMS if it.group == "mood" or it.key.startswith("mood_words_")} | {
        "wake_hour": registry.num("wake_hour", s), "sleep_hour": registry.num("sleep_hour", s),
        "mood_sync_scale": registry.num("mood_sync_scale", s),
    }


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


# ───────────────────────── 纯函数 ─────────────────────────
def rhythm(hour: float, p: dict[str, Any]) -> float:
    """一天的精力节奏（不含睡眠、活动）：按几个点线性插值。hour 是带小数的钟点。"""
    wake, bed = float(p["wake_hour"]), float(p["sleep_hour"])
    if bed <= wake:
        bed += 24
    h = hour if hour >= wake - 0.01 else hour + 24
    pts = sorted([(wake, p["e_wake"]), (float(p["e_peak_hour"]), p["e_peak"]), (float(p["e_dip_hour"]) - 1.5, p["e_peak"] * 0.95),
                  (float(p["e_dip_hour"]), p["e_dip"]), (float(p["e_evening_hour"]), p["e_evening"]), (bed, p["e_bed"])])
    pts = [q for q in pts if wake <= q[0] <= bed]
    if h <= pts[0][0]:
        return float(pts[0][1])
    for (h0, e0), (h1, e1) in zip(pts, pts[1:]):
        if h <= h1:
            return float(e0 + (e1 - e0) * ((h - h0) / ((h1 - h0) or 1)))
    return float(p["e_bed"])          # 睡着了：按睡前算（显示用；睡觉时不选状态）


def _decay(dt_s: float, half_min: float) -> float:
    return 0.5 ** (max(0.0, dt_s) / (max(1.0, half_min) * 60))


def value(st: dict[str, Any], now: float, p: dict[str, Any]) -> tuple[float, float]:
    """此刻的 (情绪, 精力)。"""
    v = float(st.get("base_v", p["base_mean"]))
    e_extra = 0.0
    for im in st.get("impulses", []):
        dt = now - im["t"]
        if dt < 0:
            continue
        v += im.get("dv", 0.0) * _decay(dt, p["mood_half_v"])
        e_extra += im.get("de", 0.0) * _decay(dt, p["mood_half_e"])
    if st.get("festival_floor") is not None:
        v = max(v, float(st["festival_floor"]))
    h = datetime.fromtimestamp(now)
    e = rhythm(h.hour + h.minute / 60, p) + (float(st.get("sleep_q", 0.5)) - 0.5) * p["sleep_energy_k"] + e_extra
    return clamp(v, -1.0, 1.0), clamp(e, 0.0, 1.0)


def current_word(st: dict[str, Any], v: float, e: float, p: dict[str, Any], rng: random.Random | None = None) -> str:
    """此刻的心情词：还在原来的区域就用原来的词；换了区域就从新区域那组里随机取一个（记在 st["qword"]）。"""
    qw = st.get("qword") or {}
    q = B.quad_of(v, e, qw.get("q"), p)
    words = {w for w, _n in B.quad_words(q, p)}
    if q != qw.get("q") or qw.get("w") not in words:
        qw = {"q": q, "w": B.pick_word(q, p, rng or random)}
        st["qword"] = qw
    return qw["w"]


def repick(st: dict[str, Any], now: float, p: dict[str, Any], rng: random.Random | None = None) -> str:
    """一件事带来了情绪：看情绪落在哪个区域，从那组里随机换一个词（尽量不和现在的一样）。"""
    v, e = value(st, now, p)
    qw = st.get("qword") or {}
    q = B.quad_of(v, e, qw.get("q"), p)
    st["qword"] = {"q": q, "w": B.pick_word(q, p, rng or random, avoid=qw.get("w"))}
    return st["qword"]["w"]


def label(v: float, e: float, prev: str | None, p: dict[str, Any]) -> str:
    """不记状态的换算（日历上「今天的底色」用）：区域里权重最大的那个词。"""
    q = B.quad_of(v, e, None, p)
    return max(B.quad_words(q, p), key=lambda x: x[1])[0]


def energy_word(e: float) -> str:
    return "精神很好" if e >= 0.75 else "精力还行" if e >= 0.5 else "有点累" if e >= 0.3 else "很累、很困"


def word_impulse(word: str, p: dict[str, Any], scale: float = 1.0) -> tuple[float, float, str | None]:
    """事件的心情词 → (推情绪, 推精力, 附加词)。不认识的词不推。"""
    if word in FLAVORS:
        dv, de, _rng = FLAVORS[word]
        return dv * scale, de * scale, word
    q = B.all_words(p).get(word)
    if word in COORD or q:
        wv, we = COORD.get(word) or B.QUAD_CENTER[q]
        return wv * p["mood_event_k"] * scale, (we - 0.5) * p["mood_event_ke"] * scale, None
    return 0.0, 0.0, None


def repeat_factor(st: dict[str, Any], src: str, now: float, p: dict[str, Any]) -> float:
    """同一类事 mood_repeat_days 天内又来了：第 k+1 次 × decay^k。"""
    days = float(p["mood_repeat_days"])
    if days <= 0 or not src:
        return 1.0
    k = sum(1 for s, t in st.get("recent", []) if s == src and now - t < days * 86400)
    return float(p["mood_repeat_decay"]) ** k


SEASON_KEY = {"春": "base_spring", "夏": "base_summer", "秋": "base_autumn", "冬": "base_winter"}


def baseline(ctx: dict[str, Any], p: dict[str, Any], rng: random.Random) -> tuple[float, list[tuple[str, float, str]]]:
    """今天的情绪基线和每一项的贡献 [(因素, 贡献, 说法)]。

    ctx：prev_avg_v（昨天平均情绪，没有就 None）、kind（今天的天气）、rain_streak（连着阴雨几天，含今天）、
    season（春夏秋冬）、weekday（0 = 周一）、sleep_q（昨晚睡眠 0~1）、yesterday_dv（昨天事件推动的总和）。"""
    f: list[tuple[str, float, str]] = []
    if ctx.get("prev_avg_v") is not None:
        c = (float(ctx["prev_avg_v"]) - p["base_mean"]) * p["base_carry"]
        f.append(("延续", c, "昨天的好心情还没散" if c > 0 else "昨天的情绪还压着一点"))
    kind = ctx.get("kind") or ""
    if kind in ("clear", "cloudy"):
        f.append(("天气", p["base_clear"] * (1 if kind == "clear" else 0.5), "天气很好" if kind == "clear" else "天气还不错"))
    elif kind in ("rain", "thunder", "overcast", "fog"):
        streak = min(4, int(ctx.get("rain_streak") or 1))
        c = (p["base_rain"] if kind in ("rain", "thunder") else p["base_rain"] * 0.5) + p["base_rain_streak"] * max(0, streak - 1)
        f.append(("天气", c, f"连着阴雨好几天了" if streak >= 3 else "外面在下雨" if kind in ("rain", "thunder") else "天阴沉沉的"))
    elif kind == "snow":
        f.append(("天气", p["base_snow"], "下雪了"))
    if ctx.get("season") in SEASON_KEY:
        c = p[SEASON_KEY[ctx["season"]]]
        f.append(("季节", c, {"春": "春天了", "夏": "夏天", "秋": "秋天了", "冬": "冬天有点冷"}[ctx["season"]]))
    wd = ctx.get("weekday")
    if wd is not None:
        if wd >= 5:
            f.append(("星期", p["base_weekend"], "今天是周末"))
        elif wd == 4:
            f.append(("星期", p["base_friday"], "明天就周末了"))
        elif wd == 0:
            f.append(("星期", p["base_monday"], "又是周一"))
    if ctx.get("sleep_q") is not None:
        c = (float(ctx["sleep_q"]) - 0.5) * p["base_sleep_k"]
        f.append(("睡眠", c, "昨晚睡得很好" if c > 0.03 else "昨晚没睡好" if c < -0.03 else "昨晚睡得一般"))
    if ctx.get("yesterday_dv"):
        c = float(ctx["yesterday_dv"]) * p["base_yesterday_k"]
        f.append(("昨天的事", c, "昨天过得挺顺" if c > 0 else "昨天有点不顺"))
    noise = rng.gauss(0.0, float(p["base_noise"]))
    f.append(("随机", noise, "说不上为什么，就是挺好" if noise > 0 else "说不上为什么，有点提不起劲"))
    total = sum(c for _n, c, _t in f)
    base = p["base_mean"] + clamp(total, -p["base_clamp"], p["base_clamp"])
    return clamp(base, -1.0, 1.0), f


def reason_text(factors: list[tuple[str, float, str]]) -> str:
    """影响最大的一两项（贡献太小的不说）拼成一句原因（本地版；模型会再润色）。"""
    top = sorted(factors, key=lambda x: -abs(x[1]))
    picked = [t for n, c, t in top if abs(c) >= 0.025 and n != "随机"][:2]       # 「说不上为什么」只在没有别的明显原因时才说
    if not picked:
        picked = [t for n, c, t in top if n == "随机" and abs(c) >= 0.05][:1]
    return "，".join(picked) if picked else "没什么特别的，平平常常的一天"


def sleep_quality(bed_v: float | None, p: dict[str, Any], rng: random.Random) -> float:
    return clamp(p["sleep_mean"] + rng.gauss(0.0, p["sleep_sd"]) + p["sleep_mood_k"] * (bed_v or 0.0), 0.0, 1.0)


def state_affinity(key: str, v: float, e: float, p: dict[str, Any]) -> float:
    """选状态时的心情 / 精力系数：e^(精力系数 × (精力 − 0.5) × 2 + 情绪系数 × 情绪)，限制在 0.15 ~ 6 倍。"""
    ek, vk = p.get(f"state_ek_{key}", 0.0), p.get(f"state_vk_{key}", 0.0)
    return clamp(math.exp(ek * (e - 0.5) * 2 + vk * v), 0.15, 6.0)


def adjust_weights(weights: dict[str, float], prev: str | None, raining: bool, v: float, e: float, create_left: int, p: dict[str, Any],
                   write_mult: float = 1.0, intents: list[dict[str, Any]] | None = None) -> dict[str, float]:
    """选状态的最后一步：别连着两次一样、下雨更想趴窗边、按心情和精力调、
    写作按灵感 / 满足感 / 连着不顺调（write_mult）、今天写够了就不写、有打算的事更容易去做。"""
    w = dict(weights)
    if prev in w:
        w[prev] *= 0.2
    if raining and "rain_watch" in w:
        w["rain_watch"] *= 2.0
    for k in w:
        if w[k] > 0:
            w[k] *= state_affinity(k, v, e, p) * B.intent_boost(k, intents or [], p)
    if "create" in w:
        w["create"] = 0.0 if create_left <= 0 else w["create"] * write_mult
    return w


def prune(st: dict[str, Any], now: float, p: dict[str, Any]) -> None:
    """去掉已经回落到可以忽略的推动、太久以前的「同一类事」记录。"""
    keep = []
    for im in st.get("impulses", []):
        dt = now - im["t"]
        if abs(im.get("dv", 0)) * _decay(dt, p["mood_half_v"]) > 0.003 or abs(im.get("de", 0)) * _decay(dt, p["mood_half_e"]) > 0.003:
            keep.append(im)
    st["impulses"] = keep[-60:]
    horizon = max(1.0, float(p["mood_repeat_days"])) * 86400
    st["recent"] = [r for r in st.get("recent", []) if now - r[1] < horizon][-200:]


def push(st: dict[str, Any], now: float, p: dict[str, Any], dv: float, de: float, src: str, text: str, flavor: str | None = None) -> dict[str, Any]:
    """推一下（同一类事重复出现时打折）。返回实际推了多少。"""
    k = repeat_factor(st, src, now, p) if src else 1.0
    im = {"t": now, "dv": dv * k, "de": de * k, "src": src, "text": text}
    if abs(im["dv"]) > 1e-4 or abs(im["de"]) > 1e-4:
        st.setdefault("impulses", []).append(im)
    if src:
        st.setdefault("recent", []).append([src, now])
    if flavor:
        st["flavor"] = {"word": flavor, "until": now + p["mood_flavor_minutes"] * 60}
    stats = st.setdefault("stats", {})
    stats["net_dv"] = float(stats.get("net_dv", 0.0)) + im["dv"]
    prune(st, now, p)
    return im


def why(st: dict[str, Any], now: float, p: dict[str, Any]) -> str:
    """此刻心情的原因：还剩得最多的那次推动（够大才算），否则今天的原因。"""
    best, best_r = None, float(p["mood_why_min"])
    for im in st.get("impulses", []):
        r = abs(im.get("dv", 0)) * _decay(now - im["t"], p["mood_half_v"])
        if r >= best_r and im.get("text"):
            best, best_r = im, r
    return best["text"] if best else str(st.get("reason") or "")


def miss_due(st: dict[str, Any], last_user: float, now: float, p: dict[str, Any]) -> bool:
    """该不该「想念」：醒着的时间里有 mood_miss_hours 没和你说话（睡觉的时间不算），而且上次想念也过了这么久。"""
    hrs = float(p["mood_miss_hours"])
    if hrs <= 0:
        return False
    d = datetime.fromtimestamp(now)
    wake = d.replace(hour=int(p["wake_hour"]), minute=0, second=0, microsecond=0).timestamp()
    ref = max(last_user or 0.0, wake if now >= wake else wake - 86400)
    return now - ref > hrs * 3600 and now - float(st.get("miss_at") or 0) > hrs * 3600


def flavor_now(st: dict[str, Any], now: float, v: float) -> str | None:
    fl = st.get("flavor")
    if not fl or now >= float(fl.get("until", 0)) or fl.get("word") not in FLAVORS:
        return None
    lo, hi = FLAVORS[fl["word"]][2]
    return fl["word"] if lo <= v <= hi else None


def view(st: dict[str, Any], now: float, p: dict[str, Any]) -> dict[str, Any]:
    """给别处用的一份：v、e、心情词（附加词优先）、原因、精力的说法。"""
    v, e = value(st, now, p)
    base = current_word(st, v, e, p)
    word = flavor_now(st, now, v) or base
    floored = st.get("festival_floor") is not None and value(dict(st, festival_floor=None), now, p)[0] < float(st["festival_floor"])
    return {"v": round(v, 3), "e": round(e, 3), "word": word, "base_word": base, "why": "今天过节" if floored else why(st, now, p), "energy": energy_word(e),
            "reason": st.get("reason") or "", "day": st.get("day")}


def asked_about_mood(text: str, p: dict[str, Any]) -> bool:
    t = text or ""
    return any(w and w in t for w in (p.get("mood_ask_words") or []))


def expression_block(mv: dict[str, Any], user_text: str, p: dict[str, Any], rng: random.Random | None = None, detail: bool = True) -> str:
    """给上下文的心情说明：心情主要靠语气和用词体现；被问到才直说；偶尔（mood_mention_prob）可以自己顺口提一句。"""
    rng = rng or random
    why_txt = f"（{mv['why']}）" if detail and mv.get("why") else ""
    s = f"【你此刻的状态】心情：{mv['word']}{why_txt}；精力：{mv['energy']}。"
    if asked_about_mood(user_text, p):
        return s + "对方在问你的状态：可以直接、自然地说出来（别夸张）。"
    s += "让它体现在语气、用词和回复的长短里（累了话少一点、开心时轻快一点），不要直接说「我很X」「我心情X」这类话。"
    if rng.random() < float(p.get("mood_mention_prob", 0)):
        s += "这一轮可以顺口提一句自己的状态，一句就好。"
    return s


# ───────────────────────── 存取（真实运行用） ─────────────────────────
KEY = "mood_state"


def load(settings: dict[str, str] | None = None) -> dict[str, Any]:
    s = settings if settings is not None else db.get_settings()
    try:
        data = json.loads(s.get(KEY) or "{}")
    except json.JSONDecodeError:
        data = {}
    return data if isinstance(data, dict) else {}


def save(st: dict[str, Any]) -> None:
    db.save_settings({KEY: json.dumps(st, ensure_ascii=False)})


def _wake_ts(day: date, p: dict[str, Any]) -> float:
    return datetime.combine(day, datetime.min.time()).timestamp() + float(p["wake_hour"]) * 3600


def day_of(now: float, p: dict[str, Any]) -> str:
    """「她的哪一天」：起床之前算前一天（夜里还是昨天）。"""
    d = datetime.fromtimestamp(now)
    if d.hour + d.minute / 60 < float(p["wake_hour"]):
        d -= timedelta(days=1)
    return d.date().isoformat()


def sample(st: dict[str, Any], now: float, p: dict[str, Any], awake: bool) -> None:
    """记一笔当天的统计（按时间加权；醒着才算）：平均情绪、平均精力、最低情绪。"""
    stats = st.setdefault("stats", {})
    last = float(st.get("sampled_at") or now)
    st["sampled_at"] = now
    if not awake:
        return
    dt = clamp(now - last, 0.0, 600.0)
    if dt <= 0:
        return
    v, e = value(st, now, p)
    stats["w"] = float(stats.get("w", 0.0)) + dt
    stats["sum_v"] = float(stats.get("sum_v", 0.0)) + v * dt
    stats["sum_e"] = float(stats.get("sum_e", 0.0)) + e * dt
    stats["min_v"] = min(float(stats.get("min_v", 1.0)), v)
    st["last_v"] = v


def averages(st: dict[str, Any]) -> tuple[float | None, float | None]:
    s = st.get("stats") or {}
    w = float(s.get("w", 0.0))
    return (s["sum_v"] / w, s["sum_e"] / w) if w > 60 else (None, None)


def new_day(st: dict[str, Any], now: float, ctx: dict[str, Any], p: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """换到新的一天：结算昨天、抽昨晚的睡眠、算今天的基线和原因、定今天写几次。返回昨天的结算（给日历存）。

    ctx：kind、rain_streak、season、weekday、festival（今天过节）——别的从 st 里自己拿。"""
    prev_avg_v, prev_avg_e = averages(st)
    yesterday = {"day": st.get("day"), "avg_v": prev_avg_v, "avg_e": prev_avg_e, "min_v": (st.get("stats") or {}).get("min_v"),
                 "net_dv": (st.get("stats") or {}).get("net_dv", 0.0), "base_v": st.get("base_v"), "sleep_q": st.get("sleep_q")}
    bed_v = st.get("bed_v", st.get("last_v"))
    sq = sleep_quality(bed_v, p, rng)
    full = dict(ctx, prev_avg_v=prev_avg_v if prev_avg_v is not None else st.get("base_v"), sleep_q=sq, yesterday_dv=yesterday["net_dv"] or 0.0)
    base, factors = baseline(full, p, rng)
    st.update({
        "day": day_of(now, p), "base_v": base, "sleep_q": sq, "factors": [[n, round(c, 4), t] for n, c, t in factors],
        "reason": reason_text(factors), "reason_src": "local", "stats": {"net_dv": 0.0}, "bed_v": None,
        "festival_floor": p["mood_festival_floor"] if ctx.get("festival") else None,
        "create": {"done": 0},
    })
    v, e = value(st, now, p)
    current_word(st, v, e, p, rng)
    return yesterday


def note_bedtime(st: dict[str, Any], now: float, p: dict[str, Any]) -> None:
    st["bed_v"] = value(st, now, p)[0]
