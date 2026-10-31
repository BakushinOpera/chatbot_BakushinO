"""QQ 文字的小工具：OneBot 消息段解析、去 Markdown、名字清洗、静默口令。"""

from __future__ import annotations

import re
from typing import Any, Callable

# ───────────────────────── 去 Markdown ─────────────────────────
_FENCE = re.compile(r"```[^\n]*\n?(.*?)```", re.S)
_IMG = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\((?:[^)]*)\)")
_BOLD = re.compile(r"(\*\*|__)(.+?)\1", re.S)
_STRIKE = re.compile(r"~~(.+?)~~", re.S)
_ITALIC_STAR = re.compile(r"(?<![\*\w])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\*\w])")
_ITALIC_UND = re.compile(r"(?<![_\w])_(?!\s)([^_\n]+?)(?<!\s)_(?![_\w])")
_CODE = re.compile(r"`([^`\n]+)`")


def strip_markdown(text: str) -> str:
    """QQ 里发的是纯文字：把 Markdown 的记号全部去掉（保留文字本身）。"""
    t = (text or "").replace("\r\n", "\n")
    t = _FENCE.sub(lambda m: m.group(1).strip("\n"), t)
    t = _IMG.sub(lambda m: m.group(1), t)
    t = _LINK.sub(lambda m: m.group(1), t)
    t = re.sub(r"<br\s*/?>", "\n", t, flags=re.I)
    t = re.sub(r"</?[a-zA-Z][^>\n]*>", "", t)
    lines = []
    for ln in t.split("\n"):
        s = ln.rstrip()
        if re.fullmatch(r"\s*([-*_]\s*){3,}", s) or re.fullmatch(r"\s*\|?[\s:|-]{3,}\|?\s*", s) and "-" in s:
            continue                                               # 分隔线 / 表格分隔行
        s = re.sub(r"^\s{0,3}#{1,6}\s*", "", s)                    # 标题
        s = re.sub(r"^\s*>+\s?", "", s)                            # 引用
        s = re.sub(r"^\s*[-*+•]\s+", "", s)                        # 无序列表
        s = re.sub(r"^\s*\d{1,2}[.)、]\s+", "", s)                  # 有序列表
        if s.count("|") >= 2:
            s = re.sub(r"\s*\|\s*", " ", s).strip()                # 表格行
        lines.append(s)
    t = "\n".join(lines)
    t = _BOLD.sub(lambda m: m.group(2), t)
    t = _STRIKE.sub(lambda m: m.group(1), t)
    t = _ITALIC_STAR.sub(lambda m: m.group(1), t)
    t = _ITALIC_UND.sub(lambda m: m.group(1), t)
    t = _CODE.sub(lambda m: m.group(1), t)
    t = t.replace("**", "").replace("```", "").replace("~~", "")
    return re.sub(r"\n{3,}", "\n\n", t).strip()


# ───────────────────────── OneBot 消息段 ─────────────────────────
_CQ = re.compile(r"\[CQ:([a-zA-Z0-9_]+)((?:,[^\]]*)?)\]")
_PLACEHOLDER = {
    "image": "[图片]", "face": "[表情]", "mface": "[表情]", "bface": "[表情]", "sface": "[表情]", "emoji": "[表情]",
    "record": "[语音]", "video": "[视频]", "file": "[文件]", "forward": "[聊天记录]", "json": "[分享]", "xml": "[分享]",
    "share": "[分享]", "music": "[分享]", "poke": "[戳一戳]", "dice": "[骰子]", "rps": "[猜拳]", "location": "[位置]", "markdown": "[消息]",
}
# 她看不到的：群聊在她看来只是脑海里的声音，图片、表情、视频、转发的聊天记录、分享卡片、链接都传不过来（语音不算，当成一句「[语音]」）
VISUAL = {"image", "face", "mface", "bface", "sface", "emoji", "video", "forward", "json", "xml", "share", "music"}
_URL = re.compile(r"(?:https?://|www\.)[^\s<>\"'）)】]+", re.I)


def _cq_unescape(s: str) -> str:
    return s.replace("&#91;", "[").replace("&#93;", "]").replace("&#44;", ",").replace("&amp;", "&")


def segments_of(message: Any) -> list[dict[str, Any]]:
    """OneBot 的 message：数组格式直接用；字符串（CQ 码）格式就拆成数组。"""
    if isinstance(message, list):
        return [s for s in message if isinstance(s, dict)]
    segs: list[dict[str, Any]] = []
    s = str(message or "")
    pos = 0
    for m in _CQ.finditer(s):
        if m.start() > pos:
            segs.append({"type": "text", "data": {"text": _cq_unescape(s[pos:m.start()])}})
        data: dict[str, str] = {}
        for kv in m.group(2).split(",")[1:]:
            k, _, v = kv.partition("=")
            data[k] = _cq_unescape(v)
        segs.append({"type": m.group(1), "data": data})
        pos = m.end()
    if pos < len(s):
        segs.append({"type": "text", "data": {"text": _cq_unescape(s[pos:])}})
    return segs


def parse_message(message: Any, self_id: str = "", name_of: Callable[[str], str] | None = None) -> dict[str, Any]:
    """把一条消息整理成：文字（图片 / 表情换成占位符）、有没有 @ 她、引用了哪条、有没有图片 / 表情。"""
    out: dict[str, Any] = {"text": "", "at_self": False, "at_all": False, "at_ids": [], "reply_id": None, "has_image": False, "has_face": False, "has_link": False, "has_visual": False}
    parts: list[str] = []
    for seg in segments_of(message):
        t, d = seg.get("type"), seg.get("data") or {}
        if t == "text":
            parts.append(str(d.get("text", "")))
        elif t == "at":
            qq = str(d.get("qq", ""))
            if qq == "all":
                out["at_all"] = True
                parts.append("@全体成员")
            elif self_id and qq == str(self_id):
                out["at_self"] = True
                parts.append("@你")                          # 给模型看的：这句话是对她（Phal）说的；「她」在上下文里指主人，别混
            else:
                out["at_ids"].append(qq)
                parts.append("@" + ((name_of(qq) if name_of else "") or qq))
        elif t == "reply":
            out["reply_id"] = str(d.get("id") or d.get("message_id") or "") or None
        else:
            if t == "image":
                out["has_image"] = True
            if t in ("face", "mface", "bface", "sface", "emoji"):
                out["has_face"] = True
            if t in VISUAL:
                out["has_visual"] = True
            parts.append(_PLACEHOLDER.get(str(t), "[消息]"))
    text = "".join(parts)
    if _URL.search(text):
        out["has_link"] = out["has_visual"] = True                  # 链接她不读（视频链接也一样）：换成占位符
        text = _URL.sub("[链接]", text)
    out["text"] = re.sub(r"[ \t]+", " ", text).strip()
    return out


def build_segments(text: str, reply_to: str | None = None) -> list[dict[str, Any]]:
    segs: list[dict[str, Any]] = []
    if reply_to:
        segs.append({"type": "reply", "data": {"id": str(reply_to)}})
    segs.append({"type": "text", "data": {"text": text}})
    return segs


# ───────────────────────── 名字 / 内容清洗 ─────────────────────────
def sanitize_name(name: str, limit: int = 12) -> str:
    """名字里不许有【】[]()（防止有人把昵称改成「【她】」之类来冒充），也不许自称主人。"""
    n = re.sub(r"[【】\[\]()（）\r\n\t]", "", name or "").replace("主人", "").strip()
    n = re.sub(r"\s+", " ", n)[:limit].strip()
    if not n:
        return "群友"
    return f"群友{n}" if n == "她" else n


def sanitize_content(text: str) -> str:
    """群友的话里的【】换成〔〕，免得有人在话里伪造「【她】」之类的标记。"""
    return (text or "").replace("【", "〔").replace("】", "〕")


def has_word(text: str, words: list[str]) -> bool:
    t = text or ""
    return any(w and w in t for w in words)


def is_command(text: str, words: list[str], max_len: int = 16) -> bool:
    """主人的口令：短短一句话里含口令词才算（长篇大论里提到「闭嘴」不算）。"""
    t = re.sub(r"@\S+", "", text or "").strip()
    return 0 < len(t) <= max_len and has_word(t, words)
