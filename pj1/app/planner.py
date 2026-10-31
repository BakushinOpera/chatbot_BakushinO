"""日历背后让模型干活的三件事：写每天的心情、催没做完的任务、对日历改动随口回应。

都用非流式的 llm.complete()，因为它们是副作用型的小生成，不需要打字机效果。
结果统一拆成气泡（复用 reply.ReplySplitter），存进 messages 表就是普通的 AI 消息。
"""

from __future__ import annotations

import json
import random
from datetime import date, timedelta
from typing import Any

from . import db, guards, llm, reply
from . import character as C
from .workdays import pending_for

ROLE_LABEL = {"user": "用户", "assistant": "你"}


def split_bubbles(text: str) -> list[str]:
    """把一段文本按空行切成气泡（和聊天里用的是同一套规则）。"""
    sp = reply.ReplySplitter()
    parts: list[str] = []
    cur = ""
    for kind, chunk in [a for c in [text] for a in sp.feed(c)] + sp.flush():
        if kind == "text":
            cur += chunk
        else:
            if cur.strip():
                parts.append(cur.strip())
            cur = ""
    if cur.strip():
        parts.append(cur.strip())
    return parts


def _persona_name(settings: dict[str, Any]) -> str:
    return C.display_name(settings)


# --------------------------------------------------------------------------
# 1. 每天的心情：先随机抽一种情绪，再让模型按这种情绪写一句话
# --------------------------------------------------------------------------
MOODS = [
    "开心", "忧郁", "孤单", "淡然", "无所事事", "雀跃", "烦躁", "犯困", "温柔", "好奇",
    "委屈", "平静", "倔强", "怀旧", "焦虑", "懒洋洋", "小得意", "空落落", "兴致勃勃", "心不在焉",
    "惬意", "期待", "安心", "元气满满", "专注", "调皮", "害羞", "想念", "暖洋洋", "跃跃欲试",
    "充实", "心软", "嘴馋", "放空", "感激", "闷闷的", "小纠结", "恍惚",
]
# 每日抽心情的权重：轻松 / 开心的多一些，低落的少一些（焦虑最少）。没写的按 1。
MOOD_WEIGHT = {
    "开心": 3, "平静": 3, "惬意": 3, "温柔": 2.5, "好奇": 2.5, "期待": 2.5, "安心": 2.5, "雀跃": 2, "淡然": 2,
    "元气满满": 2, "专注": 2, "暖洋洋": 2, "充实": 2, "兴致勃勃": 2, "小得意": 2, "调皮": 2, "放空": 2, "嘴馋": 2,
    "懒洋洋": 1.5, "无所事事": 1.5, "跃跃欲试": 1.5, "心软": 1.5, "感激": 1.5, "想念": 1.5, "害羞": 1.2, "怀旧": 1.2, "倔强": 1.2,
    "犯困": 1, "心不在焉": 0.8, "小纠结": 0.8, "恍惚": 0.6, "闷闷的": 0.6, "忧郁": 0.6, "孤单": 0.6, "烦躁": 0.6,
    "委屈": 0.5, "空落落": 0.5, "焦虑": 0.3,
}
# 事件才会带来的情绪（不在每日随机池里）：读到精彩处、写成 / 写崩了、读完一本书……
EVENT_MOODS = ["兴奋", "怅然", "沮丧", "满足", "感动"]

MOOD_PROMPT = """你在帮一个 AI 角色回顾某一天。

这一天它的心情已经定好了，是「{mood}」。下面会给你这一天它和用户的聊天记录（可能很少，甚至没有）。

请输出一个 JSON 对象，不要输出别的任何东西：
{{"line": "一句话，25 字以内"}}

要求：
- line 是第一人称，像随手写的日记，要真的带着「{mood}」这种情绪，别写成中性的描述。
- 可以顺带提到当天聊天里的一点小事，但不要总结对话，不要提到"用户"这两个字。
- 如果这天几乎没聊，就写这种情绪下的安静状态，别硬编内容。
- 不要 emoji。"""


def pick_mood(day: str) -> str:
    """按 MOOD_WEIGHT 抽一种情绪；避开前一天的，免得连着两天一个样。节日当天不抽低落的（心情有下限）。"""
    from . import festivals

    prev = db.get_mood((date.fromisoformat(day) - timedelta(days=1)).isoformat())
    pool = [m for m in MOODS if not prev or m != prev["mood"]]
    if festivals.on_date(day):
        pool = [m for m in pool if m not in festivals.NEGATIVE_MOODS] or ["开心"]
    return random.choices(pool, weights=[MOOD_WEIGHT.get(m, 1) for m in pool])[0]


def ensure_mood_word(day: str) -> dict[str, Any]:
    """这一天还没有心情就随机定一个（line 先留空，等模型补）。"""
    existing = db.get_mood(day)
    if existing:
        return existing
    return db.upsert_mood(day, pick_mood(day), "")


async def make_moods(settings: dict[str, Any], days: list[str]) -> dict[str, dict[str, Any]]:
    """给缺失的日子定情绪并写一句话，已经有一句话的直接返回。"""
    out: dict[str, dict[str, Any]] = {}
    today = date.today().isoformat()
    for day in days:
        if day > today:
            continue  # 未来的日子还没过，不编
        row = ensure_mood_word(day)
        if row["line"] or row.get("kind") == "summary" or (day == today and row.get("base") is not None):
            out[day] = row          # v8：今天那一句是早上「今天心情的原因」（life._reason_job 写），这里不再另写
            continue
        all_events = db.events_on(day)
        events = db.events_on(day, shared_only=True)           # 没告诉我的事不写进总结
        ev_moods = [e["mood"] for e in all_events if e.get("mood")]       # 但它们照样影响心情
        if ev_moods and row.get("v") is None:      # 旧数据（没有心情数值）：以最后那件事的情绪为准
            row = db.upsert_mood(day, ev_moods[-1], "")
        rows = db.archive_on(day, 80)
        transcript = "\n".join(
            f"{ROLE_LABEL.get(r['role'], r['role'])}：{r['content'][:300]}" for r in rows
        ) or "（这一天没有聊天记录）"
        try:
            raw = await llm.complete(
                settings,
                [
                    {"role": "system", "content": MOOD_PROMPT.format(mood=row["mood"])},
                    {"role": "user", "content": (
                        f"日期：{day}\n\n这一天她经历的事：\n"
                        + ("\n".join(f"- {e['created_at'][11:16]} {e['text']}" for e in events) or "（没什么特别的）")
                        + f"\n\n聊天记录：\n{transcript}")},
                ],
                temperature=0.9,
            )
            line = str(_json_object(raw).get("line") or "").strip()[:50]
        except Exception:  # noqa: BLE001 —— 生成失败不该影响日历打开
            line = ""
        if not line:
            out[day] = row          # 情绪词先显示，句子下次再补
            continue
        out[day] = db.upsert_mood(day, row["mood"], line)
    return out


SUMMARY_PROMPT = """你是「{name}」，现在是晚上九点，你要总结一下今天自己的状态变化和经历，睡前写给自己。

下面是今天发生的事（按时间）、你一早的心情，以及今天和对方的聊天。请输出 JSON，不要别的：
{{"mood": "两到四个汉字，总结之后你今晚的状态", "line": "一句话，30 字以内，第一人称，像随手写的日记", "recap": "3~5 句话，把今天的起伏和经历串起来，第一人称，口语，不要列清单"}}

要求：
- 只写今天真的发生过的事（下面列出的事和聊天），不要编造；
- mood 要反映一天下来的变化，不一定等于早上的心情；
- 不要 emoji。"""


def local_summary(day: str, baseline: str) -> dict[str, str]:
    """没有模型可用时的总结：直接用当天的事件拼，不编造。"""
    all_events = db.events_on(day)
    events = db.events_on(day, shared_only=True)
    moods = [e["mood"] for e in all_events if e.get("mood")]
    mood = moods[-1] if moods else baseline
    texts = [e["text"] for e in events][-4:]
    return {
        "mood": mood,
        "line": (texts[-1] if texts else "今天过得平平淡淡。")[:40],
        "recap": "；".join(texts) if texts else "今天没什么特别的事，平平淡淡地过去了。",
    }


def _intents_text(day: str) -> str:
    """这一天的小打算（只有当天的还在心情状态里）：「打算 A（做了）；打算 B（没来得及）」。"""
    from . import mood as M

    mst = M.load()
    its = mst.get("intents") or []
    if mst.get("day") != day or not its:
        return ""
    return "；".join(f"{it['text']}（{'做了' if it.get('done') else '没来得及'}）" for it in its)


async def make_summary(settings: dict[str, Any], day: str) -> dict[str, Any]:
    """晚上九点的一天总结：存进 moods（kind=summary），日历上显示的就是它。v9：带上今天的小打算做了没。"""
    base = ensure_mood_word(day)
    result = local_summary(day, base["mood"])
    plans = _intents_text(day)
    if plans:
        result["recap"] = (result["recap"].rstrip("。") + "。" if result["recap"] else "") + f"今天的打算：{plans}。"
    if not llm.is_echo(settings):
        events = db.events_on(day, shared_only=True)
        rows = db.archive_on(day, 60)
        transcript = "\n".join(f"{ROLE_LABEL.get(r['role'], r['role'])}：{r['content'][:160]}" for r in rows) or "（今天没怎么聊）"
        try:
            raw = await llm.complete(
                settings,
                [
                    {"role": "system", "content": SUMMARY_PROMPT.format(name=_persona_name(settings))},
                    {"role": "user", "content": (
                        f"一早的心情：{base['mood']}\n\n" + (f"今天的小打算：{plans}（recap 里顺带说一句做了没）\n\n" if plans else "") + "今天发生的事：\n"
                        + ("\n".join(f"- {e['created_at'][11:16]} {e['text']}" for e in events) or "（没什么特别的）")
                        + f"\n\n聊天：\n{transcript}")},
                ],
                temperature=0.8,
            )
            data = _json_object(raw)
            mood = guards.strip_exclaim(str(data.get("mood") or "").strip())[:8]
            line = guards.strip_exclaim(str(data.get("line") or "").strip())[:50]
            recap = guards.strip_exclaim(str(data.get("recap") or "").strip())[:400]
            if mood and line:
                result = {"mood": mood, "line": line, "recap": recap}
        except Exception:  # noqa: BLE001 —— 失败就用本地拼的，别让总结丢了
            pass
    from . import festivals

    return db.upsert_mood(day, festivals.floor_mood(result["mood"], day), result["line"], kind="summary", recap=result["recap"])


def _json_object(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[-1] if "\n" in text else text
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    return {}


# --------------------------------------------------------------------------
# 2. 拖太久的任务，主动提醒
# --------------------------------------------------------------------------
REMIND_PROMPT = """你是「{persona}」，正在跟用户日常聊天。

下面是她拖着没做的事。用 1~2 条短消息提醒她一下。

要求：
- 像朋友随口一提，不要像待办软件的通知。
- 不要列清单，不要用「-」或者数字编号。
- 不要每条都提，挑最明显的那个说，或者合起来说一句。
- 一条消息之间空一行。每条不超过 30 字。
- 不要 emoji 轰炸，最多一个。"""


async def remind_overdue(
    settings: dict[str, Any], tasks: list[dict[str, Any]], today: str
) -> list[str]:
    base = date.fromisoformat(today)
    lines = []
    for t in tasks:
        try:
            pending = pending_for(t.get("priority"), t["day"], base)
        except ValueError:
            pending = 0
        lines.append(f"- {t['title']}（{t['day']} 起，拖了 {pending} 天，阈值 {t['remind_after_days']} 天）")
    raw = await llm.complete(
        settings,
        [
            {"role": "system", "content": REMIND_PROMPT.format(persona=_persona_name(settings))},
            {"role": "user", "content": "拖着的任务：\n" + "\n".join(lines)},
        ],
        temperature=0.85,
    )
    return split_bubbles(raw)
