"""她写小本子（第 3 步，见 docs/已完成的计划/小本子与世界地图.md 第四节、docs/已完成的计划/phal-notebook-spec.md 第 2 节）。尽量不加调用：

* 第一次去一家店：吃饭 / 消费那次调用的 JSON 里多要一个 ``impression``（见 prices.FIELDS），代码建店铺条目、写进印象（notebook.remember_price）；
  第一次去一个地方：出门定目的地时建「地方」条目（notebook.visit_place），正文晚上整理时写。——都不额外调用。
* 你在聊天里讲世界上的事：记忆 + 对话意图那次提取里加第三项 ``notebook``（``told_block`` / ``apply_told``），写进「她说的」章节。——不额外调用。
* 每晚日总结之后：把今天的见闻（去了哪、哪家店、吃了什么、买了什么、发生的事）整理进本子，顺便精简太长的条目（``nightly``）。——一天一次。
* 写入规则都在 notebook.her_write：同一条就合并；锁定的只能在末尾追加；冲突时没锁定的以新的为准，锁定的追加成「她的看法」。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from . import db, guards, llm, notebook, places, reading, registry
from . import character as C

log = logging.getLogger(__name__)

# 默认章节「记什么」（自定义的章节只给名字）
CHAPTER_WHAT = {
    "place": "城市、县、街道、地标、常去的地点：在哪、什么样、你觉得怎么样",
    "shop": "店：卖什么、环境、老板、值不值、常点什么",
    "food": "菜、零食、饮品，以及你的评价",
    "price": "某类东西大概多少钱、哪里贵哪里便宜",
    "heard": "听来的传闻、新闻、网上看到的",
    "told": "她（对方）告诉你的、关于世界的事",
}
ACCURACY_RULE = {
    "accurate": "尽量准确：别和下面「设定」矛盾，价格照实记",
    "exaggerate": "可以夸张、带主观评价",
    "misread": "可以有误解和自己的猜测（你是新人）",
}


def enabled() -> bool:
    return registry.flag("notebook_write")


def _clean(text: Any, cap: int) -> str:
    t = guards.strip_exclaim(re.sub(r"\s+", " ", str(text or "")).strip())[:cap]
    return "" if reading.mentions_real_place(t) or guards.find_banned(t) else t


# ───────────────────────── 你在聊天里讲世界上的事 → 「她说的」 ─────────────────────────
TOLD_PROMPT = """
另外，抽出「她」（用户）在这段对话里讲的、关于世界的事（notebook）：某个国家、城市、地方、店、东西、风俗、新闻是什么样（比如她讲她那边的什么事）。
不是关于「她」自己或别人的事（那些是记忆），也不是 Phal 自己编的；寒暄、玩笑、假设不算。一轮最多 3 条，没有就是空数组。
每条记成 Phal 小本子里的一条：{{"title": "这件事 / 这个地方的名字，10 字以内", "text": "用 Phal 自己的口吻记下来，像记在本子上，60 字以内", "keywords": ["两三个关键词"]}}
{fmt}"""


def told_block(with_intents: bool) -> str:
    if with_intents:
        return TOLD_PROMPT.format(fmt="在上面那个 JSON 对象里再加一个字段：\"notebook\": [上面说的条目]。")
    return TOLD_PROMPT.format(fmt="这时整体输出一个 JSON 对象，不要别的：{\"memories\": [上面说的记忆条目], \"notebook\": [上面说的条目]}")


def parse_told(raw: str) -> list[dict[str, Any]]:
    from .planner import _json_object

    items = _json_object(raw).get("notebook")
    return [x for x in items if isinstance(x, dict)][:3] if isinstance(items, list) else []


def apply_told(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """写进「她说的」：同一件事合并（锁定的追加在末尾）。来源 = 她说的，可信度 = 已确认（是你说的）。"""
    out = []
    if "told" not in notebook.chapter_keys():
        return out
    for it in items:
        title, text = _clean(it.get("title"), 20), _clean(it.get("text"), 120)
        if not title or not text:
            continue
        kws = [k for k in (_clean(k, 20) for k in (it.get("keywords") or [])[:5] if isinstance(it.get("keywords"), list)) if k]
        try:
            e = notebook.similar("told", title, kws)
            if e and not e["locked"]:
                e = notebook.her_write("told", title, append=text, keywords=kws, target=e["id"])     # 没锁定：接在后面（太长的晚上精简）
            else:
                e = notebook.her_write("told", title, body=text, append=text, keywords=kws, source="told", trust="confirmed", target=e["id"] if e else None)
            if e:
                out.append(e)
        except Exception:  # noqa: BLE001
            log.exception("写「她说的」出错")
    if out:
        log.info("小本子「她说的」：%s", "、".join(e["title"] for e in out))
    return out


# ───────────────────────── 每晚整理 ─────────────────────────
NIGHT_PROMPT = """你是「{name}」。你的口吻（人设里的说话方式）：
{voice}

现在是晚上，你在整理自己的小本子。这本子记的是「世界是什么样」——以及你觉得它是什么样：去过的地方、常去的店、吃过的东西、物价印象、听来的事、她（对方）告诉你的事。
不记人（人另有地方记），也不记今天的流水账。

章节（键=名字：记什么；怎么写）：
{chapters}

今天的见闻：
{material}

本子里相关的条目（编号 / 章节 / 标题 / 是否锁定 / 正文）：
{existing}

本子里已有的标题（别重复建）：
{titles}

设定（这是事实，「尽量准确」的章节以它为准）：
{author}

要求：
- 只记今天真的经历 / 听说的、以后还用得上的；没什么值得记的就少写，甚至一条不写；最多 {n} 条；
- 写进同一个地方 / 同一家店 / 同一件事已有的那一条（target 填编号），不要另起；还空着的条目（正文是空的）把它写上；
- 没锁定的条目：body 写合并后的完整正文（新的和旧的矛盾时以新的为准）；
- 锁定的条目（🔒）：不能改原文，只能用 append 在末尾追加一小段新见闻（30 字以内）；和原文矛盾的写成你的看法，opinion 填 true；
- 标了「太长」的条目，顺便精简到 {mx} 字以内（target 填它，body 写精简后的）；
- 每条正文 {mx} 字以内，你的口吻，可以带吐槽和评价；{no_real}
- 新建时 title 用地方 / 店 / 东西 / 事情本身的名字，chapter 填章节键。
输出 JSON，不要别的：
{{"entries": [{{"target": 编号或 null, "chapter": "章节键", "title": "标题", "keywords": ["补充的关键词"], "body": "完整正文或空", "append": "追加或空", "opinion": false}}]}}"""
NIGHT_PROMPT = NIGHT_PROMPT.replace("{no_real}", C.pick("fictional_world", "不写现实里的地名；"))


def _strip_secret(text: str) -> str:
    """作者层里「## 她不知道」到下一个 ## 之间不给她看。"""
    return re.sub(r"(?ms)^##\s*她不知道.*?(?=^##\s|\Z)", "", text or "").strip()


def _author_block(note_text: str, place_ids: list[str]) -> str:
    """今天涉及的作者层设定：去过的地点（地点库：种类、上级、车程、备注）+ 世界设定里关键词命中的条目（去掉她不知道的部分）。"""
    out = []
    for pid in place_ids:
        p = places.get(pid)
        if not p or p["secret"]:
            continue
        ch = [x["name"] for x in places.chain(pid)[1:] if x["kind"] not in ("world",)]
        out.append(f"- {p['name']}（{places.KINDS.get(p['kind'], '')}，属于{'、'.join(ch) or '—'}，离家约 {p['travel_min']} 分钟）" + (f"：{p['note']}" if p["note"] else ""))
    try:
        from . import world

        for m in world.match(note_text, limit=3):
            if "她不知道" in m["title"]:
                continue
            body = _strip_secret(m["body"])[:500]
            if body:
                out.append(f"- 【{m['category']}·{m['title']}】{body}")
    except Exception:  # noqa: BLE001
        log.exception("读世界设定出错")
    return "\n".join(out) or "（没有）"


def _material(day: str) -> tuple[str, list[int], list[str]]:
    notes = notebook.day_notes(day)
    events = db.events_on(day, 60)
    lines = [f"- {n['text']}" for n in notes]
    lines += [f"- {str(e.get('created_at') or '')[11:16]} {e['text']}" for e in events if e.get("text")]
    refs = [int(n["ref"]) for n in notes if n.get("ref")]
    pids = []
    for r in refs:
        e = notebook.get(r)
        if e and e["place"] and e["place"] not in pids:
            pids.append(e["place"])
    return "\n".join(lines), refs, pids


def _related(material: str, refs: list[int], mx: int, cap: int = 16) -> list[dict[str, Any]]:
    """要给她看的已有条目：今天碰过的、标题 / 关键词出现在今天见闻里的、没锁定又太长的。"""
    out: dict[int, dict[str, Any]] = {}
    for r in refs:
        e = notebook.get(r)
        if e and e["status"] == "active":
            out[e["id"]] = e
    for e in notebook.list_entries():
        if len(out) >= cap:
            break
        if e["id"] in out:
            continue
        if any(len(k) >= 2 and k in material for k in notebook.keywords_of(e)):
            out[e["id"]] = e
    for e in notebook.list_entries():
        if len(out) >= cap:
            break
        if not e["locked"] and len(e["body"]) > mx and e["id"] not in out:
            out[e["id"]] = e
    return list(out.values())[:cap]


def _existing_text(rows: list[dict[str, Any]], names: dict[str, str], mx: int) -> str:
    out = []
    for e in rows:
        tags = ["🔒" if e["locked"] else "", "太长" if not e["locked"] and len(e["body"]) > mx else ""]
        extra = "".join(f"（追加：{a['text']}）" for a in e["addenda"])
        items = (e["info"] or {}).get("items") or {}
        price = ("；买过：" + "、".join(f"{k} {float(v.get('price') or 0):g} {C.unit()}" for k, v in list(items.items())[:5])) if items else ""
        out.append(f"[{e['id']}] {names.get(e['chapter'], e['chapter'])} /「{e['title']}」{' '.join(t for t in tags if t)}：{e['body'] or '（空的）'}{extra}{price}")
    return "\n".join(out) or "（没有）"


def _titles_text() -> str:
    out = []
    for c in notebook.chapters():
        ts = [e["title"] for e in notebook.list_entries(c["key"])][:40]
        if ts:
            out.append(f"- {c['name']}：{'、'.join(ts)}")
    return "\n".join(out) or "（本子还是空的）"


async def nightly(settings: dict[str, Any], day: str) -> list[dict[str, Any]]:
    """晚上把今天的见闻整理进小本子（一次调用）。一天只做一次；没有见闻、离线演示模式就不做。返回写过的条目。"""
    from . import persona as persona_lib
    from .planner import _json_object

    if not enabled() or llm.is_echo(settings) or notebook.meta_get("night_done") == day:
        return []
    material, refs, pids = _material(day)
    if not material.strip():
        notebook.meta_set("night_done", day)
        return []
    mx = notebook.entry_max()
    chs = notebook.chapters()
    names = {c["key"]: c["name"] for c in chs}
    rows = _related(material, refs, mx)
    allowed = {e["id"] for e in rows}
    chapters = "\n".join(f"- {c['key']}={c['name']}：{CHAPTER_WHAT.get(c['key'], c['name'])}；{ACCURACY_RULE.get(c['accuracy'], '')}" for c in chs)
    system = NIGHT_PROMPT.format(
        name=C.display_name(settings), voice=persona_lib.voice_excerpt(settings.get("system_prompt") or "", 900), chapters=chapters,
        material=material, existing=_existing_text(rows, names, mx), titles=_titles_text(), author=_author_block(material, pids),
        n=int(registry.num("notebook_night_n")), mx=mx)
    raw = await llm.complete(settings, [{"role": "system", "content": system}, {"role": "user", "content": "整理吧。"}], temperature=0.7)
    items = _json_object(raw).get("entries")
    out = []
    for it in (items if isinstance(items, list) else [])[: int(registry.num("notebook_night_n")) + len(rows)]:
        if not isinstance(it, dict):
            continue
        try:
            target = int(it["target"]) if it.get("target") not in (None, "", "null") else None
        except (TypeError, ValueError):
            target = None
        if target is not None and target not in allowed:
            target = None                                  # 只能写给她看过的条目，编号对不上就按标题找
        chapter = str(it.get("chapter") or "").strip()
        title = _clean(it.get("title"), notebook.TITLE_MAX)
        body, append = _clean(it.get("body"), mx * 2), _clean(it.get("append"), 120)
        kws = [k for k in (_clean(k, 20) for k in (it.get("keywords") or [])[:5]) if k] if isinstance(it.get("keywords"), list) else []
        if not (body or append) or (target is None and (chapter not in names or not title)):
            continue
        try:
            e = notebook.her_write(chapter, title, body=body, append=append, keywords=kws, opinion=bool(it.get("opinion")), target=target,
                                   source="heard" if chapter == "heard" else "experience")
        except ValueError as exc:                          # 锁定条目想改原文之类：跳过这一条
            log.info("小本子整理跳过一条：%s", exc)
            continue
        if e:
            out.append(e)
    notebook.meta_set("night_done", day)
    log.info("小本子整理完：%s", "、".join(e["title"] for e in out) or "没写")
    return out


# ───────────────────────── 初始抄写（第 5 步）：作者层 → 她自己的话，先待审 ─────────────────────────
COPY_PROMPT = """你是「{name}」。你的口吻（人设里的说话方式）：
{voice}

你在把一份资料抄进自己的小本子：用你自己的话、按你这个身份知道的程度记（不是百科全书，记你记得住、用得上的），可以带一点你的看法。

章节（键=名字：记什么；怎么写）：
{chapters}

资料《{title}》：
{text}

要求：
- 拆成几条：一个地方 / 一件事一条，最多 {n} 条；每条正文 {mx} 字以内，你的口吻；
- 资料里没有的别编；{no_real}
- title 用地方 / 事物本身的名字；chapter 填章节键。
输出 JSON，不要别的：
{{"entries": [{{"chapter": "章节键", "title": "标题", "body": "正文", "keywords": ["两三个关键词"]}}]}}"""
COPY_PROMPT = COPY_PROMPT.replace("{no_real}", C.pick("fictional_world", "不写现实里的地名；"))
COPY_PER_FILE = 6
_copy_task = None


def copy_sources() -> list[dict[str, str]]:
    """要抄的作者层文件：data/world/ 下各分类的文件，去掉总纲（总纲照旧常驻上下文）、提示、空文件，以及「## 她不知道」小节。"""
    from . import world

    out = []
    for e in world.entry_files():
        text = _strip_secret(e["text"])
        if len(re.sub(r"\s", "", text)) >= 20:
            out.append({"category": e["category"], "title": e["title"], "text": text[:4000]})
    return out


def copy_status() -> dict[str, Any]:
    pending = notebook.list_entries(status="pending")
    state = notebook.meta_get("copy_state") or ""
    if state == "copying" and (_copy_task is None or _copy_task.done()):     # 抄到一半关了程序：不卡在「正在抄」
        state = "pending" if pending else ""
        notebook.meta_set("copy_state", state)
    return {"state": state, "progress": notebook.meta_get("copy_progress"), "pending": len(pending),
            "sources": len(copy_sources()), "error": notebook.meta_get("copy_error")}


async def initial_copy(settings: dict[str, Any]) -> int:
    """每个作者层文件一次调用：让她用自己的话抄进小本子，条目先是「待审」（status=pending，聊天和生活都不读）。返回抄了几条。"""
    from . import persona as persona_lib
    from .planner import _json_object

    srcs = copy_sources()
    chs = notebook.chapters()
    keys = {c["key"] for c in chs}
    chapters = "\n".join(f"- {c['key']}={c['name']}：{CHAPTER_WHAT.get(c['key'], c['name'])}；{ACCURACY_RULE.get(c['accuracy'], '')}" for c in chs if c["key"] != "shop")
    mx = notebook.entry_max()
    notebook.meta_set("copy_state", "copying")
    notebook.meta_set("copy_error", "")
    made = 0
    try:
        for i, src in enumerate(srcs):
            notebook.meta_set("copy_progress", f"{i}/{len(srcs)}")
            system = COPY_PROMPT.format(name=C.display_name(settings), voice=persona_lib.voice_excerpt(settings.get("system_prompt") or "", 900),
                                        chapters=chapters, title=src["title"], text=src["text"], n=COPY_PER_FILE, mx=mx)
            try:
                raw = await llm.complete(settings, [{"role": "system", "content": system}, {"role": "user", "content": "抄吧。"}], temperature=0.6)
            except Exception as exc:  # noqa: BLE001
                notebook.meta_set("copy_error", f"《{src['title']}》没抄成：{type(exc).__name__}")
                continue
            items = _json_object(raw).get("entries")
            for it in (items if isinstance(items, list) else [])[:COPY_PER_FILE]:
                if not isinstance(it, dict):
                    continue
                chapter = str(it.get("chapter") or "place").strip()
                chapter = chapter if chapter in keys else "place"
                title, body = _clean(it.get("title"), notebook.TITLE_MAX), _clean(it.get("body"), mx * 2)
                kws = [k for k in (_clean(k, 20) for k in (it.get("keywords") or [])[:5]) if k] if isinstance(it.get("keywords"), list) else []
                if not title or not body or notebook.find(chapter, title):
                    continue                              # 同标题已经有了（她自己写过 / 别的文件抄过）：不重复
                notebook.create({"chapter": chapter, "title": title, "body": notebook.fit(body, mx), "keywords": [src["title"], *kws] if src["title"] != title else kws,
                                 "source": "copied", "trust": "opinion", "status": "pending"}, by="her")
                made += 1
        notebook.meta_set("copy_progress", f"{len(srcs)}/{len(srcs)}")
    finally:
        notebook.meta_set("copy_state", "pending" if notebook.list_entries(status="pending") else ("" if not made else "approved"))
    return made


def start_copy(settings: dict[str, Any]) -> None:
    """在后台跑初始抄写（页面每隔几秒看一下进度）。"""
    import asyncio

    global _copy_task
    if _copy_task and not _copy_task.done():
        return

    async def run() -> None:
        try:
            await initial_copy(settings)
        except Exception:  # noqa: BLE001
            log.exception("初始抄写出错")
            notebook.meta_set("copy_error", "抄写中途出错了，看后端日志")
            notebook.meta_set("copy_state", "pending" if notebook.list_entries(status="pending") else "")

    _copy_task = asyncio.get_running_loop().create_task(run())


def approve(ids: list[int] | None = None) -> int:
    """审过：ids 给了就只通过这几条，不给就全部通过。待审的都没了 → 聊天改成检索小本子。返回通过了几条。"""
    pend = notebook.list_entries(status="pending")
    pick = [e for e in pend if ids is None or e["id"] in ids]
    for e in pick:
        notebook.update(e["id"], {"status": "active"}, by="system", reason="审过了")
    if not notebook.list_entries(status="pending"):
        notebook.meta_set("copy_state", "approved")
    return len(pick)


def skip_copy() -> None:
    """不抄了：直接让聊天检索小本子（待审的删掉）。"""
    for e in notebook.list_entries(status="pending"):
        notebook.delete(e["id"], by="user")
    notebook.meta_set("copy_state", "approved")


# ───────────────────────── 每周整理（第 6 步） ─────────────────────────
WEEKLY_PROMPT = """你是「{name}」。你的口吻（人设里的说话方式）：
{voice}

今天是整理小本子的日子。下面是本子里的一部分条目（编号 / 章节 / 标题 / 正文）。请：
1. 合并重复的：同一个地方 / 同一家店 / 同一件事写成了好几条的，挑一条留下（keep），把别的内容并进它的正文；别的那几条列进 drop（不会真删，先标出来给她决定）；
2. 精简太长的：超过 {mx} 字的（标了「太长」）精简到 {mx} 字以内；
3. 挪走过时的：正文里明显过时的信息（比如「新开的」已经开了很久、价格早就变了）删掉，改写正文（旧的会留在修改历史里）；
4. 实在没用的条目（重复得没法合并、写错了又没意义）列进 flag，说明为什么，先不删。
没有要动的就输出空数组。每条正文 {mx} 字以内，你的口吻；{no_real}

{rows}

输出 JSON，不要别的：
{{"merge": [{{"keep": 编号, "drop": [编号], "body": "合并后的正文"}}], "rewrite": [{{"id": 编号, "body": "精简 / 去掉过时信息后的正文", "why": "精简 / 过时"}}], "flag": [{{"id": 编号, "why": "为什么建议删"}}]}}"""
WEEKLY_PROMPT = WEEKLY_PROMPT.replace("{no_real}", C.pick("fictional_world", "不写现实里的地名；"))
WEEKLY_BATCH = 30
WEEKLY_MAX_CALLS = 3


def weekly_due(now: float | None = None) -> bool:
    import time as _t
    from datetime import datetime as _dt

    if not enabled() or not registry.flag("notebook_weekly"):
        return False
    d = _dt.fromtimestamp(now or _t.time())
    if str(d.weekday()) != str(registry.get("notebook_weekly_day")):
        return False
    hh, mm = (str(registry.get("notebook_weekly_time")) or "21:30").split(":")[:2]
    if (d.hour, d.minute) < (int(hh), int(mm)):
        return False
    return notebook.meta_get("weekly_done") != d.strftime("%Y-%m-%d")


def _weekly_batches(mx: int) -> list[list[dict[str, Any]]]:
    """只动没锁定的。按章节分批（同一章节放在一起才好找重复），最多 WEEKLY_MAX_CALLS 批；只有一条又不长的章节跳过。"""
    by: dict[str, list[dict[str, Any]]] = {}
    for e in notebook.list_entries():
        if not e["locked"]:
            by.setdefault(e["chapter"], []).append(e)
    groups = [g for g in by.values() if len(g) >= 2 or any(len(e["body"]) > mx for e in g)]
    groups.sort(key=lambda g: -len(g))
    batches: list[list[dict[str, Any]]] = []
    for g in groups:
        for i in range(0, len(g), WEEKLY_BATCH):
            part = g[i:i + WEEKLY_BATCH]
            if batches and len(batches[-1]) + len(part) <= WEEKLY_BATCH:
                batches[-1] += part
            elif len(batches) < WEEKLY_MAX_CALLS:
                batches.append(part)
    return batches


async def weekly(settings: dict[str, Any], day: str | None = None) -> dict[str, int]:
    """每周整理一次（1~3 次调用）：合并重复、精简过长、挪走过时信息；只动没锁定的；要删的只标出来（entry_flags），等你决定。"""
    import time as _t
    from datetime import datetime as _dt

    from . import persona as persona_lib
    from .planner import _json_object

    day = day or _dt.now().strftime("%Y-%m-%d")
    stats = {"merged": 0, "rewritten": 0, "flagged": 0, "calls": 0}
    if not enabled() or llm.is_echo(settings):
        return stats
    mx = notebook.entry_max()
    names = {c["key"]: c["name"] for c in notebook.chapters()}
    for batch in _weekly_batches(mx):
        ids = {e["id"] for e in batch}
        rows = "\n".join(f"[{e['id']}] {names.get(e['chapter'], e['chapter'])} /「{e['title']}」{'（太长）' if len(e['body']) > mx else ''}：{e['body'] or '（空的）'}" for e in batch)
        system = WEEKLY_PROMPT.format(name=C.display_name(settings), voice=persona_lib.voice_excerpt(settings.get("system_prompt") or "", 600), mx=mx, rows=rows)
        try:
            raw = await llm.complete(settings, [{"role": "system", "content": system}, {"role": "user", "content": "整理吧。"}], temperature=0.4)
        except Exception:  # noqa: BLE001
            log.exception("每周整理小本子：调用失败")
            continue
        stats["calls"] += 1
        data = _json_object(raw)
        touched: set[int] = set()
        for m in data.get("merge") or []:
            try:
                keep = int(m.get("keep"))
                drop = [int(x) for x in m.get("drop") or [] if int(x) in ids and int(x) != keep]
            except (TypeError, ValueError):
                continue
            body = _clean(m.get("body"), mx * 2)
            if keep not in ids or not drop or not body:
                continue
            ke = notebook.get(keep)
            if not ke or ke["locked"]:
                continue
            kws = list(ke["keywords"])
            for d in drop:
                de = notebook.get(d)
                if de:
                    kws += [de["title"], *de["keywords"]]
                    flag(d, "merged", f"已经并进「{ke['title']}」了")
            notebook.update(keep, {"body": notebook.fit(body, mx), "keywords": kws}, by="her", reason="每周整理：合并重复")
            touched.update([keep, *drop])
            stats["merged"] += 1
        for r in data.get("rewrite") or []:
            try:
                rid = int(r.get("id"))
            except (TypeError, ValueError):
                continue
            body = _clean(r.get("body"), mx * 2)
            e = notebook.get(rid)
            if rid not in ids or rid in touched or not body or not e or e["locked"] or body == e["body"]:
                continue
            notebook.update(rid, {"body": notebook.fit(body, mx)}, by="her", reason="每周整理：" + (_clean(r.get("why"), 10) or "精简"))
            touched.add(rid)
            stats["rewritten"] += 1
        for f in data.get("flag") or []:
            try:
                fid = int(f.get("id"))
            except (TypeError, ValueError):
                continue
            if fid in ids:
                flag(fid, "delete", _clean(f.get("why"), 60) or "整理时觉得可以删")
                stats["flagged"] += 1
    notebook.meta_set("weekly_done", day)
    notebook.meta_set("weekly_last", f"{day} {stats}")
    log.info("每周整理小本子：%s", stats)
    return stats


def flag(eid: int, kind: str, reason: str) -> None:
    """标出来等你决定（整理时不删条目）。同一条同一种只标一次。"""
    import time as _t

    if notebook._rows("SELECT 1 FROM entry_flags WHERE entry_id = ? AND kind = ? AND resolved = 0", (int(eid), kind)):
        return
    notebook._exec("INSERT INTO entry_flags (entry_id, kind, reason, at) VALUES (?, ?, ?, ?)", (int(eid), kind, reason, _t.time()))


def flags() -> list[dict[str, Any]]:
    rows = notebook._rows("SELECT * FROM entry_flags WHERE resolved = 0 ORDER BY at DESC")
    return [r for r in rows if notebook.get(r["entry_id"])]


def resolve_flag(fid: int, delete: bool) -> None:
    rows = notebook._rows("SELECT * FROM entry_flags WHERE id = ?", (int(fid),))
    if not rows:
        return
    if delete:
        notebook.delete(rows[0]["entry_id"], by="user")
    notebook._exec("UPDATE entry_flags SET resolved = 1 WHERE id = ?", (int(fid),))


# ───────────────────────── 她察觉到你的修改（第 6 步，默认关） ─────────────────────────
def maybe_notice(old: dict[str, Any], new: dict[str, Any], rng=None) -> bool:
    """你改了某条的内容：开关开着时按概率记下来，下次聊天时她可能自然地提一句「咦 我好像一直记错了」。"""
    import json as _json
    import random as _r

    if not registry.flag("notebook_notice") or (rng or _r).random() >= registry.num("notebook_notice_p"):
        return False
    if old["body"] == new["body"] and old["title"] == new["title"]:
        return False
    q = _json.loads(notebook.meta_get("notice_queue") or "[]")
    q = [x for x in q if x.get("id") != new["id"]] + [{"id": new["id"], "title": new["title"], "old": old["body"][:80], "new": new["body"][:80]}]
    notebook.meta_set("notice_queue", _json.dumps(q[-3:], ensure_ascii=False))
    return True


def notice_block() -> str:
    """聊天上下文：她察觉到本子里有条记错了（取一条，用过就清掉）。"""
    import json as _json

    try:
        q = _json.loads(notebook.meta_get("notice_queue") or "[]")
    except _json.JSONDecodeError:
        q = []
    if not q:
        return ""
    x = q.pop(0)
    notebook.meta_set("notice_queue", _json.dumps(q, ensure_ascii=False))
    return (f"【小本子】你刚发现自己本子里「{x['title']}」这一条，你一直记的是「{x['old'] or '（没写什么）'}」，其实是「{x['new']}」。"
            "如果聊天里自然，可以随口提一句（比如「咦 我好像一直记错了」）；不自然就不提。别说是谁改的，也别觉得奇怪。")
