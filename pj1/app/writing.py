"""写作：Phal 的写作文件夹 + 我的文字文件夹。

* ``data/writing/``     —— 我给 Phal 建的空白文档，她试图创作时真的往里写字。
  一份文档没超过一万字就继续往里写；写满之后，她下次想写，要先问我能不能再开一份新的。
* ``data/my_writing/``  —— 我自己的文字（随笔、小说……），她写东西时会模仿它的笔触。

文档就是普通的 .txt 文件（文件名 = 标题），可以在文件夹里直接打开、放进去、删掉；网页里改标题 = 改文件名。
数据库里只记元信息和历史版本（每次改动前留一份快照，随时可以撤销）。
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from . import db, guards, llm, registry
from . import character as C
from .config import DATA_DIR
from .planner import _json_object

log = logging.getLogger(__name__)

WRITING_DIR = DATA_DIR / "writing"
MINE_DIR = DATA_DIR / "my_writing"
LETTER_DIR = DATA_DIR / "letters"          # 她每月月末写给我的信：单独的「信」分类，只读
DOC_LIMIT = 10000                 # 默认：一份文档的字数上限（不含空白）；实际值见 doc_limit()（设置页可改）


def doc_limit() -> int:
    return int(registry.num("writing_doc_limit"))
MAX_VERSIONS = 30
KINDS = {"phal": WRITING_DIR, "mine": MINE_DIR, "letter": LETTER_DIR}
EXTS = (".txt", ".md")


class DocError(ValueError):
    pass


# --------------------------------------------------------------------------
# 基础：文件 + 元信息
# --------------------------------------------------------------------------
SECTION_SEP = "* * *"       # 一份文档里隔开不同文字的分隔符（单独一行）
_SEP_RE = re.compile(r"(?m)^[ \t]*(?:\*[ \t]*){3,}[ \t]*$|^[ \t]*[-—]{3,}[ \t]*$")


def split_sections(content: str) -> list[str]:
    """按分隔符把一份文档拆成「一段一段不同的文字」。"""
    return [p.strip() for p in _SEP_RE.split(content or "") if p.strip()]


def join_sections(parts: list[str]) -> str:
    return ("\n\n" + SECTION_SEP + "\n\n").join(p.strip() for p in parts if p.strip()) + "\n"


def _record_chunk(doc_id: int, text: str) -> None:
    _exec("INSERT INTO doc_chunks (doc_id, text, created_at) VALUES (?, ?, ?)", (int(doc_id), text, _now()))


def count_chars(text: str) -> int:
    return len(re.sub(r"\s+", "", text or ""))


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _folder(kind: str) -> Path:
    if kind not in KINDS:
        raise DocError("文档类型只能是 phal / mine / letter")
    KINDS[kind].mkdir(parents=True, exist_ok=True)
    return KINDS[kind]


_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_title(title: str) -> str:
    t = _BAD.sub(" ", (title or "").strip())
    t = re.sub(r"\s+", " ", t).strip(" .")
    return t[:60]


def _q(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    with db._lock:  # noqa: SLF001
        return [dict(r) for r in db.connect().execute(sql, params).fetchall()]


def _exec(sql: str, params: tuple = ()) -> int:
    with db._lock:  # noqa: SLF001
        conn = db.connect()
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid if sql.lstrip().upper().startswith("INSERT") else cur.rowcount


def read_text(path: Path) -> str:
    from .books import decode_bytes

    return decode_bytes(path.read_bytes()).replace("\r\n", "\n")


def sync_folder(kind: str) -> None:
    """文件夹是真相：用户手动放进去的文件登记上，被删掉的文件从列表里去掉。"""
    folder = _folder(kind)
    files = {p.name: p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in EXTS and not p.name.startswith(".")}
    rows = _q("SELECT * FROM docs WHERE kind = ?", (kind,))
    known = {r["filename"]: r for r in rows}
    for name, path in files.items():
        if name not in known:
            now = _now()
            chars = count_chars(read_text(path))
            _exec(
                "INSERT INTO docs (kind, filename, title, chars, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (kind, name, path.stem, chars, now, now),
            )
    for name, r in known.items():
        if name not in files:
            _exec("DELETE FROM docs WHERE id = ?", (r["id"],))
            _exec("DELETE FROM doc_versions WHERE doc_id = ?", (r["id"],))


def cn_date(day: str) -> str:
    """2026-10-31 → 2026年10月31日。"""
    try:
        d = datetime.strptime(day[:10], "%Y-%m-%d")
    except ValueError:
        return ""
    return f"{d.year}年{d.month}月{d.day}日"


def _decorate(d: dict[str, Any]) -> dict[str, Any]:
    d["full"] = d["kind"] == "phal" and d["chars"] >= doc_limit()
    d["limit"] = doc_limit() if d["kind"] == "phal" else None
    # 只有信在标题下面显示一行小字：年份和日期（其他文档不变）
    d["dated_text"] = cn_date(d.get("dated") or d["created_at"]) if d["kind"] == "letter" else None
    d["readonly"] = d["kind"] == "letter"
    return d


def list_docs(kind: str) -> list[dict[str, Any]]:
    sync_folder(kind)
    return [_decorate(r) for r in _q("SELECT * FROM docs WHERE kind = ? ORDER BY id DESC", (kind,))]


def get_doc(doc_id: int) -> dict[str, Any] | None:
    rows = _q("SELECT * FROM docs WHERE id = ?", (int(doc_id),))
    return _decorate(rows[0]) if rows else None


def doc_path(doc: dict[str, Any]) -> Path:
    return _folder(doc["kind"]) / doc["filename"]


def read_doc(doc_id: int) -> tuple[dict[str, Any], str]:
    doc = get_doc(doc_id)
    if not doc:
        raise DocError("这份文档不存在")
    path = doc_path(doc)
    if not path.exists():
        sync_folder(doc["kind"])
        raise DocError("文档文件不见了（可能被手动删掉了）")
    return doc, read_text(path)


def _unique_name(folder: Path, title: str, exclude: str | None = None) -> str:
    base = safe_title(title) or "未命名"
    name, n = f"{base}.txt", 2
    while (folder / name).exists() and name != exclude:
        name = f"{base} ({n}).txt"
        n += 1
    return name


def create_doc(kind: str, title: str, content: str = "") -> dict[str, Any]:
    folder = _folder(kind)
    name = _unique_name(folder, title)
    (folder / name).write_text(content or "", encoding="utf-8")
    now = _now()
    doc_id = _exec(
        "INSERT INTO docs (kind, filename, title, chars, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (kind, name, Path(name).stem, count_chars(content), now, now),
    )
    if kind == "phal":
        db.save_settings({"writing_ask_day": "", "writing_waiting": "0"})   # 有新文档了：她不用再等了
    return get_doc(doc_id)  # type: ignore[return-value]


def create_letter(title: str, content: str, dated: str) -> dict[str, Any]:
    """她写给我的信：存进单独的「信」分类，不受写作文件夹的规则限制（没有字数上限、不用先要空白文档）。dated = 月末那天。"""
    doc = create_doc("letter", title, content)
    _exec("UPDATE docs SET dated = ? WHERE id = ?", (dated, doc["id"]))
    return get_doc(doc["id"])  # type: ignore[return-value]


def rename_doc(doc_id: int, title: str) -> dict[str, Any]:
    doc = get_doc(doc_id)
    if not doc:
        raise DocError("这份文档不存在")
    if doc["kind"] == "letter":
        raise DocError("信是只读的，写好之后不再改（标题也一样）")
    if not safe_title(title):
        raise DocError("标题不能是空的")
    folder = _folder(doc["kind"])
    new_name = _unique_name(folder, title, exclude=doc["filename"])
    if new_name != doc["filename"]:
        old = folder / doc["filename"]
        if old.exists():
            old.rename(folder / new_name)
        _exec("UPDATE docs SET filename = ?, title = ?, updated_at = ? WHERE id = ?",
              (new_name, Path(new_name).stem, _now(), doc["id"]))
    return get_doc(doc_id)  # type: ignore[return-value]


def delete_doc(doc_id: int) -> bool:
    doc = get_doc(doc_id)
    if not doc:
        return False
    path = doc_path(doc)
    if path.exists():
        path.unlink()
    _exec("DELETE FROM doc_versions WHERE doc_id = ?", (doc["id"],))
    _exec("DELETE FROM doc_chunks WHERE doc_id = ?", (doc["id"],))
    _exec("DELETE FROM doc_story WHERE doc_id = ?", (doc["id"],))
    return bool(_exec("DELETE FROM docs WHERE id = ?", (doc["id"],)))


def _snapshot(doc: dict[str, Any], content: str, reason: str) -> None:
    last = _q("SELECT content FROM doc_versions WHERE doc_id = ? ORDER BY id DESC LIMIT 1", (doc["id"],))
    if last and last[0]["content"] == content:
        return
    _exec("INSERT INTO doc_versions (doc_id, content, reason, created_at) VALUES (?, ?, ?, ?)",
          (doc["id"], content, reason, _now()))
    _exec("DELETE FROM doc_versions WHERE doc_id = ? AND id NOT IN "
          "(SELECT id FROM doc_versions WHERE doc_id = ? ORDER BY id DESC LIMIT ?)",
          (doc["id"], doc["id"], MAX_VERSIONS))


def save_content(doc_id: int, content: str, reason: str = "手动修改") -> dict[str, Any]:
    """整份保存（网页编辑器用）。改动前先留一份快照，随时能撤销。"""
    doc, old = read_doc(doc_id)
    if doc["kind"] == "letter":
        raise DocError("信是只读的，写好之后不再改")
    content = (content or "").replace("\r\n", "\n")
    if content != old:
        _snapshot(doc, old, f"{reason}之前")
        doc_path(doc).write_text(content, encoding="utf-8")
        _exec("UPDATE docs SET chars = ?, updated_at = ? WHERE id = ?", (count_chars(content), _now(), doc["id"]))
    return get_doc(doc_id)  # type: ignore[return-value]


def list_versions(doc_id: int) -> list[dict[str, Any]]:
    rows = _q("SELECT id, reason, created_at, length(content) AS size FROM doc_versions WHERE doc_id = ? "
              "ORDER BY id DESC", (int(doc_id),))
    return rows


def restore_version(doc_id: int, version_id: int) -> dict[str, Any]:
    rows = _q("SELECT * FROM doc_versions WHERE id = ? AND doc_id = ?", (int(version_id), int(doc_id)))
    if not rows:
        raise DocError("没有这个历史版本")
    return save_content(doc_id, rows[0]["content"], reason="恢复历史版本")


# --------------------------------------------------------------------------
# Phal 现在往哪份文档里写
# --------------------------------------------------------------------------
def available_doc() -> dict[str, Any] | None:
    """没写满一万字的文档里，最近改过的那份（一份没写满就继续在它上面写）。"""
    docs = [d for d in list_docs("phal") if not d["full"]]
    if not docs:
        return None
    return max(docs, key=lambda d: (d["updated_at"], d["id"]))


def writing_blocked() -> bool:
    """没有能写的文档了：要么还没有，要么都写满了。"""
    return available_doc() is None


ASK_FULL = [
    "《{t}》写满一万字了。我下次想写的话，能再给我开一份新的吗",
    "那份《{t}》满了。想接着写，得麻烦你给我开个新文档",
]
ASK_NONE = [
    "我想写点东西，可手上还没有空白文档。能给我开一份吗",
    "想动笔了，但没有本子。你给我建个空白文档好不好",
]


def maybe_ask_new_doc() -> bool:
    """想写但没有能写的文档：向你要一份（本地文案，不调模型；每天最多问一次；睡着 / 超出频率时不问）。"""
    from . import reading

    today = datetime.now().strftime("%Y-%m-%d")
    if db.get_settings().get("writing_ask_day") == today:
        return False
    docs = list_docs("phal")
    if docs:
        latest = max(docs, key=lambda d: d["id"])
        text = random.choice(ASK_FULL).format(t=latest["title"])
    else:
        text = random.choice(ASK_NONE)
    if reading.post_proactive("create_ask", text, {"kind": "ask_doc"}):
        db.save_settings({"writing_ask_day": today, "writing_waiting": "1"})
        return True
    return False


# --------------------------------------------------------------------------
# 我的文字：她模仿的对象
# --------------------------------------------------------------------------
def style_samples(n: int = 3, size: int = 420) -> list[str]:
    """从我的文字里随机抽几段（对齐段落边界），给她模仿笔触用。"""
    docs = list_docs("mine")
    pool: list[str] = []
    for d in docs[:12]:
        try:
            text = read_text(doc_path(d))
        except OSError:
            continue
        paras = [p.strip() for p in text.split("\n") if len(p.strip()) >= 20]
        if not paras:
            continue
        for _ in range(3):
            i = random.randrange(len(paras))
            chunk, j = "", i
            while j < len(paras) and len(chunk) < size:
                chunk += paras[j] + "\n"
                j += 1
            pool.append(chunk.strip()[: size + 120])
    random.shuffle(pool)
    return pool[:n]


# --------------------------------------------------------------------------
# 去 AI 味
# --------------------------------------------------------------------------
BANNED_PHRASES = [
    "仿佛", "宛如", "宛若", "一抹", "不禁", "竟然", "缓缓", "静静地", "微微", "嘴角上扬", "嘴角微扬",
    "心中一震", "心头一紧", "心中暗想", "岁月", "时光", "宿命", "灵魂深处", "整个世界", "刹那间", "莫名的",
    "不由得", "说不清", "一股暖流", "眼眸", "眸子", "瞳孔", "深邃", "交织", "如同", "犹如", "似乎在诉说",
    "悄然", "默默地", "倏地", "一丝", "恍惚间", "无尽的", "无声地", "那一刻", "这一刻",
]
BANNED_PATTERNS = [
    (re.compile(r"不是[^。！？\n]{1,24}而是"), "「不是……而是……」句式"),
    (re.compile(r"(?:就)?像[^。！？\n]{1,16}一样[^。！？\n]{0,8}，(?:又)?像[^。！？\n]{1,16}一样"), "连环「像……一样」比喻"),
    (re.compile(r"(?:她|他)(?:知道|明白|意识到|感觉到)[，,]"), "直接解释心理的「她知道，」"),
]


def ai_flavor_hits(text: str) -> list[str]:
    hits = [p for p in BANNED_PHRASES if p in text]
    for pat, name in BANNED_PATTERNS:
        if pat.search(text):
            hits.append(name)
    return hits


DEAI_RULES = """去 AI 味的写法（很重要）：
- 像真人写的：句子长短参差，允许碎、允许跳、允许没写完整，别求圆满；
- 用具体的、有点出人意料的细节（名字、数字、物件、动作、气味），少用抽象形容词和「氛围词」；
- 不要点题，不要说教，不要在结尾上价值、不要总结升华、不要用一句漂亮话收尾；
- 不要排比三连，不要连环比喻；不要直接解释人物心里想什么，让动作和话去露；
- 对话要像人说话：有废话、有打断、有答非所问；
- 下面这些词和句式一个都别出现：{banned}。"""


def _banned_inline() -> str:
    return "、".join(BANNED_PHRASES[:30]) + "、「不是……而是……」、「就像……一样」连用"


# --------------------------------------------------------------------------
# 她写一段（试图创作 / 我让她写）
# --------------------------------------------------------------------------
WRITE_PROMPT = """你是「{name}」。你的人设（只用来决定你是个什么样的人、有什么眼光，不是写作腔调）：
{persona}

你在一份叫《{title}》的文档里写东西。现在文档里已经有 {chars} 字。{doc_part}

{story_part}
{story_rules}

{style_part}
{deai}

{task}
只输出 JSON，不要别的：{{"text": "你写的这一段正文", "story": "更新后的故事笔记，150 字以内：主线是什么 / 主要人物各自想要什么 / 还没解决的事 / 下一段打算让什么发生"}}"""

STORY_RULES = """写的是故事，不是素材堆：
- 这一段要让情节往前走一步——发生一件具体的事（冲突、发现、选择、意外、谈话里的转折、关系的变化），读完比读前多知道点什么；
- 人物有想要的东西，也有挡在前面的东西；让他们做事、说话、做选择，不要站着看风景；
- 环境和动作描写只在为情节服务时才写，一段里不超过三分之一；不要整段都在写天气、光线、物件摆放；
- 前面埋下的线索和没解决的事要接上或推进，别丢；也可以埋一个新的小钩子；
- 不要每一段都收得很圆满，留个口子给下一段。"""


# --------------------------------------------------------------------------
# 故事笔记：每段文字一条，写下一段之前先看，写完更新
# --------------------------------------------------------------------------
def get_story(doc_id: int) -> list[str]:
    rows = _q("SELECT notes FROM doc_story WHERE doc_id = ?", (int(doc_id),))
    if not rows:
        return []
    try:
        data = json.loads(rows[0]["notes"])
    except json.JSONDecodeError:
        return []
    return [str(x) for x in data] if isinstance(data, list) else []


def set_story_note(doc_id: int, index: int, note: str) -> None:
    notes = get_story(doc_id)
    while len(notes) <= index:
        notes.append("")
    notes[index] = (note or "").strip()[:400]
    _exec("INSERT INTO doc_story (doc_id, notes, updated_at) VALUES (?, ?, ?) "
          "ON CONFLICT(doc_id) DO UPDATE SET notes = excluded.notes, updated_at = excluded.updated_at",
          (int(doc_id), json.dumps(notes, ensure_ascii=False), _now()))


async def _llm_piece(settings: dict[str, Any], system: str, user: str, temperature: float = 0.95) -> tuple[str, str]:
    """返回 (正文, 更新后的故事笔记)。"""
    raw = await llm.complete(settings, [{"role": "system", "content": system}, {"role": "user", "content": user}],
                             temperature=temperature)
    data = _json_object(raw)
    text = str(data.get("text") or "").strip()
    if not text:                       # 模型没按 JSON 给：整段当正文，去掉代码围栏
        text = re.sub(r"^```\w*\n?|```$", "", (raw or "").strip()).strip()
        if text.startswith("{"):       # 是 JSON 但解析不了（被截断 / 引号没转义）：只抠出正文，不能把 {"text": 这些写进文档
            text, story = _salvage_json(text)
            return text, story
    return text, str(data.get("story") or "").strip()


def _salvage_json(raw: str) -> tuple[str, str]:
    """从坏掉的 {"text": "...", "story": "..."} 里尽量抠出两个字段；抠不出正文就返回空（这次算没写成）。"""
    def field(name: str) -> str:
        m = re.search(r'"' + name + r'"\s*:\s*"(.*?)(?:"\s*[,}]|$)', raw, flags=re.S)
        if not m:
            return ""
        v = m.group(1)
        if m.end() == len(raw) and v.endswith('"') and not v.endswith('\\"'):
            v = v[:-1]                 # 截在字段结尾的引号上（后面没有 , 或 }）
        try:
            v = json.loads('"' + v.replace('\n', '\\n').rstrip('\\') + '"')
        except json.JSONDecodeError:
            v = v.replace('\\n', '\n').replace('\\"', '"')
        return v.strip()
    return field("text"), field("story")


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").strip()
    text = re.sub(r"^#+\s*", "", text, flags=re.M)        # 不要 markdown 标题
    text = re.sub(r"\*\*|__", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return guards.strip_exclaim(text.strip())          # 感叹号由代码去掉（不算 AI 味、不为它重写一次）


async def write_piece(
    settings: dict[str, Any], doc: dict[str, Any], instruction: str | None = None,
    mode: str = "continue", section_index: int | None = None,
) -> dict[str, Any] | None:
    """往 doc 里写一段。去 AI 味：提示词约束 + 本地词表检查，不合格重写一次。

    mode：
    * continue    接着文档最后写（默认）
    * new_section 另起一段新的文字：用分隔符 * * * 和前面的隔开，不接着前面写
    * revisit     回去续写之前的某一段（section_index 指第几段）：新写的接在那一段的末尾，后面的段落不动
    """
    if llm.is_echo(settings):
        return None
    d, existing = read_doc(doc["id"])
    if d["kind"] != "phal":
        raise DocError("只能往 Phal 的文档里写")
    if d["chars"] >= doc_limit():
        raise DocError("这份文档已经写满一万字了")
    sections = split_sections(existing)
    if mode == "revisit" and (not sections or section_index is None or not 0 <= section_index < len(sections)):
        mode = "continue"
    if mode == "new_section" and not sections:
        mode = "continue"
    name = C.display_name(settings)
    persona = (settings.get("system_prompt") or "")[:900]
    samples = style_samples()
    style_part = ""
    if samples:
        style_part = (
            "下面是她自己写的文字。写的时候模仿它的笔触：句子的节奏、用词习惯、标点、叙述的距离感。"
            "只学「怎么写」，不要抄里面的内容、人物和句子：\n"
            + "\n---\n".join(samples) + "\n"
        )
    ask = f"她要你这样写：{instruction}\n" if instruction else ""
    notes = get_story(d["id"])
    if mode == "new_section":
        idx = len(sections)
        doc_part = ("文档里已经有别的内容了（只给你看一眼，免得重复；这次**不要接着它写**）：\n"
                    + existing[-400:] + "\n")
        task = ask + ("另写一段新的、和前面不是一路的文字（250~500 字）：新的故事的开头——"
                      "谁、想要什么、出了什么事，先把这件事立起来。")
    elif mode == "revisit":
        idx = section_index  # type: ignore[assignment]
        sect = sections[section_index]
        doc_part = "你要回头续写文档里之前写过的一段，那一段是：\n" + sect[-1500:] + "\n"
        task = ask + "接着这一段往下写一小段（200~450 字），和它保持同一个视角、时态和文风，别重复它已经写过的，要让情节往前走。"
    elif existing.strip():
        idx = max(0, len(sections) - 1)
        doc_part = f"文档最后的部分是：\n{existing[-1500:]}\n"
        task = ask + "接着往下写一段（250~500 字），和已有文字保持同一个视角、时态和文风，别重复前面写过的，要让情节往前走。"
    else:
        idx = 0
        doc_part = "文档还是空白的。\n"
        task = ask + "按标题自己定写什么，写一个故事的开头（250~500 字）：谁、想要什么、出了什么事。"
    note = notes[idx] if idx < len(notes) else ""
    if note:
        story_part = "这段故事你之前记下的笔记（写完要更新它）：\n" + note
    elif existing.strip() and mode != "new_section":
        story_part = "这段文字还没有故事笔记：先从上面已有的文字里理出它的主线、人物和没解决的事，再往下写。"
    else:
        story_part = "这是一个新的故事：先在心里定好主线、主要人物想要什么、会遇到什么阻碍，再动笔。"
    system = WRITE_PROMPT.format(
        name=name, persona=persona, title=d["title"], chars=d["chars"], doc_part=doc_part,
        story_part=story_part, story_rules=STORY_RULES,
        style_part=style_part, deai=DEAI_RULES.format(banned=_banned_inline()), task=task)
    user = "写吧。"
    story = ""
    try:
        text, story = await _llm_piece(settings, system, user)
        text = _clean(text)
        hits = ai_flavor_hits(text)
        if hits and text:
            retry_user = (f"你上一稿里出现了这些 AI 味的东西：{'、'.join(hits[:10])}。"
                          "换成具体的、朴素的写法重写这一段，别带上它们。\n\n上一稿：\n" + text)
            retry, retry_story = await _llm_piece(settings, system, retry_user)
            retry = _clean(retry)
            if retry and len(ai_flavor_hits(retry)) <= len(hits):
                text, story = retry, (retry_story or story)
    except Exception as exc:  # noqa: BLE001
        log.warning("写作调用模型失败：%s: %s", type(exc).__name__, exc)
        return None
    if not text:
        return None
    if mode == "new_section":
        new_content = join_sections(sections + [text])
    elif mode == "revisit":
        sections[section_index] = sections[section_index].rstrip() + "\n\n" + text
        new_content = join_sections(sections)
    else:
        new_content = (existing.rstrip("\n") + "\n\n" if existing.strip() else "") + text + "\n"
    _snapshot(d, existing, "Phal 写之前")
    doc_path(d).write_text(new_content, encoding="utf-8")
    _exec("UPDATE docs SET chars = ?, updated_at = ? WHERE id = ?", (count_chars(new_content), _now(), d["id"]))
    _record_chunk(d["id"], text)
    if story:
        set_story_note(d["id"], idx, story)
    fresh = get_doc(d["id"])
    return {"doc": fresh, "text": text, "added": count_chars(text), "full": bool(fresh and fresh["full"]),
            "mode": mode, "flavor_hits": ai_flavor_hits(text)}


# --------------------------------------------------------------------------
# 写之前先想：接着写？回头续写？另起一段？向她要新文档？
# --------------------------------------------------------------------------
P_REVISIT = 0.20          # （旧，不再用）每次写作回去续写之前内容的概率
P_NEW_DOC = 0.40          # 所有文档都写到 NEW_DOC_FILL 以后，想写新东西时先问她要新文档的概率（settings: writing_p_new_doc）
P_SELF_DELETE = 0.35      # （旧，不再用）心情低落时回头删掉写得不好的内容的概率
P_NEWEST_DOC = 0.60       # 挑文档：最近写过的那份的概率；其余在别的没写满的文档里随机（settings: writing_p_newest_doc）
P_NEW_SECTION = 0.25      # 挑段落：另起一段写全新东西的概率；其余在已有的段落里随机挑一段接着写（settings: writing_p_new_section）
NEW_DOC_FILL = 0.80       # 所有文档都写到上限的这么多以后，才可能问她要新文档（settings: writing_new_doc_fill）

PLAN_PROMPT = """你是「{name}」。你的口吻和性格（人设里的说话方式）：
{voice}

你想写点东西。{where}
{tail}

{extra}你现在的状态：心情「{mood}」。{inspiration}

根据你现在的想法决定：
- continue：{cont_q}
- idea：你想到了什么想写的东西，一句话（15 字以内，别写成总结）；如果没有什么特别的想法、只是单纯想写，就留空字符串。
只输出 JSON，不要别的：{{"continue": true, "idea": "…"}}"""


def _setting_float(settings: dict[str, Any], key: str, default: float) -> float:
    if key in registry.BY_KEY:
        return min(1.0, max(0.0, registry.num(key, settings)))
    try:
        return min(1.0, max(0.0, float(settings.get(key) if settings.get(key) not in (None, "") else default)))
    except (TypeError, ValueError):
        return default


def revisit_candidates(docs: list[dict[str, Any]], current: dict[str, Any] | None) -> list[tuple[dict[str, Any], int]]:
    """能「回头续写」的之前的内容：没写满的文档里，除了当前文档最新的那一段之外的、有一定长度的段落。"""
    out: list[tuple[dict[str, Any], int]] = []
    for d in docs:
        try:
            secs = split_sections(read_text(doc_path(d)))
        except OSError:
            continue
        for i, sec in enumerate(secs):
            if len(sec) < 60:
                continue
            if current is not None and d["id"] == current["id"] and i == len(secs) - 1:
                continue            # 最新的那一段就是「接着写」，不算回头
            out.append((d, i))
    return out


def asked_today() -> bool:
    return db.get_settings().get("writing_ask_day") == datetime.now().strftime("%Y-%m-%d")


# 「手头在写的那份」：她最近一次说要写（或者写了）的文档。几个小时之内接着写它，不会一会儿说写这份、一会儿又换那份
STICK_HOURS = 12.0         # settings: writing_stick_hours


def set_focus(doc_id: int, title: str, idea: str = "") -> None:
    db.save_settings({"writing_focus": json.dumps({"doc_id": doc_id, "title": title, "idea": idea, "at": time.time(), "written_at": 0},
                                                  ensure_ascii=False)})


def mark_focus_written(doc_id: int) -> None:
    f = focus(any_age=True)
    if f and f.get("doc_id") == doc_id:
        f["written_at"] = time.time()
        db.save_settings({"writing_focus": json.dumps(f, ensure_ascii=False)})


def focus(now: float | None = None, any_age: bool = False) -> dict[str, Any] | None:
    try:
        f = json.loads(db.get_settings().get("writing_focus") or "")
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(f, dict) or not f.get("doc_id"):
        return None
    hours = registry.num("writing_stick_hours")
    last = max(float(f.get("at") or 0), float(f.get("written_at") or 0))
    if not any_age and (now or time.time()) - last > hours * 3600:
        return None
    return f


def choose_doc(docs: list[dict[str, Any]], settings: dict[str, Any], rng: random.Random | None = None) -> dict[str, Any]:
    """挑这次写哪份：手头在写的那份（几个小时内说过要写 / 写过）就接着写它；
    否则最近写过的那份占 P_NEWEST_DOC，其余在别的没写满的文档里随机。"""
    rng = rng or random
    f = focus()
    if f:
        for d in docs:
            if d["id"] == f["doc_id"]:
                return d
    newest = max(docs, key=lambda d: (d["updated_at"], d["id"]))
    others = [d for d in docs if d["id"] != newest["id"]]
    if others and rng.random() >= _setting_float(settings, "writing_p_newest_doc", P_NEWEST_DOC):
        return rng.choice(others)
    return newest


def all_docs_filled(settings: dict[str, Any]) -> bool:
    """所有文档（她的）都写到上限的 NEW_DOC_FILL 以上了吗（写满的算 100%）。"""
    docs = list_docs("phal")
    fill = _setting_float(settings, "writing_new_doc_fill", NEW_DOC_FILL)
    return bool(docs) and all(d["full"] or d["chars"] >= fill * doc_limit() for d in docs)


async def plan_writing(
    settings: dict[str, Any], instruction: str | None = None, doc_hint: dict[str, Any] | None = None,
    mood: str = "平静", inspiration: str = "", rng: random.Random | None = None,
) -> dict[str, Any]:
    """决定这次怎么写。返回 plan：
    branch：continue 接着最后一段写 / revisit 接着之前的某一段写（section）/ new_section 另起一段写全新的东西 /
            new_doc 先向她要一份新文档（不写）/ no_doc 手上没有能写的文档
    motive：idea（想到了什么才想写）或 whim（只是想写了）；idea 是那句想法。
    v10.6：先挑文档（最近写过的 60%，其余随机），再挑段落（另起一段 25%，其余在已有段落里随机挑一段），
    然后把那一段的末尾给模型看，由她决定接不接得上（接不上就改成另起一段）；
    只有所有文档都写到 80% 以后，想写新东西时才可能问她要新文档。"""
    rng = rng or random
    docs = [d for d in list_docs("phal") if not d["full"]]
    if not docs:
        return {"branch": "no_doc"}
    cur = doc_hint if (doc_hint and not doc_hint.get("full") and doc_hint.get("kind") == "phal") else choose_doc(docs, settings, rng)
    content = read_text(doc_path(cur))
    secs = split_sections(content)
    p_new_section = _setting_float(settings, "writing_p_new_section", P_NEW_SECTION)
    # 挑段落：空文档只能接着写；否则随机挑一段续写，或者另起一段
    if not secs or not content.strip():
        target, idx = "continue", None
    elif rng.random() < p_new_section:
        target, idx = "new_section", None
    else:
        idx = rng.randrange(len(secs))
        target = "continue" if idx == len(secs) - 1 else "revisit"

    cont: bool | None = None
    idea = ""
    if not llm.is_echo(settings):
        try:
            from . import persona as persona_lib

            extra = f"她刚才让你写点东西：「{instruction}」。\n" if instruction else ""
            if target == "new_section":
                where = f"你打算在《{cur['title']}》里另起一段，写点和前面不一样的新东西。前面写到的末尾是："
                tail = content[-300:].strip() or "（还是空白的）"
                cont_q = "这个想法要不要就这样另起一段写（true）；要是其实更想接着前面写，填 false。"
            elif target == "revisit":
                where = f"你翻到《{cur['title']}》（{cur['chars']} 字，共 {len(secs)} 段）里之前写的第 {idx + 1} 段，它的末尾是："
                tail = secs[idx][-400:].strip()
                cont_q = "要不要接着这一段往下写。脑子里的想法和它接得上，就接（true）；接不上、或者想写点别的，就不接（false）。"
            else:
                where = f"你最近在写的文档是《{cur['title']}》（{cur['chars']} 字，共 {max(1, len(secs))} 段；不同的文字之间用单独一行的 * * * 隔开），末尾是："
                tail = content[-400:].strip() or "（还是空白的）"
                cont_q = "要不要接着这份文档往下写。脑子里的新想法和它接得上，就接（true）；接不上、或者想写点别的，就不接（false）。"
            raw = await llm.complete(
                settings,
                [{"role": "system", "content": PLAN_PROMPT.format(
                    name=C.display_name(settings),
                    voice=persona_lib.voice_excerpt(settings.get("system_prompt") or "", 900),
                    where=where, tail=tail, cont_q=cont_q, extra=extra, mood=mood, inspiration=inspiration or "")},
                 {"role": "user", "content": "想好了就说。"}],
                temperature=0.9,
            )
            data = _json_object(raw)
            if isinstance(data.get("continue"), bool):
                cont = data["continue"]
            idea = re.sub(r"[！!]", "", str(data.get("idea") or "").strip())[:24]
        except Exception as exc:  # noqa: BLE001
            log.warning("决定怎么写的调用失败，随机定：%s", exc)
    if cont is None:
        cont = True

    plan: dict[str, Any] = {"doc_id": cur["id"], "idea": idea, "motive": "idea" if idea else "whim", "continue_wanted": bool(cont)}
    if target == "new_section" and not cont:
        target, idx = "continue", None                     # 其实更想接着前面写
    elif target in ("continue", "revisit") and not cont and content.strip():
        target, idx = "new_section", None                  # 接不上：另起一段
    if target == "new_section" and all_docs_filled(settings) and not asked_today() \
            and rng.random() < _setting_float(settings, "writing_p_new_doc", P_NEW_DOC):
        target = "new_doc"                                 # 文档都快写满了，想写新的：问她要一份新文档
    plan["branch"] = target
    if target == "revisit":
        plan["section"] = idx
    return plan


# 写之前她先说一声：为什么写 —— 是想到了什么，还是只是想写了。本地文案，不调模型。
_ANN: dict[tuple[str, str], list[str]] = {
    ("continue", "idea"): ["刚想到{idea}\n想接着写《{t}》", "脑子里冒出{idea}\n接着写《{t}》吧"],
    ("continue", "whim"): ["没什么特别的\n就是想写了\n接着写《{t}》", "嗯 就是想写\n《{t}》接着来"],
    ("revisit", "idea"): ["刚想到{idea}\n想回去把《{t}》前面那段接上", "{idea}\n突然想回头续《{t}》里之前写的"],
    ("revisit", "whim"): ["忽然想把《{t}》前面那段接着写\n没为什么", "就是想写\n回头续一下《{t}》之前那段"],
    ("new_section", "idea"): ["刚想到{idea}\n跟《{t}》里写的不是一路\n我在后面隔开写", "{idea}\n《{t}》后面空一行隔开\n另写一段"],
    ("new_section", "whim"): ["就是想写点别的\n我在《{t}》后面隔开写", "嗯 想换个写法\n在《{t}》后面另起一段"],
    ("new_doc", "idea"): ["刚想到{idea}\n跟现有的都不搭\n能给我建个新文档存吗", "有个新想法：{idea}\n想单独放\n你能给我建个新文档吗"],
    ("new_doc", "whim"): ["想写点新的东西\n跟现有的不搭\n能给我建个新文档吗", "就是想写新的\n能不能给我建个新文档"],
}


def announce_lines(plan: dict[str, Any], title: str) -> list[str]:
    key = (plan.get("branch", "continue"), plan.get("motive", "whim"))
    tpl = random.choice(_ANN.get(key) or _ANN[("continue", "whim")])
    idea = (plan.get("idea") or "").strip().rstrip("。")
    text = tpl.format(idea=idea, t=title)
    return [ln for ln in text.split("\n") if ln.strip()]


# --------------------------------------------------------------------------
# 我改了她的文档并保存：她读修改记录，可能问原因，也可能犟着把我的改动撤回去
# --------------------------------------------------------------------------
P_INSIST = 0.25            # 读完改动之后，她坚持自己的版本、把改动撤回的概率（settings: writing_p_insist）
EDIT_MIN_CHANGE = 4        # 改动少于这么多字（算增 + 删）就当没看见，不去烦我


def describe_edit(old: str, new: str, limit: int = 1800) -> tuple[str, int]:
    """把两个版本的差别按段落写成人读得懂的修改记录。返回 (记录文本, 改动字数)。"""
    import difflib

    a = [p.strip() for p in (old or "").replace("\r\n", "\n").split("\n") if p.strip()]
    b = [p.strip() for p in (new or "").replace("\r\n", "\n").split("\n") if p.strip()]
    lines: list[str] = []
    changed = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        before, after = "\n".join(a[i1:i2]), "\n".join(b[j1:j2])
        if tag == "delete":
            changed += count_chars(before)
            lines.append(f"【删掉】{before[:300]}")
        elif tag == "insert":
            changed += count_chars(after)
            lines.append(f"【新增】{after[:300]}")
        else:
            # 改了一段：按字算动了多少，免得改一个标点也算「大改」
            for t2, x1, x2, y1, y2 in difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
                if t2 != "equal":
                    changed += max(count_chars(before[x1:x2]), count_chars(after[y1:y2]))
            lines.append(f"【原来】{before[:300]}\n【改成】{after[:300]}")
    text = "\n".join(lines)
    if len(text) > limit:
        text = text[:limit] + "\n……（后面还有改动，没列全）"
    return text, changed


EDIT_REACT_PROMPT = """你是「{name}」。你的口吻（人设里的说话方式）：
{voice}

你写的《{title}》被她改了，她刚点了保存。下面是你读到的修改记录：
{diff}

你是个有小脾气的人，对自己写的东西有自己的想法，不是别人怎么改都乐意。
{stance}
用 1~2 条短消息对她说，每条 25 字以内，口语，不要像评语。
别复述整份修改记录，只就最让你在意的一处说。
只输出 JSON：{{"lines": ["…", "…"]}}"""

_STANCE_INSIST = (
    "这次你决定**坚持自己的版本**，已经把她的改动撤回去了（她可以在文档的「历史」里找回）。"
    "说一声为什么——是哪一处你不同意、你当时是怎么想的。嘴硬一点、有点犟，但不要凶，也别阴阳怪气。"
)
_STANCE_FREE = (
    "你自己拿主意怎么反应，三种里挑最像你这会儿的：\n"
    "1) 她改得有道理或无所谓：接受，可以随口说一句哪里改得好 / 哪里还是不太习惯；\n"
    "2) 没看懂为什么这么改：直接问她原因，问一处，别一口气问好几个；\n"
    "3) 有一处不太服气：嘟囔着表达不满，说出你的理由，但这次先留着她的改动。\n"
    "烈度很轻，像熟人之间，别吵起来。"
)


async def react_to_edit(settings: dict[str, Any], doc: dict[str, Any], diff: str, insist: bool) -> list[str]:
    """她读完修改记录，对我说的话（1~2 条）。模型不可用时返回空列表。"""
    if llm.is_echo(settings):
        return []
    from . import guards
    from . import persona as persona_lib

    raw = await llm.complete(
        settings,
        [{"role": "system", "content": EDIT_REACT_PROMPT.format(
            name=C.display_name(settings),
            voice=persona_lib.voice_excerpt(settings.get("system_prompt") or "", 900),
            title=doc["title"], diff=diff, stance=_STANCE_INSIST if insist else _STANCE_FREE)},
         {"role": "user", "content": "说吧。"}],
        temperature=0.9,
    )
    data = _json_object(raw)
    lines = [str(x).strip() for x in (data.get("lines") or []) if str(x).strip()]
    if not lines:
        lines = guards.split_messages(raw)
    return [guards.strip_exclaim(ln) for ln in lines][:2]


# --------------------------------------------------------------------------
# 心情低落时：回头删掉自己觉得写得不好的内容（只删她自己写的，不碰你写 / 改过的）
# --------------------------------------------------------------------------
DELETE_PROMPT = """你是「{name}」。你的口吻：
{voice}

你现在心情「{mood}」，回头看自己写在《{title}》里的东西，有些越看越不满意。下面是你自己写的最后几段，带编号：
{paras}

选出你想删掉的段落（最多 2 段；如果其实都还行、不想删，就一段也别选）。
why：用你的口吻说一句删的原因，10 字以内。
只输出 JSON：{{"delete": [编号], "why": "…"}}"""


def self_written_paragraphs(doc_id: int, content: str) -> list[tuple[int, str]]:
    """文档里「她自己写的」段落（整段在她记下的某个片段里）。返回 [(段落下标, 文字)]。"""
    chunks = [r["text"] for r in _q("SELECT text FROM doc_chunks WHERE doc_id = ? ORDER BY id", (int(doc_id),))]
    paras = re.split(r"\n\s*\n", content or "")
    out = []
    for i, p in enumerate(paras):
        t = p.strip()
        if t and not _SEP_RE.fullmatch(t) and len(t) >= 10 and any(t in c for c in chunks):
            out.append((i, t))
    return out


async def self_delete(settings: dict[str, Any], doc: dict[str, Any], mood: str) -> dict[str, Any] | None:
    """她回头看，删掉写得不好的段落（改动前留快照，可以在文档页撤销）。没删返回 None。"""
    if llm.is_echo(settings):
        return None
    d, content = read_doc(doc["id"])
    cands = self_written_paragraphs(d["id"], content)[-12:]
    if not cands:
        return None
    from . import persona as persona_lib

    numbered = "\n".join(f"[{n}] {t[:160]}" for n, (_, t) in enumerate(cands, 1))
    try:
        raw = await llm.complete(
            settings,
            [{"role": "system", "content": DELETE_PROMPT.format(
                name=C.display_name(settings),
                voice=persona_lib.voice_excerpt(settings.get("system_prompt") or "", 800),
                mood=mood, title=d["title"], paras=numbered)},
             {"role": "user", "content": "看吧。"}],
            temperature=0.8,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("回头删写得不好的内容：模型调用失败：%s", exc)
        return None
    data = _json_object(raw)
    picks = []
    for x in (data.get("delete") or [])[:2]:
        try:
            n = int(x)
        except (TypeError, ValueError):
            continue
        if 1 <= n <= len(cands) and n not in picks:
            picks.append(n)
    if not picks:
        return None
    drop_idx = {cands[n - 1][0] for n in picks}
    paras = re.split(r"\n\s*\n", content)
    kept = [p for i, p in enumerate(paras) if i not in drop_idx]
    new_content = "\n\n".join(p.strip() for p in kept if p.strip())
    new_content = (new_content + "\n") if new_content else ""
    deleted = [cands[n - 1][1] for n in picks]
    _snapshot(d, content, "Phal 删之前")
    doc_path(d).write_text(new_content, encoding="utf-8")
    _exec("UPDATE docs SET chars = ?, updated_at = ? WHERE id = ?", (count_chars(new_content), _now(), d["id"]))
    why = re.sub(r"[！!]", "", str(data.get("why") or "").strip())[:14]
    return {"doc": get_doc(d["id"]), "deleted": deleted, "why": why}


def delete_lines(count: int, why: str) -> list[str]:
    first = "删了一段" if count <= 1 else f"删了{count}段"
    return [first, why or random.choice(["越看越不满意", "写得不好", "看着难受"])]


# --------------------------------------------------------------------------
# 她改文档（我明确让她改的时候）
# --------------------------------------------------------------------------
EDIT_PROMPT = """你是「{name}」。人设（只用来决定你的眼光和语气）：
{persona}

她让你修改你写的文档《{title}》。当前全文如下：
=====
{content}
=====

她的要求：{instruction}

规则：
- 用「找原文 → 换成新文字」的方式改：edits 里每一项的 find 必须是上面全文里**一字不差**、且只出现一次的一小段（够定位就行）；
  replace 是替换后的文字。要删掉一段就让 replace 为空字符串。
- 她要「接着写 / 加一段」就用 append（追加在全文最后）。
- 只改她要求的地方，别动别的。不要解释。
{deai}

只输出 JSON：{{"edits": [{{"find": "...", "replace": "..."}}], "append": ""}}"""


async def edit_doc(settings: dict[str, Any], doc: dict[str, Any], instruction: str) -> dict[str, Any] | None:
    if llm.is_echo(settings):
        return None
    d, content = read_doc(doc["id"])
    if d["kind"] != "phal":
        raise DocError("她只能改自己的文档")
    name = C.display_name(settings)
    system = EDIT_PROMPT.format(name=name, persona=(settings.get("system_prompt") or "")[:700], title=d["title"],
                                content=content[-9000:], instruction=instruction,
                                deai=DEAI_RULES.format(banned=_banned_inline()))
    try:
        raw = await llm.complete(settings, [{"role": "system", "content": system}, {"role": "user", "content": "改吧。"}],
                                 temperature=0.7)
    except Exception as exc:  # noqa: BLE001
        log.warning("改文档调用模型失败：%s", exc)
        return None
    data = _json_object(raw)
    new = content
    applied, skipped = 0, 0
    for item in (data.get("edits") or [])[:12]:
        if not isinstance(item, dict):
            continue
        find, rep = str(item.get("find") or ""), str(item.get("replace") or "")
        if not find or new.count(find) != 1:      # 找不到 / 不唯一：不动，绝不乱改
            skipped += 1
            continue
        new = new.replace(find, rep, 1)
        applied += 1
    appended = _clean(str(data.get("append") or ""))
    if appended:
        new = new.rstrip("\n") + "\n\n" + appended + "\n"
        applied += 1
    if new == content:
        return {"doc": d, "applied": 0, "skipped": skipped, "changed": False}
    _snapshot(d, content, "Phal 修改之前")
    doc_path(d).write_text(new, encoding="utf-8")
    _exec("UPDATE docs SET chars = ?, updated_at = ? WHERE id = ?", (count_chars(new), _now(), d["id"]))
    return {"doc": get_doc(d["id"]), "applied": applied, "skipped": skipped, "changed": True}


# --------------------------------------------------------------------------
# 聊天里的明确命令：写 / 改
# --------------------------------------------------------------------------
_NEG = re.compile(r"(?:不要|别|不用|不必|不想|不许|不准|不需要|没让你|先别|无需)\s*(?:再|去|给我)?\s*(?:写|改|修改|重写|润色|续写)")
_WRITE = re.compile(
    r"(?:试着|试试|尝试|来|帮我|给我|替我|你|去|就|那就|现在|先)?\s*"
    r"(?:写一写|写一下|写一段|写一篇|写几句|写点|写首|写个|写写|写下来|动笔|续写|接着写|继续写|往下写)"
)
_WRITE_PAST = re.compile(r"写(?:的|得|了|过|完|好|不)")
_EDIT = re.compile(r"(?:修改|改一下|改改|改成|改写|重写|润色|删掉|删除|去掉|换成|替换|改掉|调整一下)")
_DOCREF = re.compile(r"《[^》]{1,40}》|文档|这篇|那篇|这段|那段|这一段|那一段|第[一二三四五六七八九十0-9]+段|你写的|刚才写的|刚写的|开头|结尾|最后一段")


def detect_command(text: str) -> tuple[str | None, str | None]:
    """识别「明确的命令」。返回 (write|edit|None, 指到的文档标题或 None)。

    只认明确的祈使：试着写一写 / 写一段 / 接着写；把《X》里…改成…。
    「你写得真好」「别写了」「改天聊」这类不算。"""
    t = (text or "").strip()
    if not t or len(t) > 400 or _NEG.search(t):
        return None, None
    m = re.search(r"《([^》]{1,40})》", t)
    hint = m.group(1) if m else None
    if _EDIT.search(t) and _DOCREF.search(t):
        return "edit", hint
    w = _WRITE.search(t)
    if not w or _WRITE_PAST.search(t[w.start(): w.end() + 1]):
        return None, None
    # 得是在对她说：带「你 / 帮我 / 给我 / 试着」之类，或者整句就是个祈使句；「我想写点日记」不算
    addressed = re.search(r"你|帮我|给我|替我|让你|请|试着|试试", t) or re.match(r"\s*(?:写|续写|接着|继续|往下|来|动笔|先|那就|现在)", t)
    if addressed:
        return "write", hint
    return None, None


def pick_doc(hint: str | None) -> dict[str, Any] | None:
    docs = list_docs("phal")
    if hint:
        for d in docs:
            if hint in d["title"] or d["title"] in hint:
                return d
    return available_doc() or (docs[0] if docs else None)


async def handle_command(settings: dict[str, Any], kind: str, text: str, hint: str | None) -> dict[str, Any]:
    """执行聊天里的写 / 改命令。返回 {note, events}：note 给聊天回复用，events 通知前端刷新卡片。"""
    events = [{"type": "doc"}]
    doc = pick_doc(hint)
    if llm.is_echo(settings):
        return {"note": "【她让你写 / 改文档，但现在没有配置模型，你没法真的动笔。如实告诉她。】", "events": []}
    if kind == "write":
        # 先回复，再切到写作状态，然后才开始写：这里只负责判断能不能写、决定怎么写，并给这一轮回复一个提示；
        # 真正的状态切换和动笔，在回复发完之后（见 life.begin_writing_now）。
        hinted = pick_doc(hint) if hint else None
        if hinted and (hinted["kind"] != "phal" or hinted["full"]):
            doc_for_plan = None
            if hinted["kind"] == "phal" and hinted["full"]:
                return {"note": f"【她让你写东西，但《{hinted['title']}》已经写满一万字了，所以你没写。像平时那样告诉她，并向她要一份新文档，她建好你才能写。不要假装写了。】",
                        "events": [], "plan": None}
        else:
            doc_for_plan = hinted
        plan = await plan_writing(settings, instruction=text, doc_hint=doc_for_plan)
        if plan["branch"] == "no_doc":
            full = [d for d in list_docs("phal") if d["full"]]
            why = (f"《{full[0]['title']}》已经写满一万字了" if full else "你手上还没有空白文档")
            return {"note": f"【她让你写东西，但{why}，所以你没写。像平时那样告诉她，并向她要一份新文档，她建好你才能写。不要假装写了。】",
                    "events": [], "plan": None}
        title = (get_doc(plan["doc_id"]) or {}).get("title", "")
        if plan["branch"] == "new_doc":
            idea = plan.get("idea") or "想写点新的东西"
            db.save_settings({
                "writing_pending": json.dumps({"idea": plan.get("idea", ""), "motive": plan["motive"], "instruction": text}, ensure_ascii=False),
                "writing_waiting": "1", "writing_ask_day": datetime.now().strftime("%Y-%m-%d"),
            })
            return {"note": (f"【她让你写点东西。你有个新想法（{idea}），但它和你手上的文档都不搭，想单独存一份。"
                             "这一轮回复里，就像平时那样答应她，并问她能不能给你建一个新文档存这个新想法；先别写，也别假装已经写了。】"),
                    "events": [], "plan": None}
        how = {"continue": f"接着写《{title}》", "revisit": f"回头把《{title}》里之前那段接着写",
               "new_section": f"在《{title}》后面用分隔符隔开，另写一段新的"}[plan["branch"]]
        return {
            "note": (f"【她明确让你写点东西。你答应了：按你的性子回应她（嘴上嫌麻烦也行），一两句，告诉她你这就去写（你打算{how}）。"
                     "只是回应，**不要**在这条回复里写出任何正文，也别假装已经写好了。回复之后你会切到写作的状态，"
                     "然后才动笔；动笔前你还会再跟她说一声你是怎么想的。】"),
            "events": [], "plan": plan,
        }
    # edit
    if not doc:
        return {"note": "【她让你改文档，但你手上还没有文档。如实告诉她。】", "events": []}
    try:
        res = await edit_doc(settings, doc, text)
    except DocError as exc:
        return {"note": f"【你没能改成：{exc}。如实告诉她。】", "events": []}
    if not res:
        return {"note": "【你想改，但这次没改成（模型出错了）。如实告诉她，别假装改了。】", "events": []}
    if not res["changed"]:
        return {"note": f"【她让你改《{doc['title']}》，但你没找到该改的地方（或者要改的原文对不上），所以什么都没动。如实告诉她，请她说得具体一点。】",
                "events": []}
    return {
        "note": (f"【她明确让你修改文档，你已经真的改了《{res['doc']['title']}》里的 {res['applied']} 处"
                 + (f"（另有 {res['skipped']} 处没找到对应的原文，没动）" if res["skipped"] else "")
                 + "。告诉她改好了，简短，别复述全文。她不满意可以在文档页撤销。】"),
        "events": events + [{"type": "doc", "doc_id": res["doc"]["id"]}],
    }


# --------------------------------------------------------------------------
# 给聊天上下文用
# --------------------------------------------------------------------------
def context_lines() -> list[str]:
    docs = list_docs("phal")
    if not docs:
        return ["- 你的写作本：还没有文档（她会给你建空白的）。"]
    lines = ["- 你的写作文档（你真的在里面写字）："]
    for d in docs[:4]:
        lines.append(f"  · 《{d['title']}》{d['chars']}/{doc_limit()} 字" + ("，写满了" if d["full"] else ""))
    f = focus()
    if f:
        mins = int((time.time() - float(f["at"])) / 60)
        ago = f"{mins} 分钟前" if mins < 90 else f"{mins // 60} 个小时前"
        idea = f"（当时想到的是：{f['idea']}）" if f.get("idea") else ""
        done = "，已经动笔写了一段" if float(f.get("written_at") or 0) >= float(f["at"]) else "，还没写完"
        lines.append(f"- 你手头在写的是《{f['title']}》：{ago}跟她说过要写它{idea}{done}。"
                     "聊到写东西时说的就是这一份，别说成别的文档，也别临时改主意换一份。")
    mine = list_docs("mine")
    if mine:
        lines.append(f"- 她的文字放在「我的文字」里（{len(mine)} 份），你写东西时会模仿它的笔触。")
    letters = list_docs("letter")
    if letters:
        lines.append(f"- 你每月月末会写一封信给她，放在「信」里（写好就不再改）；已经写过 {len(letters)} 封，最近一封是《{letters[0]['title']}》（{letters[0]['dated_text']}）。")
    return lines


# --------------------------------------------------------------------------
# 你在聊天里提到的文档：把正文真的读给她（不然她只知道标题，会拿别的东西顶替）
# --------------------------------------------------------------------------
REF_CAP = 6000          # 一次最多给她看多少字（太长的节选头尾）
PAGE = 6000             # read_doc 工具每页多少字
DOC_OWNER = {"mine": "她自己写的文字（放在「我的文字」里）", "phal": "你自己写的文档（在你的写作文件夹里）",
             "letter": "你自己写给她的信（放在「信」里，写好就不再改）"}


def find_referenced(text: str, limit: int = 2) -> list[dict[str, Any]]:
    """用户这句话里提到了哪些文档：《标题》/「标题」，或者三个字以上的标题直接出现，
    或者「看看 / 读 / 打开 / 在 + 标题」。两个字的短标题（如「随笔」）只认带书名号的，免得日常聊天误触。"""
    t = text or ""
    out: list[dict[str, Any]] = []
    for d in list_docs("mine") + list_docs("phal") + list_docs("letter"):
        title = d["title"]
        if not title:
            continue
        quoted = f"《{title}》" in t or f"「{title}」" in t or f"“{title}”" in t
        plain = len(title) >= 3 and title in t
        verb = len(title) >= 2 and re.search(r"(?:看看|读读|读一读|看一下|看下|打开|读|看|在)\s*" + re.escape(title), t)
        if quoted or plain or verb:
            out.append(d)
    return out[:limit]


def mentions_my_writing(text: str) -> bool:
    return bool(re.search(r"我的文字|我写的(?:东西|文章|文字|小说|随笔)|我的(?:随笔|小说)", text or ""))


def _excerpt(content: str) -> tuple[str, bool]:
    if len(content) <= REF_CAP:
        return content, False
    head, tail = int(REF_CAP * 0.75), int(REF_CAP * 0.25)
    return f"{content[:head]}\n…（中间省略 {len(content) - REF_CAP} 字，需要的话用 read_doc 工具往后翻）…\n{content[-tail:]}", True


def reference_block(text: str) -> str:
    """给聊天上下文的「她提到的文档」块。没提到就返回空串。"""
    refs = find_referenced(text)
    parts: list[str] = []
    for d in refs:
        try:
            content = read_text(doc_path(d)).strip()
        except OSError:
            continue
        shown, cut = _excerpt(content)
        who = DOC_OWNER[d["kind"]]
        parts.append(f"《{d['title']}》——{who}，{d['chars']} 字{'，下面是节选' if cut else '，下面是全文'}：\n"
                     + (shown or "（这份文档现在是空的）"))
    if not parts and mentions_my_writing(text):
        mine = list_docs("mine")
        if mine:
            lines = []
            for d in mine[:4]:
                try:
                    head = read_text(doc_path(d)).strip()[:160].replace("\n", " ")
                except OSError:
                    head = ""
                lines.append(f"· 《{d['title']}》（{d['chars']} 字）开头：{head}…")
            parts.append("「我的文字」里有这些她自己写的文字（只给了开头）：\n" + "\n".join(lines)
                         + "\n想看某一篇的内容，调用 read_doc 工具读它，别凭印象说。")
        else:
            parts.append("她说的「我的文字」里目前还没有任何文档，你什么也没看到——如实告诉她，别编。")
    if not parts:
        return ""
    return ("【她提到的文档】下面是你真的读到的内容，以它为准，别凭印象。归属很重要：标着「她自己写的」的就是她写的；"
            "你以前在聊天里写给她的东西（比如你写过的诗）是你写的，不是她的文字，千万别弄混。\n\n" + "\n\n".join(parts))


def read_doc_tool(title: str, kind: str | None = None, part: int = 1) -> str:
    """给模型的 read_doc 工具：按标题读一份文档（分页）。"""
    title = (title or "").strip().strip("《》「」")
    pools = [kind] if kind in ("mine", "phal", "letter") else ["mine", "phal", "letter"]
    found = None
    for k in pools:
        docs = list_docs(k)
        found = next((d for d in docs if d["title"] == title), None) or next((d for d in docs if title and (title in d["title"] or d["title"] in title)), None)
        if found:
            break
    if not found:
        names = [f"《{d['title']}》（{'她的' if d['kind'] == 'mine' else '你的信' if d['kind'] == 'letter' else '你的'}）"
                 for d in list_docs("mine") + list_docs("phal") + list_docs("letter")]
        return f"没有找到叫「{title}」的文档。现有的：" + ("、".join(names[:20]) or "（一份都没有）") + "。如实告诉她你没找到，别编。"
    try:
        content = read_text(doc_path(found)).strip()
    except OSError:
        return "这份文档的文件读不到了。"
    part = max(1, int(part or 1))
    pages = max(1, (len(content) + PAGE - 1) // PAGE)
    chunk = content[(part - 1) * PAGE: part * PAGE]
    who = {"mine": "她自己写的文字", "letter": f"你写给她的信，{found['dated_text']}"}.get(found["kind"], "你自己写的文档")
    return (f"《{found['title']}》（{who}，{found['chars']} 字）第 {part}/{pages} 页：\n{chunk or '（这份文档是空的）'}"
            + (f"\n（还有后面，part={part + 1} 继续读）" if part < pages else ""))
