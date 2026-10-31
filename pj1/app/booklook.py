"""书和小物件的「外观参数」。画出来的样子完全由这组参数决定（画的代码在 web/iso.js），想扩展就加参数，不用改存储。

书（look）：
  main 主色 / accent 点缀色 / ink 书名颜色 / pattern 花纹（plain stripes dots band grid diamond wave）
  w 书脊宽（厚度，cm）/ h 书脊高（书高，cm）/ cw 封面宽（cm）
  tl 书名排版（v 竖排 / h 横排 / s 分两栏）/ tf 书名字体（serif sans round）/ tp 书名位置（t 上 c 中 b 下）
  band 书脊上下装饰带（0 / 1 / 2 条）/ emblem 封面小徽标（none dot star leaf line）
封面和书脊都由这一组参数生成，所以是同一本书的样子。

- 导入的书：用阅读器书架里的默认封面颜色（见 web/reader.js 的 COVERS）+ 素净的样式生成书脊；
- Phal 自己买的书：购买时随机生成书名和外观参数（random_look），是不可阅读的摆设书。

小物件（item_look）：shape 形状（plant plush stack cloth device lamp box）/ color / accent / size，按名字稳定地生成。
"""

from __future__ import annotations

import hashlib
import random
from typing import Any

# 阅读器书架里默认封面的颜色（和 web/reader.js 的 COVERS 一致）
READER_COVERS = ["#e3ddd3", "#d9e0d9", "#e0d9df", "#d8dee7", "#e8ddd0", "#e2e2d3", "#e4d8d8", "#d6e1e3"]
READER_INK = "#3b352e"

PATTERNS = ["plain", "stripes", "dots", "band", "grid", "diamond", "wave"]
PATTERN_W = [3, 2, 2, 2, 1, 1, 1]
LAYOUTS = ["v", "h", "s"]
FONTS = ["serif", "sans", "round"]
POSITIONS = ["t", "c", "b"]
EMBLEMS = ["none", "dot", "star", "leaf", "line"]

# 低饱和的蓝色调为主，夹几本灰粉 / 灰绿 / 沙色：(主色, 点缀色, 书名颜色)
PALETTES = [
    ("#d8e2f2", "#7f98c4", "#2f3f5c"), ("#b9c9e4", "#5f7cab", "#25375a"), ("#a6b9d9", "#e6eefb", "#22344f"), ("#c5c9e6", "#8087bd", "#2b2f5c"),
    ("#8fa5c8", "#e9f0fb", "#1f2d47"), ("#dfe8f4", "#9aaed0", "#34476a"), ("#aebfd8", "#f2e4ea", "#26364f"), ("#7c93b9", "#dce7f7", "#f4f7fc"),
    ("#e9d9d9", "#c78f9e", "#4a3340"), ("#c9d8d3", "#7ea396", "#2f4540"), ("#f0e6d2", "#b69a64", "#4a3f2a"), ("#e2d4e6", "#9a82ad", "#3f3050"),
    ("#cdd6e8", "#4f6a9a", "#1f2d4a"), ("#b8c4dc", "#f6efe0", "#2a3a58"),
]
SURNAMES = "沈林陆叶顾苏宋江许何梁罗唐韩冯曹彭"
GIVEN = ["清", "远", "安", "宁", "舟", "禾", "言", "遥", "知", "秋", "屿", "白", "岚", "栀", "桐", "予", "楚", "晏"]

_A = ["夜", "雾", "潮", "灯", "雨", "星", "旧", "远", "静", "蓝", "冬", "晚", "纸", "风", "岛", "湖", "雪", "月"]
_B = ["航船", "中人", "居记", "汐表", "塔", "邮票", "来信", "笔记", "地图", "手账", "札记", "小集", "旅人", "回声", "漫游", "案卷", "记事", "笔谈"]


def random_title(rng: random.Random | None = None) -> str:
    """随机书名（虚构，两到五个字）。模型不可用时的兜底。"""
    rng = rng or random
    t = rng.choice(_A) + rng.choice(_B)
    if rng.random() < 0.3:
        t += rng.choice(["", "集", "录"])
    return t


def random_author(rng: random.Random | None = None) -> str:
    rng = rng or random
    return rng.choice(SURNAMES) + rng.choice(GIVEN) + (rng.choice(GIVEN) if rng.random() < 0.45 else "")


def random_look(rng: random.Random | None = None) -> dict[str, Any]:
    """Phal 买的书：随机一组外观参数。"""
    rng = rng or random
    main, accent, ink = rng.choice(PALETTES)
    return {
        "v": 1, "main": main, "accent": accent, "ink": ink, "pattern": rng.choices(PATTERNS, PATTERN_W)[0],
        "w": round(rng.uniform(1.5, 4.4), 1), "h": round(rng.uniform(20, 30), 1), "cw": round(rng.uniform(13.5, 19), 1),
        "tl": rng.choice(LAYOUTS), "tf": rng.choice(FONTS), "tp": rng.choice(POSITIONS), "band": rng.choice([0, 1, 1, 2]), "emblem": rng.choice(EMBLEMS),
    }


def _shade(hexcol: str, k: float) -> str:
    c = hexcol.lstrip("#")
    r, g, b = (int(c[i : i + 2], 16) for i in (0, 2, 4))
    return "#" + "".join(f"{max(0, min(255, int(v * k))):02x}" for v in (r, g, b))


def default_look(book: dict[str, Any]) -> dict[str, Any]:
    """导入的书：阅读器书架里的默认封面颜色 + 素净的样式。书脊厚度看字数，书高按 id 稳定地错开一点。"""
    bid = int(book.get("id") or 0)
    main = READER_COVERS[bid % len(READER_COVERS)]
    chars = int(book.get("chars") or 0)
    return {
        "v": 1, "main": main, "accent": _shade(main, 0.84), "ink": READER_INK, "pattern": "plain",
        "w": round(max(1.5, min(4.8, 1.5 + chars / 160000)), 1), "h": float(21 + (bid * 7) % 9), "cw": float(14 + (bid * 3) % 4),
        "tl": "v", "tf": "sans", "tp": "t", "band": 0, "emblem": "none",
    }


# ───────────────────────── 小物件 ─────────────────────────
SHAPE_OF = {"plant": "plant", "bear": "plush", "book": "stack", "dress": "cloth", "game": "device", "lamp": "lamp", "gift": "box"}
ITEM_COLORS = {
    "plant": [("#8fb7a8", "#6f9a8d"), ("#9fc0a0", "#7aa27d"), ("#8db4b9", "#6a9aa1")],
    "plush": [("#cdb7a8", "#a88f80"), ("#e6d0d6", "#c79fae"), ("#b9c4dc", "#8fa0c4"), ("#e3d9c0", "#c4b48a")],
    "stack": [("#c8d4e8", "#8fa5c8"), ("#e5d6d6", "#c09aa4")],
    "cloth": [("#d8c5d2", "#b89fb0"), ("#c5d3e6", "#8fa5c8"), ("#e3dccb", "#c4b79a")],
    "device": [("#c8d4e8", "#8fa5c8"), ("#d6d0e6", "#9a8fc4")],
    "lamp": [("#f0e2b8", "#c9b078"), ("#e6d3d3", "#b88f8f")],
    "box": [("#d6c3df", "#b59cc4"), ("#c3d6df", "#8fb0c4"), ("#e3d3bf", "#c4a97f")],
}
POTS = ["#e8d9c4", "#d9c4b4", "#cfd8e6", "#e6d0d6"]


def item_look(icon: str, name: str, item_id: int = 0) -> dict[str, Any]:
    """小物件的外观参数：同一件东西每次都生成同一个样子（按名字 + id 取随机数种子）。"""
    shape = SHAPE_OF.get(icon, "box")
    rng = random.Random(int(hashlib.md5(f"{name}|{item_id}".encode("utf-8")).hexdigest()[:12], 16))
    color, accent = rng.choice(ITEM_COLORS[shape])
    look = {"v": 1, "shape": shape, "color": color, "accent": accent, "size": round(rng.uniform(0.85, 1.2), 2)}
    if shape == "plant":
        look["pot"] = rng.choice(POTS)
    return look


# ───────────────────────── 杂物区的消耗品（零食、奶茶）：随机生成的外观 ─────────────────────────
SNACK_COLORS = [("#f3d9c4", "#e0a77f"), ("#d9e8d3", "#8fb98a"), ("#f1d3dc", "#d98fa5"), ("#d6dff3", "#8fa5d9"), ("#f4ecc6", "#d9c26b"), ("#e6d6f0", "#b08fd1"), ("#f6d6c9", "#e58f7a")]
CUP_COLORS = [("#fbf1e4", "#e8c9a0"), ("#fdf3f6", "#f0b9c9"), ("#f1f7ef", "#c4dfb4"), ("#f6f0fb", "#cdb9e6"), ("#fff6e6", "#f2c66f"), ("#eef6fb", "#a9cfe8")]
SLEEVES = ["#e9a9ba", "#8fb7a8", "#9fb4d9", "#e6c27a", "#b9a3d4", "#e58f7a"]
TOPPINGS = ["pearl", "pearl", "jelly", "foam", "none"]
FRUIT_BY_WORD = (                                      # 水果名里的字 → 颜色
    (("苹果",), "#d9534f"), (("橘", "橙"), "#f2a23c"), (("香蕉",), "#f2d35b"), (("葡萄",), "#7b4fa0"), (("草莓",), "#e2475b"),
    (("梨",), "#d9d36a"), (("桃",), "#f4a9a0"), (("猕猴桃", "奇异果"), "#8a9a4b"), (("蓝莓",), "#4b5ea8"), (("樱桃",), "#b8233a"),
)
SNACK_BY_WORD = (
    (("面包", "蛋糕", "饼", "曲奇", "酥", "吐司", "麻薯", "泡芙"), "bread"),
    (("糖", "巧克力", "果冻", "布丁", "软糖", "棒棒糖"), "candy"),
    (("脆", "薯", "膨化", "海苔", "坚果", "瓜子", "虾条", "饼干", "干"), "bag"),
)


def clutter_look(kind: str, name: str, seed: int = 0) -> dict[str, Any]:
    """杂物区消耗品的外观参数：每次买都是随机的（按名字 + 时间戳取种子，所以存下来以后不变）。
    零食平放在桌上：bag 薯片袋 / box 小盒子 / bar 条状 / bread 面包 / candy 几颗糖；奶茶：cup（杯身、杯盖、吸管、杯套、小料随机）。"""
    rng = random.Random(int(hashlib.md5(f"{kind}|{name}|{seed}".encode("utf-8")).hexdigest()[:12], 16))
    if kind == "milktea":
        body, drink = rng.choice(CUP_COLORS)
        return {"v": 1, "shape": "cup", "body": body, "drink": drink, "lid": rng.choice(["#ffffff", "#f4f1ec", "#e9eef5"]), "straw": rng.choice(SLEEVES),
                "sleeve": rng.choice(SLEEVES), "topping": rng.choice(TOPPINGS), "size": round(rng.uniform(0.92, 1.12), 2), "yaw": round(rng.uniform(-25, 25), 1)}
    if kind == "dessert":                                   # 自己烤的甜点：小碟子 + 一块（蛋糕 / 曲奇 / 布丁……颜色按名字随机）
        cream, base = rng.choice([("#fff4e6", "#e8c08f"), ("#ffe3ea", "#f0c9a0"), ("#f3ead8", "#c99a62"), ("#fff8d6", "#e6b86a")])
        return {"v": 1, "shape": "cake", "cream": cream, "base": base, "plate": rng.choice(["#ffffff", "#eef3fb", "#fbeef2"]),
                "berry": rng.choice(["#e05a6a", "#f2a23c", "#8a5ac8", ""]), "size": round(rng.uniform(0.9, 1.1), 2), "yaw": round(rng.uniform(-30, 30), 1)}
    if kind == "fruit":                                     # 水果：几个果子（颜色按名字猜，猜不到随机）放在小盘子上
        col = next((c for words, c in FRUIT_BY_WORD if any(w in name for w in words)), None) or rng.choice([c for _w, c in FRUIT_BY_WORD])
        return {"v": 1, "shape": "fruit", "color": col, "count": rng.choice([1, 2, 3, 3, 4]), "plate": rng.choice(["#ffffff", "#eef3fb", "#f6efe3"]),
                "size": round(rng.uniform(0.9, 1.1), 2), "yaw": round(rng.uniform(-30, 30), 1)}
    if kind == "meal":                                      # 饭菜：一只碗（或外卖盒）+ 上面的菜色
        box = any(w in name for w in ("外卖", "盒", "便当", "套餐"))
        return {"v": 1, "shape": "takeout" if box else "bowl", "bowl": rng.choice(["#ffffff", "#e9eef5", "#f3e6d4"]) if not box else "#f4f1ec",
                "food": rng.choice(["#e9b866", "#d9784f", "#9cbf6b", "#c98c5a", "#f1d9a6"]), "size": round(rng.uniform(0.95, 1.1), 2), "yaw": round(rng.uniform(-20, 20), 1)}
    shape = next((s for words, s in SNACK_BY_WORD if any(w in name for w in words)), rng.choice(["bag", "bag", "box", "bar"]))
    color, accent = rng.choice(SNACK_COLORS)
    return {"v": 1, "shape": shape, "color": color, "accent": accent, "pattern": rng.choice(["plain", "stripes", "dots", "band"]),
            "w": round(rng.uniform(11, 15), 1), "d": round(rng.uniform(7.5, 10.5), 1), "size": round(rng.uniform(0.9, 1.15), 2), "yaw": round(rng.uniform(-35, 35), 1)}
