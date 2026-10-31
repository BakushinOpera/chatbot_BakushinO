"""价格带和修正系数的默认值（设置页里都能改，见 registry.py 和 prices.py）。单位：块（按浙江一个小县城的物价）。

不 import 别的模块：registry.py 要在加载时读这里的默认值。
"""

from __future__ import annotations

from . import character as C

# (键, 名字, 例子, 最低, 常见低, 常见高, 最高, 是吃的)
BANDS: list[tuple[str, str, str, float, float, float, float, bool]] = [
    ("breakfast", "早餐", "包子豆浆、油条、春卷、烧饼、豆腐脑", 3, 6, 12, 18, True),
    ("noodle", "面粉馄饺", "羊肉面、炒年糕、拌川、小馄饨、水饺、米线", 10, 15, 25, 38, True),
    ("fastfood", "快餐盖饭", "盖浇饭、黄焖鸡、简餐套饭、麻辣烫", 13, 18, 28, 38, True),
    ("burger", "汉堡炸鸡", "汉堡、炸鸡、薯条套餐", 15, 25, 40, 55, True),
    ("drink", "奶茶饮品", "奶茶、果茶、柠檬茶", 4, 12, 20, 32, True),
    ("coffee", "咖啡", "连锁咖啡、独立咖啡馆", 9.9, 12, 25, 38, True),
    ("snack", "零食", "薯片、饼干、软糖、辣条、零食铺称重", 3, 8, 20, 45, True),
    ("dessert", "甜点烘焙", "面包、蛋糕切块、泡芙、蛋挞", 5, 12, 28, 45, True),
    ("fruit", "水果", "一盒草莓、几个橘子、一串葡萄、果切", 6, 12, 30, 50, True),
    ("streetfood", "小吃夜宵", "烧烤串串、炸串、臭豆腐、关东煮", 10, 25, 50, 80, True),
    ("chinese", "中餐馆", "家常炒菜、本地菜馆", 30, 45, 80, 130, True),
    ("hotpot", "火锅砂锅", "火锅、砂锅、干锅、串串香", 45, 70, 110, 160, True),
    ("bbq", "烤肉韩料", "自助烤肉、韩式烤肉、部队锅", 50, 70, 110, 160, True),
    ("japanese", "日料", "拉面、寿司简餐、居酒屋、高档 omakase", 30, 60, 120, 260, True),
    ("western", "西餐", "牛排、意面、披萨、西式简餐", 40, 60, 120, 220, True),
    ("buffet", "自助餐", "酒店自助、海鲜自助", 80, 110, 180, 300, True),
    ("delivery", "外卖", "上面这些点外卖（含配送费、打包费）", 15, 22, 38, 60, True),
    ("clothes", "衣服", "T 恤、卫衣、裙子、裤子", 40, 80, 200, 350, False),
    ("coat", "外套", "大衣、羽绒服、针织开衫", 120, 200, 450, 800, False),
    ("shoes", "鞋", "帆布鞋、运动鞋、靴子", 60, 120, 300, 600, False),
    ("accessory", "饰品", "发夹、手链、耳钉、小项链", 10, 20, 80, 200, False),
    ("cosmetics", "化妆护肤", "口红、护手霜、面膜、洗面奶", 15, 40, 150, 350, False),
    ("trinket", "杂物小玩意", "贴纸、钥匙扣、小摆件、文具", 3, 10, 40, 90, False),
    ("book", "书", "实体书", 20, 35, 65, 120, False),
    ("game", "买断游戏", "独立游戏、3A 大作", 15, 40, 120, 400, False),
    ("iap", "游戏内购", "月卡、小皮肤、抽卡", 6, 30, 128, 328, False),
]
BAND = {b[0]: b for b in BANDS}
BAND_KEYS = [b[0] for b in BANDS]
FOOD_BANDS = tuple(b[0] for b in BANDS if b[7])
NO_MOD = ("game", "iap")                              # 游戏和内购不受档次 / 噱头 / 地点影响
IAP_TIERS = (6, 30, 68, 128, 198, 328, 648)

# (键, 名字, 系数, 说明)
TIERS: list[tuple[str, str, float, str]] = [
    ("street", "路边摊", 0.7, "小摊、菜场、夫妻店"),
    ("chain", "连锁 / 普通店", 1.0, "基准"),
    ("mall", "商场", 1.3, "商场里的店、景区附近"),
    ("luxury", "高档", 2.5, "高档餐厅、精品店"),
]
LUXURY_OK = ("japanese", "western", "buffet", "clothes", "coat", "shoes", "cosmetics")
GIMMICKS: list[tuple[str, str, float, str]] = [
    ("none", "没有", 1.0, ""),
    ("hot", "网红店", 1.2, "排队、拍照打卡"),
    ("limited", "节日 / 季节限定", 1.15, "圣诞款、樱花季"),
    ("ip", "IP 联动", 1.35, "联名款、动漫联动套餐"),
    ("rare", "稀有食材", 1.8, "和牛、蓝鳍金枪鱼、松茸（只在吃的里）"),
    ("ripoff", "被坑了", 1.5, "景区店、游客店、没看价就点了"),
]
PLACES: list[tuple[str, str, float, str]] = C.price_places()      # 地点系数的默认行：角色配置里的 price_places
MODS = {"tier": TIERS, "gimmick": GIMMICKS, "place": PLACES}
MOD_DEFAULT = {"tier": "chain", "gimmick": "none", "place": C.home_price_place()}

# 设置页的表格：列定义 + 默认行
BAND_COLUMNS = [("lo", "最低", 0.0, 100000.0, 0.1), ("c_lo", "常见（低）", 0.0, 100000.0, 0.1),
                ("c_hi", "常见（高）", 0.0, 100000.0, 0.1), ("hi", "最高", 0.0, 100000.0, 0.1)]
BAND_DEFAULTS = [{"lo": b[3], "c_lo": b[4], "c_hi": b[5], "hi": b[6]} for b in BANDS]
BAND_ROWS = [f"{b[1]}（{b[2]}）" for b in BANDS]
MOD_COLUMNS = [("k", "系数", 0.1, 10.0, 0.05)]

# 消费类别（money.CATS 的键）→ 可以落在哪些价格带里（第一个是默认）
CAT_BANDS: dict[str, tuple[str, ...]] = {
    "milktea": ("drink", "coffee"),
    "snack": ("snack", "dessert"),
    "fruit": ("fruit",),
    "clothes": ("clothes", "coat", "shoes"),
    "accessory": ("accessory", "cosmetics"),
    "game_buy": ("game",),
    "iap": ("iap",),
    "trinket": ("trinket",),
    "book": ("book",),
    "takeout": ("delivery",),
    "dine": tuple(k for k in FOOD_BANDS if k not in ("delivery", "drink", "coffee", "snack", "fruit")),
}


