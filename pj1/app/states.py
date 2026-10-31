"""她的所有状态：名字、默认权重、持续时间、允许出现的时间段、天气条件、花不花钱、算不算「在外面」。

这里只放**默认值和文案**；设置页里每个状态的这些条件都可以改（见 registry.py，键是 state_*_<状态>）。
random=False 的状态不参加随机抽取，由别的机制安排：吃饭相关由「饭点调度」（meals.py），生病由生病判定（第 3 期）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import character as C

# 天气条件：any 不限 / rain_only 只在下雨 / no_rain 不下雨时 / no_precip 没有雨雪时
WEATHER_CHOICES = [("any", "不限"), ("rain_only", "只在下雨时"), ("no_rain", "不下雨时"), ("no_precip", "没有雨雪时")]


@dataclass
class StateDef:
    key: str
    label: str
    weight: float                    # 默认权重（只在 random=True 时有用）
    dur: tuple[int, int]             # 持续时间，分钟
    hours: tuple[int, int] = (0, 24)  # 允许出现的时间段 [起, 止)，点
    weather: str = "any"
    spends: bool = False             # 可能触发消费（第 4 期）
    outside: bool = False            # 算「在外面」：回复更慢、更短
    random: bool = True
    blurbs: list[str] = field(default_factory=list)
    needs_friend: bool = False       # 要有朋友（人物档案里有朋友）才会出现


DEFS: list[StateDef] = [
    # —— 原有 ——
    StateDef("tea", "喝茶", 18, (5, 15)),
    StateDef("read", "看书", 28, (10, 30)),
    StateDef("daze", "发呆", 14, (5, 20)),
    StateDef("video", "刷视频", 16, (5, 20)),
    StateDef("game", "打游戏", 12, (10, 30), spends=True),
    StateDef("create", "试图创作", 14, (15, 40)),
    # —— 新增 ——
    StateDef("occult", "练习神秘学", 8, (15, 40), hours=(9, 22),
             blurbs=["对着一本小册子比划手势", "在纸上画一个圈，又擦掉", "试着静下来感应点什么", "摆了一排小石子，不知道在等什么", "照着书上念了一段，念到一半笑场了"]),
    StateDef("sfx", "帮妈妈录音效", 5, (20, 50), hours=(9, 20),
             blurbs=["被抓去拿着话筒走来走去", "在录门轴的吱呀声", "对着一盆水哗啦哗啦", "举着挂麦克风的杆子，手酸了", "给妈妈当拟音的帮手"]),
    StateDef("cook", "做饭", 0, (40, 60), random=False, blurbs=["在厨房忙活", "切菜切到一半", "锅里在咕嘟咕嘟响"]),
    StateDef("eat", "吃饭", 0, (20, 40), random=False, blurbs=["在客厅和家人一起吃饭", "在吃饭，边吃边聊"]),
    StateDef("tea_brew", "泡茶", 0, (1, 3), random=False, blurbs=["在客厅泡茶", "等水烧开", "在挑今天喝哪种茶"]),     # 喝茶之前：先去客厅泡 1~3 分钟
    StateDef("eat_alone", "一个人在房间吃饭", 0, (20, 40), random=False, blurbs=["爸妈出差了，一个人在房间里吃饭", "端着碗在食物桌边吃", "边吃边刷手机"]),
    StateDef("dine_out", "出门吃饭", 0, (45, 90), outside=True, spends=True, random=False, blurbs=["在外面吃饭"]),
    StateDef("homework", "写作业", 10, (30, 60), hours=(9, 22),
             blurbs=["对着习题发愁", "写了两行，开始削铅笔", "作业堆在桌上，一样一样来", "在查一个公式", "写着写着开始画小人"]),
    StateDef("shower", "洗澡", 6, (10, 20), hours=(7, 22), blurbs=["在洗澡", "水开得有点烫", "在浴室哼歌"]),
    StateDef("nap", "午睡", 12, (20, 45), hours=(12, 15), blurbs=["在午睡", "趴着睡着了", "窗帘拉了一半，睡得很沉"]),
    StateDef("music", "听歌", 10, (10, 30), hours=(7, 22),
             blurbs=["戴着耳机听歌", "把同一首歌单曲循环了", "在找一首只记得旋律的歌", "跟着哼，歌词记不全"]),
    StateDef("anime", "追剧", 10, (20, 60), hours=(9, 22), spends=True,
             blurbs=["在追一部番", "看到一半被剧情气到了", "说好只看一集", "在补之前落下的几集"]),
    StateDef("draw", "画画", 8, (20, 60), hours=(9, 22),
             blurbs=["在画一张随手的涂鸦", "橡皮擦了又擦", "给一只小动物上色", "在练手，线条总是歪"]),
    StateDef("tidy", "收拾房间", 6, (10, 30), hours=(9, 21), blurbs=["在收拾房间", "翻出一个很久没见的东西", "把书桌清出一块空地", "整理衣柜，越整越乱"]),
    StateDef("walk", "散步", 6, (15, 40), hours=(9, 20), weather="no_precip", outside=True,
             blurbs=["在附近走走", "沿着河边慢慢走", "随便走，没有目的地"]),
    StateDef("shop", "逛街", 4, (40, 90), hours=(10, 19), weather="no_rain", spends=True, outside=True,
             blurbs=["在街上逛", "在一家小店里晃", "什么都想看，什么都没买"]),
    StateDef("friends", "和朋友出去", 4, (60, 150), hours=(10, 19), weather="no_precip", spends=True, outside=True,
             blurbs=["和朋友出去玩了", "在外面和朋友待着"], needs_friend=True),
    StateDef("rain_watch", "趴窗边看雨", 15, (10, 30), hours=(7, 22), weather="rain_only",
             blurbs=["趴在窗边看雨", "数着雨滴顺着玻璃往下滑", "雨声很好听，不想动", "看对面屋檐在滴水"]),
    StateDef("sick", "生病躺着", 0, (120, 240), random=False,
             blurbs=["躺着，没力气", "裹着被子，头有点晕", "喝了点热水，又睡过去了"]),
    # —— v9 新增 ——
    StateDef("calligraphy", "练字", 5, (15, 40), hours=(9, 22),
             blurbs=["在描字帖", "一笔一画地写，写歪了一个", "墨蹭到手上了", "同一个字写了十遍"]),
    StateDef("craft", "做手工", 5, (30, 90), hours=(9, 22),
             blurbs=["在拼装模型，零件摊了一桌", "对着说明书找一块小零件", "在给模型上色，屏住呼吸描边", "等涂装晾干，手痒想碰"]),
    StateDef("bake", "烘焙", 3, (40, 90), hours=(10, 20),
             blurbs=["在搅面糊", "烤箱里飘出香味了", "在等蛋糕胚凉下来", "面粉沾到脸上了"]),
    StateDef("photo", "出门拍照", 3, (30, 80), hours=(9, 18), weather="no_precip", outside=True,
             blurbs=["拿着相机在街上找角度", "蹲在地上拍一朵小花", "拍了一张，看了看，又拍一张"]),
    StateDef("album", "整理相册", 4, (15, 40), hours=(9, 22),
             blurbs=["在翻以前拍的照片", "把糊掉的照片一张张删掉", "给照片分文件夹"]),
    StateDef("netshop", "逛网店", 6, (10, 40), hours=(9, 23), spends=True,
             blurbs=["在网店里翻来翻去", "把一件东西加进购物车又删掉", "在比较两家的价格"]),
    StateDef("call", "和朋友打电话", 4, (15, 50), hours=(10, 22), needs_friend=True,
             blurbs=["在和朋友打视频", "抱着手机笑", "电话那头在讲八卦"]),
    StateDef("tryon", "试衣服", 3, (15, 40), hours=(9, 21),
             blurbs=["对着镜子试衣服", "衣服摊了一床", "试了三件，还是穿回第一件"]),
    StateDef("puzzle_family", "和家人拼图 / 玩桌游", 3, (30, 80), hours=(18, 22),
             blurbs=["和爸爸妈妈在拼一幅拼图", "妈妈又把找到的边角藏起来了", "在玩桌游，爸爸输了不认"]),
    StateDef("boardgame_friends", "和朋友玩桌游", 2, (90, 180), hours=(13, 21), weather="no_rain", spends=True, outside=True,
             blurbs=["在朋友家玩桌游", "在桌游店里，桌上摊满了卡牌", "这局快赢了"], needs_friend=True),
    StateDef("boardgame_online", "和朋友线上玩桌游", 3, (30, 90), hours=(13, 23),
             blurbs=["和朋友开了一局线上桌游", "戴着耳机边玩边聊", "在等朋友出牌"], needs_friend=True),
    StateDef("synth", "玩妈妈的合成器", 3, (20, 60), hours=(9, 21),
             blurbs=["在妈妈的合成器上乱按", "调出一个很怪的声音，自己乐了", "拿着录音笔录窗外的声音", "在录环境音：雨声、风声、楼下的车"]),
    StateDef("errand", "跑腿", 3, (20, 50), hours=(9, 20), spends=True, outside=True,
             blurbs=["被妈妈叫去买东西", "拎着购物袋往回走", "在小卖部对着清单找酱油"]),
    StateDef("unbox", "拆快递", 3, (5, 20), hours=(9, 22),
             blurbs=["在拆快递", "胶带缠得太紧，找剪刀", "盒子比东西大三倍"]),
    StateDef("laze", "赖床", 20, (10, 40), hours=(7, 10),
             blurbs=["醒了，但不想起", "缩在被子里刷手机", "闹钟按掉了第二次", "再躺五分钟"]),
    StateDef("exercise", "跳绳 / 瑜伽", 4, (15, 40), hours=(7, 21),
             blurbs=["在跳绳，数到一半忘了", "在做瑜伽，腿抖了", "拉伸的时候差点睡着"]),
    StateDef("shelf", "整理书架", 3, (15, 40), hours=(9, 21),
             blurbs=["在整理书架", "把书按颜色排了一遍", "翻出一本以为丢了的书"]),
]


def _extra_states() -> None:
    """挂载点：角色目录里的 states.json（[{key, label, weight, dur: [最短, 最长]分钟, hours, weather, spends, outside, random, blurbs, needs_friend}]）。
    key 和已有的一样就覆盖那一项，不一样就加一个新状态（房间里没有位置的状态，她会像出门一样不在画面里）。"""
    for x in C.extra("states") or []:
        if not isinstance(x, dict) or not x.get("key") or not x.get("label"):
            continue
        fields = {k: x[k] for k in StateDef.__dataclass_fields__ if k in x}
        for k in ("dur", "hours"):
            if k in fields:
                fields[k] = tuple(int(v) for v in fields[k])
        fields.setdefault("weight", 5.0)
        fields.setdefault("dur", (10, 30))
        d = StateDef(**fields)
        i = next((n for n, old in enumerate(DEFS) if old.key == d.key), None)
        if i is None:
            DEFS.append(d)
        else:
            DEFS[i] = d


_extra_states()
if not C.feature("occult"):                    # 角色没开神秘学：不会随机去练
    DEFS[:] = [StateDef(**{**d.__dict__, "random": False}) if d.key == "occult" else d for d in DEFS]
BY_KEY = {d.key: d for d in DEFS}

# 在外面时的这几个状态：回复更慢、更短
MEAL_STATES = ("cook", "eat", "eat_alone", "dine_out")


def weather_ok(cond: str, raining: bool, snowing: bool) -> bool:
    if cond == "rain_only":
        return raining
    if cond == "no_rain":
        return not raining
    if cond == "no_precip":
        return not raining and not snowing
    return True
