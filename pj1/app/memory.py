"""长期记忆：结构化的具体条目，不是摘要。三类，每条一句话：

1. ``about_user``  关于她的事（用户本人：身份、喜好、经历、说过的计划）
2. ``phal_fact``   Phal 自己说过的事实 —— 她随口编的东西（商品名、视频内容、游戏进度、买了什么、花了多少钱……）。
                   最需要记，否则她会前后矛盾。
3. ``shared``      我们之间发生过的事（约定、有分量的话题、承诺）

* 每轮对话后自动提取新条目（后台，不拖慢回复），去重；和已有条目矛盾 / 是它的更新时，**以新的为准**（旧的被替换，不再出现）；
* 聊天时按相关性检索若干条注入，不是全塞进去。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

from . import db, llm
from . import character as C

log = logging.getLogger(__name__)

CATEGORIES = {
    "about_user": "关于她的事",
    "phal_fact": "你自己说过的事",
    "shared": "你们之间发生过的事",
}
LABEL_SHORT = {"about_user": "关于我", "phal_fact": "Phal 说过", "shared": "我们之间"}
# 注入时各类最多几条（memory_limit=12 时）；memory_limit 变了按比例缩放
SHARE = {"about_user": 4, "phal_fact": 5, "shared": 3}
MAX_NEW_PER_TURN = 5

_extracting = False
_again = False


# --------------------------------------------------------------------------
# 检索：按相关性挑几条
# --------------------------------------------------------------------------
def _bigrams(text: str) -> set[str]:
    t = "".join(ch for ch in (text or "") if ch.isalnum())
    return {t[i: i + 2] for i in range(len(t) - 1)} | ({t} if len(t) == 1 else set())


def retrieve(query: str, total: int = 12, min_fill: int = 3) -> list[dict[str, Any]]:
    """和这段对话最相关的几条记忆，分类限量（关于她 / Phal 说过的 / 之间的事）。
    几乎都不相关时，用最近的几条垫一垫，免得上下文里一条记忆都没有。"""
    pool = db.list_memories(400)
    if not pool or total <= 0:
        return []
    grams = _bigrams(query)
    sets = {m["id"]: _bigrams(m["content"]) for m in pool}
    df: dict[str, int] = {}
    for g in sets.values():
        for x in g:
            df[x] = df.get(x, 0) + 1
    now = time.time()
    scored: list[tuple[float, dict[str, Any]]] = []
    for m in pool:
        overlap = sum(1.0 if df[x] <= 3 else 0.25 for x in grams & sets[m["id"]])
        age_days = max(0.0, (now - float(m.get("ts") or now)) / 86400)
        recency = 0.35 / (1.0 + age_days / 14)               # 新的略微优先
        scored.append((overlap + recency, m))
    scored.sort(key=lambda x: -x[0])

    scale = total / 12
    quota = {c: max(1, round(n * scale)) for c, n in SHARE.items()}
    picked: list[dict[str, Any]] = []
    used = {c: 0 for c in quota}
    for score, m in scored:
        c = m.get("category") or "about_user"
        if c not in quota or used[c] >= quota[c]:
            continue
        if score < 1.0 and len(picked) >= min_fill:          # 没什么相关性的不硬塞
            continue
        picked.append(m)
        used[c] += 1
        if len(picked) >= total:
            break
    return picked


def block(memories: list[dict[str, Any]]) -> str:
    """注入上下文的记忆块，分三组。「你自己说过的事」特别强调要保持一致。"""
    if not memories:
        return ""
    groups: dict[str, list[str]] = {c: [] for c in CATEGORIES}
    for m in memories:
        groups.setdefault(m.get("category") or "about_user", []).append(f"- [{m['created_at'][:10]}] {m['content']}")
    parts = ["【长期记忆】以下是和这段对话相关的记忆条目，不必刻意提起："]
    if groups["about_user"]:
        parts.append("关于她的事：\n" + "\n".join(groups["about_user"]))
    if groups["phal_fact"]:
        parts.append("你自己说过的事（这些是你亲口说的，前后必须一致，别矛盾）：\n" + "\n".join(groups["phal_fact"]))
    if groups["shared"]:
        parts.append("你们之间发生过的事：\n" + "\n".join(groups["shared"]))
    return "\n".join(parts)


# --------------------------------------------------------------------------
# 提取
# --------------------------------------------------------------------------
EXTRACT_PROMPT = """你是记忆整理器。下面是一小段对话：「她」是用户，「Phal」是被扮演的角色。
Phal 是一个有自己生活的人，她说过的话里有很多是她随口编出来的「事实」。

请抽出值得长期记住的具体信息，分三类：
- about_user：关于她（用户）的事：身份、喜好、经历、正在做的事、说过的计划；
- phal_fact：Phal 自己说过的事实。商品名、视频内容、游戏进度、买了什么、花了多少钱、去过哪、做了什么，
  哪怕很琐碎也要记——不记的话她以后会前后矛盾；
- shared：她们之间发生过的事：约定、有分量的话题、承诺、争执和和好。

规则：
- 每条一句话，具体（保留名字、数字、价格），不超过 40 字；用「她」指用户，Phal 就写 Phal；
- 寒暄、没有信息量的不记；一轮最多 {n} 条；
- 下面会给出已有的相关记忆（带编号）。如果新信息和某条已有记忆**矛盾**，或是它的**更新**，在 replaces 里填那条的编号（以新的为准）；
  如果只是重复已有的，不要输出。
只输出 JSON 数组，不要别的：
[{{"category": "about_user|phal_fact|shared", "content": "…", "replaces": null}}]
没有值得记的就输出 []。"""


def parse_output(raw: str) -> tuple[list[dict[str, Any]], list[Any]]:
    """提取的输出 → (记忆条目, 意图原始条目)。带意图时是 {"memories": [...], "intents": [...]}，只有记忆时是数组。"""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[-1] if "\n" in text else text
    ob, lb = text.find("{"), text.find("[")
    if ob >= 0 and (lb < 0 or ob < lb):
        try:
            data = json.loads(text[ob: text.rfind("}") + 1])
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            its = data.get("intents")
            return parse_items(json.dumps(data.get("memories") or [], ensure_ascii=False)), its if isinstance(its, list) else []
    return parse_items(text), []


def parse_items(raw: str) -> list[dict[str, Any]]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[-1] if "\n" in text else text
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        data = json.loads(text[start: end + 1])
    except json.JSONDecodeError:
        return []
    items = []
    for d in data if isinstance(data, list) else []:
        if not isinstance(d, dict):
            continue
        cat = str(d.get("category") or "").strip()
        content = str(d.get("content") or "").strip()
        if cat not in CATEGORIES or len(content) < 4:
            continue
        rep = d.get("replaces")
        try:
            rep = int(rep) if rep not in (None, "", "null") else None
        except (TypeError, ValueError):
            rep = None
        items.append({"category": cat, "content": content[:80], "replaces": rep})
    return items[:MAX_NEW_PER_TURN]


def apply_items(items: list[dict[str, Any]], session_id: str | None, source: str = "auto") -> list[dict[str, Any]]:
    """把提取出的条目写进库：去重；带 replaces 的，新的替换旧的。"""
    created = []
    for it in items:
        old = db.get_memory(it["replaces"]) if it.get("replaces") else None
        saved = db.add_memory(it["content"], category=it["category"], session_id=session_id, source=source)
        if saved is None:
            continue
        if old and not old.get("replaced_by"):
            db.supersede_memory(old["id"], saved["id"])
            saved["replaced"] = old["id"]
        created.append(saved)
    return created


def _transcript(rows: list[dict[str, Any]], name: str) -> str:
    lines = []
    for r in rows:
        who = C.pronoun() if r["role"] == "user" else name
        lines.append(f"{who}：{(r['content'] or '')[:500]}")
    return "\n".join(lines)


def _is_ooc(row: dict[str, Any]) -> bool:
    raw = row.get("meta")
    if not raw:
        return False
    try:
        return bool(json.loads(raw).get("ooc")) if isinstance(raw, str) else bool(raw.get("ooc"))
    except (json.JSONDecodeError, AttributeError):
        return False


def drop_ooc(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """出戏（【OOC】）的消息和它的回复，不写进长期记忆。"""
    out: list[dict[str, Any]] = []
    skipping = False
    for r in rows:
        if r["role"] == "user":
            skipping = _is_ooc(r)
        if skipping or _is_ooc(r):
            continue
        out.append(r)
    return out


async def extract_rows(settings: dict[str, Any], rows: list[dict[str, Any]], session_id: str | None,
                       with_intents: bool | None = None) -> list[dict[str, Any]]:
    """从一段对话里提取新记忆（带上相关的已有记忆让模型判断去重 / 替换）；
    开着「说过的打算」时，同一次调用里一起抽她的打算 / 承诺（存进 chat_intents，见 intents.py）。"""
    from . import intents, notebook_write

    rows = [r for r in drop_ooc(rows) if r["role"] in ("user", "assistant")]
    if not any(r["role"] == "user" for r in rows) or llm.is_echo(settings):
        return []
    with_intents = intents.enabled() if with_intents is None else with_intents
    with_nb = notebook_write.enabled()              # 小本子：她讲的世界上的事 → 「她说的」（同一次调用）
    name = C.display_name(settings)
    text = _transcript(rows, name)
    known = retrieve(text, total=14, min_fill=0)
    known_txt = "\n".join(f"[{m['id']}] ({m['category']}) {m['content']}" for m in known) or "（还没有）"
    raw = await llm.complete(
        settings,
        [
            {"role": "system", "content": EXTRACT_PROMPT.format(n=MAX_NEW_PER_TURN) + (intents.prompt_block() if with_intents else "")
             + (notebook_write.told_block(with_intents) if with_nb else "")},
            {"role": "user", "content": f"已有的相关记忆：\n{known_txt}\n\n对话：\n{text}"},
        ],
        temperature=0.2,
    )
    mems, raw_intents = parse_output(raw)
    if with_nb:
        try:
            notebook_write.apply_told(notebook_write.parse_told(raw))
        except Exception:  # noqa: BLE001
            log.exception("写小本子「她说的」出错")
    if with_intents and raw_intents:
        last_reply = next((r["id"] for r in reversed(rows) if r["role"] == "assistant"), None)
        try:
            saved = intents.apply_raw(raw_intents, source_msg_id=last_reply)
            if saved:
                log.info("记下她说过的打算：%s", "；".join(it["text"] for it in saved))
        except Exception:  # noqa: BLE001
            log.exception("保存意图出错")
    if settings.get("enable_auto_memory") != "1":
        return []
    return apply_items(mems, session_id)


async def extract_pending(settings: dict[str, Any], session_id: str, force: bool = False) -> list[dict[str, Any]]:
    """提取自上次以来新增的对话里的记忆（和她说过的打算）。每轮都做（「每几轮提取一次」已不起作用）。
    上一次还没提取完时又来了一轮：记下来，那次完了马上再跑一遍（不漏掉这一轮）。"""
    global _extracting, _again
    if _extracting:
        _again = True
        return []
    created: list[dict[str, Any]] = []
    _extracting = True
    try:
        while True:
            _again = False
            try:
                marker = int(db.get_settings().get("auto_memory_marker") or 0)
            except (TypeError, ValueError):
                marker = 0
            rows = db.messages_after(session_id, marker, 40)
            if not rows:
                break
            created += await extract_rows(settings, rows, session_id)
            db.save_settings({"auto_memory_marker": str(rows[-1]["id"])})
            if not _again:
                break
        return created
    finally:
        _extracting = False


_background: set[asyncio.Task] = set()


def schedule(session_id: str) -> None:
    """一轮对话结束后，后台提取记忆（失败只记日志，不影响聊天）。"""
    from . import intents

    settings = db.get_settings()
    if (settings.get("enable_auto_memory") != "1" and not intents.enabled()) or llm.is_echo(settings):
        return

    async def runner() -> None:
        try:
            await extract_pending(db.get_settings(), session_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("提取记忆失败：%s: %s", type(exc).__name__, exc)

    task = asyncio.get_running_loop().create_task(runner())
    _background.add(task)
    task.add_done_callback(_background.discard)
