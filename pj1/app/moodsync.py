"""心情同步：你跟她分享自己的心情时，她有一定概率变成类似的心情（比如你说你很开心，她也可能跟着开心起来）。

省钱的做法：先用关键词筛（零成本）；只有出现「开心 / 难过 / 烦 / 累……」这类字眼的消息，才多调用一次模型，
用很短的提示词确认「这句话是不是在说你自己的心情、是哪一类」。没有字眼的闲聊不多花一分钱。

* 确认你确实在分享自己的心情 → 掷一次骰子（概率 ``mood_sync_prob``）→ 中了就从「这类心情对应的几个词」里挑一个，
  用 ``life.apply_mood`` 按这个词推她一下（倍率 ``mood_sync_scale``），之后按真实时间慢慢回落（见 mood.py）。
* 两次判断之间至少隔 ``mood_sync_cooldown_min`` 分钟；设置里 ``mood_sync`` 可以整个关掉；离线演示模式（没配 API）不判断。
* 节日当天心情有下限的规则照旧（见 ``life.live_mood``）。
"""

from __future__ import annotations

import logging
import random
import re
import time
from typing import Any

from . import llm, registry
from .planner import _json_object

log = logging.getLogger(__name__)

# 你说的心情分成这几类 → 她可能变成的心情词（都在 planner.MOODS / EVENT_MOODS 里）
LABELS: dict[str, list[str]] = {
    "开心": ["开心", "雀跃", "小得意", "兴致勃勃", "元气满满"],
    "兴奋": ["兴奋", "雀跃", "兴致勃勃", "跃跃欲试"],
    "期待": ["期待", "跃跃欲试", "雀跃"],
    "满足": ["满足", "温柔", "平静", "开心", "充实"],
    "感动": ["感动", "心软", "温柔", "感激"],
    "放松": ["惬意", "暖洋洋", "懒洋洋", "安心"],
    "难过": ["忧郁", "委屈", "空落落", "心软"],
    "孤单": ["孤单", "想念", "怀旧"],
    "烦躁": ["烦躁", "倔强", "闷闷的"],
    # 你焦虑时她多半是替你挂着心，不是跟着一起慌（「焦虑」本身留得很少）
    "焦虑": ["想念", "小纠结", "心不在焉", "心软"],
    "疲惫": ["犯困", "懒洋洋", "放空"],
    "平静": ["平静", "淡然", "安心"],
    "无聊": ["无所事事", "懒洋洋", "放空", "嘴馋"],
}

# 关键词筛：只要像是在说心情就过（宁可多放几条给模型确认，也别漏）
KEYWORD = re.compile(
    "开心|高兴|快乐|兴奋|激动|太好了|超开心|好开心|幸福|满足|感动|治愈|放松|舒服|惬意|期待|等不及|"
    "难过|伤心|想哭|崩溃|郁闷|失落|沮丧|低落|丧|心情不好|不开心|委屈|"
    "孤独|孤单|寂寞|"
    "烦|生气|气死|愤怒|火大|不爽|"
    "焦虑|紧张|担心|害怕|压力|慌|"
    "好累|很累|太累|累死|疲惫|困死|好困|没精神|"
    "无聊|没意思|心情"
)
_MAX_LEN = 400
_last = 0.0                                      # 上一次判断的时刻（省钱用的冷却）

PROMPT = """判断下面这句话里，说话的人（用户）是不是在说「他自己此刻的心情 / 感受」，如果是，属于哪一类。
类别只能从这些里选一个：{labels}。
不是在说自己的心情（比如在聊别人、转述、提问、说天气、说事情经过但没提感受）就选「无」。
只输出 JSON：{{"mood": "类别或无"}}"""


def candidates(label: str) -> list[str]:
    return list(LABELS.get(label) or [])


def looks_emotional(text: str) -> bool:
    t = (text or "").strip()
    return 2 <= len(t) <= _MAX_LEN and bool(KEYWORD.search(t))


async def classify(settings: dict[str, Any], text: str) -> str:
    """让模型判断这句话说的是哪类心情；不是在说自己的心情返回「无」。"""
    messages = [{"role": "system", "content": PROMPT.format(labels="、".join(LABELS))}, {"role": "user", "content": text.strip()[:_MAX_LEN]}]
    raw = await llm.complete(settings, messages, temperature=0.1)
    label = str(_json_object(raw).get("mood") or "无").strip()
    return label if label in LABELS else "无"


async def process(settings: dict[str, Any], text: str, rng: random.Random | None = None, now: float | None = None) -> dict[str, Any] | None:
    """这条用户消息要不要让她被感染。返回 {"label", "word", "minutes"}（她的心情已经改了），或 None。"""
    global _last
    if not registry.flag("mood_sync") or not looks_emotional(text) or llm.is_echo(settings):
        return None
    now = now if now is not None else time.time()
    cool = registry.num("mood_sync_cooldown_min") * 60
    if cool and now - _last < cool:
        return None
    _last = now                                               # 先记下：判断失败了也别连着重试
    try:
        label = await classify(settings, text)
    except Exception as exc:  # noqa: BLE001 —— 判断不出来就算了，不影响聊天
        log.info("心情判断失败：%s: %s", type(exc).__name__, exc)
        return None
    if label == "无":
        _last = 0.0                                           # 没在说自己的心情：不算一次判断
        return None
    rng = rng or random
    if rng.random() >= registry.num("mood_sync_prob"):
        return None
    from . import life                                        # 避免循环导入

    cur, _why = life.live_mood()
    pool = [w for w in candidates(label) if w != cur] or candidates(label)
    word = rng.choice(pool)
    # v8：按这个词推她一下（倍率 mood_sync_scale），之后按真实时间慢慢回落
    life.apply_mood(word, f"听你说你{label}，她也被带着{word}起来", src="moodsync", scale=registry.num("mood_sync_scale"))
    return {"label": label, "word": word}


def reset_cooldown() -> None:
    global _last
    _last = 0.0
