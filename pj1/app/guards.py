"""说话风格的代码层兜底：不只靠提示词，下面这些在后端处理。

* 感叹号 → 句号（句中）或直接删掉（句末）；
* 按换行和句号把回复切成多条消息，分开显示，中间加随机延迟；
* 单条太长就截断（在逗号 / 问号处收）；条数太多只留前几条；
* 禁词检查：命中就重新生成，最多重试 2 次。禁词表单独存一个可编辑的文件 ``data/banned_words.txt``，
  **不写进人设提示词**（写进提示词反而容易让模型去提它）；
* 模型附带的结构化动作（如读书指令）从正文里剥掉，对用户隐藏；
* 出戏开关：消息以【OOC】开头时，这条不经过上面这些后处理、不写入长期记忆。
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path
from typing import Any

from .config import DATA_DIR

BANNED_PATH = DATA_DIR / "banned_words.txt"
INITIAL_BANNED = ["生存法则", "干货", "总而言之", "首先", "其次", "接住了", "接得住", "宝子", "抱抱你"]
BANNED_HEADER = (
    "# Phal 的禁词表：一行一个词或一句说法，# 开头的是注释。\n"
    "# 回复里命中任何一个，就会自动重新生成（最多重试 2 次）。改完保存即可，不用重启。\n"
)

MAX_PART_CHARS = 60        # 单条消息最长多少字，超过就截断
MAX_PARTS = 6              # 一次回复最多几条
MAX_RETRIES = 2            # 禁词命中后最多重新生成几次


# --------------------------------------------------------------------------
# 禁词表（文件）
# --------------------------------------------------------------------------
def ensure_banned_file(extra: list[str] | None = None) -> None:
    """第一次建文件：初始禁词 + 以前在界面里加过的（settings 里的旧禁词表迁移过来）。"""
    if BANNED_PATH.exists():
        return
    BANNED_PATH.parent.mkdir(parents=True, exist_ok=True)
    words = list(dict.fromkeys(INITIAL_BANNED + [w for w in (extra or []) if w]))
    BANNED_PATH.write_text(BANNED_HEADER + "\n".join(words) + "\n", encoding="utf-8")


_banned_cache: tuple[float, list[str]] = (-1.0, [])


def load_banned() -> list[str]:
    global _banned_cache
    ensure_banned_file()
    try:
        mtime = BANNED_PATH.stat().st_mtime
    except OSError:
        return []
    if _banned_cache[0] != mtime:
        words = []
        for ln in BANNED_PATH.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#"):
                words.append(ln)
        _banned_cache = (mtime, list(dict.fromkeys(words)))
    return list(_banned_cache[1])


def save_banned(words: list[str]) -> None:
    words = list(dict.fromkeys(w.strip() for w in words if w and w.strip() and not w.strip().startswith("#")))
    BANNED_PATH.parent.mkdir(parents=True, exist_ok=True)
    BANNED_PATH.write_text(BANNED_HEADER + "\n".join(words) + "\n", encoding="utf-8")


def find_banned(text: str, words: list[str] | None = None) -> list[str]:
    words = load_banned() if words is None else words
    return [w for w in words if w and w in (text or "")]


# --------------------------------------------------------------------------
# 文本处理
# --------------------------------------------------------------------------
_EXCL = re.compile(r"[！!]+")


def strip_exclaim(text: str) -> str:
    """感叹号：句末直接删掉；句中换成句号。"""
    def repl(m: re.Match[str]) -> str:
        tail = text[m.end(): m.end() + 1]
        return "" if (not tail or tail in "\n\r」』）)”\"'") else "。"
    return _EXCL.sub(repl, text or "")


_CALL = re.compile(r"\{[^{}\n]*\"action\"\s*:[^{}\n]*\}")


def extract_calls(text: str) -> tuple[str, list[dict[str, Any]]]:
    """把模型附带的 {"action": "...", 其它参数} 从正文里剥掉，返回 (干净的正文, 动作列表，每个是一个 dict)。

    读书指令只有 action 一个字段；add_item 这类还带参数（name / category / location ……）。
    """
    calls: list[dict[str, Any]] = []

    def take(m: re.Match[str]) -> str:
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return ""                                  # 写坏了的动作：剥掉，不执行
        if isinstance(data, dict) and re.fullmatch(r"[a-z_]+", str(data.get("action") or "")):
            calls.append(data)
        return ""

    clean = _CALL.sub(take, text or "")
    # 万一模型把动作写成了残缺的样子，也别让它露出来
    clean = re.sub(r"\{\s*\"action\"[^}\n]*$", "", clean, flags=re.M)
    return clean.strip(), calls


def extract_actions(text: str) -> tuple[str, list[str]]:
    """同 extract_calls，但只返回动作名（不管参数）。"""
    clean, calls = extract_calls(text)
    return clean, [c["action"] for c in calls]


def _cut(part: str, limit: int) -> str:
    if len(part) <= limit:
        return part
    window = part[:limit]
    best = max(window.rfind(c) for c in "，、；？…,;?")
    return (window[:best] if best >= limit * 0.4 else window).rstrip("，、；,;")


def split_messages(text: str, max_len: int = MAX_PART_CHARS, max_parts: int = MAX_PARTS) -> list[str]:
    """按换行和句号切成多条消息（句号本身是分隔符，不留在句末）；太长的截断，太多的只留前几条。"""
    parts: list[str] = []
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        for seg in re.split(r"(?<=。)", line):
            seg = seg.strip().rstrip("。").strip()
            if seg:
                parts.append(_cut(seg, max_len))
    return parts[:max_parts]


def is_ooc(text: str) -> bool:
    return bool(re.match(r"\s*[【\[]\s*ooc\s*[】\]]", text or "", re.I))


def delay_for(part: str, scale: float = 1.0) -> float:
    """下一条消息出现前的随机停顿（秒）：越长的越久，模拟打字。scale=0 关掉延迟。"""
    if scale <= 0:
        return 0.0
    from . import registry

    return scale * (random.uniform(registry.num("type_pause_min_s"), registry.num("type_pause_max_s"))
                    + min(registry.num("type_len_cap_s"), len(part) * registry.num("type_per_char_s")))
