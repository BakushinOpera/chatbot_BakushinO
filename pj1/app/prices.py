"""价格合理性（v13，见 docs/已完成的计划/价格带草稿.md）：价格带 × 修正系数（档次 / 噱头 / 地点）→ 允许范围；去过的店、吃过的东西记价格。

* 她生成消费或吃饭时，模型给出结构化信息：店名、东西、大类、档次、噱头、地点、价格。
* 代码按「价格带 × 档次 × 噱头 × 地点」算允许范围：超出就让模型重新想一次，还不行就夹回范围内（`check`）。
* 价格尾数：100 以下 .9 多（22.9），100 以上多是 199 / 268 这种整数，偶尔 .9（`pretty`）；游戏内购只取常见档位。
* 同一家店的同一样东西再出现：价格和上次一致，或者在 ±10% 里浮动（记在小本子的店铺条目里，`recall` / `remember`，见 notebook.py）。
* 价格带和系数都在设置页（「零花钱」分组）里能改。单位：钱（1 钱 ≈ 1 元）。
"""

from __future__ import annotations

import math
import random
import re
from typing import Any

from . import character as C
from . import notebook, registry
from .price_defs import (  # noqa: F401  —— 默认值放在 price_defs（registry 加载时要读），这里转一手方便别处用 prices.XXX
    BAND, BANDS, BAND_COLUMNS, BAND_DEFAULTS, BAND_KEYS, BAND_ROWS, CAT_BANDS, FOOD_BANDS, GIMMICKS, IAP_TIERS, LUXURY_OK, MODS,
    MOD_COLUMNS, MOD_DEFAULT, NO_MOD, PLACES, TIERS,
)


# ───────────────────────── 读设置 ─────────────────────────
def band(key: str) -> dict[str, float]:
    rows = registry.get("price_bands")
    try:
        r = rows[BAND_KEYS.index(key)]
        out = {k: float(r[k]) for k in ("lo", "c_lo", "c_hi", "hi")}
    except (ValueError, IndexError, KeyError, TypeError):
        d = BAND[key]
        out = {"lo": d[3], "c_lo": d[4], "c_hi": d[5], "hi": d[6]}
    out["lo"], out["hi"] = min(out["lo"], out["hi"]), max(out["lo"], out["hi"])
    return out


def mod_k(kind: str, key: str) -> float:
    table = MODS[kind]
    keys = [t[0] for t in table]
    if key not in keys:
        key = MOD_DEFAULT[kind]
    rows = registry.get(f"price_mod_{kind}")
    try:
        return float(rows[keys.index(key)]["k"])
    except (IndexError, KeyError, TypeError, ValueError):
        return table[keys.index(key)][2]


def label(kind: str, key: str) -> str:
    return {t[0]: t[1] for t in MODS[kind]}.get(key, "")


def _by_label(kind: str, v: Any) -> str:
    """模型可能写键（mall）也可能写名字（商场）：都认。认不出来就用默认。"""
    s = str(v or "").strip()
    for k, name, *_ in MODS[kind]:
        if s == k or s == name or (name and s and (s in name.split(" / ") or name.startswith(s))):
            return k
    return MOD_DEFAULT[kind]


def _band_by_label(v: Any, allowed: tuple[str, ...]) -> str:
    s = str(v or "").strip()
    for k in allowed:
        if s == k or s == BAND[k][1]:
            return k
    return allowed[0]


def normalize(data: dict[str, Any], cat: str) -> dict[str, Any]:
    """把模型给的结构化信息规整好：大类必须在这个消费类别允许的价格带里；高档只在部分大类里有；稀有食材只在吃的里。"""
    allowed = CAT_BANDS.get(cat) or (cat,)
    b = _band_by_label(data.get("band"), allowed)
    tier, gim, place = _by_label("tier", data.get("tier")), _by_label("gimmick", data.get("gimmick")), _by_label("place", data.get("place"))
    if b in NO_MOD:
        tier, gim, place = "chain", "none", C.home_price_place()
    if tier == "luxury" and b not in LUXURY_OK:
        tier = "mall"
    if gim == "rare" and b not in FOOD_BANDS:
        gim = "none"
    try:
        price = float(str(data.get("price")).replace(C.unit(), "").replace("钱", "").replace("元", "").strip())
    except (TypeError, ValueError):
        price = 0.0
    shop = re.sub(r"\s+", "", str(data.get("shop") or ""))[:16]
    return {"band": b, "tier": tier, "gimmick": gim, "place": place, "price": price if math.isfinite(price) else 0.0, "shop": shop}


# ───────────────────────── 允许范围 / 检查 ─────────────────────────
def mult(info: dict[str, Any]) -> float:
    if info["band"] in NO_MOD:
        return 1.0
    return mod_k("tier", info["tier"]) * mod_k("gimmick", info["gimmick"]) * mod_k("place", info["place"])


def allowed(info: dict[str, Any], hi_override: float | None = None) -> tuple[float, float]:
    b = band(info["band"])
    m = mult(info)
    hi = hi_override if hi_override is not None else b["hi"]
    return round(b["lo"] * m, 1), round(hi * m, 1)


def common(info: dict[str, Any]) -> tuple[float, float]:
    b = band(info["band"])
    m = mult(info)
    return round(b["c_lo"] * m, 1), round(b["c_hi"] * m, 1)


def in_range(info: dict[str, Any], hi_override: float | None = None) -> bool:
    lo, hi = allowed(info, hi_override)
    return lo <= info["price"] <= hi


def clamp(info: dict[str, Any], hi_override: float | None = None, rng: random.Random | None = None) -> float:
    """夹回允许范围：没给价格（0）就在「常见」区间里取一个。"""
    rng = rng or random
    lo, hi = allowed(info, hi_override)
    p = info["price"]
    if p <= 0:
        clo, chi = common(info)
        p = rng.uniform(max(lo, clo), max(lo, min(hi, chi)))
    return min(hi, max(lo, p))


def why_expensive(info: dict[str, Any]) -> str:
    """贵在哪：给提示词用，让她分享时自然提一句（商场里的 / 联名款 / 被坑了）。"""
    bits = []
    if info["tier"] in ("mall", "luxury"):
        bits.append(label("tier", info["tier"]) + "里的" if info["tier"] == "mall" else "高档店")
    if info["gimmick"] != "none":
        bits.append({"hot": "网红店", "limited": "限定款", "ip": "联名款", "rare": "用了稀有食材", "ripoff": "被坑了"}[info["gimmick"]])
    if mod_k("place", info["place"]) > 1.0:
        bits.append(f"在{label('place', info['place'])}，那边物价高")
    return "、".join(bits)


# ───────────────────────── 尾数 ─────────────────────────
def pretty(p: float, info: dict[str, Any], rng: random.Random | None = None, lo: float | None = None, hi: float | None = None) -> float:
    """价格尾数：100 以下一半是 x.9（22.9），三成整数或 .5；100 以上多是 199 / 268 这种整数，偶尔 x.9。游戏内购只取常见档位。"""
    rng = rng or random
    if info["band"] == "iap":
        cands = [t for t in IAP_TIERS if (lo is None or t >= lo) and (hi is None or t <= hi)] or list(IAP_TIERS)
        return float(min(cands, key=lambda t: abs(t - p)))
    r = rng.random()
    if p < 100:
        if r < 0.5:
            q = math.floor(p) + 0.9 if p >= 1 else 0.9
        elif r < 0.8:
            q = round(p * 2) / 2
            q = float(round(q)) if rng.random() < 0.6 else q
        else:
            q = round(p, 1)
    else:
        n = int(round(p))
        if r < 0.45:
            q = float(n - n % 10 + 9 if n % 10 >= 4 else n - n % 10 - 1)       # 199 / 189
        elif r < 0.7:
            q = float(n - n % 10 + 8)                                          # 268
        elif r < 0.85:
            q = float(round(n, -1))
        else:
            q = n + 0.9
    if lo is not None and q < lo:
        q = lo
    if hi is not None and q > hi:
        q = math.floor(hi) + (0.9 if hi - math.floor(hi) >= 0.9 else 0.0) if hi >= 1 else hi
        q = min(q, hi)
    return round(max(q, 0.1), 1)


# ───────────────────────── 去过的店、吃过的东西（存在小本子的店铺条目里，见 notebook.py） ─────────────────────────
def _norm(s: str) -> str:
    return notebook.norm(s)


def recall(shop: str, item: str) -> dict[str, Any] | None:
    return notebook.recall_price(shop, item)


def remember(shop: str, item: str, info: dict[str, Any], price: float, impression: str = "") -> None:
    notebook.remember_price(shop, item, info, price, impression=impression)       # 价格不改：以第一次的为准，之后都在它的 ±10% 里浮动


def known(bands: tuple[str, ...], n: int = 8) -> list[dict[str, Any]]:
    return notebook.known_prices(tuple(bands), n)


def all_known(n: int = 200) -> list[dict[str, Any]]:
    return notebook.known_prices(None, n)


def repeat_price(prev: float, info: dict[str, Any], rng: random.Random | None = None) -> float:
    """同一家店同一样东西：六成和上次一样，其余在 ±10% 里浮动（尾数照样偏 .9）。"""
    rng = rng or random
    if rng.random() < registry.num("price_repeat_same"):
        return prev
    lo, hi = prev * 0.9, prev * 1.1
    return pretty(rng.uniform(lo, hi), info, rng, lo=round(lo, 1), hi=round(hi, 1))


# ───────────────────────── 给提示词 ─────────────────────────
def pricey_places() -> str:
    """比家里贵的地方（地点系数 > 1），给「为什么贵」那句话用：「杭州 / 上海」。"""
    return " / ".join(n for k, n, _x, _d in PLACES if mod_k("place", k) > 1.0) or "大城市"


def place_habit() -> str:
    """「平时就在新昌，偶尔去嵊州；杭州 / 上海要特地出门才去。」——按地点系数和角色配置生成。"""
    home = C.home_price_place()
    near = [n for k, n, _x, _d in PLACES if k != home and mod_k("place", k) <= 1.0]
    far = [n for k, n, _x, _d in PLACES if mod_k("place", k) > 1.0]
    out = f"平时就在{label('place', home)}" + (f"，偶尔去{'、'.join(near)}" if near else "")
    return out + (f"；{' / '.join(far)}要特地出门才去。" if far else "。")


def prompt_block(cat: str, budget: float | None = None, here: str | None = None, pick: bool = False, rng: random.Random | None = None) -> str:
    """告诉模型：这一类有哪些价格带、档次 / 噱头 / 地点各是什么、去过哪些店，以及要输出的字段。
    here：她现在在哪（地点库的 id）。给了就固定地点系数，不让模型自己选（见 places.py）。
    pick：这一带有去过的店时，先定这次去老店还是试新店（老店的概率见设置；去老店就带上她上次买过什么、对这家店的印象）。"""
    allowed_b = CAT_BANDS.get(cat) or (cat,)
    lines = [f"【价格】{C.money_note()}。这一类东西的价格带（一个人一份，普通店、在{label('place', C.home_price_place())}）："]
    for k in allowed_b:
        b = band(k)
        lines.append(f"- {BAND[k][1]}（{BAND[k][2]}）：常见 {b['c_lo']:g}~{b['c_hi']:g} {C.unit()}，最低 {b['lo']:g}，最高 {b['hi']:g}")
    if not all(k in NO_MOD for k in allowed_b):
        lines.append("店铺档次（tier）：" + "；".join(f"{n}（×{mod_k('tier', k):g}，{d}）" for k, n, _x, d in TIERS)
                     + f"。高档只有{'、'.join(BAND[k][1] for k in LUXURY_OK)}才有。")
        lines.append("噱头（gimmick）：" + "；".join(f"{n}（×{mod_k('gimmick', k):g}{('，' + d) if d else ''}）" for k, n, _x, d in GIMMICKS) + "。大多数时候是「没有」。")
        if here:
            from . import places as places_mod

            pp = places_mod.price_place(here)
            lines.append(f"地点（place）：你现在在{places_mod.label(here) or label('place', pp)}，place 填「{label('place', pp)}」（×{mod_k('place', pp):g}）。")
        else:
            lines.append("地点（place）：" + "；".join(f"{n}（×{mod_k('place', k):g}，{d}）" for k, n, _x, d in PLACES)
                         + "。" + place_habit())
        lines.append(f"价格 = 价格带 × 档次 × 噱头 × 地点，别离谱：一碗羊肉面不会要 200 {C.unit()}，但商场里的高档日料两百多是正常的。")
    if pick:
        lines += _pick_shop(allowed_b, here, rng or random)
    rec = known(allowed_b, 6)
    if rec:
        lines.append("你去过的店 / 买过的东西（再去同一家买同一样，价格差不多）：" + "；".join(
            f"{r['shop'] or '某家店'}的{r['item']} {r['price']:g} {C.unit()}" for r in rec))
    if budget is not None:
        lines.append(f"这次最多花得起 {budget:g} {C.unit()}（超过就换便宜点的，别硬买）。")
    return "\n".join(lines)


FIELDS = (f'"shop": "店名（可以虚构{C.pick("fictional_world", "，别用现实里的地名")}），路边摊可以写「路边小摊」", '
          '"band": "大类，填上面价格带的名字", "tier": "店铺档次的名字", "gimmick": "噱头的名字，没有就填「没有」", '
          f'"place": "地点的名字", "price": 价格（数字，单位{C.unit()}）, '
          '"impression": "第一次来的店：对这家店的一两句印象（你的口吻，40 字以内，环境、味道、老板、值不值都行）；来过的店有新感觉才写，没有就留空"')


def _pick_shop(bands: tuple[str, ...], here: str | None, rng: random.Random) -> list[str]:
    """这一带去过的店：按设置的概率去老店（按去过的次数挑一家），否则试一家新店。一家都没去过就不说（自然是新店）。"""
    cands = notebook.shops_near(here, bands)
    if not cands:
        return []
    if rng.random() < registry.num("notebook_old_shop_p"):
        e = rng.choices([c[0] for c in cands], [c[1] for c in cands])[0]
        return [f"这次去的是你去过的「{e['title']}」，shop 就填「{e['title']}」。{notebook.shop_brief(e)}"]
    return ["这次试一家你没去过的店：shop 写一个新店名（别和下面列过的店重名）。"]
