"""Phal 看书 / 创作时让模型干活的部分（只有这里会调模型）。

原则：
* 看什么、看多久、要不要偷懒、要不要分享 —— 全部由 life.py 里的代码随机决定；
* 模型只负责「读到这段原文之后写什么」，而且只准评论喂给它的原文；
* 引用的原文必须在这一段里**一字不差**地找得到，找不到的整条丢掉 —— 防止编造书里没有的东西。
"""

from __future__ import annotations

import logging
import random
from typing import Any

from . import daily, db, guards, library as lib, llm, persona as persona_lib, registry
from . import character as C
from .planner import _json_object

log = logging.getLogger(__name__)

MAX_ANNOTATIONS = 3
MAX_PER_HOUR = 3          # 每小时主动消息总数上限
MAX_SHARES_PER_SESSION = 2


def _persona(settings: dict[str, Any]) -> tuple[str, str]:
    return C.display_name(settings), persona_lib.voice_excerpt(settings.get("system_prompt") or "", 1500)


# 书里出现的现实国家、地名「不做处理」：她不评论作者国籍，也不在自己写的注释 / 消息里提现实国家名。
# 提示词里要求；这里再在代码里兜底（命中就整条丢掉，不发 / 不存）。
REAL_PLACE_WORDS = [
    "中国", "美国", "英国", "日本", "法国", "德国", "俄罗斯", "苏联", "意大利", "西班牙", "葡萄牙", "印度", "加拿大",
    "澳大利亚", "韩国", "朝鲜", "巴西", "埃及", "希腊", "荷兰", "瑞士", "瑞典", "挪威", "丹麦", "芬兰", "波兰", "土耳其",
    "伊朗", "以色列", "越南", "泰国", "新加坡", "阿根廷", "墨西哥", "爱尔兰", "奥地利", "比利时", "古巴", "国籍",
    "英国人", "美国人", "中国人", "日本人", "法国人", "德国人", "俄国", "英伦", "欧洲", "亚洲", "美洲", "非洲",
    # 菜名、店名里常带的现实地名 / 地域词（她的世界是虚构的）
    "兰州", "北京", "上海", "广州", "深圳", "四川", "重庆", "成都", "湖南", "湖北", "云南", "山东", "广东", "潮汕", "杭州", "西安",
    "武汉", "天津", "新疆", "台湾", "香港", "澳门", "陕西", "山西", "福建", "江西", "贵州", "东北", "绍兴", "宁波", "苏州", "南京",
    "长沙", "日式", "韩式", "泰式", "美式", "法式", "意式", "英式", "德式", "港式", "川菜", "湘菜", "粤菜", "鲁菜",
]


def mentions_real_place(text: str) -> bool:
    if not C.feature("fictional_world"):
        return False                       # 现实世界版：提到现实地名很正常
    return any(w in (text or "") for w in REAL_PLACE_WORDS + registry.get("real_place_words"))


def can_send_proactive() -> bool:
    if daily.is_sleeping():
        return False          # 睡着了，不会半夜来找你
    return lib.proactive_count_since(3600) < int(registry.num("notify_max_per_hour"))


def post_proactive(kind: str, content: str, meta: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """把一条主动消息发进聊天（带来源标记），并记进频率限制表。感叹号在这里统一去掉（提示词里不用再写）。"""
    content = guards.strip_exclaim((content or "").strip())
    if not content or not can_send_proactive():
        return None
    msg = db.add_message(db.current_session_id(), "assistant", content, source=kind, meta=meta)
    lib.log_proactive(kind, msg["id"])
    return msg


# --------------------------------------------------------------------------
# 读一段书
# --------------------------------------------------------------------------
READ_PROMPT = """你是「{name}」。下面是你的人设（只用来决定你的语气和性格）：
{persona}

你现在正在看一本书，刚读完下面这一段。像一个真的在读书的人那样，在书页上写几条旁注。

规则：
- 你的口吻就是上面人设里「怎么说话」那一节：短句，口语，嘴懒一点、可以有点毒舌但不刻薄，可以想到一半，别每条都夸。
- 不评论作者是哪国人{no_real_country}。
- 只评论【原文】里真实出现的内容。不要编造书里没有的情节、人物或句子，也不要把你对这本书的印象当成原文。
- annotations：0~{max_ann} 条。quote 必须是原文里**一字不差**的一小段（6~30 个字，不能跨段落）；
  note 是你的批注，一句短话，口语，用你的语气。没什么想写的就给空数组。
- share：只有遇到特别想立刻告诉对方的地方才填（高潮、文笔特别好、气人、看不懂），否则 null。{share_rule}
  quote 同样要一字不差、不超过 40 字；reaction 是你的反应，一两句话，别剧透式地总结情节。
- private_note：给自己的一两句话笔记（读到哪了、什么感觉），以后翻书和聊天时会看到。
- feeling：读完这一段你的心情，只能是这几个之一：平淡 / 有意思 / 兴奋 / 气人 / 难过 / 困惑。
  大多数段落是「平淡」，只有真的被打动、被气到时才选别的。

只输出 JSON，不要别的：
{{"annotations": [{{"quote": "...", "note": "..."}}], "share": null, "private_note": "...", "feeling": "平淡"}}"""


def _find_quote(chunk: str, quote: str) -> int:
    quote = (quote or "").strip()
    if len(quote) < 4 or "\n" in quote:
        return -1
    return chunk.find(quote)


async def process_chunk(
    settings: dict[str, Any], book: dict[str, Any], chapter: int, chapter_title: str,
    start: int, text: str, allow_share: bool,
) -> dict[str, Any]:
    """读完 text（章节里 start 开始的一段）之后：存注释、存笔记、可能发一条消息。返回统计。"""
    result = {"annotations": 0, "shared": False, "note": False, "feeling": "平淡"}
    if llm.is_echo(settings) or len(text.strip()) < 80:
        return result
    name, persona = _persona(settings)
    notes = lib.notes_for_book(book["id"], 4)
    notes_txt = "\n".join(f"- {n['text']}" for n in notes) or "（还没有）"
    share_rule = "这次可以分享。" if allow_share else "这次不要分享，share 必须是 null。"
    try:
        raw = await llm.complete(
            settings,
            [
                {"role": "system", "content": READ_PROMPT.format(
                    name=name, persona=persona, max_ann=MAX_ANNOTATIONS, share_rule=share_rule)},
                {"role": "user", "content": (
                    f"书名：《{book['title']}》 作者：{book['author'] or '不详'}\n章节：{chapter_title}\n\n"
                    f"你之前的读书笔记：\n{notes_txt}\n\n【原文】\n{text}")},
            ],
            temperature=0.8,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("读书会话调用模型失败：%s: %s", type(exc).__name__, exc)
        return result
    data = _json_object(raw)
    feeling = str(data.get("feeling") or "平淡").strip()
    result["feeling"] = feeling if feeling in ("平淡", "有意思", "兴奋", "气人", "难过", "困惑") else "平淡"

    for item in (data.get("annotations") or [])[:MAX_ANNOTATIONS]:
        if not isinstance(item, dict):
            continue
        quote, note = str(item.get("quote") or "").strip(), guards.strip_exclaim(str(item.get("note") or "").strip())
        idx = _find_quote(text, quote)
        if idx < 0 or not note:
            continue
        if mentions_real_place(note):
            continue
        if lib.add_annotation(book["id"], chapter, start + idx, len(quote), quote, note[:80]):
            result["annotations"] += 1

    private = guards.strip_exclaim(str(data.get("private_note") or "").strip())
    if private and not mentions_real_place(private):
        lib.add_note(book["id"], chapter, private)
        result["note"] = True

    share = data.get("share")
    if allow_share and isinstance(share, dict):
        quote, reaction = str(share.get("quote") or "").strip(), guards.strip_exclaim(str(share.get("reaction") or "").strip())
        idx = _find_quote(text, quote)
        if idx >= 0 and reaction and len(quote) <= 60 and not mentions_real_place(reaction):
            body = f"「{quote}」\n\n{reaction}"
            msg = post_proactive("reading", body, {
                "kind": "share", "book_id": book["id"], "title": book["title"],
                "chapter": chapter, "offset": start + idx,
            })
            result["shared"] = msg is not None
    return result


READ_PROMPT = READ_PROMPT.replace("{no_real_country}", C.pick("fictional_world", "，也不提现实里的国家名（书里出现的国家、地名，你当作书里的东西就好）"))


READ_DONE_PROMPT = """你是「{name}」。人设（只用来决定语气）：
{persona}

你刚看完一阵子的书，下面是你这次读书时记的笔记。用 1~2 句话，像随口跟对方说起一样，说说读完的感受或一个念头。
只准基于笔记里写到的东西，不要编造书里的内容。只输出这句话本身。"""


async def read_done_thought(settings: dict[str, Any], book: dict[str, Any]) -> dict[str, Any] | None:
    if llm.is_echo(settings) or not can_send_proactive():
        return None
    notes = lib.notes_for_book(book["id"], 3)
    if not notes:
        return None
    name, persona = _persona(settings)
    try:
        raw = await llm.complete(
            settings,
            [
                {"role": "system", "content": READ_DONE_PROMPT.format(name=name, persona=persona)},
                {"role": "user", "content": f"书：《{book['title']}》\n笔记：\n" + "\n".join(f"- {n['text']}" for n in notes)},
            ],
            temperature=0.85,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("读后感调用模型失败：%s", exc)
        return None
    return post_proactive("read_done", raw.strip().strip("\"“”"), {"kind": "read_done", "book_id": book["id"], "title": book["title"]})


# --------------------------------------------------------------------------
# 试图创作
# --------------------------------------------------------------------------
CREATE_KINDS = [("诗", 4), ("片段", 4), ("开头", 2)]

CREATE_PROMPT = """你是「{name}」。人设（只用来决定语气和审美）：
{persona}

你刚才试着写点小东西。体裁：{kind}。{outcome_rule}
灵感来源（可以用，也可以不用）：{inspiration}

只输出 JSON：{{"title": "...", "content": "...", "share_line": "..."}}
- {content_rule}
- share_line：如果要拿给对方看，你会怎么说，带点不好意思或不满意的语气，一句话。"""

OUTCOME_DONE = "这次写成了，是一篇完整的小东西（诗 4~10 行；片段 / 开头 80~200 字）。"
OUTCOME_ABANDONED = "这次没写成，写到一半自己不满意，放弃了。content 只写那几行残稿（1~3 行），title 留空。"


async def create_piece(settings: dict[str, Any], succeeded: bool, share: bool) -> dict[str, Any] | None:
    """写一件小东西进草稿本；按概率拿给对方看。写成 / 放弃由调用方（代码）决定。"""
    if llm.is_echo(settings):
        return None
    kind = random.choices([k for k, _ in CREATE_KINDS], [w for _, w in CREATE_KINDS])[0]
    name, persona = _persona(settings)
    notes = lib.recent_notes(3)
    inspiration = "；".join(f"《{n['book_title']}》里你记过：{n['text']}" for n in notes) or "（没有特别的）"
    try:
        raw = await llm.complete(
            settings,
            [
                {"role": "system", "content": CREATE_PROMPT.format(
                    name=name, persona=persona, kind=kind, inspiration=inspiration,
                    outcome_rule=OUTCOME_DONE if succeeded else OUTCOME_ABANDONED,
                    content_rule="content 是作品正文本身，别写解释。")},
                {"role": "user", "content": "写吧。"},
            ],
            temperature=0.95,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("创作调用模型失败：%s", exc)
        return None
    data = _json_object(raw)
    content = str(data.get("content") or "").strip()
    if not content:
        return None
    draft = lib.add_draft(kind, str(data.get("title") or ""), content, "done" if succeeded else "abandoned", inspiration[:120])
    if share:
        line = str(data.get("share_line") or "").strip()
        if succeeded:
            body = f"{line}\n\n{content}".strip()
        else:
            body = line or "写了几行，又删了。"
        if post_proactive("create", body, {"kind": "draft", "draft_id": draft["id"]}):
            lib.mark_draft_shared(draft["id"])
    return draft
