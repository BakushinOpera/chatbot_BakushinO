"""生活规则（纯函数，life.py 用，参数一律从 p 字典里拿）。

* v10：读书 / 做饭 / 出门也有结果分档（各自的情绪反应范围；读书的档真的影响读多少，出门的档决定回来那件事的基调）。

* 心情词：按「情绪 × 精力」分四个区域，每个区域一组词（设置页里维护）；进入一个区域、或者一件事带来的情绪落在某个区域时，从那组随机取一个词。
* 写作结果分档：顺利 / 写了一点 / 卡住 / 不满意 / 删了重写。每档的概率受心情、精力、灵感影响；每档有自己的事件文字和情绪反应范围。
* 事件的基调：好事 / 平常 / 不顺，用洗牌袋抽（默认 正面和平常 : 不顺 ≈ 3 : 1，不会扎堆）；同一类事件的「不顺」有冷却期（默认 3 天）。
* 不是每件事都带情绪：写作和日常事件默认只有约 40% 产生情绪反应，其余只记录。
* 灵感值：读书、事件、天气、和你聊天会加，随时间衰减；写作的欲望跟着灵感走。写完一大段之后几天里「写够了」（满足感），写作欲望降低。
* 行为适应：写作连着不顺，下次去写的概率降低，并换个做法：歇几个小时 / 换个题材 / 先去看书 / 来问你（每周有上限）。
* 每天的小打算：早上定 1~2 个，之后相关的事更容易去做；没做完的顺延到第二天。
"""

from __future__ import annotations

import math
import random
from typing import Any

# ───────────────────────── 心情词：四个区域 ─────────────────────────
QUADS: list[tuple[str, str]] = [("hv_he", "高兴、有精神"), ("hv_le", "高兴、没精神"), ("lv_he", "低落、有精神"), ("lv_le", "低落、没精神")]
QUAD_DEFAULTS: dict[str, list[str]] = {
    "hv_he": ["开心", "雀跃", "兴致勃勃", "元气满满", "跃跃欲试", "期待", "小得意", "好奇", "专注", "充实"],
    "hv_le": ["惬意", "暖洋洋", "安心", "温柔", "满足", "平静", "懒洋洋", "放空", "淡然"],
    "lv_he": ["烦躁", "小纠结", "委屈", "心不在焉", "坐立不安", "焦虑:0.3"],
    "lv_le": ["忧郁", "空落落", "闷闷的", "恍惚", "犯困", "无所事事", "沮丧"],
}
QUAD_CENTER = {"hv_he": (0.4, 0.75), "hv_le": (0.3, 0.3), "lv_he": (-0.4, 0.7), "lv_le": (-0.4, 0.25)}


def parse_words(items: list[str] | None) -> list[tuple[str, float]]:
    """「词」或「词:权重」（也认「词*权重」「词：权重」）→ [(词, 权重)]。"""
    out = []
    for raw in items or []:
        s = str(raw).strip().replace("：", ":").replace("*", ":")
        if not s:
            continue
        w, _, n = s.partition(":")
        try:
            weight = float(n) if n.strip() else 1.0
        except ValueError:
            weight = 1.0
        if w.strip() and weight > 0:
            out.append((w.strip(), weight))
    return out


def quad_words(q: str, p: dict[str, Any]) -> list[tuple[str, float]]:
    return parse_words(p.get(f"mood_words_{q}")) or parse_words(QUAD_DEFAULTS[q])


def quad_of(v: float, e: float, prev: str | None, p: dict[str, Any]) -> str:
    """(情绪, 精力) 在哪个区域。离分界线不够远就保持原来的区域（防止在分界线上来回跳）。"""
    vs, es, h = float(p.get("mood_v_split", -0.1)), float(p.get("mood_e_split", 0.55)), float(p.get("mood_q_hyst", 0.04))
    if prev in QUAD_CENTER:
        pv_high, pe_high = prev.startswith("hv"), prev.endswith("he")
        hv = v >= vs - h if pv_high else v >= vs + h
        he = e >= es - h if pe_high else e >= es + h
    else:
        hv, he = v >= vs, e >= es
    return ("hv" if hv else "lv") + "_" + ("he" if he else "le")


def pick_word(q: str, p: dict[str, Any], rng: random.Random, avoid: str | None = None) -> str:
    words = quad_words(q, p)
    pool = [(w, n) for w, n in words if w != avoid] or words
    return rng.choices([w for w, _ in pool], [n for _, n in pool])[0]


def all_words(p: dict[str, Any]) -> dict[str, str]:
    """词 → 所在区域（同一个词写在几组里时，以第一个为准）。"""
    out: dict[str, str] = {}
    for q, _l in QUADS:
        for w, _n in quad_words(q, p):
            out.setdefault(w, q)
    return out


# ───────────────────────── 写作结果分档 ─────────────────────────
WRITE_TIERS: list[tuple[str, str]] = [("smooth", "顺利"), ("some", "写了一点"), ("stuck", "卡住"), ("unhappy", "不满意"), ("rewrite", "删了重写")]
TIER_COLUMNS = [("w", "基础权重", 0.0, 10.0, 0.01), ("ke", "精力系数", -5.0, 5.0, 0.1), ("kv", "情绪系数", -5.0, 5.0, 0.1), ("ki", "灵感系数", -5.0, 5.0, 0.1),
                ("dv", "情绪反应（平均）", -1.0, 1.0, 0.01), ("sd", "情绪反应（波动）", 0.0, 1.0, 0.01), ("de", "精力变化", -1.0, 1.0, 0.01),
                ("insp", "用掉的灵感", 0.0, 1.0, 0.01), ("sat", "满足感（写够了）", 0.0, 1.0, 0.01)]
TIER_DEFAULTS = [  # 顺利 / 写了一点 / 卡住 / 不满意 / 删了重写
    {"w": 0.25, "ke": 0.8, "kv": 0.6, "ki": 1.2, "dv": 0.28, "sd": 0.1, "de": -0.06, "insp": 0.45, "sat": 0.6},
    {"w": 0.35, "ke": 0.2, "kv": 0.2, "ki": 0.3, "dv": 0.1, "sd": 0.08, "de": -0.04, "insp": 0.15, "sat": 0.15},
    {"w": 0.2, "ke": -1.6, "kv": -0.4, "ki": -0.8, "dv": -0.15, "sd": 0.08, "de": -0.05, "insp": 0.05, "sat": 0.0},
    {"w": 0.12, "ke": -0.4, "kv": -1.2, "ki": -0.3, "dv": -0.18, "sd": 0.08, "de": -0.04, "insp": 0.1, "sat": 0.0},
    {"w": 0.08, "ke": 0.0, "kv": -0.6, "ki": 0.2, "dv": -0.02, "sd": 0.12, "de": -0.06, "insp": 0.2, "sat": 0.3},
]
WRITE_TEXT = {
    "smooth": ["顺顺利利写完一大段《{t}》", "《{t}》一口气写了好长一段，写得很顺"],
    "some": ["在《{t}》里写了一点", "《{t}》写了几句，写到哪算哪"],
    "stuck": ["对着《{t}》坐了半天，一个字没写出来", "《{t}》卡住了，不知道接下来怎么写"],
    "unhappy": ["写了几行，越看越不满意，划掉了", "写了一段，不喜欢，留在草稿本里没往文档里放"],
    "rewrite": ["把《{t}》前面写的删了一段，重新写了", "推翻了《{t}》之前的写法，重写了一遍"],
}
GOOD_TIERS, BAD_TIERS = {"smooth", "some"}, {"stuck", "unhappy"}


def _rows(p: dict[str, Any], key: str, defaults: list[dict[str, float]]) -> list[dict[str, float]]:
    rows = p.get(key)
    return rows if isinstance(rows, list) and len(rows) == len(defaults) else defaults


def tier_weights(v: float, e: float, insp: float, p: dict[str, Any]) -> list[float]:
    rows = _rows(p, "write_tiers", TIER_DEFAULTS)
    ref = float(p.get("insp_ref", 0.25))                  # 灵感按「比平常多 / 少」算（平常大约 0.25）
    return [max(0.0, r["w"]) * math.exp(r["ke"] * (e - 0.5) * 2 + r["kv"] * v + r["ki"] * (insp - ref) * 2) for r in rows]


def sample_tier(v: float, e: float, insp: float, p: dict[str, Any], rng: random.Random) -> str:
    w = tier_weights(v, e, insp, p)
    return rng.choices([k for k, _ in WRITE_TIERS], w)[0] if sum(w) > 0 else "some"


def tier_row(tier: str, p: dict[str, Any]) -> dict[str, float]:
    return _rows(p, "write_tiers", TIER_DEFAULTS)[[k for k, _ in WRITE_TIERS].index(tier)]


def tier_label(tier: str) -> str:
    return dict(WRITE_TIERS).get(tier, tier)


def write_text(tier: str, title: str, rng: random.Random) -> str:
    return rng.choice(WRITE_TEXT[tier]).format(t=title or "那篇东西")


def reaction(row: dict[str, float], rng: random.Random) -> tuple[float, float]:
    """一次情绪反应：情绪按 (平均, 波动) 抽，精力取平均。"""
    return rng.gauss(row["dv"], max(0.0, row["sd"])), float(row.get("de", 0.0))


def reacts(kind: str, p: dict[str, Any], rng: random.Random) -> bool:
    """这件事带不带情绪（写作 / 日常事件默认约 40%）。"""
    return rng.random() < float(p.get("react_p_writing" if kind == "writing" else "react_p_event", 0.4))


# ───────────────────────── 读书 / 做饭 / 出门的结果分档 ─────────────────────────
# 每档：基础权重 w、精力系数 ke、情绪系数 kv（同写作的算法，没有灵感项）；情绪反应（平均 dv，波动 sd）、精力变化 de。
ACT_TIERS: dict[str, list[tuple[str, str]]] = {
    "read": [("absorbed", "读得入迷"), ("some", "读了一些"), ("bored", "读不进去")],
    "cook": [("great", "做得很成功"), ("ok", "普普通通"), ("flop", "有点失手")],
    "out": [("fun", "玩得很开心"), ("ok", "一般般"), ("meh", "有点扫兴")],
}
ACT_LABEL = {"read": "读书", "cook": "做饭", "out": "出门"}
ACT_COLUMNS = [("w", "基础权重", 0.0, 10.0, 0.01), ("ke", "精力系数", -5.0, 5.0, 0.1), ("kv", "情绪系数", -5.0, 5.0, 0.1),
               ("dv", "情绪反应（平均）", -1.0, 1.0, 0.01), ("sd", "情绪反应（波动）", 0.0, 1.0, 0.01), ("de", "精力变化", -1.0, 1.0, 0.01)]
ACT_DEFAULTS: dict[str, list[dict[str, float]]] = {
    "read": [{"w": 0.3, "ke": 0.6, "kv": 0.5, "dv": 0.15, "sd": 0.08, "de": 0.0},
             {"w": 0.5, "ke": 0.0, "kv": 0.0, "dv": 0.03, "sd": 0.05, "de": 0.0},
             {"w": 0.2, "ke": -1.2, "kv": -0.8, "dv": -0.12, "sd": 0.06, "de": -0.02}],
    "cook": [{"w": 0.3, "ke": 0.4, "kv": 0.3, "dv": 0.15, "sd": 0.07, "de": 0.0},
             {"w": 0.55, "ke": 0.0, "kv": 0.0, "dv": 0.02, "sd": 0.05, "de": 0.0},
             {"w": 0.15, "ke": -0.8, "kv": -0.4, "dv": -0.12, "sd": 0.06, "de": -0.02}],
    "out": [{"w": 0.4, "ke": 0.5, "kv": 0.5, "dv": 0.18, "sd": 0.08, "de": 0.0},
            {"w": 0.45, "ke": 0.0, "kv": 0.0, "dv": 0.03, "sd": 0.05, "de": 0.0},
            {"w": 0.15, "ke": -0.6, "kv": -0.5, "dv": -0.14, "sd": 0.07, "de": -0.03}],
}
ACT_TEXT: dict[str, dict[str, list[str]]] = {
    "read": {"absorbed": ["读《{t}》读得入迷，一口气读了好多", "《{t}》越读越停不下来"],
             "some": ["读了一会儿《{t}》", "《{t}》又往下读了一点"],
             "bored": ["翻了几页《{t}》，读不进去", "《{t}》看了几行就走神了，放下了"]},
    "cook": {"great": ["做的{t}特别成功，很好吃", "{t}做得比上次好多了"],
             "ok": ["做了{t}，味道还行", "{t}做得普普通通"],
             "flop": ["{t}有点做砸了，咸了一点", "{t}火候没掌握好，糊了一点，还是吃了"]},
    "out": {"fun": ["{t}，玩得很开心", "{t}，回来心情很好"],
            "ok": ["{t}，平平常常", "{t}，没什么特别的"],
            "meh": ["{t}，有点扫兴", "{t}，不太顺，早早回来了"]},
}
OUT_TONE = {"fun": "good", "ok": "neutral", "meh": "bad"}           # 出门的档 → 回来时那件事的基调
READ_EFFECT = {"absorbed": (1.15, None), "some": (1.0, None), "bored": (0.7, (0.15, 0.35))}   # 读书的档：读的速度倍率、提前放下（在计划时长的几成）
BAD_ACT = {"read": "bored", "cook": "flop", "out": "meh"}


def act_rows(kind: str, p: dict[str, Any]) -> list[dict[str, float]]:
    return _rows(p, f"{kind}_tiers", ACT_DEFAULTS[kind])


def sample_act(kind: str, v: float, e: float, p: dict[str, Any], rng: random.Random) -> str:
    keys = [k for k, _ in ACT_TIERS[kind]]
    w = [max(0.0, r["w"]) * math.exp(r["ke"] * (e - 0.5) * 2 + r["kv"] * v) for r in act_rows(kind, p)]
    return rng.choices(keys, w)[0] if sum(w) > 0 else keys[1]


def act_row(kind: str, tier: str, p: dict[str, Any]) -> dict[str, float]:
    return act_rows(kind, p)[[k for k, _ in ACT_TIERS[kind]].index(tier)]


def act_label(kind: str, tier: str) -> str:
    return dict(ACT_TIERS[kind]).get(tier, tier)


def act_text(kind: str, tier: str, title: str, rng: random.Random) -> str:
    return rng.choice(ACT_TEXT[kind][tier]).format(t=title)


def after_act(st: dict[str, Any], kind: str, tier: str, now: float, p: dict[str, Any]) -> bool:
    """读书 / 做饭 / 出门连着不顺：记次数；读书连着读不进去够 adapt_n 次，就歇几个小时不读（返回 True）。"""
    key = f"{kind}_bad_streak"
    st[key] = int(st.get(key, 0)) + 1 if tier == BAD_ACT[kind] else 0
    if kind == "read" and st[key] >= int(p.get("adapt_n", 2)):
        st["read_pause_until"] = now + float(p.get("adapt_pause_h", 6)) * 3600
        st[key] = 0
        return True
    return False


# ───────────────────────── 事件的基调：洗牌袋 + 不顺的冷却 ─────────────────────────
TONES: list[tuple[str, str]] = [("good", "好事"), ("neutral", "平常"), ("bad", "不顺")]
TONE_COLUMNS = [("dv", "情绪反应（平均）", -1.0, 1.0, 0.01), ("sd", "情绪反应（波动）", 0.0, 1.0, 0.01), ("de", "精力变化", -1.0, 1.0, 0.01)]
TONE_DEFAULTS = [{"dv": 0.2, "sd": 0.08, "de": 0.02}, {"dv": 0.02, "sd": 0.06, "de": 0.0}, {"dv": -0.2, "sd": 0.08, "de": -0.03}]


def tone_row(tone: str, p: dict[str, Any]) -> dict[str, float]:
    return _rows(p, "event_tones", TONE_DEFAULTS)[[k for k, _ in TONES].index(tone)]


def refill_bag(p: dict[str, Any], rng: random.Random) -> list[str]:
    """一袋签：总数 bag_size，正面和平常 : 不顺 = bag_ratio : 1；正面和平常里好事占 bag_good_share。打乱。"""
    size = max(2, int(p.get("bag_size", 12)))
    ratio = max(0.0, float(p.get("bag_ratio", 3.0)))
    bad = round(size / (1 + ratio)) if ratio > 0 else size
    rest = size - bad
    good = round(rest * float(p.get("bag_good_share", 0.5)))
    bag = ["bad"] * bad + ["good"] * good + ["neutral"] * (rest - good)
    rng.shuffle(bag)
    return bag


def draw_tone(st: dict[str, Any], etype: str, now: float, p: dict[str, Any], rng: random.Random) -> str:
    """抽一张签（袋子空了就重装）。抽到「不顺」但同一类事件冷却期内已经不顺过 → 改成平常。st 里存 bag / neg_at。"""
    bag = st.get("bag") or []
    if not bag:
        bag = refill_bag(p, rng)
    tone = bag.pop()
    st["bag"] = bag
    neg = st.setdefault("neg_at", {})
    cd = float(p.get("neg_cooldown_days", 3)) * 86400
    if tone == "bad":
        if now - float(neg.get(etype, 0)) < cd:
            tone = "neutral"
        else:
            neg[etype] = now
    return tone


# ───────────────────────── 灵感和满足感 ─────────────────────────
def _decayed(st: dict[str, Any], key: str, now: float, half_s: float) -> float:
    v = float(st.get(key, 0.0))
    t = float(st.get(key + "_t", now))
    return v * 0.5 ** (max(0.0, now - t) / max(1.0, half_s))


def insp(st: dict[str, Any], now: float, p: dict[str, Any]) -> float:
    return _decayed(st, "insp", now, float(p.get("insp_half_h", 18)) * 3600)


def add_insp(st: dict[str, Any], now: float, amount: float, p: dict[str, Any]) -> float:
    v = max(0.0, min(1.5, insp(st, now, p) + amount))
    st["insp"], st["insp_t"] = v, now
    return v


def sat(st: dict[str, Any], now: float, p: dict[str, Any]) -> float:
    return _decayed(st, "sat", now, float(p.get("sat_half_days", 2)) * 86400)


def add_sat(st: dict[str, Any], now: float, amount: float, p: dict[str, Any]) -> float:
    v = max(0.0, min(1.0, sat(st, now, p) + amount))
    st["sat"], st["sat_t"] = v, now
    return v


def write_mult(st: dict[str, Any], now: float, p: dict[str, Any]) -> float:
    """写作被选中的倍率：灵感越多越想写；写完一大段之后（满足感）、连着不顺（适应）都会降。"""
    i, s = insp(st, now, p), sat(st, now, p)
    streak = int(st.get("write_bad_streak", 0))
    m = (float(p.get("insp_floor", 0.1)) + float(p.get("insp_k", 2.0)) * i) * max(float(p.get("sat_floor", 0.05)), 1 - s)
    if streak >= int(p.get("adapt_n", 2)):
        m *= float(p.get("adapt_mult", 0.5)) ** (streak - int(p.get("adapt_n", 2)) + 1)
    if now < float(st.get("write_pause_until", 0)):
        m = 0.0
    return m


# ───────────────────────── 写作连着不顺：换个做法 ─────────────────────────
ADAPTS: list[tuple[str, str]] = [("pause", "歇几个小时不写"), ("switch", "下次换个题材 / 写法"), ("read", "先去看会儿书"), ("ask", "来问你")]


def after_write(st: dict[str, Any], tier: str, now: float, p: dict[str, Any], rng: random.Random, ask_ok: bool) -> str | None:
    """写完一次：更新连着不顺的次数；够了就挑一个换做法的办法（返回它的键）。ask_ok：这周还能来问你。"""
    if tier in BAD_TIERS:
        st["write_bad_streak"] = int(st.get("write_bad_streak", 0)) + 1
    elif tier in GOOD_TIERS:
        st["write_bad_streak"] = 0
    if int(st.get("write_bad_streak", 0)) < int(p.get("adapt_n", 2)) or tier not in BAD_TIERS:
        return None
    opts = [(k, float(p.get(f"adapt_w_{k}", 0.25))) for k, _ in ADAPTS if k != "ask" or ask_ok]
    opts = [(k, w) for k, w in opts if w > 0]
    if not opts:
        return None
    pick = rng.choices([k for k, _ in opts], [w for _, w in opts])[0]
    if pick == "pause":
        st["write_pause_until"] = now + float(p.get("adapt_pause_h", 6)) * 3600
    elif pick == "switch":
        st["write_switch"] = True
    return pick


# ───────────────────────── 每天的小打算 ─────────────────────────
# (键, 相关的状态, 说法, 需要什么)
INTENTS: list[tuple[str, tuple[str, ...], str, str]] = [
    ("write", ("create",), "写一段《{doc}》", "doc"),
    ("read", ("read",), "把《{book}》再看一点", "book"),
    ("tidy", ("tidy",), "收拾一下房间", ""),
    ("shelf", ("shelf",), "整理书架", ""),
    ("bake", ("bake",), "烤点甜点", ""),
    ("craft", ("craft",), "把模型拼完 / 涂完", ""),
    ("calligraphy", ("calligraphy",), "练一页字", ""),
    ("exercise", ("exercise",), "跳会儿绳 / 做做瑜伽", ""),
    ("draw", ("draw",), "画点东西", ""),
    ("album", ("album",), "整理一下相册", ""),
    ("photo", ("photo",), "出门拍点照片", "out"),
    ("walk", ("walk",), "出去走走", "out"),
    ("call", ("call",), "给{friend}打个电话", "friend"),
    ("synth", ("synth",), "玩会儿妈妈的合成器", ""),
    ("netshop", ("netshop",), "逛逛网店", ""),
]


def make_intents(carry: list[dict[str, Any]], ctx: dict[str, Any], p: dict[str, Any], rng: random.Random) -> list[dict[str, Any]]:
    """今天的小打算：先接着昨天没做完的（每个最多顺延 intent_carry_days 天），再补到 1~2 个。
    写作出现的比例 ≈ intent_write_share（有能写的文档时）。ctx：doc、book、friend（名字，没有就空）、out_ok（今天能出门）。"""
    out = []
    for it in carry:
        if not it.get("done") and int(it.get("carried", 0)) < int(p.get("intent_carry_days", 2)):
            out.append(dict(it, carried=int(it.get("carried", 0)) + 1))
    want = 2 if rng.random() < float(p.get("intent_n2_p", 0.45)) else 1
    have = {it["key"] for it in out}
    ok = []
    for key, sts, text, need in INTENTS:
        if key in have or (need == "doc" and not ctx.get("doc")) or (need == "book" and not ctx.get("book")) \
                or (need == "friend" and not ctx.get("friend")) or (need == "out" and not ctx.get("out_ok", True)):
            continue
        ok.append((key, sts, text))
    while len(out) < want and ok:
        writes = [x for x in ok if x[0] == "write"]
        others = [x for x in ok if x[0] != "write"]
        share = float(p.get("intent_write_share", 0.25))
        pick = writes[0] if writes and (not others or rng.random() < share) else rng.choice(others or writes)
        ok.remove(pick)
        key, sts, text = pick
        out.append({"key": key, "states": list(sts), "text": text.format(doc=ctx.get("doc") or "", book=ctx.get("book") or "", friend=ctx.get("friend") or "朋友"),
                    "done": False, "carried": 0})
    return out


def intent_boost(key: str, intents: list[dict[str, Any]], p: dict[str, Any]) -> float:
    return float(p.get("intent_boost", 2.0)) if any(not it.get("done") and key in it.get("states", []) for it in intents or []) else 1.0


def mark_intent(intents: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    """做完一件和打算有关的事：把那个打算记成做了。返回它。"""
    for it in intents or []:
        if not it.get("done") and key in it.get("states", []):
            it["done"] = True
            return it
    return None


# ───────────────────────── 外出回来：按概率发生一件事 ─────────────────────────
OUTING_EVENTS: dict[str, list[str]] = {
    "friends": ["friend"], "boardgame_friends": ["friend"], "dine_out": ["stranger", "money"], "shop": ["money", "stranger", "item"],
    "walk": ["season", "stranger"], "photo": ["season", "item", "stranger"], "errand": ["stranger", "home", "money"],
}
