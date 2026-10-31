"""设置注册表：所有可调的设置项在这里声明一次（键、分区、类型、默认值、范围、一句说明）。

* 设置页（/api/config）按它自动生成界面、校验、恢复默认；
* 业务代码用 ``registry.num("键")`` / ``registry.flag("键")`` 取值——没改过就是这里的默认值，
  所以「模块里的常量」和「设置页的默认值」永远是同一份。
* 值都存在 settings 表里（字符串）；禁词表是单独的文件 data/banned_words.txt，走特殊通道。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

from . import behavior as _BH
from . import config, db, states
from . import character as C

# 分区（顺序就是设置页里的顺序）。没有任何设置项、也没有自定义面板的分区不会显示。
GROUPS: list[tuple[str, str]] = [
    ("persona", "人设"),
    ("people", "人物"),
    ("world", "世界"),
    ("style", "说话风格"),
    ("burst", "连发和等待"),
    ("intent", "说过的打算"),
    ("routine", "作息"),
    ("states", "状态与概率"),
    ("meals", "吃饭"),
    ("money", "零花钱"),
    ("room", "她的房间"),
    ("letters", "月末的信"),
    ("events", "事件"),
    ("weather", "天气"),
    ("festivals", "节日"),
    ("reading", "读书、写作"),
    ("notify", "通知"),
    ("model", "模型与记忆"),
    ("mood", "心情与精力"),
]
HIDDEN_GROUPS: set[str] = set()
# 「心情与精力」分区在设置页里只显示这几项（v10 定稿：其余用默认值，需要时改这里放出来）
MOOD_SECTIONS: list[tuple[str, tuple[str, ...]]] = [
    ("心情", ("base_mean", "mood_half_v", "mood_e_split", "mood_mention_prob")),
    ("哪些事带情绪", ("react_p_writing", "react_p_event", "react_p_act")),
    ("事件", ("bag_ratio", "neg_cooldown_days", "outing_event_p")),
    ("写作和小打算", ("create_max_per_day", "insp_half_h", "intent_write_share")),
]
MOOD_VISIBLE = tuple(k for _s, ks in MOOD_SECTIONS for k in ks)
# 有自己专门界面的分区：前端在这些分区里渲染对应的面板（festivals / people 整个分区就是面板，world 是面板 + 若干设置项）
PANELS = {"festivals", "people", "world"}
CUSTOM_GROUPS = {"festivals", "people"}


@dataclass
class Item:
    key: str
    group: str
    label: str
    hint: str
    kind: str                       # bool | int | float | text | longtext | list | secret | table（一组行，每行若干数字）| select | time（HH:MM）
    default: Any = None
    min: float | None = None
    max: float | None = None
    step: float | None = None
    unit: str = ""
    section: str = ""               # 分区里的小标题
    store: str = "settings"         # settings | banned_file
    choices: list[tuple[str, str]] = field(default_factory=list)     # kind=select：(值, 名字)
    columns: list[tuple[str, str, float, float, float]] = field(default_factory=list)   # kind=table：每列 (键, 名字, 最小, 最大, 步长)
    row_labels: list[str] = field(default_factory=list)   # kind=table：每行的名字（不给就显示「N 月」）
    hidden: bool = False            # 不在设置页显示（废弃了但旧代码还会读的项）


ITEMS: list[Item] = []
BY_KEY: dict[str, Item] = {}


def _add(*items: Item) -> None:
    for it in items:
        assert it.key not in BY_KEY, it.key
        ITEMS.append(it)
        BY_KEY[it.key] = it


def _prob(key: str, group: str, label: str, hint: str, default: float, section: str = "", hidden: bool = False) -> Item:
    return Item(key, group, label, hint, "float", default, 0.0, 1.0, 0.05, "0~1", section, hidden=hidden)


# ───────────────────────── 人设 ─────────────────────────
_add(
    Item("persona_name", "persona", "名字", "显示在顶部和通知里的名字。", "text", "小"),
    Item("system_prompt", "persona", "人设正文", "她是什么样的人、怎么说话、怎么使用时间和记忆。改完停手一会儿自动保存。",
         "longtext", config.DEFAULT_PERSONA),
)

# ───────────────────────── 说话风格 ─────────────────────────
_add(
    Item("banned_words", "style", "禁词表", "不许出现的词或说法。她的回复命中就会自动重新生成（最多 2 次）。存在 data/banned_words.txt。",
         "list", [], store="banned_file"),
    Item("common_words", "style", "常用词", "希望她多用的词、口头禅。是倾向不是任务，挑几个最像的就行。", "list", []),
    Item("temperature", "style", "温度", "越高越随性、越不稳定；越低越规矩。", "float", 0.9, 0.0, 2.0, 0.05),
    Item("split_replies", "style", "把回复拆成多条短消息", "像聊微信一样，一条一个气泡。", "bool", True),
    Item("reply_delay", "style", "条与条之间的停顿（倍率）", "模拟打字的停顿时间的倍数，0 = 不停顿。", "float", 1.0, 0.0, 5.0, 0.1),
)

# ───────────────────────── 作息 ─────────────────────────
_add(
    Item("life_sleep", "routine", "她会睡觉", "关掉后她不再按作息睡觉，随时都醒着。", "bool", True),
    Item("sleep_hour", "routine", "睡觉时间（点）", "到这个点她睡着，不再主动找你。", "int", 22, 0, 23, 1, "点"),
    Item("wake_hour", "routine", "起床时间（点）", "到这个点她醒来。", "int", 7, 0, 23, 1, "点"),
    Item("summary_hour", "routine", "日总结时间（点）", "这个点之后，整理今天的经历，把一句话和心情记进日历。", "int", 21, 0, 23, 1, "点"),
    Item("goodnight_window_min", "routine", "晚安的有效时间（分钟）", "睡觉点之后这么久内打开网页，她还会补一声晚安；更晚就不补了。", "int", 30, 0, 240, 5, "分钟"),
    Item("meal_lunch_start", "routine", "午饭最早开始（时:分）", "午饭会在这个时间之后的「时间窗」里随机一个时刻开始。", "time", "11:30", section="三餐"),
    Item("meal_lunch_window_min", "routine", "午饭时间窗（分钟）", "从最早开始起，往后多少分钟内随机。", "int", 60, 0, 240, 5, "分钟"),
    Item("meal_dinner_center", "routine", "晚饭大约几点（时:分）", "晚饭在这个时间前后浮动。", "time", "18:00"),
    Item("meal_dinner_jitter_min", "routine", "晚饭前后浮动（分钟）", "", "int", 30, 0, 120, 5, "分钟"),
    Item("meal_cook_lead_min", "routine", "自己做饭提前多久开始（分钟）", "自己做的话，提前这么久进入「做饭」状态，做完进入「吃饭」。", "int", 60, 10, 180, 5, "分钟"),
    Item("meal_late_grace_min", "routine", "错过饭点多久就算没吃（分钟）", "页面没开着、错过了饭点，超过这么久就不补了。", "int", 90, 10, 300, 5, "分钟"),
    Item("chat_pause_s", "routine", "聊天后暂停多久（秒）", "你发消息后，她的活动暂停这么久，没动静了才算聊完。", "int", 120, 0, 3600, 10, "秒"),
)

# ───────────────────────── 状态与概率 ─────────────────────────
for _d in states.DEFS:
    _sec = f"状态：{_d.label}"
    if _d.random:
        _add(Item(f"state_w_{_d.key}", "states", f"「{_d.label}」的权重", "越大越容易轮到；0 = 不会出现。各状态之间按比例随机。", "float", float(_d.weight), 0.0, 100.0, 1.0, section=_sec))
    _add(
        Item(f"state_dur_{_d.key}_min", "states", f"「{_d.label}」最短（分钟）", "这个状态最短持续多久（页面开着的时间）。", "int", _d.dur[0], 1, 600, 1, "分钟", section=_sec if not _d.random else ""),
        Item(f"state_dur_{_d.key}_max", "states", f"「{_d.label}」最长（分钟）", "最长持续多久。", "int", _d.dur[1], 1, 600, 1, "分钟"),
        Item(f"state_from_{_d.key}", "states", f"「{_d.label}」最早几点", "这个状态只会在这个点之后出现。", "int", _d.hours[0], 0, 24, 1, "点"),
        Item(f"state_to_{_d.key}", "states", f"「{_d.label}」最晚几点", "到这个点之前才会出现（24 = 不限）。", "int", _d.hours[1], 0, 24, 1, "点"),
        Item(f"state_weather_{_d.key}", "states", f"「{_d.label}」的天气条件", "哪种天气下才会出现。", "select", _d.weather, choices=states.WEATHER_CHOICES),
        Item(f"state_spends_{_d.key}", "states", f"「{_d.label}」可能花钱", "做这件事时可能触发消费（零花钱，后面的更新用）。", "bool", _d.spends),
        Item(f"state_outside_{_d.key}", "states", f"「{_d.label}」算在外面", "在外面时她回复更慢、更短。", "bool", _d.outside),
    )
_add(
    Item("outside_reply_min_s", "states", "在外面时，回复前多等几秒（最少）", "她在外面时，看到消息到回你之前先等一会儿。", "int", 4, 0, 120, 1, "秒", section="在外面"),
    Item("outside_reply_max_s", "states", "在外面时，回复前多等几秒（最多）", "", "int", 12, 0, 300, 1, "秒"),
    Item("outside_delay_mult", "states", "在外面时，条与条之间停顿的倍率", "在外面时每条消息之间的间隔更长。", "float", 2.5, 1.0, 10.0, 0.5),
    Item("why_enabled", "states", "记下每次随机决定的原因（「为什么」）", "下一个状态、这顿怎么吃、今天的事、买东西：抽中了什么、各因素、最终概率。状态栏和「生活事件」旁边的「为什么」里看。", "bool", True),
    Item("why_keep_days", "states", "「为什么」留几天", "", "int", 7, 1, 60, 1, "天"),
)
_add(
    _prob("writing_p_revisit", "states", "（旧）写作时回头续写之前内容的概率", "v10.6 起不再用：写哪一段改成随机挑（见「读书、写作」里的写作设置）。", 0.20, "概率", hidden=True),
    _prob("life_read_refuse_prob", "states", "没读满时拒绝「去读书」的概率", "你叫她读书时，即使今天没到上限，她也可能推掉。", 0.30),
    _prob("life_interrupt_prob", "states", "打游戏 / 刷视频被打断时先撒娇的概率", "你来找正玩着的她，她先撒娇问「干嘛打扰我」的概率；一段对话只问一次。", 0.50),
)

# ───────────────────────── 读书、写作 ─────────────────────────
_add(
    _prob("life_read_day_prob", "reading", "每天读书的概率", "每天开始时随机决定今天读不读书。", 0.70, "读书"),
    Item("life_read_cap_min", "reading", "每天读书上限（分钟）", "每天累计最多读这么久。", "int", 30, 0, 240, 5, "分钟"),
    _prob("slack_chance", "reading", "读书时走神的概率", "每读完一块，走神 / 偷懒的概率。", 0.10),
    _prob("quit_early_chance", "reading", "一开始就想「看一会儿就不看了」的概率", "", 0.18),
    _prob("post_read_thought", "reading", "看完一轮后发读后感的概率", "", 0.40),
    _prob("create_success", "reading", "试图创作时写成的概率", "没写成的会变成划掉的废稿，留在草稿本里。", 0.55, "写作"),
    _prob("share_draft_done", "reading", "写成之后主动分享的概率", "", 0.35),
    _prob("share_draft_abandoned", "reading", "写崩了之后也分享的概率", "", 0.10),
    Item("writing_stick_hours", "reading", "手头那份文档要写多久才可能换", "她说过要写某份文档（或刚写过）之后，这么多小时内都接着写那份，不会一会儿换一份。", "float", 12.0, 0.0, 72.0, 1.0, "小时", "写作：写哪份、写哪段"),
    _prob("writing_p_newest_doc", "reading", "写作时挑最近写过的那份文档的概率", "过了上面那段时间才用到；其余在别的没写满的文档里随机挑一份。", 0.60),
    _prob("writing_p_new_section", "reading", "另起一段写全新东西的概率", "其余在已有的段落里随机挑一段接着写（写之前会先看那一段末尾写到哪了）。", 0.25),
    _prob("writing_new_doc_fill", "reading", "所有文档写到多少以后才可能要新文档", "按字数上限算；没到之前她只会在已有文档里另起一段，不会问你要新文档。", 0.80),
    _prob("writing_p_new_doc", "reading", "那之后想写新东西时问你要新文档的概率", "所有文档都写到上面那个比例以后才会用到；否则就在已有文档里用 * * * 分隔另起一段。", 0.40),
    _prob("writing_p_self_delete", "reading", "（旧）心情低落时删掉写得不好的内容的概率", "v9 起不再用：删不删由写作结果分档里的「删了重写」决定。", 0.35, hidden=True),
    _prob("writing_p_insist", "reading", "我改了她的文档后，她坚持己见、改回去的概率", "改回去前会留快照，在文档「历史」里能找回。", 0.25),
    Item("writing_doc_limit", "reading", "一份文档的字数上限", "写满之后她就要向你要新文档。", "int", 10000, 1000, 200000, 500, "字"),
    Item("edit_react_delay_s", "reading", "我保存文档后她多久才反应（秒）", "这段时间内再保存会合并成一次。", "float", 20.0, 0.0, 600.0, 1.0, "秒"),
    Item("edit_min_change", "reading", "改动多少字以内她当没看见", "改得太少就不去烦你。", "int", 4, 0, 100, 1, "字"),
)

# ───────────────────────── 天气 ─────────────────────────
# 每月气候表（联网关闭或拉取失败时，用它模拟天气）：平均温度、日内温度波动、下雨概率、下雪概率
CLIMATE_COLUMNS = [
    ("t", "平均气温（℃）", -40.0, 50.0, 0.5),
    ("swing", "气温波动（±℃）", 0.0, 20.0, 0.5),
    ("rain", "下雨概率（0~1）", 0.0, 1.0, 0.05),
    ("snow", "下雪概率（0~1）", 0.0, 1.0, 0.01),
]


def _climate(rows: list[tuple[float, float, float, float]]) -> list[dict[str, float]]:
    return [{"t": t, "swing": sw, "rain": r, "snow": sn} for t, sw, r, sn in rows]


# 她那边：浙江绍兴新昌一带（亚热带季风，6 月有梅雨）
CLIMATE_HER = _climate([
    (4.5, 4.0, 0.25, 0.04), (6.5, 4.0, 0.33, 0.04), (10.5, 4.5, 0.40, 0.00), (16.5, 5.0, 0.40, 0.00),
    (21.0, 5.0, 0.42, 0.00), (25.0, 4.5, 0.50, 0.00), (29.0, 5.0, 0.30, 0.00), (28.5, 5.0, 0.35, 0.00),
    (24.0, 4.5, 0.35, 0.00), (18.5, 5.0, 0.20, 0.00), (12.5, 5.0, 0.20, 0.00), (6.5, 4.5, 0.20, 0.02),
])
# 你那边：英格兰中部（温带海洋性，常年多雨、少雪）
CLIMATE_USER = _climate([
    (4.0, 3.5, 0.45, 0.05), (4.3, 3.5, 0.40, 0.06), (6.5, 4.0, 0.40, 0.03), (9.0, 4.5, 0.38, 0.00),
    (12.5, 5.0, 0.38, 0.00), (15.5, 5.0, 0.38, 0.00), (17.5, 5.0, 0.36, 0.00), (17.2, 5.0, 0.38, 0.00),
    (14.5, 4.5, 0.40, 0.00), (11.0, 4.0, 0.45, 0.00), (7.0, 3.5, 0.47, 0.01), (4.5, 3.5, 0.47, 0.04),
])

_add(
    Item("weather_online", "weather", "联网获取天气", "开着：用 Open-Meteo（免费、不用 key）取真实天气，只会发出经纬度。"
         "关掉，或者取不到时：按下面的每月气候表模拟天气。", "bool", True),
    Item("weather_refresh_hours", "weather", "多久更新一次天气（小时）", "启动时拉一次，之后每隔这么久拉一次（页面开着时）。", "float", 3.0, 0.5, 24.0, 0.5, "小时"),
    Item("her_lat", "weather", "她那边的纬度", f"她所在的地方用哪里的天气（真实地名不进她的上下文）。默认是{C.get('weather', {}).get('her_note', '')}。", "float", float(C.get("weather", {}).get("her_lat", 29.50)), -90.0, 90.0, 0.01, section="位置"),
    Item("her_lon", "weather", "她那边的经度", "东经为正，西经为负。", "float", float(C.get("weather", {}).get("her_lon", 120.90)), -180.0, 180.0, 0.01),
    Item("user_lat", "weather", "你那边的纬度", f"你所在的地方。" + (f"在她的世界里，这是{C.user_place()}。真实地名不会出现在她的上下文里。" if C.user_place() else ""), "float", float(C.get("weather", {}).get("user_lat", 52.41)), -90.0, 90.0, 0.01),
    Item("user_lon", "weather", "你那边的经度", "东经为正，西经为负。", "float", float(C.get("weather", {}).get("user_lon", -1.51)), -180.0, 180.0, 0.01),
    Item("weather_hot_c", "weather", "算「高温」的气温（℃）", "达到这个温度，她会觉得热，影响吃什么、要不要出门。", "float", 33.0, 10.0, 50.0, 0.5, "℃", section="对她的影响"),
    Item("weather_cold_snap_delta", "weather", "算「寒潮」的降温幅度（℃）", "比当月平均低这么多，算寒潮。", "float", 8.0, 2.0, 30.0, 0.5, "℃"),
    Item("weather_snow_message_prob", "weather", "下雪时主动发开心消息的概率", "每次下雪最多一条；同时她心情变好。", "float", 1.0, 0.0, 1.0, 0.05, "0~1"),
    Item("climate_her", "weather", "她那边的每月气候表", "离线时用来模拟天气。一行一个月（1 月到 12 月）。天气有延续性：今天下雨，明天更可能继续下。",
         "table", CLIMATE_HER, columns=CLIMATE_COLUMNS, section="每月气候表（离线模拟用）"),
    Item("climate_user", "weather", "你那边的每月气候表", "同上，是你那边的。", "table", CLIMATE_USER, columns=CLIMATE_COLUMNS),
)

# ───────────────────────── 人物 ─────────────────────────
_add(
    Item("people_draft_count", "people", "朋友草稿一次生成几个", "点「让 Phal 生成朋友草稿」时生成多少个同龄朋友的草稿，你审阅修改后才算数。", "int", 4, 1, 8, 1, "个"),
)

# ───────────────────────── 吃饭 ─────────────────────────
_add(
    Item("meals_enabled", "meals", "启用吃饭安排", "关掉后她不会按饭点做饭 / 吃饭 / 出门吃饭，状态只按随机来。", "bool", True),
    Item("meal_w_self", "meals", "自己做的权重", "每顿饭按权重决定怎么吃，还会受工作日 / 周末、心情、天气、季节、零花钱影响。", "float", 30.0, 0.0, 100.0, 1.0, section="吃法的权重"),
    Item("meal_w_takeout", "meals", "点外卖的权重", "", "float", 25.0, 0.0, 100.0, 1.0),
    Item("meal_w_alone_out", "meals", "一个人出去吃的权重", "", "float", 10.0, 0.0, 100.0, 1.0),
    Item("meal_w_friend_out", "meals", "和朋友出去吃的权重", "只有「人物」里有定稿的朋友才会选到。", "float", 10.0, 0.0, 100.0, 1.0),
    Item("meal_w_family", "meals", "和家人一起吃的权重", "爸爸很忙，所以工作日午饭几乎不会，更多在晚上和周末。", "float", 15.0, 0.0, 100.0, 1.0),
    _prob("meal_announce_prob", "meals", "饭前主动跟你说想吃什么的概率", "饭前她可能主动说一句今天想吃什么、准备做什么。", 0.4, "其它"),
    Item("meal_repeat_days", "meals", "几天内不重复吃同样的东西", "吃过的菜会记下来，这几天内不会再吃一样的。", "int", 4, 0, 30, 1, "天"),
    Item("family_away_days_min", "meals", "每月家人出差几天（最少）", "每个月随机连着几天爸妈出差不在家：她一个人吃饭，自己做或点外卖，饭菜摆到房间的食物桌上在房间里吃；"
         "平时在家吃饭是和家人在客厅吃。两项都填 0 = 不出差。", "int", 2, 0, 15, 1, "天", section="家人出差"),
    Item("family_away_days_max", "meals", "每月家人出差几天（最多）", "出差天数在最少和最多之间随机，每个月定一次。", "int", 3, 0, 15, 1, "天"),
)

# ───────────────────────── 零花钱 ─────────────────────────
from . import money_defs as _md  # noqa: E402
from . import price_defs as _pr  # noqa: E402

_add(
    Item("money_enabled", "money", "启用零花钱", "关掉后不发零花钱、不记账、不消费，余额也不再影响她。", "bool", True),
    Item("money_monthly", "money", f"每月零花钱（{C.unit()}）", f"每月发放的额度。{(C.currency().get('real') + '，') if C.currency().get('real') else ''}1 {C.unit()} = {C.sub_per_unit()} {C.currency().get('sub', '')}；账本里保留一位小数。", "float", 1500.0, 0.0, 100000.0, 10.0, C.unit()),
    Item("money_grant_day", "money", "每月几号发", "到这一天（或这之后第一次打开网页）发放；没开网页漏掉的月份，下次打开时补发。", "int", 1, 1, 28, 1, "号"),
    Item("money_carry", "money", "月底结余滚入下个月", "关掉的话，每次发零花钱时上个月剩下的会清零。", "bool", True),
    Item("money_low", "money", f"余额低于多少算「紧」（{C.unit()}）", "低于它：少点外卖、少买东西，聊天时她嘴上可能抱怨。", "float", 300.0, 0.0, 100000.0, 10.0, "钱", section="余额影响她的选择"),
    Item("money_tight", "money", f"余额低于多少算「很紧」（{C.unit()}）", "低于它：几乎不花钱，大件的更不买。", "float", 100.0, 0.0, 100000.0, 10.0, "钱"),
    Item("money_factor_low", "money", "「紧」时的花钱意愿", "乘在点外卖、出去吃、各种消费的概率上，1 = 不受影响。", "float", 0.5, 0.0, 1.0, 0.05),
    Item("money_factor_tight", "money", "「很紧」时的花钱意愿", "同上，更小。", "float", 0.15, 0.0, 1.0, 0.05),
    Item("money_complain_prob", "money", "余额紧时聊天里她抱怨一句的概率", "每轮聊天掷一次，中了就在上下文里提醒她可以自然地抱怨一句，不用每次都提。", "float", 0.4, 0.0, 1.0, 0.05, "0~1"),
    Item("spend_check_min", "money", "多久考虑一次要不要买东西（分钟）", "页面开着时，每隔这么久掷一次骰子。", "int", 20, 5, 240, 5, "分钟", section="什么时候买东西"),
    Item("spend_prob_spend", "money", "「可能花钱」的状态里，每次买东西的概率", "逛街、打游戏、追剧、出门吃饭、和朋友出去等（在「状态与概率」里勾了「花钱」的状态）。", "float", 0.3, 0.0, 1.0, 0.05, "0~1"),
    Item("spend_prob_idle", "money", "其它状态下，每次买东西的概率", "比如在家刷视频时点杯奶茶、网购个小东西。", "float", 0.05, 0.0, 1.0, 0.01, "0~1"),
    Item("spend_state_boost", "money", "和状态相关的东西的权重倍率", "打游戏时更容易内购、买游戏；逛街时更容易买衣服、饰品；和朋友出去更容易买奶茶。", "float", 5.0, 1.0, 20.0, 0.5),
    Item("spend_mood_boost", "money", "心情差时奶茶 / 零食的权重倍率", "心情差时更想靠吃的安慰自己。", "float", 2.5, 1.0, 10.0, 0.5),
    Item("spend_weather_boost", "money", "天气 / 季节相关的权重倍率", "天热、夏天更想买冷饮；降温、冬天更想买热饮和衣服。", "float", 1.5, 1.0, 10.0, 0.5),
    Item("spend_max_per_day", "money", "每天最多买几次东西", "不含外卖和出去吃。", "int", 4, 0, 20, 1, "次"),
    Item("spend_big_price", "money", f"多少钱以上算「大件」（{C.unit()}）", "大件有冷却时间，也更容易主动告诉你。", "float", 80.0, 1.0, 100000.0, 10.0, "钱"),
    Item("spend_big_cooldown_days", "money", "大件至少隔几天买一次", "0 = 不限制。", "int", 3, 0, 30, 1, "天"),
    Item("spend_iap_big_prob", "money", "游戏内购偶尔冲动的概率", "中了的话，价格会超过上面表里的最高价（冲动消费）。", "float", 0.06, 0.0, 1.0, 0.01, "0~1"),
    Item("spend_iap_big_max", "money", "冲动内购最高多少钱", "冲动内购的价格不会超过这个数。", "float", 328.0, 1.0, 100000.0, 10.0, "钱"),
    Item("price_skew", "money", "（旧）价格偏向便宜的程度", "v13 起不再用：价格由她按价格带 × 修正系数自己定。", "float", 2.5, 1.0, 6.0, 0.5, hidden=True),
    Item("spend_share_prob", "money", "买完主动告诉你的概率", "带上理由，比如「今天降温了 买了杯热的」。受每小时主动消息上限限制。", "float", 0.35, 0.0, 1.0, 0.05, "0~1", section="告诉你 / 记下来"),
    Item("spend_share_prob_big", "money", "大件买完主动告诉你的概率", "超过「大件」线的消费更容易说出来。", "float", 0.7, 0.0, 1.0, 0.05, "0~1"),
    Item("money_memory_min", "money", f"多少钱以上的消费写进记忆（{C.unit()}）", "会留下来的东西（放进房间的）总是会写。", "float", 30.0, 0.0, 100000.0, 5.0, "钱"),
    Item("meal_family_pays", "money", "和家人出门吃饭时，家里人付钱", "打开：不扣她的钱。外卖、一个人出去吃总是自己付；和朋友出去吃按下面的 AA / 请客。", "bool", True),
    _prob("meal_p_aa", "money", "和朋友出去吃：AA 的概率", "她只付自己那份。下面三项加起来不到 1 的部分也算 AA。", 0.75),
    _prob("meal_p_treated", "money", "和朋友出去吃：朋友请客的概率", "她一分不花，饭前那句话里会提一下。", 0.15),
    _prob("meal_p_treat", "money", "和朋友出去吃：她请客的概率", "她付两份。", 0.10),
    Item("price_bands", "money", f"价格带（{C.unit()}，一个人一份）",
         f"按大类给出价格范围（在「连锁 / 普通店 · {C.price_places()[0][1] if C.price_places() else '家附近'} · 没噱头」时）。她买东西 / 吃饭时自己定价格，超出「最低~最高 × 下面的系数」就重新想一次，还不行就夹回范围内；"
         "「常见」区间是告诉她一般花多少。" + C.pick("occult", f"神秘学用品很贵：800 {C.unit()}起，上不封顶（想要更夸张就把最高价调高），她买完至少还会剩下「紧」线那么多钱。"), "table", _pr.BAND_DEFAULTS, columns=_pr.BAND_COLUMNS, row_labels=_pr.BAND_ROWS, section="价格带和修正系数"),
    Item("price_mod_tier", "money", "店铺档次的系数", "高档只在日料、西餐、自助、衣服、外套、鞋、化妆护肤里出现。游戏、游戏内购、神秘学用品不受这三张表影响。", "table",
         [{"k": t[2]} for t in _pr.TIERS], columns=_pr.MOD_COLUMNS, row_labels=[f"{t[1]}（{t[3]}）" for t in _pr.TIERS]),
    Item("price_mod_gimmick", "money", "噱头的系数", "她分享贵的东西时，会自然地提一句为什么贵（联名款、商场里的、被坑了）。", "table",
         [{"k": t[2]} for t in _pr.GIMMICKS], columns=_pr.MOD_COLUMNS, row_labels=[t[1] + (f"（{t[3]}）" if t[3] else "") for t in _pr.GIMMICKS]),
    Item("price_mod_place", "money", "地点的系数", "第一行是她住的地方（系数 1）；比 1 大的地方要特地出门才去，她会说得出为什么贵。", "table",
         [{"k": t[2]} for t in _pr.PLACES], columns=_pr.MOD_COLUMNS, row_labels=[f"{t[1]}（{t[3]}）" for t in _pr.PLACES]),
    _prob("price_repeat_same", "money", "同一家店同一样东西，价格和上次完全一样的概率", "其余在上次价格的 ±10% 里浮动（尾数照样偏 .9）。", 0.6),
)
for _k, _n, _lo, _hi, _w, _room in _md.CATS:
    _occ = _k in _md.RESERVE_CATS
    _add(
        Item(f"price_min_{_k}", "money", f"（旧）「{_n}」最低价", "v13 起改用「价格带」。", "float", _lo, 0.1, 100000.0, 0.1, "钱", hidden=True),
        Item(f"price_max_{_k}", "money", f"（旧）「{_n}」最高价", "v13 起改用「价格带」。", "float", _hi, 0.1, 100000.0, 0.1, "钱", hidden=True),
        Item(f"spend_w_{_k}", "money", f"「{_n}」的权重", "越大越常买，0 = 不会随机买这类。神秘学用品很贵，默认大约一个月买一次。" if _occ else "越大越常买，0 = 不会随机买这类。", "float", _w, 0.0, 100.0, 0.5),
        Item(f"spend_room_{_k}", "money", f"「{_n}」放进房间", "买了会留下来的东西（衣服、游戏、饰品……）自动加进她的房间。", "bool", _room),
    )
for _k, _n, _lo, _hi in _md.MEAL_CATS:
    _add(
        Item(f"price_min_{_k}", "money", f"（旧）「{_n}」最低价", "v13 起改用「价格带」。", "float", _lo, 0.1, 100000.0, 0.1, "钱", hidden=True),
        Item(f"price_max_{_k}", "money", f"（旧）「{_n}」最高价", "v13 起改用「价格带」。", "float", _hi, 0.1, 100000.0, 0.1, "钱", hidden=True),
    )

# ───────────────────────── 事件 ─────────────────────────
from . import event_defs as _ev  # noqa: E402

_add(
    Item("events_enabled", "events", "启用生活事件", "关掉后她的日子里不会再发生这些小事，也不会生病。", "bool", True),
    Item("event_share_prob", "events", "发生后主动告诉你的概率", "没告诉你的事不写进日总结，但你问起「今天怎么样」时她可能提到。", "float", 0.5, 0.0, 1.0, 0.05, "0~1"),
    Item("ev_count_0", "events", "每天 0 件事的权重", "每天早上起床时抽一个数，决定今天发生几件事。", "float", 40.0, 0.0, 100.0, 1.0, section="每天几件事"),
    Item("ev_count_1", "events", "每天 1 件事的权重", "", "float", 40.0, 0.0, 100.0, 1.0),
    Item("ev_count_2", "events", "每天 2 件事的权重", "", "float", 15.0, 0.0, 100.0, 1.0),
    Item("ev_count_3", "events", "每天 3 件事的权重", "", "float", 5.0, 0.0, 100.0, 1.0),
)
for _sid, _lo, _hi, _w in _ev.SLOTS:
    _add(Item(f"ev_slot_{_sid}", "events", f"{_lo:02d}–{_hi:02d} 点的权重", "事件更容易发生在权重高的时段；21 点以后到起床不会发生。", "float", _w, 0.0, 20.0, 0.5,
              section="发生在什么时段" if _sid == "0709" else ""))
for _k, (_label, _desc, _who) in _ev.TYPES.items():
    _w, _from, _to, _wth, _out = _ev.DEFAULTS[_k]
    _add(
        Item(f"ev_w_{_k}", "events", f"「{_label}」的权重", "越大越容易抽到这种事。", "float", _w, 0.0, 100.0, 1.0, section="事件类型"),
        Item(f"ev_from_{_k}", "events", f"「{_label}」最早几点", "只在这个时段内发生；到点不满足条件会顺延一次。", "int", _from, 0, 24, 1, "点"),
        Item(f"ev_to_{_k}", "events", f"「{_label}」最晚几点", "", "int", _to, 0, 24, 1, "点"),
        Item(f"ev_weather_{_k}", "events", f"「{_label}」的天气条件", "哪种天气下才会发生（户外的事下雨不发生）。", "select", _wth, choices=states.WEATHER_CHOICES),
        Item(f"ev_out_{_k}", "events", f"「{_label}」需要出门", "需要出门的事，她睡觉 / 生病时不会发生。", "bool", _out),
    )
_add(
    Item("sick_p_base", "events", "平时每天生病的概率", "每天早上单独判定一次。默认 0.015 ≈ 一年 8 次（约一个半月一次，加上换季和降温）。", "float", 0.015, 0.0, 1.0, 0.005, "0~1", section="生病"),
    Item("sick_p_season", "events", "季节交替时每天生病的概率", "3、6、9、12 月的头几天。", "float", 0.06, 0.0, 1.0, 0.01, "0~1"),
    Item("sick_season_window_days", "events", "季节交替算几天", "每个季节开始后的这么多天内算「季节交替」。", "int", 7, 1, 31, 1, "天"),
    Item("sick_p_drop", "events", "气温骤降 / 寒潮时每天生病的概率", "", "float", 0.08, 0.0, 1.0, 0.01, "0~1"),
    Item("sick_drop_c", "events", "24 小时内降多少度算骤降", "", "float", 6.0, 1.0, 30.0, 0.5, "℃"),
    Item("sick_days_min", "events", "生病最少几天", "", "int", 2, 1, 14, 1, "天"),
    Item("sick_days_max", "events", "生病最多几天", "", "int", 3, 1, 14, 1, "天"),
    Item("occult_insight_every", "events", "每几次神秘学经历有一条小心得", "攒够这么多次，她会有一条自己摸索出来的小心得。0 = 不写。", "int", 3, 0, 20, 1, "次", section="神秘学"),
    Item("occult_ask_prob", "events", "分享神秘学的事时，改成来请教你的概率", "在她眼里你是神秘学家，她可能会就这件事来问你。", "float", 0.3, 0.0, 1.0, 0.05, "0~1"),
)

# ───────────────────────── 心情同步（你分享心情 → 她可能被感染） ─────────────────────────
_add(
    Item("mood_sync", "states", "你分享心情时，她可能被感染", "你说「我今天好开心 / 好累 / 有点难过」这类话时，她有一定概率变成类似的心情（持续一段时间）。先用关键词筛一遍，筛中的才多调一次模型确认（短提示词）。", "bool", True, section="心情同步"),
    Item("mood_sync_prob", "states", "被感染的概率", "每次你分享心情（并且模型确认你确实在说自己的心情）时掷一次。", "float", 0.5, 0.0, 1.0, 0.05, "0~1"),
    Item("mood_sync_scale", "states", "被感染时心情推动的倍率", "你分享的心情会推她一下（之后慢慢回落），越大推得越多。", "float", 0.6, 0.0, 2.0, 0.05, "×"),
    Item("mood_sync_cooldown_min", "states", "两次判断之间至少隔多久", "省钱用：这段时间里再分享心情也不会重新判断（0 = 不限）。", "int", 10, 0, 240, 1, "分钟"),
)

# ───────────────────────── 心情词表（v9：按「情绪 × 精力」四个区域，设置页里维护） ─────────────────────────
for _q, _ql in _BH.QUADS:
    _add(Item(f"mood_words_{_q}", "states", f"「{_ql}」的心情词", "她的心情落在这个区域时从这组里随机取一个。一行一个；想让某个词少出现，写成「词:权重」（比如「焦虑:0.3」，默认 1）。"
              "分界线在「心情与精力」里调。", "list", list(_BH.QUAD_DEFAULTS[_q]), section="心情词表" if _q == "hv_he" else ""))

# ───────────────────────── 心情与精力（设置页只显示 MOOD_VISIBLE 里的几项） ─────────────────────────
def _f(key: str, label: str, hint: str, default: float, lo: float, hi: float, step: float, unit: str = "", section: str = "") -> Item:
    return Item(key, "mood", label, hint, "float", default, lo, hi, step, unit, section)


_add(
    _f("mood_half_v", "情绪推动的半衰期", "事件推了一下情绪之后，过这么久回落一半（真实时间，应用关着也在回落）。", 90, 5, 1440, 5, "分钟", "情绪"),
    _f("mood_half_e", "精力推动的半衰期", "活动消耗 / 恢复的精力，过这么久回落一半。", 120, 5, 1440, 5, "分钟"),
    _f("mood_event_k", "事件推动情绪的倍率", "事件的心情词换算成推动：情绪 = 这个词的情绪值 × 倍率。", 0.5, 0.0, 2.0, 0.05, "×"),
    _f("mood_event_ke", "事件推动精力的倍率", "精力 = （这个词的精力值 − 0.5）× 倍率。", 0.4, 0.0, 2.0, 0.05, "×"),
    _f("mood_repeat_days", "多少天内算「同一类事又来了」", "同一类事件（比如写作失败）在这段时间里再发生，对心情的影响打折。", 3, 0, 14, 1, "天"),
    _f("mood_repeat_decay", "同一类事每多一次，影响打几折", "第 2 次 × 这个数，第 3 次 × 它的平方……", 0.6, 0.1, 1.0, 0.05, "×"),
    _f("mood_v_split", "「高兴 / 低落」的分界", "情绪低于这个值算「低落」那两组（四个区域的心情词表在设置页的「状态与概率 → 心情词表」）。", -0.1, -1.0, 1.0, 0.01),
    _f("mood_e_split", "「有精神 / 没精神」的分界", "精力低于这个值算「没精神」那两组。", 0.55, 0.0, 1.0, 0.01),
    _f("mood_q_hyst", "换区域的门槛", "离分界线不够这么远就不换区域（防止在线上来回跳）。", 0.04, 0.0, 0.3, 0.01),
    _f("mood_why_min", "显示「为什么」的门槛", "某次推动还剩这么多，就用它当此刻心情的原因；否则用今天的原因。", 0.08, 0.0, 1.0, 0.01),
    _f("mood_festival_floor", "节日当天情绪的下限", "过节那天情绪不低于这个值。", 0.15, -1.0, 1.0, 0.05),
    _f("mood_flavor_minutes", "附加心情词挂多久", "想念、嘴馋、怀旧这类词被触发后显示多久。", 60, 10, 1440, 10, "分钟"),
    _f("mood_miss_hours", "多久没聊算「想念」", "这么久没和你说话，心情还行时显示「想念」，低落时显示「孤单」（0 = 不用）。", 8, 0, 72, 1, "小时"),
    _f("mood_snack_min", "饭前多久可能「嘴馋」", "饭点前这么久、精力还行时，可能显示「嘴馋」（0 = 不用）。", 40, 0, 180, 5, "分钟"),

    _f("base_mean", "平均基线", "每天情绪基线的中心（略偏愉快）。", 0.08, -1.0, 1.0, 0.01, "", "每日基线"),
    _f("base_carry", "前一天的延续", "昨天的平均情绪带到今天的比例。", 0.4, 0.0, 1.0, 0.05, "×"),
    _f("base_noise", "随机波动", "每天基线的随机波动（标准差）。", 0.1, 0.0, 1.0, 0.01),
    _f("base_clamp", "基线的范围", "基线最多偏离中心这么多。", 0.6, 0.1, 1.0, 0.05),
    _f("base_sleep_k", "昨晚睡眠的影响", "（睡眠质量 − 0.5）× 这个数。", 0.3, 0.0, 2.0, 0.05, "×"),
    _f("base_yesterday_k", "昨天发生的事的影响", "昨天所有事件推动的总和 × 这个数。", 0.25, 0.0, 2.0, 0.05, "×"),
    _f("base_clear", "晴天", "", 0.08, -0.5, 0.5, 0.01),
    _f("base_rain", "下雨", "", -0.06, -0.5, 0.5, 0.01),
    _f("base_rain_streak", "连续阴雨每多一天", "连着阴雨的天数 × 这个数（最多算 4 天）。", -0.04, -0.5, 0.5, 0.01),
    _f("base_snow", "下雪", "", 0.08, -0.5, 0.5, 0.01),
    _f("base_spring", "春天", "", 0.04, -0.5, 0.5, 0.01),
    _f("base_summer", "夏天", "", 0.0, -0.5, 0.5, 0.01),
    _f("base_autumn", "秋天", "", -0.02, -0.5, 0.5, 0.01),
    _f("base_winter", "冬天", "", -0.04, -0.5, 0.5, 0.01),
    _f("base_friday", "周五", "", 0.04, -0.5, 0.5, 0.01),
    _f("base_weekend", "周末", "", 0.06, -0.5, 0.5, 0.01),
    _f("base_monday", "周一", "", -0.04, -0.5, 0.5, 0.01),

    _f("e_wake", "刚醒时", "一天的精力节奏（不含睡眠和活动的影响）：起床那一刻。", 0.45, 0.0, 1.0, 0.01, "", "精力节奏"),
    _f("e_peak", "上午最高", "", 0.82, 0.0, 1.0, 0.01),
    _f("e_peak_hour", "上午最高在几点", "", 10, 6, 13, 0.5, "点"),
    _f("e_dip", "午后低谷", "午后犯困。", 0.52, 0.0, 1.0, 0.01),
    _f("e_dip_hour", "午后低谷在几点", "", 14, 12, 17, 0.5, "点"),
    _f("e_evening", "傍晚回升", "", 0.68, 0.0, 1.0, 0.01),
    _f("e_evening_hour", "傍晚回升在几点", "", 17.5, 15, 21, 0.5, "点"),
    _f("e_bed", "睡前", "睡觉那一刻。", 0.32, 0.0, 1.0, 0.01),
    _f("sleep_mean", "睡眠质量的平均", "每晚的睡眠质量 0~1，这是平均值。", 0.65, 0.0, 1.0, 0.01, "", "睡眠"),
    _f("sleep_sd", "睡眠质量的波动", "", 0.15, 0.0, 0.5, 0.01),
    _f("sleep_mood_k", "睡前心情对睡眠的影响", "睡前情绪 × 这个数，加到睡眠质量上（心情差更容易没睡好）。", 0.2, 0.0, 1.0, 0.05, "×"),
    _f("sleep_energy_k", "睡眠对第二天精力的影响", "（睡眠质量 − 0.5）× 这个数，整天的精力都跟着高低。", 0.35, 0.0, 1.0, 0.05, "×"),

    _f("create_max_per_day", "一天最多写几次", "写作由灵感驱动（下面），但一天最多写这么多次。", 3, 0, 10, 1, "次", "写作：灵感、满足感、换做法"),
    _f("insp_half_h", "灵感的半衰期", "灵感过这么久剩一半。", 18, 1, 240, 1, "小时"),
    _f("insp_floor", "没灵感时也想写的程度", "写作被选中的倍率 = （这个数 + 倍率 × 灵感）×（1 − 满足感）。", 0.1, 0.0, 2.0, 0.05),
    _f("insp_k", "灵感对写作欲望的倍率", "", 2.0, 0.0, 10.0, 0.1, "×"),
    _f("insp_read", "看完一次书加多少灵感", "", 0.12, 0.0, 1.0, 0.01),
    _f("insp_event", "发生一件事加多少灵感", "好事再加一点（下一项）。", 0.08, 0.0, 1.0, 0.01),
    _f("insp_event_good", "好事额外加多少灵感", "", 0.06, 0.0, 1.0, 0.01),
    _f("insp_weather", "下雨 / 下雪的一天加多少灵感", "起床时看天气加一次。", 0.08, 0.0, 1.0, 0.01),
    _f("insp_snow_first", "第一场雪加多少灵感", "", 0.25, 0.0, 1.0, 0.01),
    _f("insp_chat", "和你聊一次加多少灵感", "隔一阵（下一项）才算新的一次。", 0.04, 0.0, 1.0, 0.01),
    _f("insp_chat_gap_min", "聊天多久算新的一次", "", 30, 1, 600, 1, "分钟"),
    _f("sat_half_days", "满足感的半衰期", "写完一大段之后「写够了」的感觉，过这么久剩一半。", 2, 0.1, 30, 0.1, "天"),
    _f("sat_floor", "满足感最多压到多少", "写作欲望最多降到原来的这么多倍。", 0.05, 0.0, 1.0, 0.01),
    _f("react_p_writing", "写作带来情绪反应的概率", "其余只记录，不影响心情。", 0.4, 0.0, 1.0, 0.05, "0~1"),
    _f("react_p_event", "日常事件带来情绪反应的概率", "其余只记录，不影响心情（生病、下雪、心情同步不受这个影响）。", 0.4, 0.0, 1.0, 0.05, "0~1"),
    _f("react_p_act", "读书 / 做饭 / 出门的结果带来情绪反应的概率", "读得入迷 / 读不进去、饭做得成功 / 失手、出门玩得开心 / 扫兴……其余只记录。", 0.4, 0.0, 1.0, 0.05, "0~1"),
    _f("adapt_n", "连着几次不顺就换做法", "写作连着「卡住 / 不满意」这么多次。", 2, 1, 10, 1, "次"),
    _f("adapt_mult", "之后去写的概率再乘", "每多一次不顺再乘一次。", 0.5, 0.0, 1.0, 0.05, "×"),
    _f("adapt_w_pause", "换做法：歇几个小时（权重）", "", 0.35, 0.0, 10.0, 0.05),
    _f("adapt_w_switch", "换做法：换题材 / 写法（权重）", "", 0.25, 0.0, 10.0, 0.05),
    _f("adapt_w_read", "换做法：先去看书（权重）", "", 0.25, 0.0, 10.0, 0.05),
    _f("adapt_w_ask", "换做法：来问你（权重）", "你没及时回，她也可能自己接着写。", 0.05, 0.0, 10.0, 0.05),
    _f("adapt_pause_h", "歇多久", "", 6, 0.5, 72, 0.5, "小时"),
    _f("ask_per_week", "卡住了来问你，每周最多几次", "", 2, 0, 20, 1, "次"),
    _f("insp_ref", "灵感的「平常水平」", "写作分档里灵感的影响按（灵感 − 这个数）算：比平常多就更顺，少就更容易卡。", 0.25, 0.0, 1.5, 0.01),

    _f("bag_size", "一袋签有几张", "事件的基调用洗牌袋抽：抽完一袋再装一袋，不会扎堆。", 12, 2, 60, 1, "张", "事件：基调和外出"),
    _f("bag_ratio", "正面和平常 : 不顺", "默认 3 : 1。", 3.0, 0.0, 20.0, 0.1, ": 1"),
    _f("bag_good_share", "正面和平常里，好事占多少", "", 0.5, 0.0, 1.0, 0.05, "0~1"),
    _f("neg_cooldown_days", "同一类事件「不顺」的冷却期", "这段时间里同一类事件再抽到「不顺」就改成平常。", 3, 0, 30, 0.5, "天"),
    _f("outing_event_p", "外出回来发生一件事的概率", "和朋友出去、出门吃饭、逛街、散步、拍照、跑腿、去朋友家玩桌游结束时；和朋友有关的只用人物档案里的朋友。", 0.5, 0.0, 1.0, 0.05, "0~1"),

    _f("intent_n2_p", "一天有两个小打算的概率", "否则一个。", 0.45, 0.0, 1.0, 0.05, "0~1", "每天的小打算"),
    _f("intent_write_share", "小打算里写作的比例", "", 0.25, 0.0, 1.0, 0.05, "0~1"),
    _f("intent_boost", "有打算的事更容易去做（倍率）", "", 2.0, 1.0, 10.0, 0.1, "×"),
    _f("intent_carry_days", "没做完最多顺延几天", "", 2, 0, 7, 1, "天"),
    _f("intent_done_min", "做多久才算做了", "", 10, 1, 120, 1, "分钟"),

    _f("mood_mention_prob", "主动说出自己心情的概率", "每轮聊天掷一次；平时只通过语气、用词体现。被你问到时才直说。", 0.08, 0.0, 1.0, 0.01, "0~1", "表达"),
    Item("write_tiers", "mood", "写作结果分档", "每行一档：顺利 / 写了一点 / 卡住 / 不满意 / 删了重写。概率 ∝ 基础权重 × e^(精力系数 ×（精力 − 0.5）× 2 + 情绪系数 × 情绪 + 灵感系数 ×（灵感 − 0.5）× 2)。"
         "有情绪反应时，情绪按（平均，波动）抽。写完扣掉一些灵感、加一些满足感。", "table", _BH.TIER_DEFAULTS, columns=_BH.TIER_COLUMNS, section="写作结果分档"),
    Item("read_tiers", "mood", "读书结果分档", "每行一档：读得入迷 / 读了一些 / 读不进去。概率 ∝ 基础权重 × e^(精力系数 ×（精力 − 0.5）× 2 + 情绪系数 × 情绪)。读得入迷读得快一点，读不进去读得慢、早早放下。",
         "table", _BH.ACT_DEFAULTS["read"], columns=_BH.ACT_COLUMNS, section="读书 / 做饭 / 出门的结果分档"),
    Item("cook_tiers", "mood", "做饭结果分档", "每行一档：做得很成功 / 普普通通 / 有点失手。", "table", _BH.ACT_DEFAULTS["cook"], columns=_BH.ACT_COLUMNS),
    Item("out_tiers", "mood", "出门结果分档", "每行一档：玩得很开心 / 一般般 / 有点扫兴。回来时如果发生一件事，基调跟着这一档（开心 → 好事，扫兴 → 不顺）。", "table", _BH.ACT_DEFAULTS["out"], columns=_BH.ACT_COLUMNS),
    Item("event_tones", "mood", "事件基调的情绪反应", "每行一种：好事 / 平常 / 不顺。有情绪反应时，情绪按（平均，波动）抽。", "table", _BH.TONE_DEFAULTS, columns=_BH.TONE_COLUMNS, section="事件基调的情绪反应"),
    Item("mood_ask_words", "mood", "算「在问她心情」的说法", "你的消息里有这些词，就允许她直接说自己的心情和状态。", "list",
         ["心情", "开心吗", "高兴吗", "难过吗", "怎么了", "还好吗", "累不累", "累吗", "困吗", "不开心", "状态怎么样", "今天怎么样"]),
)

# 选状态时参考心情和精力：权重 × e^(精力系数 × (精力 − 0.5) × 2 + 情绪系数 × 情绪)；做完一件事精力加减多少
STATE_MOOD_DEFAULTS: dict[str, tuple[float, float, float]] = {     # 键：(精力系数, 情绪系数, 做完后精力变化)
    "tea": (-0.4, 0.0, 0.06), "read": (0.3, 0.2, -0.03), "daze": (-0.8, -0.3, 0.04), "video": (-0.6, -0.2, -0.02),
    "game": (0.2, 0.1, -0.04), "create": (0.8, 0.5, -0.08), "occult": (0.5, 0.2, -0.08), "sfx": (0.4, 0.0, -0.08),
    "cook": (0.0, 0.0, -0.06), "eat": (0.0, 0.0, 0.08), "eat_alone": (0.0, 0.0, 0.08), "dine_out": (0.0, 0.0, -0.06), "homework": (0.6, 0.0, -0.1),
    "shower": (0.0, 0.0, 0.05), "nap": (-1.2, 0.0, 0.3), "music": (-0.2, -0.4, 0.04), "anime": (-0.5, -0.3, -0.02),
    "draw": (0.4, 0.4, -0.05), "tidy": (0.5, 0.1, -0.06), "walk": (0.6, 0.2, -0.1), "shop": (0.6, 0.4, -0.15),
    "friends": (0.7, 0.5, -0.2), "rain_watch": (-0.3, -0.2, 0.03), "sick": (0.0, 0.0, 0.0),
    "calligraphy": (0.3, 0.1, -0.04), "craft": (0.4, 0.3, -0.06), "bake": (0.4, 0.4, -0.08), "photo": (0.6, 0.4, -0.1),
    "album": (-0.2, 0.0, 0.0), "netshop": (-0.3, 0.1, -0.02), "call": (0.2, 0.3, -0.03), "tryon": (0.2, 0.5, -0.03),
    "puzzle_family": (0.0, 0.2, 0.0), "boardgame_friends": (0.6, 0.5, -0.15), "boardgame_online": (0.2, 0.3, -0.05),
    "synth": (0.3, 0.3, -0.04), "errand": (0.3, -0.1, -0.08), "unbox": (0.0, 0.3, 0.02), "laze": (-1.5, -0.2, 0.05),
    "exercise": (0.7, 0.2, -0.12), "shelf": (0.4, 0.1, -0.05),
}
for _d in states.DEFS:
    _ek, _vk, _de = STATE_MOOD_DEFAULTS.get(_d.key, (0.0, 0.0, 0.0))
    _sec = "选状态：参考精力和情绪" if _d is states.DEFS[0] else ""
    _add(
        _f(f"state_ek_{_d.key}", f"「{_d.label}」精力系数", "正数 = 精力足时更想做；负数 = 累的时候更想做。", _ek, -3.0, 3.0, 0.1, "", _sec),
        _f(f"state_vk_{_d.key}", f"「{_d.label}」情绪系数", "正数 = 心情好时更想做；负数 = 低落时更想做。", _vk, -3.0, 3.0, 0.1),
        _f(f"state_de_{_d.key}", f"「{_d.label}」做完后精力", "做完这件事精力加减多少（之后慢慢回落）。", _de, -1.0, 1.0, 0.01),
    )

# ───────────────────────── 她的房间 ─────────────────────────
ROOM_CATEGORIES = ["植物 plant", "小物件 bear", "书与本子 book", "衣服饰品 dress", "电子与游戏 game", "家具摆设 lamp", "其他 gift"]
ROOM_AREAS = ["窗边", "沙发", "书桌", "床", "床头柜", "衣柜", "书架"]
_add(
    Item("room_categories", "room", "物品分类", "物品进房间时由模型从这里选一个分类（选错了在房间页面上改）。每项在名字后面空一格写图标：内置的有 plant（植物）、bear（小熊）、book（本子）、dress（裙子）、game（手柄）、lamp（台灯）、gift（礼盒），也可以直接写一个 emoji。"
         "图标只出现在分类名旁边。改名后，旧分类下的东西会暂时归到最后一个分类里，数据不会丢。「食物」是系统自带的分类（不用写在这里，删不掉）：吃的喝的归这一类，放书桌或食物桌，有保质期，她可能会吃掉。", "list", ROOM_CATEGORIES),
    Item("room_areas", "room", "房间的区域", "房间里的区域，也是她摆东西的地方。窗边（窗下的矮柜 + 落地灯）/ 沙发 / 书桌 / 床 / 床头柜 / 衣柜 / 书架在房间里有固定位置，其它名字会排在房间下面。书桌上的「杂物区」和北墙边的「食物桌」放吃的，那是系统自己管的，不在这里。"
         "删掉的区域里的东西会显示在「别处」。（新房间没有门，所以没有「门口」。）", "list", ROOM_AREAS),
    Item("room_shelf_cap", "room", "书架最多放几本书", "书架放满后，新书横着堆在地上（椅子旁边、靠着墙）。导入的书和她买的书都算。", "int", 50, 1, 500, 1, "本"),
    Item("room_floor_cap", "room", "地上最多堆几本书", "书架放满、地上也堆满了，她就会把自己买的书「收纳」进收纳篮（不对外展示，但一直保存）。", "int", 20, 0, 200, 1, "本"),
    Item("room_store_batch", "room", "一次收纳几本书", "她收纳时，从自己买的书里一次收走这么多本（最新的几本不收）。你随时可以在房间里调整、交换书架上的书和收纳篮里的书。", "int", 10, 1, 100, 1, "本"),
    Item("room_chat_add", "room", "聊天里自动收下你送的东西", "你在聊天里说送了她、寄给了她什么，她会收下并放进房间。关掉后只能在房间页面上手动加。", "bool", True),
    Item("room_relevant_max", "room", "聊天时最多带进几件相关的东西", "你聊到的东西、区域或分类相关的物品，最多带这么多件进她的上下文。", "int", 5, 0, 20, 1, "件"),
    Item("room_mention_prob", "room", "聊天时她主动想起房间里某样东西的概率", "每轮聊天掷一次，中了就在上下文里随机带一件，她可能自然提一句（比如「窗边那盆今天开花了」）。", "float", 0.15, 0.0, 1.0, 0.05, "0~1"),
    Item("room_recent_days", "room", "多少天内算「最近新添的」", "新添的东西在这几天内会一直带在她的上下文里，她会记得自己刚收到了什么。", "int", 3, 0, 30, 1, "天"),
    Item("food_desk_share", "room", "新的食物放书桌（而不是食物桌）的比例", "她买的、烤的零食奶茶甜点水果，按这个比例放书桌的杂物区，其余放北墙边的食物桌；饭菜总是放食物桌。", "float", 0.4, 0.0, 1.0, 0.05, "0~1", section="食物"),
    Item("food_portions_max", "room", "一份食物最多分几次吃完", "每份食物随机分 1 ~ 这么多次吃完（你加的食物可以自己定）。", "int", 3, 1, 10, 1, "次"),
    Item("food_eat_rate", "room", "想吃东西的频率（每小时）", "在喝茶、看书、发呆、刷视频、打游戏、写东西、写作业、听歌、追剧、画画、看雨、生病躺着、烘焙、打电话、线上桌游、赖床时，有能吃的东西，平均每小时想吃这么多次（心情、精力不变时）。", "float", 0.35, 0.0, 10.0, 0.05, "次 / 小时"),
    Item("food_eat_kv", "room", "心情越差越想吃（系数）", "想吃的频率 × e^(−系数 × 情绪)。0 = 和心情无关。", "float", 1.2, 0.0, 5.0, 0.1),
    Item("food_eat_ke", "room", "越累越想吃（系数）", "想吃的频率 × e^(系数 ×（0.5 − 精力）× 2)。0 = 和精力无关。", "float", 0.8, 0.0, 5.0, 0.1),
    Item("food_eat_min_min", "room", "吃一次最少几分钟", "她拿起一份吃的，吃这么久到「最多」之间随机（吃一次只吃掉一份）。", "int", 3, 1, 10, 1, "分钟"),
    Item("food_eat_max_min", "room", "吃一次最多几分钟", "吃东西最多持续这么久。", "int", 10, 1, 30, 1, "分钟"),
    Item("food_life_dessert_min_h", "room", "甜点的保质期（最短）", "她自己买 / 做的甜点，保质期在最短和最长之间随机；你放的按你设的。", "float", 15.0, 0.5, 720.0, 0.5, "小时"),
    Item("food_life_dessert_max_h", "room", "甜点的保质期（最长）", "和最短一样就是固定的保质期。", "float", 48.0, 0.5, 720.0, 0.5, "小时"),
    Item("food_life_milktea_min_h", "room", "奶茶的保质期（最短）", "她自己买 / 做的奶茶，保质期在最短和最长之间随机；你放的按你设的。", "float", 12.0, 0.5, 720.0, 0.5, "小时"),
    Item("food_life_milktea_max_h", "room", "奶茶的保质期（最长）", "和最短一样就是固定的保质期。", "float", 12.0, 0.5, 720.0, 0.5, "小时"),
    Item("food_life_snack_min_h", "room", "零食的保质期（最短）", "她自己买 / 做的零食，保质期在最短和最长之间随机；你放的按你设的。", "float", 24.0, 0.5, 720.0, 0.5, "小时"),
    Item("food_life_snack_max_h", "room", "零食的保质期（最长）", "和最短一样就是固定的保质期。", "float", 72.0, 0.5, 720.0, 0.5, "小时"),
    Item("food_life_fruit_min_h", "room", "水果的保质期（最短）", "她自己买 / 做的水果，保质期在最短和最长之间随机；你放的按你设的。", "float", 24.0, 0.5, 720.0, 0.5, "小时"),
    Item("food_life_fruit_max_h", "room", "水果的保质期（最长）", "和最短一样就是固定的保质期。", "float", 72.0, 0.5, 720.0, 0.5, "小时"),
    Item("food_life_meal_min_h", "room", "饭菜的保质期（最短）", "她自己买 / 做的饭菜，保质期在最短和最长之间随机；你放的按你设的。", "float", 12.0, 0.5, 720.0, 0.5, "小时"),
    Item("food_life_meal_max_h", "room", "饭菜的保质期（最长）", "和最短一样就是固定的保质期。", "float", 24.0, 0.5, 720.0, 0.5, "小时"),
    Item("room_render_quality", "room", "渲染精度", "书桌视角窗外动画（天空、云、雨雪、玻璃上的雨滴）画得多精细、多流畅。高 = 按屏幕清晰度画（最多 1.5 倍）、每秒 30 帧；中 = 1 倍、24 帧；低 = 0.75 倍、15 帧，最省电。房间觉得卡就往低调。",
         "select", "high", choices=[("high", "高"), ("mid", "中"), ("low", "低")], section="画面"),
)

# ───────────────────────── 连发和等待（见 app/burst.py） ─────────────────────────
_add(
    Item("burst_enabled", "burst", "等我连发完再回", "关掉后每条消息都马上单独回（不等、不插话、不合并）。", "bool", True, section="等多久"),
    Item("burst_base_s", "burst", "收到消息先等几秒", "这段时间里我接着发的，会和它算成一轮一起回。", "float", 2.0, 0.0, 30.0, 0.5, "秒"),
    Item("burst_busy_window_mult", "burst", "她忙的时候等的时间乘", "出门、打游戏、追剧、写东西这类忙碌状态：像没在盯着手机。", "float", 2.0, 1.0, 10.0, 0.1, "×"),
    Item("burst_typing_fresh_s", "burst", "我多久没按键就不算在打字", "前端每 0.7 秒报一次「正在输入」；超过这么久没收到就当我停了。", "float", 2.5, 0.5, 10.0, 0.1, "秒"),
    Item("burst_idle_after_s", "burst", "我停下来多久算打完了", "注意到我在打字时：我停手超过这么久，她就一起回。", "float", 2.0, 0.5, 30.0, 0.5, "秒"),
    Item("burst_max_wait_s", "burst", "最长等多久", "从这一轮第一条算起；等到这么久我还在打，就先回已经发出来的。", "float", 15.0, 2.0, 120.0, 1.0, "秒"),
    Item("burst_p_notice", "burst", "她注意到我在打字的概率", "注意到：等我打完（或者先插一句）；没注意到：窗口一到就回已经发的，后面的算新的一轮。", "float", 0.7, 0.0, 1.0, 0.05, "0~1", section="注意到我在打字"),
    Item("burst_k_idle", "burst", "闲着的时候乘", "喝茶、发呆、赖床、看雨、听歌、刷视频……：更容易注意到、更爱插话。", "float", 1.2, 0.0, 3.0, 0.05, "×"),
    Item("burst_k_normal", "burst", "一般的时候乘", "", "float", 1.0, 0.0, 3.0, 0.05, "×"),
    Item("burst_k_busy", "burst", "忙的时候乘", "出门、打游戏、追剧、写东西、看书、做饭……：更不容易注意到、更少插话。", "float", 0.5, 0.0, 3.0, 0.05, "×"),
    Item("burst_p_interject", "burst", "注意到以后先插一句的概率", "只回应我已经发出来的，一两句；否则等我打完一起回。也按上面三档乘。", "float", 0.3, 0.0, 1.0, 0.05, "0~1"),
    Item("burst_p_quote", "burst", "一轮两条时引用回复的概率", "这一轮可以引用：她可以挑一条引用着回（不是必须）；一条的时候不引用。", "float", 0.35, 0.0, 1.0, 0.05, "0~1", section="引用和打断"),
    Item("burst_p_quote_many", "burst", "一轮三条以上时引用回复的概率", "", "float", 0.7, 0.0, 1.0, 0.05, "0~1"),
    Item("burst_p_discard", "burst", "她发到一半我又发了：丢掉没发的重新回的概率", "否则先把手上这句发完，再回新的。已经发出去的不撤回。", "float", 0.5, 0.0, 1.0, 0.05, "0~1"),
    Item("type_k_idle", "burst", "闲着时打字的停顿乘", "她「正在输入…」到下一条出现的时间（本来跟字数挂钩），闲着时快一点。在外面另外再乘「在外面时的停顿倍率」。", "float", 0.8, 0.1, 5.0, 0.05, "×", section="她打字的快慢"),
    Item("type_k_normal", "burst", "一般时打字的停顿乘", "", "float", 1.0, 0.1, 5.0, 0.05, "×"),
    Item("type_pause_min_s", "burst", "条与条之间的停顿：基础最短", "每条之前先随机停一下（最短~最长之间），再加上按字数算的那部分。", "float", 0.5, 0.0, 10.0, 0.1, "秒"),
    Item("type_pause_max_s", "burst", "条与条之间的停顿：基础最长", "", "float", 1.2, 0.0, 10.0, 0.1, "秒"),
    Item("type_per_char_s", "burst", "每个字多停多久", "越长的一条打得越久。", "float", 0.05, 0.0, 1.0, 0.01, "秒"),
    Item("type_len_cap_s", "burst", "按字数多停的上限", "再长也最多多停这么久。", "float", 1.4, 0.0, 30.0, 0.1, "秒"),
    Item("type_k_busy", "burst", "忙的时候打字的停顿乘", "打游戏、追剧、写东西、看书、做饭……：一边忙一边回，慢一点。", "float", 1.4, 0.1, 5.0, 0.05, "×"),
)

# ───────────────────────── 说过的打算（对话意图，见 app/intents.py） ─────────────────────────
_add(
    Item("intent_enabled", "intent", "她说过的打算会影响她之后做的事", "比如她说「待会儿吃可颂」「晚饭不吃了」「等下去看书」，或者答应了你什么：之后按概率真的照做；做不到时记得自己说过、会解释。关掉后只当聊天。", "bool", True, section="遵守的概率"),
    Item("intent_p_casual", "intent", "随口一说的，照做的概率", "", "float", 0.6, 0.0, 1.0, 0.05, "0~1"),
    Item("intent_p_agreed", "intent", "答应了你的，照做的概率", "", "float", 0.85, 0.0, 1.0, 0.05, "0~1"),
    Item("intent_p_firm", "intent", "说得很坚定的，照做的概率", "", "float", 0.95, 0.0, 1.0, 0.05, "0~1"),
    Item("intent_k_bad_mood", "intent", "心情差时照做的概率乘", "", "float", 0.85, 0.0, 1.5, 0.05, "×", section="受什么影响"),
    Item("intent_k_tired", "intent", "很累（精力不到 30%）时乘", "", "float", 0.85, 0.0, 1.5, 0.05, "×"),
    Item("intent_k_rain_out", "intent", "下雨时「出门」类的打算乘", "", "float", 0.75, 0.0, 1.5, 0.05, "×"),
    Item("intent_conflict_mult", "intent", "说了要在家做的事时，出门的事 / 出去吃饭的概率乘", "比如说了在家吃可颂：朋友约饭、出门的事件还是可能发生（发生了就算没做到），但概率乘这个数。", "float", 0.3, 0.0, 1.0, 0.05, "×"),
    Item("intent_p_tell", "intent", "没做到时，主动跟你提一句的概率", "比如「本来要吃可颂的 被朋友拉出去了」。不提的话，你问起时她也会解释。", "float", 0.7, 0.0, 1.0, 0.05, "0~1", section="没做到的时候"),
    Item("intent_p_followup", "intent", "没吃成的东西留到之后再吃的概率", "比如「可颂留着当夜宵」。", "float", 0.4, 0.0, 1.0, 0.05, "0~1"),
    Item("intent_max_days", "intent", "打算最长管几天", "她说的时间范围超过这个就截短；更远的不记。", "float", 3.0, 0.5, 14.0, 0.5, "天"),
)

# ───────────────────────── 月末的信 ─────────────────────────
_add(
    Item("letters_enabled", "letters", "启用月末的信", "每月最后一天晚上、日总结之后，她写一封信给你，存在右侧「信」那一栏里。关掉后不再写。", "bool", True),
    Item("letter_after_summary_min", "letters", "日总结之后多久开始写信（分钟）", "日总结写完后再等这么久。", "int", 2, 0, 120, 1, "分钟"),
    Item("letter_chars_min", "letters", "信大约最少多少字", "只是告诉她大概写多长，太短的会重写。", "int", 300, 50, 5000, 50, "字"),
    Item("letter_chars_max", "letters", "信大约最多多少字", "告诉她大概写到多长为止。", "int", 800, 50, 5000, 50, "字"),
    Item("letter_emoji_max", "letters", "整封信里颜文字 / emoji 最多几处", "信的语气更日常，可以有一点，但不会很多；超出的 emoji 会被去掉。", "int", 3, 0, 20, 1, "处"),
    Item("letter_notify", "letters", "写完后主动发消息告诉你", "比如「写了封信 放在那边了」，不受每小时主动消息上限限制。", "bool", True),
    Item("letter_catchup_months", "letters", "页面没开着时，最多补写几个月的信", "错过了月末那天，下次打开时补写（日期仍是那个月的最后一天）。0 = 不补。第一次使用之前的月份不会补。", "int", 3, 0, 12, 1, "个月"),
)

# ───────────────────────── 通知 ─────────────────────────
_add(
    Item("notify_max_per_hour", "notify", "每小时主动消息上限", "她主动发来的消息（几条算一次）每小时最多这么多次。0 = 不主动发。", "int", 3, 0, 60, 1, "次"),
    Item("out_home_notify", "notify", "出门和回家时告诉你", "她每次出门、回家都会主动发一条消息（模型现写）。不占「每小时主动消息上限」的次数，也无关概率，每次都发。", "bool", True),
)

# ───────────────────────── 世界 ─────────────────────────
_add(
    Item("real_place_words", "world", "现实地名检查表（补充）", "虚构世界的角色才用：这里的词（现实里的国家、城市等）出现在她的批注、菜名之类里会被拦下重写。现实世界版的角色不检查。"
         "程序自带一批，这里是额外加的。世界总纲和条目放在 data/world/ 里。", "list", []),
    # 小本子（app/notebook.py）：章节和各章节的准确度在上面的「小本子」面板里改
    Item("notebook_write", "world", "她自己写小本子", "第一次去的店 / 地方建条目、写印象；你在聊天里讲世界上的事记进「她说的」；每晚日总结后把当天的见闻整理进本子（每天多一次模型调用）。"
         "关掉后她不再写（买东西的店和价格照样记）。", "bool", True, section="小本子"),
    Item("notebook_entry_max", "world", "每条最多多少字", "她写的正文超过这么多字时，晚上整理时会自己精简；没做到就截到句子结尾。你自己改的不受限制。", "int", 300, 50, 2000, 10, "字"),
    Item("notebook_night_n", "world", "每晚整理最多写几条", "晚上整理本子时最多新建 / 合并几条（精简太长的条目不算在内）。", "int", 6, 1, 20, 1, "条"),
    _prob("notebook_old_shop_p", "world", "去老店的概率", "吃饭 / 买东西时，这一带有去过的店：这么大概率去老店（按去过的次数挑），否则试一家新店。去老店时她会想起上次买过什么、对这家店的印象。", 0.6),
    Item("notebook_chat_n", "world", "聊天时最多带几条", "聊天时按你这条消息和最近三轮（你的和她的）的关键词检索小本子，带上最相关的几条，每条截到上面的字数。初始抄写审完之前，世界设定的条目也照旧带上。", "int", 4, 0, 12, 1, "条"),
    Item("notebook_life_n", "world", "她自己生活时最多带几条", "吃饭、消费、出门、事件这些生成前，按这次的地点（连同上级）/ 店名 / 类别检索小本子。写作不带。", "int", 3, 0, 12, 1, "条"),
    Item("notebook_weekly", "world", "每周整理本子", "合并重复、精简过长、把过时的挪进修改历史；只动没锁定的，要删的只标出来等你决定（小本子页面里看）。每周 1~3 次模型调用。", "bool", True),
    Item("notebook_weekly_day", "world", "每周哪天整理", "每周在这一天整理一次本子（上一项开着时）。", "select", "6",
         choices=[("0", "周一"), ("1", "周二"), ("2", "周三"), ("3", "周四"), ("4", "周五"), ("5", "周六"), ("6", "周日")]),
    Item("notebook_weekly_time", "world", "整理的时间（时:分）", "到这个时间之后整理（晚上 9 点的日总结之后）。", "time", "21:30"),
    Item("notebook_notice", "world", "她察觉到你的修改", "关着：你改了小本子，她就当自己一直是这么认为的。开着：有一定概率在下次聊天时自然地提一句「咦 我好像一直记错了」。",
         "bool", False),
    _prob("notebook_notice_p", "world", "察觉的概率", "上一项开着时才有用。", 0.10),
)

# ───────────────────────── 模型与记忆 ─────────────────────────
_add(
    Item("api_base", "model", "API Base", "任何 OpenAI 兼容的接口地址。", "text", config.DEFAULT_SETTINGS.get("api_base", "")),
    Item("api_key", "model", "API Key", "只存在你的电脑上。留空 = 不改；清除后进入离线演示模式。", "secret", ""),
    Item("model", "model", "模型名", "比如 deepseek-chat。", "text", config.DEFAULT_SETTINGS.get("model", "")),
    Item("max_tokens", "model", "最大长度", "一次回复最多多少 token，0 = 不限制。", "int", 2048, 0, 32000, 256),
    Item("recent_limit", "model", "短期上下文条数", "每轮对话拼进上下文里最近多少条聊天记录。", "int", 24, 1, 200, 1, "条"),
    Item("memory_limit", "model", "注入的长期记忆条数", "每轮最多带进来几条相关的长期记忆。", "int", 12, 0, 100, 1, "条"),
    Item("context_char_budget", "model", "短期上下文的字数预算", "超了就从最旧的开始丢。", "int", 12000, 1000, 200000, 1000, "字"),
    Item("context_total_budget", "model", "整份上下文的字数上限", "人设 + 设定 + 状态 + 记忆 + 聊天记录加起来。超了先裁记忆、小本子、世界条目，还超再从最旧的聊天记录开始丢；人设、总纲、状态、打算不裁。0 = 不限。", "int", 30000, 0, 400000, 1000, "字"),
    Item("world_overview_cap", "world", "每一类总纲最多常驻多少字", "超过的部分截掉（总纲每轮都带，别太长）。", "int", 1800, 200, 20000, 100, "字"),
    Item("world_entry_cap", "world", "每个世界设定条目最多带多少字", "", "int", 1000, 100, 20000, 100, "字"),
    Item("world_max_entries", "world", "一次最多带几个世界设定条目", "聊天里提到关键词才带（小本子审完以后聊天改读小本子）。", "int", 3, 0, 20, 1, "条"),
    Item("context_keep_recent", "model", "聊天记录至少留几条", "总预算再紧，最近这几条也留着。", "int", 6, 0, 50, 1, "条"),
    Item("usage_enabled", "model", "统计运行开销", "每次调模型记下用途、耗时、token，在本分区最上面的表里看最近几轮。", "bool", True, section="运行开销"),
    Item("usage_stream_tokens", "model", "聊天回复也统计 token", "流式回复要接口在最后附上用量（DeepSeek、OpenAI 都支持）；个别接口不认这个参数会报错，关掉就行（只统计次数和耗时）。", "bool", True),
    Item("usage_keep_days", "model", "开销记录留几天", "", "int", 7, 1, 90, 1, "天"),
    Item("usage_rounds_shown", "model", "表里显示最近几轮", "", "int", 10, 1, 100, 1, "轮"),
    Item("enable_tools", "model", "允许她自己检索历史记录", "让模型可以主动翻聊天记录、读文档。", "bool", True),
    Item("enable_auto_memory", "model", "自动整理长期记忆", "每隔一阵从聊天里整理出记忆条目。", "bool", True),
    Item("auto_memory_every", "model", "每多少条消息整理一次", "", "int", 20, 5, 200, 5, "条"),
    Item("memory_extract_every", "model", "每几轮对话提取一次记忆", "（不再起作用：记忆和「说过的打算」合在一起，每轮都提取。）", "int", 1, 1, 50, 1, "轮", hidden=True),
)

# ───────────────────────── 取值 ─────────────────────────
_NO_DEFAULT_OVERRIDES = {"api_key"}


def _typed(it: Item, raw: Any) -> Any:
    if it.kind == "bool":
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    if it.kind == "int":
        return int(float(raw))
    if it.kind == "float":
        return float(raw)
    if it.kind == "select":
        v = "" if raw is None else str(raw)
        if v not in {c[0] for c in it.choices}:
            raise ValueError("不是可选的值")
        return v
    if it.kind == "time":
        m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", "" if raw is None else str(raw))
        if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
            raise ValueError("时间格式应为 时:分，比如 11:30")
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    if it.kind == "table":
        data = raw
        if isinstance(raw, str):
            try:
                data = json.loads(raw or "[]")
            except json.JSONDecodeError:
                return it.default
        if not isinstance(data, list):
            raise ValueError("格式不对")
        rows = []
        for row in data:
            if not isinstance(row, dict):
                raise ValueError("格式不对")
            clean = {}
            for key, label, lo, hi, _step in it.columns:
                v = float(row.get(key, 0))
                if not lo <= v <= hi:
                    raise ValueError(f"「{label}」要在 {lo:g}~{hi:g} 之间")
                clean[key] = v
            rows.append(clean)
        if len(rows) != len(it.default):
            raise ValueError(f"应该有 {len(it.default)} 行")
        return rows
    if it.kind == "list":
        if isinstance(raw, list):
            return [str(x).strip() for x in raw if str(x).strip()]
        try:
            data = json.loads(raw or "[]")
        except (json.JSONDecodeError, TypeError):
            return []
        return [str(x).strip() for x in data if str(x).strip()] if isinstance(data, list) else []
    return "" if raw is None else str(raw)


def _stored(it: Item, settings: dict[str, str] | None = None) -> Any:
    """库里存着的值（没有就 None）。"""
    if it.store == "banned_file":
        from . import guards

        return guards.load_banned()
    s = settings if settings is not None else db.get_settings()
    raw = s.get(it.key)
    # config.DEFAULT_SETTINGS 会把一些键预先填进 settings（比如 system_prompt）：当作「存着的」没问题
    return None if raw is None else raw


def _character_defaults() -> None:
    """角色配置里的「settings」（这个角色调好的数值）当作设置页的默认值。坏的值跳过、照用代码里的默认。
    测试设 CHAT_CODE_DEFAULTS=1 时不套（测的是代码里的默认值和逻辑）。"""
    if os.getenv("CHAT_CODE_DEFAULTS"):
        return
    for k, v in (C.get("settings") or {}).items():
        it = BY_KEY.get(k)
        if it is None:
            continue
        try:
            it.default = _typed(it, v)
        except (TypeError, ValueError):
            continue


_character_defaults()


def get(key: str, settings: dict[str, str] | None = None) -> Any:
    it = BY_KEY[key]
    raw = _stored(it, settings)
    if raw is None or (raw == "" and it.kind in ("int", "float", "bool")):
        return it.default
    try:
        return _typed(it, raw)
    except (TypeError, ValueError):
        return it.default


def num(key: str, settings: dict[str, str] | None = None) -> float:
    return float(get(key, settings))


def flag(key: str, settings: dict[str, str] | None = None) -> bool:
    return bool(get(key, settings))


# ───────────────────────── 给设置页 ─────────────────────────
def _view(it: Item, settings: dict[str, str]) -> dict[str, Any]:
    value = get(it.key, settings)
    out: dict[str, Any] = {
        "key": it.key, "label": it.label, "hint": it.hint, "kind": it.kind, "section": it.section,
        "default": None if it.kind == "secret" else it.default, "min": it.min, "max": it.max, "step": it.step, "unit": it.unit,
        "choices": [{"value": v, "label": lb} for v, lb in it.choices] if it.kind == "select" else None,
        "columns": [{"key": k, "label": lb, "min": lo, "max": hi, "step": st} for k, lb, lo, hi, st in it.columns] if it.kind == "table" else None,
        "row_labels": it.row_labels or None,
        "modified": (value != it.default) if it.kind != "secret" else bool(settings.get("api_key")),
    }
    if it.kind == "secret":
        out["value"] = ""
        out["secret_set"] = bool(settings.get(it.key))
        out["secret_mask"] = config.API_KEY_MASK if settings.get(it.key) else ""
    else:
        out["value"] = value
    return out


def view() -> dict[str, Any]:
    settings = db.get_settings()
    groups = []
    for gid, label in GROUPS:
        if gid in HIDDEN_GROUPS:
            continue
        if gid == "mood":                                    # 只放几项，按自己的小节排
            items = []
            for sec, keys in MOOD_SECTIONS:
                items += [dict(_view(BY_KEY[k], settings), section=sec if i == 0 else "") for i, k in enumerate(keys)]
        else:
            items = [_view(it, settings) for it in ITEMS if it.group == gid and not it.hidden]
        if items or gid in CUSTOM_GROUPS:
            groups.append({"id": gid, "label": label, "custom": gid in CUSTOM_GROUPS and not items,
                           "panel": gid if gid in PANELS else None, "items": items})
    return {"groups": groups}


def group_view(gid: str) -> list[dict[str, Any]]:
    """某一个分区的全部设置项（包括设置页里不显示的）。"""
    settings = db.get_settings()
    return [_view(it, settings) for it in ITEMS if it.group == gid]


def validate(it: Item, raw: Any) -> Any:
    """校验并转成要存的字符串；不合法抛 ValueError。"""
    if it.kind == "secret":
        return str(raw or "").strip()
    v = _typed(it, raw)
    if it.kind in ("int", "float"):
        if it.min is not None and v < it.min:
            raise ValueError(f"不能小于 {it.min:g}")
        if it.max is not None and v > it.max:
            raise ValueError(f"不能大于 {it.max:g}")
    if it.kind == "text" and it.key in ("persona_name",):
        v = v.strip() or it.default
    if it.key in ("room_categories", "room_areas"):
        v = list(dict.fromkeys(v))
        if not v:
            raise ValueError("至少要留一项")
        if it.key == "room_categories":
            names = [x.split(None, 1)[0] for x in v]
            if len(set(names)) != len(names):
                raise ValueError("分类名字不能重复")
        elif any(" " in x for x in v):
            raise ValueError("区域名字里不要有空格")
    return v


def _to_store(it: Item, v: Any) -> str:
    if it.kind == "bool":
        return "1" if v else "0"
    if it.kind in ("list", "table"):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def apply(values: dict[str, Any]) -> tuple[list[str], dict[str, str]]:
    """保存一批设置。返回 (成功的键, {键: 错误})。"""
    saved: list[str] = []
    errors: dict[str, str] = {}
    patch: dict[str, str] = {}
    for key, raw in values.items():
        it = BY_KEY.get(key)
        if it is None:
            errors[key] = "没有这个设置项"
            continue
        try:
            if it.kind == "secret":
                if raw is None:                       # 显式 null = 清除
                    patch[key] = ""
                elif str(raw).strip() and str(raw).strip() != config.API_KEY_MASK:
                    patch[key] = str(raw).strip()
                else:
                    continue                          # 空 / 掩码 = 不改
            else:
                v = validate(it, raw)
                if it.store == "banned_file":
                    from . import guards

                    guards.save_banned(v)
                else:
                    patch[key] = _to_store(it, v)
            saved.append(key)
        except (TypeError, ValueError) as exc:
            errors[key] = str(exc) or "值不合法"
    if patch:
        db.save_settings(patch)
    return saved, errors


def reset(keys: list[str]) -> list[str]:
    done: list[str] = []
    patch: dict[str, str] = {}
    for key in keys:
        it = BY_KEY.get(key)
        if it is None or it.kind == "secret":
            continue
        if it.store == "banned_file":
            from . import guards

            guards.save_banned([])
        else:
            patch[key] = _to_store(it, it.default)
        done.append(key)
    if patch:
        db.save_settings(patch)
    return done


# 状态的权重 / 时长（给 life.py 用）
def state_weight(key: str) -> float | None:
    return float(get(f"state_w_{key}")) if f"state_w_{key}" in BY_KEY else None


def state_duration(key: str) -> tuple[int, int] | None:
    if f"state_dur_{key}_min" not in BY_KEY:
        return None
    lo, hi = int(get(f"state_dur_{key}_min")), int(get(f"state_dur_{key}_max"))
    return (min(lo, hi) * 60, max(lo, hi) * 60)


def state_window(key: str) -> tuple[int, int]:
    return int(get(f"state_from_{key}")), int(get(f"state_to_{key}"))


def state_cond(key: str) -> dict[str, object]:
    """某个状态现在配置的条件。"""
    return {"hours": state_window(key), "weather": get(f"state_weather_{key}"),
            "spends": bool(get(f"state_spends_{key}")), "outside": bool(get(f"state_outside_{key}"))}


def minutes_of(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)
