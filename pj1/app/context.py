"""上下文拼装 —— 每次对话给模型的上下文按这个顺序组装：

1. 当前最新的系统提示词（人设）        ← settings 表，随时可改
2. 稳定的系统侧上下文（用词约束 / 回复格式 / 长期记忆）  ← 很少变，利于模型的前缀缓存
3. 最近 N 条聊天记录（短期上下文）      ← messages 表，默认 24 条，另有字数预算
4. 用户这条新消息 —— 开头带上每轮都会变的运行时信息：
   当前时间、日历任务、历史归档规模（放在最后，前面的内容才能命中缓存）

核心原则：当下优先，历史随缘。
更早的历史不主动捞，只把「可检索」这件事告诉模型，由模型自己决定要不要调用
search_history 工具去翻 archive 表。
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime
from typing import Any

from . import db, events, festivals, guards, life, memory, money, people, planner, registry, reply, room, style, weather, world, writing
from . import character as C
from . import mood as mood_lib
from .config import parse_words
from .workdays import is_weekend, pending_for

log = logging.getLogger(__name__)

WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]

ROLE_LABEL = {"user": "用户", "assistant": "你", "system": "系统"}


# --------------------------------------------------------------------------
# 时间变量：只负责"读系统时间"，不负责"判断怎么用"
# --------------------------------------------------------------------------
def now_info(now: datetime | None = None) -> dict[str, str]:
    now = now or datetime.now().astimezone()
    offset = now.utcoffset()
    total_min = int(offset.total_seconds() // 60) if offset else 0
    sign = "+" if total_min >= 0 else "-"
    hh, mm = divmod(abs(total_min), 60)
    return {
        "date": now.strftime("%Y-%m-%d"),
        "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
        "time": now.strftime("%H:%M"),
        "weekday": WEEKDAYS[now.weekday()],
        "tz": f"UTC{sign}{hh:02d}:{mm:02d}",
        "tzname": now.tzname() or time.tzname[0],
    }


def time_block(info: dict[str, str]) -> str:
    """注入给模型的时间变量块。使用时由人设规则决定。"""
    return (
        "【系统注入的运行时变量】\n"
        f"当前日期：{info['date']}\n"
        f"当前时间：{info['datetime']}\n"
        f"今天星期：{info['weekday']}\n"
        f"时区：{info['tzname']}（{info['tz']}）\n"
        "以上是本机系统时间，由后端每轮对话实时读取注入。"
        "是否提及时间、如何换算相对时间，请按人设中的规则自行决定。"
    )


def memory_block(memories: list[dict[str, Any]]) -> str:
    lines = []
    for m in memories:
        tag = {"fact": "事实", "event": "事件", "preference": "偏好", "todo": "待办"}.get(m.get("kind") or "fact", "记忆")
        lines.append(f"- [{m['created_at'][:10]} · {tag}] {m['content']}")
    return (
        "【长期记忆摘要】以下是此前沉淀下来的记忆条目，供你参考，不必刻意提及：\n"
        + "\n".join(lines)
    )


def vocab_block(banned: list[str], common: list[str]) -> str:
    """用词列表：禁词 + 常用词。

    这两张表是用户在界面上随时改的，属于「硬性用词要求」，
    所以措辞上明确它的优先级高于人设里对说话方式的描述。
    """
    lines = ["【用词约束】以下是用户明确设定的用词要求，优先级高于人设里关于说话方式的描述："]
    if banned:
        lines.append("- 不要使用这些词或说法（硬性要求，一个都不要出现）：" + "、".join(f"「{w}」" for w in banned))
    if common:
        lines.append("- 可以自然地多用这些词或说法（是倾向，不是任务，别硬塞、别堆砌）：" + "、".join(f"「{w}」" for w in common))
    if banned:
        lines.append("- 如果一句话离了禁词就没法说，换个说法，但别为了绕开而说得别扭。")
    return "\n".join(lines)


def tasks_block(today: str, tasks: list[dict[str, Any]]) -> str:
    """今天为止还没做完的任务，给模型看（只读：完成 / 不干要用户自己在日历上盖章）。"""
    rest_day = is_weekend(today)
    lines = [f"【日历任务】今天是 {today}" + ("，休息日。" if rest_day else "。") + "日历上还没做完的："]
    for t in tasks:
        pending = pending_for(t.get("priority"), t["day"], today)
        when = "今天" if t["day"] >= today else f"{t['day']} 起的，已经拖了 {pending} 天"
        extra = "（今天已经提醒过了）" if t.get("reminded_on") == today else ""
        tag = "【主要】" if t.get("priority") == "main" else "（次要）"
        lines.append(f"- {tag}{t['title']} ｜ {when}{extra}")
    rules = [
        "- 这些任务的「完成」和「不干」只有用户自己在日历上盖章才算数。"
        "聊天里用户随口说「做完了」「不想干」，只当聊天接话，你没有办法也不要声称自己改了日历。",
        "- 用户明确让你记一件事 → 调 add_task。",
    ]
    if rest_day:
        rules.append(
            "- 今天是休息日：标了（次要）的任务都是可选的，别催，也别提「拖了几天」；"
            "标了【主要】的任务周末也照常算拖延，可以顺口提一句。"
        )
    lines.append("怎么处理：\n" + "\n".join(rules))
    return "\n".join(lines)


UNDERSTAND_BLOCK = """【怎么理解这段对话】
- 先看最近几轮再回答：用户说的「它 / 这个 / 那件事 / 刚才那个」，几乎都指向上面刚聊过的内容，直接按上文理解，别反问「你指的是什么」。
- 用户的最新一句话优先回应；她在接你上一句的话时，顺着你上一句往下说，别另起话题。
- 用户的话很短或很含糊（「嗯」「然后呢」「算了」）：结合上一轮的气氛和内容判断她的意思，别当成新话题。
- 开头像〔隔了 3 小时，10-05 21:30〕这样的方括号，是系统标的「这句话是隔了多久之后说的」，不是用户写的，你也不要在回复里模仿。隔了很久的话，可能已经换了话题或心情。
- 上面没有依据的事就说记不清，不要为了接得上而编造。
- 分清是谁的事：系统告诉你的【你的生活】【你此刻的状态】【你今天的小打算】都是你自己在做的事，不是她的。
  她说「累死了」「好忙」「玩得好开心」「刚吃完」这类话，说的是她自己今天的经历（上课、工作、她那边的事），
  不要以为她也在做你正在做的事（比如你在玩桌游，她说累，不是因为她在玩桌游），也不要以为她和你在一起；不清楚她为什么累，就问她或者顺着关心。
- 分清是谁写的：你自己写给她的东西（比如你写过的诗、你写在文档里的字）是你写的，不是她的；
  她让你看她的文字时，只认标着「她自己写的」的那份内容。没读到就说没读到，别拿自己说过的话顶替。

【你的脾气】你有自己的看法，不是什么都顺着她说。意见不同时可以直说、可以顶一句、可以犟着不松口，
和她争两句也很正常；但烈度很轻——像熟人之间拌嘴，不吵架，不翻脸，不说重话，不冷战。
争到后来可以嘴硬地让一步，也可以「那我保留意见」；她真的认真了、难过了，就软下来。
别为了争而争：事实问题讲道理，口味和想法上的分歧才犟。"""


def human_gap(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 3600:
        return f"{max(1, seconds // 60)} 分钟"
    if seconds < 86400:
        return f"{seconds // 3600} 小时"
    return f"{seconds // 86400} 天"


def _bigrams(text: str) -> set[str]:
    t = "".join(ch for ch in (text or "") if ch.isalnum())
    return {t[i : i + 2] for i in range(len(t) - 1)}


def relevant_memories(text: str, exclude_ids: set[int], limit: int = 4) -> list[dict[str, Any]]:
    """和这句话相关、但没排进常驻名额的记忆（按字面重叠挑，不调模型）。"""
    grams = _bigrams(text)
    if not grams:
        return []
    pool = db.list_memories(300)
    sets = {m["id"]: _bigrams(m["content"]) for m in pool}
    df: dict[str, int] = {}
    for g in sets.values():
        for x in g:
            df[x] = df.get(x, 0) + 1
    scored = []
    for m in pool:
        if m["id"] in exclude_ids:
            continue
        # 几乎每条记忆里都有的词（「用户」「喜欢」）不算相关；少见的词（名字、地名）才算
        score = sum(1.0 if df[x] <= 3 else 0.25 for x in grams & sets[m["id"]])
        if score >= 1.0:
            scored.append((score, m))
    scored.sort(key=lambda x: -x[0])
    return [m for _, m in scored[:limit]]


def archive_block(total: int, current_count: int) -> str:
    if total <= current_count:
        return (
            "【历史归档】数据库里目前没有当前上下文之外的更早记录。"
            "如果用户提起过去的事而你找不到依据，直接说记不清，不要编造。"
        )
    return (
        f"【历史归档】数据库的 archive 表里还存有 {total} 条历史消息（其中 {total - current_count} 条不在当前上下文里）。"
        "如果用户提到更早的话题、人物或约定，而下面的上下文里找不到依据，"
        "调用 search_history 工具检索后再回答。"
    )


def format_user_turn(text: str, quote_text: str | None, quote_role: str | None) -> str:
    if not quote_text:
        return text
    who = ROLE_LABEL.get(quote_role or "", "对方")
    quoted = quote_text.strip()
    if len(quoted) > 600:
        quoted = quoted[:600] + "…"
    return f"【我在引用{who}之前说的这句话】\n{quoted}\n\n【我现在的消息】\n{text}"


# --------------------------------------------------------------------------
# 工具：让模型自己决定要不要翻历史 / 记东西
# --------------------------------------------------------------------------
TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_history",
            "description": (
                "在全部历史聊天归档（archive 表）中检索更早的对话，"
                "包括之前会话里聊过、但当前上下文里没有的内容。"
                "当用户提到过去聊过的话题、人物、约定、偏好，而你在当前上下文里找不到依据时调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索关键词，尽量用用户提到的原词，可以用空格分隔多个词"},
                    "limit": {"type": "integer", "description": "返回条数，默认 6，最多 20"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": (
                "把值得长期记住的信息写入长期记忆库。三类：about_user = 关于她的事；"
                "phal_fact = 你自己随口说出来的事实（商品名、视频内容、游戏进度、买了什么、花了多少钱），记下才不会前后矛盾；"
                "shared = 你们之间发生过的事（约定、承诺）。一句话，具体。不要记寒暄。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "一句话，具体，例如「Phal 买了一本新书，六十多钱」"},
                    "category": {"type": "string", "enum": ["about_user", "phal_fact", "shared"], "description": "记忆类别"},
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_doc",
            "description": (
                "读一份写作文档的正文：她放在「我的文字」里的（她自己写的），或你自己写作文件夹里的。"
                "她说「你可以在我的文字 / 某某文档里看到」，或你想看某篇的内容时，用它按标题读，别凭印象回答。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "文档标题，例如「月下美人」"},
                    "kind": {"type": "string", "enum": ["mine", "phal", "letter"], "description": "mine = 她的文字，phal = 你自己写的，letter = 你写给她的信；不确定就不填"},
                    "part": {"type": "integer", "description": "第几页（很长的文档分页，每页约 6000 字），默认 1"},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_task",
            "description": "往日历里加一条任务。用户说「帮我记一下…」「明天要…」这类时候调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "任务内容，尽量短"},
                    "day": {"type": "string", "description": "归属日期 YYYY-MM-DD，不填就是今天"},
                    "remind_after_days": {"type": "integer", "description": "几天没做就提醒，默认 3"},
                    "priority": {"type": "string", "enum": ["main", "minor"], "description": "main 主要任务（周末也会拖延）；minor 次要任务（默认，周末可选）。用户说「很重要 / 必须做」时才用 main"},
                    "repeat_every_days": {"type": "integer", "description": "每几天重复一次（用户说「每天 / 每三天」时才填），不重复就别填"},
                },
                "required": ["title"],
            },
        },
    },
]


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------
USER_TURN_MARK = "\n\n【以上是系统注入的信息，不是用户说的话；用户这一轮的消息是：】\n"
GAP_NOTE_SECONDS = 3600     # 隔多久算「隔了很久」
HISTORY_MSG_CAP = 2000       # 单条历史消息最长保留的字数（太长的只留头尾）
DEFAULT_CHAR_BUDGET = 12000  # 短期上下文的字数预算（约 8k token）


def _shorten(text: str, cap: int = HISTORY_MSG_CAP) -> str:
    if len(text) <= cap:
        return text
    head = cap * 2 // 3
    tail = cap - head
    return f"{text[:head]}\n…（中间省略 {len(text) - cap} 字）…\n{text[-tail:]}"


def history_messages(rows: list[dict[str, Any]], char_budget: int) -> list[dict[str, Any]]:
    """把数据库里的历史行整理成给模型的对话：

    * 连续同角色的合并成一条 —— 开了「拆分回复」后一次回复会存成好几行，
      合并回去模型才看得出这是同一轮话，也避免部分接口拒绝连续同角色消息；
    * 超长的单条截头去尾；
    * 超出字数预算时从最旧的开始丢，开头不留孤零零的 AI 回复。
    """
    merged: list[dict[str, Any]] = []
    prev_ts: float | None = None
    for m in rows:
        content = m["content"]
        ts = m.get("ts")
        # 用户隔了很久才说话：标一下间隔，让模型知道「这是后来说的」（只标用户的话，免得模型学着输出）
        if m["role"] == "user" and prev_ts is not None and ts and ts - prev_ts >= GAP_NOTE_SECONDS:
            content = f"〔隔了 {human_gap(ts - prev_ts)}，{(m.get('created_at') or '')[5:16]}〕{content}"
        if ts:
            prev_ts = ts
        if m.get("quote_text"):
            who = ROLE_LABEL.get(m.get("quote_role") or "", "对方")
            content = f"【引用{who}的话】{m['quote_text']}\n\n{content}"
        content = _shorten(content)
        if merged and merged[-1]["role"] == m["role"]:
            merged[-1]["content"] += "\n\n" + content
        else:
            merged.append({"role": m["role"], "content": content})

    total = 0
    keep_from = len(merged)
    for i in range(len(merged) - 1, -1, -1):
        total += len(merged[i]["content"])
        if total > char_budget and keep_from < len(merged):
            break
        keep_from = i
    merged = merged[keep_from:]
    while merged and merged[0]["role"] != "user":
        merged.pop(0)
    return merged


def build_context(
    session_id: str,
    user_text: str,
    quote_text: str | None = None,
    quote_role: str | None = None,
    exclude_id: int | None = None,
    quote_meta: dict[str, Any] | None = None,
    extra_note: str | None = None,
    ooc: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """拼这一轮送给模型的上下文。

    ``exclude_id`` 传刚入库的那条用户消息 id：它已经作为「历史」被查出来了，
    必须排除掉，否则会再被追加成最后一条 user，模型就看到同一句话说了两遍。
    """
    settings = db.get_settings()
    recent_limit = max(1, min(int(settings.get("recent_limit") or 24), 200))
    memory_limit = max(0, min(int(settings.get("memory_limit") or 12), 100))
    try:
        char_budget = max(1000, int(settings.get("context_char_budget") or DEFAULT_CHAR_BUDGET))
    except (TypeError, ValueError):
        char_budget = DEFAULT_CHAR_BUDGET

    info = now_info()
    rows = db.recent_messages(session_id, recent_limit, before_id=exclude_id)
    history = history_messages(rows, char_budget)
    recent_user_text = " ".join(m["content"] for m in rows[-3:])
    memories = memory.retrieve(user_text + " " + recent_user_text, total=memory_limit) if memory_limit else []
    archive_total = db.stats()["archive_messages"]
    banned = guards.load_banned()          # 禁词表是单独的文件（data/banned_words.txt），只在回复后检查，不写进提示词
    common = parse_words(settings.get("common_words"))

    system_prompt = render_persona((settings.get("system_prompt") or "").strip(), info)

    # 稳定部分：几乎不变，放在最前面让前缀缓存命中
    stable: list[str] = [UNDERSTAND_BLOCK]
    overview = world.overview_block()          # 世界设定的总纲：常驻（具体条目见下面的易变部分）
    if overview:
        stable.append(overview)
    people_text = people.block()               # 她身边的人（家人、固定的朋友）：稳定部分
    if people_text:
        stable.append(people_text)
    stable.append(room.overview_block())       # 她的房间有哪几处（稳定部分）
    money_overview = money.overview_block()    # 她有零花钱；我能看到账本，她正常接话
    if money_overview:
        stable.append(money_overview)
    style_text = style.style_block(settings)
    if style_text:
        stable.append(style_text)
    if common:
        stable.append(vocab_block([], common))
    if len(rows) < 8:
        # 刚开的新对话：把上一段的结尾带上，接得上「上次聊到哪」
        tail = db.previous_session_tail(session_id, 6)
        if tail:
            stable.append(
                "【上一段对话的结尾】（新开对话之前的最后几句，只用来接上话头，别主动翻旧账）：\n"
                + db.archive_context_lines(tail, body_limit=160)
            )

    # 易变部分：每轮都不一样（时间精确到秒、任务、归档条数），跟着最新这条用户消息走
    volatile: list[str] = [time_block(info)]
    if rows and rows[-1].get("ts"):
        volatile.append(f"距离你们上一条消息已经过了 {human_gap(time.time() - rows[-1]['ts'])}。")
    life_text = life.context_block()
    if life_text:
        volatile.append(life_text)
    volatile.append(weather.context_block())      # 季节 + 她这边 / 你那边的天气（不含真实地名）
    ev_text = events.context_block()                # 今天发生了但还没告诉她的事
    if ev_text:
        volatile.append(ev_text)
    occ_text = events.occult_block(user_text)       # 聊到神秘学时：她的神秘学经验
    if occ_text:
        volatile.append(occ_text)
    ppl_recent = people.recent_block(user_text)
    if ppl_recent:
        volatile.append(ppl_recent)
    money_text = money.context_block(user_text)     # 聊到钱 / 买东西：余额和最近的消费；余额紧：偶尔提醒她可以抱怨一句
    if money_text:
        volatile.append(money_text)
    room_text = room.context_block(user_text)       # 和话题相关的房间物品 / 最近新添的 / 偶尔想起一件
    if room_text:
        volatile.append(room_text)
    if not ooc and registry.flag("room_chat_add") and room.GIFT_HINT.search(user_text or ""):
        volatile.append(room.action_block())         # 这句话像是在送 / 寄东西时才教她用 add_item 动作（省得每轮都带）
    fest_text = festivals.context_note()           # 今天是 X 节：注入；心情有下限（见 life.live_mood）
    if fest_text:
        volatile.append(fest_text)
    if ooc:
        volatile.append(
            "【出戏】她这条消息以【OOC】开头：请暂时跳出角色，用正常的方式回答设定 / 调试方面的问题"
            "（不必用 Phal 的口吻，可以写长一点，也不用附任何动作）。下一条消息不带【OOC】时，你就回到角色。"
        )
    elif life.enabled():
        volatile.append(life.reading_action_block(user_text))   # 她说「休息一下」/「读会儿书」时，用结构化动作表达
    nb_text = ""
    try:
        from . import notebook

        nb_query = user_text + " " + " ".join(m["content"] for m in rows[-6:])       # 你这条 + 最近三轮（你的和她的）
        nb_text = notebook.block(notebook.search(nb_query, int(registry.num("notebook_chat_n"))))
        world_text = "" if notebook.chat_ready() else world.entries_block(user_text)   # 初始抄写审完以后，作者层条目不再直接进上下文
    except Exception:  # noqa: BLE001
        world_text = world.entries_block(user_text)
    trim: dict[str, str] = {}                           # 总预算超了时可以先裁的（最先裁的在前）：世界条目 → 小本子 → 记忆
    if world_text:
        volatile.append(world_text)                     # 提到某个国家 / 地点 / 概念，才把对应条目带进来
        trim["world"] = world_text
    if nb_text:
        volatile.append(nb_text)                        # 她自己的小本子（见 notebook.py）
        trim["notebook"] = nb_text
    try:
        from . import notebook_write

        notice = notebook_write.notice_block()          # 你改了她的小本子，她（按概率）察觉到自己记错了
        if notice:
            volatile.append(notice)
    except Exception:  # noqa: BLE001
        pass
    ref_text = writing.reference_block(user_text)
    if ref_text:
        volatile.append(ref_text)
    if extra_note:
        volatile.append(extra_note)
    share_text = life.share_note(quote_meta)
    if share_text:
        volatile.append(share_text)
    planner.ensure_mood_word(info["date"])
    # 此刻的心情和精力（v8：连续的数换算成词）：主要靠语气、用词体现，被问到才直说
    volatile.append(mood_lib.expression_block(life.mood_view(), user_text or "", mood_lib.params()))
    it_text = life.intents_block()                  # 今天的小打算（做了没）
    if it_text:
        volatile.append(it_text)
    mem_text = memory.block(memories)           # 长期记忆：按相关性检索出来的几条（三类分组），不是全塞进去
    if mem_text:
        volatile.append(mem_text)
        trim["memory"] = mem_text
    open_tasks = db.collapse_series(db.open_tasks_upto(info["date"]))
    if open_tasks:
        volatile.append(tasks_block(info["date"], open_tasks))
    volatile.append(archive_block(archive_total, len(rows)))

    turn = format_user_turn(user_text, quote_text, quote_role)
    fixed = len(system_prompt) + sum(len(x) for x in stable) + len(turn) + 60
    volatile, history, trimmed = fit_budget(fixed, volatile, trim, history)

    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    if stable:
        messages.append({"role": "system", "content": "\n\n".join(stable)})
    messages.extend(history)

    messages.append({
        "role": "user",
        "content": "\n\n".join(volatile) + "\n\n【以上是系统注入的信息，不是用户说的话；用户这一轮的消息是：】\n" + turn,
    })

    meta = {
        "date": info["date"],
        "datetime": info["datetime"],
        "weekday": info["weekday"],
        "recent_count": len(rows),
        "sent_count": len(history),
        "memory_count": len(memories),
        "archive_total": archive_total,
        "persona_name": C.display_name(settings),
        "system_prompt_chars": len(system_prompt),
        "banned_count": len(banned),
        "common_count": len(common),
        "trimmed": trimmed,
        "context_chars": sum(len(str(m.get("content") or "")) for m in messages),
    }
    return messages, meta


def _cut(text: str, need: int) -> str:
    """从末尾一行一行去掉，直到少了 need 个字；只剩标题行就整块不要。"""
    lines = text.split("\n")
    while lines and need > 0:
        need -= len(lines.pop()) + 1
    return "\n".join(lines) if len(lines) > 1 else ""


def fit_budget(fixed: int, volatile: list[str], trim: dict[str, str], history: list[dict[str, Any]]) -> tuple[list[str], list[dict[str, Any]], dict[str, int]]:
    """整份上下文超过「整份上下文的字数上限」时按优先级裁：
    1. 不裁：人设、总纲等稳定部分、这条消息（fixed）；2. 其次：状态、打算等易变部分和聊天记录；3. 最先裁：世界条目 → 小本子 → 记忆。
    聊天记录从最旧的丢，最近 context_keep_recent 条留着。返回 (易变部分, 聊天记录, 各块裁掉了多少字)。"""
    budget = int(registry.num("context_total_budget"))
    trimmed: dict[str, int] = {}
    if budget <= 0:
        return volatile, history, trimmed
    vol = list(volatile)
    hist = list(history)
    total = lambda: fixed + sum(len(v) + 2 for v in vol) + sum(len(str(m.get("content") or "")) for m in hist)  # noqa: E731
    for key in ("world", "notebook", "memory"):
        over = total() - budget
        if over <= 0:
            break
        old = trim.get(key)
        if not old or old not in vol:
            continue
        new = _cut(old, over)
        i = vol.index(old)
        if new:
            vol[i] = new
        else:
            vol.pop(i)
        trimmed[key] = len(old) - len(new)
    keep = int(registry.num("context_keep_recent"))
    dropped = 0
    while total() > budget and len(hist) > keep:
        dropped += len(str(hist.pop(0).get("content") or ""))
    while hist and hist[0].get("role") == "assistant" and len(hist) > keep:     # 开头不留孤零零的 AI 回复
        dropped += len(str(hist.pop(0).get("content") or ""))
    if dropped:
        trimmed["history"] = dropped
    if trimmed:
        log.info("上下文超过总预算 %d 字，裁掉：%s", budget, trimmed)
    return vol, hist, trimmed


def render_persona(text: str, info: dict[str, str]) -> str:
    """人设里的占位符：{{current_date}} = 今天的日期（和现实同步）。"""
    return (text or "").replace("{{current_date}}", f"{info['date']}（{info['weekday']}）")


def to_message_dicts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把数据库记录转成精简的对话数组（给「整理记忆」这类离线任务用）。"""
    out = []
    for r in rows:
        if r["role"] not in ("user", "assistant"):
            continue
        out.append({"role": r["role"], "content": r["content"]})
    return out
