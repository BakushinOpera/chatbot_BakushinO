"""说话风格学习。

两路来源，都会注入到系统侧上下文（很少变化，不影响提示词缓存）：

1. **风格笔记** —— 模型读过往*全部*聊天里 AI 的回复（最近的 + 整个历史里均匀抽样），
   总结出的口吻要点，存在 settings.style_notes（可以在界面里直接改）。
2. **用户亲手改过的回答** —— 你改一条 AI 回复，就留下一对「原回复 → 你改成的样子」，
   这是最标准的口吻，最近几条会当范例原样给模型看，权重最高。
"""

from __future__ import annotations

import logging
from typing import Any

from . import db, llm

log = logging.getLogger(__name__)

LEARN_EVERY = 30        # AI 回复每新增这么多条，重新学一次
MAX_NOTES_CHARS = 700

LEARN_PROMPT = """你在帮一个 AI 角色总结它「怎么说话」。

下面是它在过往聊天里说过的话（按时间排序，覆盖整个聊天历史），以及用户亲手改过的回答
（「原回复 → 用户改成的样子」，这是最标准的口吻，权重最高）。

请归纳出它的说话风格，写成 6~10 条要点，每条一行，以「- 」开头，覆盖：
句子长短、语气和态度、常用句式和口头禅、标点和 emoji 习惯、怎么回应用户的情绪、
喜欢什么样的表达、不会说什么样的话。

要求：
- 写风格，不写内容：不要复述具体聊过的事，不要出现用户的隐私信息。
- 用「你」称呼这个角色，例如「你习惯用短句，一般一两句话说完」。
- 如果给了「已有的风格笔记」，在它的基础上修正、补充，别推翻重来；与新样本矛盾的地方以新样本为准。
- 总共不超过 {limit} 字，只输出要点本身。"""


def _clip(text: str, n: int = 160) -> str:
    text = (text or "").strip().replace("\n", " / ")
    return text if len(text) <= n else text[:n] + "…"


def edits_block(limit: int = 5) -> str:
    edits = db.recent_edits(limit)
    if not edits:
        return ""
    lines = [
        "【用户亲手改过你的回答】下面每一对是：你原来的回复 → 用户改成的样子。"
        "这是她最想要的口吻，请照着这个调子说话（学语气和方式，别照搬具体内容）："
    ]
    for e in reversed(edits):   # 老的在前
        if e.get("user_text"):
            lines.append(f"· 她说：{_clip(e['user_text'], 80)}")
        lines.append(f"  你原来说：{_clip(e['before'])}")
        lines.append(f"  她改成：{_clip(e['after'])}")
    return "\n".join(lines)


def style_block(settings: dict[str, Any]) -> str:
    notes = (settings.get("style_notes") or "").strip()
    parts = []
    if notes:
        parts.append(
            "【你的说话风格】这是从你过往所有聊天里总结出来的口吻，保持一致，别突然换了个人：\n" + notes
        )
    ed = edits_block()
    if ed:
        parts.append(ed)
    return "\n\n".join(parts)


async def learn_style(settings: dict[str, Any]) -> str:
    """读全部历史 + 用户的修改，重写风格笔记。返回新笔记。"""
    samples = db.style_samples()
    edits = db.recent_edits(12)
    if len(samples) < 8 and not edits:
        return (settings.get("style_notes") or "").strip()   # 聊得太少，学不出什么

    parts = []
    old = (settings.get("style_notes") or "").strip()
    if old:
        parts.append("已有的风格笔记：\n" + old)
    if edits:
        parts.append("用户亲手改过的回答（权重最高）：\n" + "\n".join(
            f"- 原：{_clip(e['before'], 200)}\n  改：{_clip(e['after'], 200)}" for e in reversed(edits)
        ))
    parts.append("过往的回复样本：\n" + "\n".join(f"- {_clip(t, 140)}" for t in samples))

    raw = await llm.complete(
        settings,
        [
            {"role": "system", "content": LEARN_PROMPT.format(limit=MAX_NOTES_CHARS)},
            {"role": "user", "content": "\n\n".join(parts)},
        ],
        temperature=0.3,
    )
    notes = (raw or "").strip()[:MAX_NOTES_CHARS + 100]
    if notes:
        db.save_settings({
            "style_notes": notes,
            "style_marker": str(db.count_assistant_archive()),
        })
    return notes


def should_learn(settings: dict[str, Any]) -> bool:
    if settings.get("enable_style_learning") != "1" or llm.is_echo(settings):
        return False
    try:
        marker = int(settings.get("style_marker") or 0)
    except (TypeError, ValueError):
        marker = 0
    total = db.count_assistant_archive()
    if total < marker:      # 删了不少记录
        return False
    return total - marker >= LEARN_EVERY
