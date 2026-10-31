"""人设文本的小工具：按「## 小节」取出需要的部分。"""

from __future__ import annotations

import re


def sections(text: str) -> dict[str, str]:
    """{小节标题: 小节正文}；第一个 ## 之前的内容放在键 "" 下。"""
    parts = re.split(r"(?m)^##\s+(.+?)\s*$", text or "")
    out = {"": parts[0].strip()}
    for i in range(1, len(parts) - 1, 2):
        out[parts[i].strip()] = parts[i + 1].strip().strip("-").strip()
    return out


def voice_excerpt(text: str, cap: int = 1500) -> str:
    """只取「你是谁 / 怎么说话 / 看书 / 慢慢变得像她」这几节：读书注释、分享消息的口吻用它，
    不用把家庭、地名、钱这些整份塞进去。人设里没有这些小节就退回开头的一段。"""
    secs = sections(text)
    want = ["你是谁", "怎么说话", "看书", "慢慢变得像她"]
    picked = [f"## {k}\n{v}" for k in want for kk, v in secs.items() if kk == k and v]
    head = secs.get("", "")
    body = "\n\n".join(([head] if head else []) + picked) if picked else (text or "")
    return body[:cap]
