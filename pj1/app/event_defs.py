"""生活事件的类型和默认条件（只放数据，设置注册表和 events.py 都用它，避免循环导入）。"""

from __future__ import annotations

from . import character as C

# 类型：(名字, 例子 / 说明, 涉及的人)
TYPES: dict[str, tuple[str, str, str]] = {
    "mom": ("和妈妈", "被抓去当录音效的帮手、一起做饭、被妈妈念叨", "mom"),
    "dad": ("和爸爸", "爸爸难得早回家、被问学习、爸爸又在加班", "dad"),
    "parents": ("和父母", "一起吃饭、一起出门", "parents"),
    "friend": ("和朋友", "和朋友的日常小事（只能是下面给出的朋友）", "friend"),
    "stranger": ("和路人", "买东西时的小插曲、街上看到的事（路人不用起名字）", ""),
    # —— v9 新增 ——
    "item": ("物品", "快递到了、东西坏了、找到以前丢的东西、蝴蝶兰开花或掉叶子", ""),
    "net": ("网络", "追的剧更新或者烂尾、关注的作者更新、游戏抽卡出金或者歪了、朋友群里的八卦", ""),
    "mishap": ("意外小事", "停电、下雨没带伞、手机没电、打碎杯子、楼下装修太吵", ""),
    "body": ("身体", "失眠、吃撑、落枕、熬夜后犯困（小毛病，不是生病）", ""),
    "season": ("季节", "第一场雪、落叶、换季收衣服、年末大扫除（要和现在的季节对得上）", ""),
    "home": ("家里", "爸爸出差带礼物回来、亲戚来访、妈妈做的音效出现在某个作品里", "parents"),
    "money": ("钱", "零花钱见底、碰上打折", ""),
}
# 事件的基调（代码先定好，再让模型照着写）：good 好事 / neutral 平常的小事 / bad 不顺
TONE_LABEL = {"good": "一件小小的好事", "neutral": "一件平常的小事（不好不坏）", "bad": "一件有点不顺的小事（别写得太惨）"}
SICK = ("生病", "换季着凉了，难受，没力气", "")
# 默认条件：权重、最早点、最晚点、天气条件、需要出门
DEFAULTS: dict[str, tuple[float, int, int, str, bool]] = {
    "mom": (15.0, 9, 21, "any", False),
    "dad": (8.0, 17, 21, "any", False),
    "parents": (10.0, 10, 21, "any", False),
    "friend": (20.0, 10, 20, "no_rain", True),
    "stranger": (22.0, 9, 20, "no_precip", True),
    "item": (14.0, 9, 21, "any", False),
    "net": (16.0, 9, 21, "any", False),
    "mishap": (8.0, 8, 21, "any", False),
    "body": (8.0, 7, 21, "any", False),
    "season": (8.0, 8, 20, "any", False),
    "home": (8.0, 10, 21, "any", False),
    "money": (8.0, 9, 21, "any", False),
}
SLOTS = [("0709", 7, 9, 2.0), ("0912", 9, 12, 6.0), ("1214", 12, 14, 4.0), ("1418", 14, 18, 6.0), ("1821", 18, 21, 4.0)]   # 低 / 高 / 中 / 高 / 中
LAST_HOUR = 21                       # 21 点以后不发生事件

LOCAL = {
    "mom": ["被妈妈抓去帮忙录音效，举了一下午麦克风", "和妈妈一起做了顿饭，妈妈嫌我切得太粗", "妈妈念叨我房间乱"],
    "dad": ["爸爸今天难得早回家，问了句最近学习怎么样", "爸爸又在加班，晚饭只有我和妈妈"],
    "parents": ["晚上和爸爸妈妈一起吃了饭", "周末被爸爸妈妈拉着出门走了走"],
    "friend": ["和{friend}在路上碰到，聊了几句", "{friend}给我发了条很蠢的消息，笑了一会儿"],
    "stranger": ["买东西的时候店员认错了人，闹了个小误会", "路上看到一只猫蹲在车顶上，不肯下来"],
    "sick": ["好像着凉了，头昏昏的，没力气"],
}
# 没有模型时按基调用的本地句子（每种事件 × 好事 / 平常 / 不顺）
LOCAL_TONE: dict[str, dict[str, list[str]]] = {
    "mom": {"good": ["和妈妈一起做了顿饭，妈妈夸我切得整齐了"], "neutral": ["被妈妈抓去帮忙录音效，举了一下午麦克风"], "bad": ["妈妈念叨我房间乱，念了半小时"]},
    "dad": {"good": ["爸爸今天难得早回家，带了我爱吃的点心"], "neutral": ["爸爸问了句最近学习怎么样，我嗯了一声"], "bad": ["爸爸又在加班，晚饭只有我和妈妈"]},
    "parents": {"good": ["晚上和爸爸妈妈一起吃了饭，聊得挺开心"], "neutral": ["周末被爸爸妈妈拉着出门走了走"], "bad": ["爸爸妈妈为一点小事拌了两句嘴"]},
    "friend": {"good": ["{friend}给我发了条很蠢的消息，笑了一会儿"], "neutral": ["和{friend}在路上碰到，聊了几句"], "bad": ["约{friend}出来，{friend}临时有事来不了"]},
    "stranger": {"good": ["路上看到一只猫蹲在车顶上，不肯下来"], "neutral": ["买东西的时候店员认错了人，闹了个小误会"], "bad": ["排了半天队，到我前面一个卖完了"]},
    "item": {"good": ["快递到了，比想的还好看", "找到了以前丢的那支笔", "蝴蝶兰开了一朵新的"], "neutral": ["快递到了，是之前随手买的小东西"],
             "bad": ["耳机坏了一边，只剩一边有声音", "蝴蝶兰掉了一片叶子"]},
    "net": {"good": ["追的剧更新了，这集很好看", "关注的作者终于更新了", "抽卡出金了"], "neutral": ["朋友群里在聊一个八卦，看了一会儿"],
            "bad": ["抽卡歪了", "追的剧这集有点烂尾的意思"]},
    "mishap": {"good": ["下雨没带伞，楼下阿姨借了我一把"], "neutral": ["停电了一会儿，点了蜡烛，还挺有意思", "手机没电了，干脆发了会儿呆"],
               "bad": ["打碎了一个杯子", "楼下装修，电钻响了一下午"]},
    "body": {"good": ["昨晚睡得特别沉，起来浑身轻松"], "neutral": ["晚饭吃撑了，在屋里来回走"], "bad": ["落枕了，脖子转不过来", "熬夜之后一下午都在犯困"]},
    "season": {"good": ["窗外的树叶黄了，很好看", "第一场雪下下来了"], "neutral": ["换季了，把薄衣服收起来"], "bad": ["换季收衣服，翻出一件被虫蛀了的毛衣"]},
    "home": {"good": ["爸爸出差回来，给我带了个小礼物", "妈妈做的音效出现在一部片子里，全家一起看"], "neutral": ["亲戚来家里坐了坐"],
             "bad": ["亲戚来访，被问了一晚上成绩"]},
    "money": {"good": ["喜欢的店在打折，捡了个便宜"], "neutral": ["算了算这个月的零花钱"], "bad": ["零花钱快见底了"]},
}


def _extra_types() -> None:
    """挂载点：角色目录里的 events.json（{类型键: {label, hint, needs, weight, from, to, weather, outside, local: [本地句子], tone: {good/neutral/bad: [...]}}}）。
    键和已有的一样就覆盖，不一样就加一个新的事件类型（设置页「生活事件」里自动出现它的权重、时段）。"""
    for key, x in (C.extra("events") or {}).items():
        if not isinstance(x, dict) or not x.get("label"):
            continue
        TYPES[key] = (str(x["label"]), str(x.get("hint") or ""), str(x.get("needs") or ""))
        DEFAULTS[key] = (float(x.get("weight", 8.0)), int(x.get("from", 9)), int(x.get("to", 21)), str(x.get("weather") or "any"), bool(x.get("outside")))
        if x.get("local"):
            LOCAL[key] = [str(t) for t in x["local"]]
        if isinstance(x.get("tone"), dict):
            LOCAL_TONE[key] = {k: [str(t) for t in v] for k, v in x["tone"].items() if isinstance(v, list)}


_extra_types()
