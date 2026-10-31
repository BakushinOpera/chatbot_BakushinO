"""月末的信：每月最后一天晚上，在日总结之后，她写一封信给我。

* 写信前先汇总这个月的内容给她回忆：每天的一句话（日总结）、记忆里新增的条目、发生过的事（包括没告诉我的）、
  零花钱账本、读过的书和笔记、写作、房间里新添的东西、节日。
* 信是一份新文档，存在单独的「信」分类里（``data/letters/``），不受写作文件夹的规则限制（没有字数上限、不用先要空白文档）。
  标题由她自己取，**标题里不带日期**；文档卡片的标题下面用一行小字显示年份和日期（只有信这样显示）。
* 语气比写作更日常、随性，可以有颜文字、emoji，但不多。写完后主动发一条消息告诉我。
* 信只读：她不会删改已经写好的信（writing.save_content / rename_doc 对信直接拒绝）。
* 页面没开着、错过了月末那天：下次打开时补写（日期仍是那个月的最后一天）。第一次使用以前的月份不补。
"""

from __future__ import annotations

import calendar
import json
import logging
import random
import re
from datetime import date, datetime
from typing import Any

from . import daily, db, festivals, llm, money, registry, room, writing
from . import character as C

log = logging.getLogger(__name__)

NOTICE_LINES = ["写了封信 放在那边了", "这个月的信写好了 放在右边那栏了", "写了封信给你 在「信」那里", "给你写了封信 有空看看"]
FALLBACK_TITLE = "这个月的碎碎念"
_DATE_IN_TITLE = re.compile(r"\d{4}\s*年|\d{1,2}\s*月|[一二三四五六七八九十]{1,3}\s*月|\d{1,2}\s*[./-]\s*\d{1,2}|\d{1,2}\s*[日号]")
_EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿⭐❤️]")


def enabled() -> bool:
    return registry.flag("letters_enabled")


# ───────────────────────── 哪几个月写过 ─────────────────────────
def _month(d: date | datetime) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _prev(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:])
    return f"{y - 1:04d}-12" if m == 1 else f"{y:04d}-{m - 1:02d}"


def last_day(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:])
    return f"{ym}-{calendar.monthrange(y, m)[1]:02d}"


def months_between(a: str, b: str) -> list[str]:
    """从 a 月到 b 月（含两头）。"""
    out, cur = [], a
    while cur <= b and len(out) < 240:
        out.append(cur)
        y, m = int(cur[:4]), int(cur[5:])
        cur = f"{y + 1:04d}-01" if m == 12 else f"{y:04d}-{m + 1:02d}"
    return out


def done_months() -> list[str]:
    try:
        data = json.loads(db.get_settings().get("letters_done") or "[]")
    except json.JSONDecodeError:
        data = []
    return [str(x) for x in data] if isinstance(data, list) else []


def mark_done(ym: str) -> None:
    done = done_months()
    if ym not in done:
        db.save_settings({"letters_done": json.dumps(sorted(done + [ym])[-60:])})


def since(now: float) -> str:
    """从哪个月开始写信：第一次用的那个月（更早的月份不补）。"""
    s = db.get_settings().get("letters_since") or ""
    if not s:
        s = _month(datetime.fromtimestamp(now))
        db.save_settings({"letters_since": s})
    return s


def _after_summary(now: float) -> bool:
    """今天的日总结写完了（或者等了十分钟还没动静），再等几分钟。"""
    s = db.get_settings()
    today = daily.today_str(now)
    if s.get("life_summary_day") != today:
        return False
    at = float(s.get("life_summary_at") or 0)
    row = db.get_mood(today)
    finished = bool(row and row.get("kind") == "summary")
    return (finished or now >= at + 600) and now >= at + registry.num("letter_after_summary_min") * 60


def due(now: float) -> str | None:
    """现在该写哪个月的信，没有就 None。先补写已经过去的月份，再看今天是不是月末、日总结写完没有。"""
    if not enabled():
        return None
    d = datetime.fromtimestamp(now)
    cur = _month(d)
    start = since(now)
    done = set(done_months())
    cap = int(registry.num("letter_catchup_months"))
    missed = [m for m in months_between(start, _prev(cur)) if m not in done]
    if cap <= 0:
        missed = []
    elif len(missed) > cap:
        missed = missed[-cap:]                               # 只补最近几个月，更早的就不补了
    if missed:
        return missed[0]
    if cur not in done and cur >= start and d.day == calendar.monthrange(d.year, d.month)[1] and _after_summary(now):
        return cur
    return None


# ───────────────────────── 汇总这个月 ─────────────────────────
def _like(ym: str) -> str:
    return ym + "%"


def _q(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    with db._lock:
        return [dict(r) for r in db.connect().execute(sql, params).fetchall()]


def materials(ym: str) -> dict[str, list[str]]:
    """这个月发生过的事，一个分区一个列表（没有的分区是空列表）。只给她真的记过的，不编。"""
    out: dict[str, list[str]] = {}
    moods = db.moods_between(f"{ym}-01", f"{ym}-31")
    out["days"] = [f"{int(day[8:])} 日（{r['mood']}）：{r['line']}" for day, r in sorted(moods.items()) if r.get("line")][:31]
    mem = [m for m in db.list_memories(600) if (m.get("created_at") or "")[:7] == ym and m.get("source") not in ("event", "money", "room", "letter")]
    out["memories"] = [m["content"] for m in reversed(mem)][:10]
    ev = _q("SELECT * FROM life_events WHERE day LIKE ? AND etype IS NOT NULL AND etype <> '' AND etype <> 'spend' ORDER BY id", (_like(ym),))
    out["events"] = [f"{int(e['day'][8:])} 日 {e['text']}" + ("" if e["shared"] else "（这件你没跟她说过）") for e in ev][-14:]
    s = money.month_summary(ym)
    lines: list[str] = []
    if s["spent"] > 0:
        top = "、".join(f"{c['label']} {money.fmt(c['amount'])}" for c in s["by_category"][:3])
        lines.append(f"这个月一共花了 {money.fmt(s['spent'])} {C.unit()}（{s['count']} 笔），大头是：{top}")
        big = _q("SELECT item, delta FROM money_ledger WHERE day LIKE ? AND kind = 'spend' ORDER BY delta ASC LIMIT 1", (_like(ym),))
        if big:
            lines.append(f"最贵的一笔：{big[0]['item']}（{money.fmt(-big[0]['delta'])} {C.unit()}）")
        lines.append(f"月底余额 {money.fmt(s['end_balance'])} {C.unit()}")
    out["money"] = lines
    notes = _q("SELECT n.text, b.title FROM reading_notes n JOIN books b ON b.id = n.book_id WHERE n.created_at LIKE ? ORDER BY n.id LIMIT 8", (_like(ym),))
    out["reading"] = [f"《{n['title']}》：{n['text']}" for n in notes]
    chunks = _q("SELECT d.title AS title, COUNT(c.id) AS n, COALESCE(SUM(LENGTH(c.text)), 0) AS chars FROM doc_chunks c JOIN docs d ON d.id = c.doc_id "
                "WHERE c.created_at LIKE ? AND d.kind = 'phal' GROUP BY d.id", (_like(ym),))
    drafts = _q("SELECT title, status FROM drafts WHERE created_at LIKE ? ORDER BY id LIMIT 5", (_like(ym),))
    out["writing"] = ([f"《{c['title']}》里写了 {c['n']} 段，约 {c['chars']} 字" for c in chunks]
                      + [f"草稿《{d['title']}》（{'写崩了，划掉了' if d['status'] == 'abandoned' else '写成了'}）" for d in drafts])
    out["items"] = [f"{it['name']}（{room.SOURCE_CTX.get(it['source'], '')}" + (f"，{it['note']}" if it["note"] else "") + "）"
                    for it in room.list_items() if it["got_on"][:7] == ym and it["source"] != "original"][:10]
    fest: list[str] = []
    y, m = int(ym[:4]), int(ym[5:])
    for dd in range(1, calendar.monthrange(y, m)[1] + 1):
        for f in festivals.on_date(date(y, m, dd)):
            fest.append(f"{m} 月 {dd} 日 {f['name']}")
    out["festivals"] = fest
    return out


SECTION_TITLES = [("days", "每天的一句话"), ("memories", "你记下来的事"), ("events", "发生过的事"), ("money", "零花钱"),
                  ("reading", "读书和笔记"), ("writing", "写作"), ("items", "房间里新添的东西"), ("festivals", "节日")]


def materials_text(mats: dict[str, list[str]]) -> str:
    parts = []
    for key, title in SECTION_TITLES:
        if mats.get(key):
            parts.append(f"【{title}】\n" + "\n".join(f"- {x}" for x in mats[key]))
    return "\n\n".join(parts) or "（这个月没什么特别的记录，平平淡淡的。）"


# ───────────────────────── 写信 ─────────────────────────
LETTER_PROMPT = """你是「{name}」，现在是{month}月的最后一天晚上。你要给对方写一封信（是信，不是聊天消息），说说这个月。
你的口吻（人设里的说话方式）：
{voice}

下面是这个月你自己的经历和记录。**只能用这些**，不要编造；没有的就不写：

{materials}

写法：
- 格式：第一行是「标题：xxx」，空一行，然后是正文。标题你自己取，像随手起的名字，**不要带日期、年份、月份、数字**；
- 语气日常、随性，像写给熟人的信，不是总结报告，不要列清单，不要小标题；
- 可以偶尔带一个颜文字或 emoji，整封信最多 {emoji_max} 处，别堆；
- 长度大约 {lo}~{hi} 字，分几段；
- 挑你真正想说的几件事写，不用面面俱到；零花钱、读书、写作、新东西、节日，想提才提；有些事你当时没跟对方说过，可以趁这封信说；
- {no_real}不提学校和班级；
- 称呼随你（可以不称呼，直接开头），结尾不用署日期。"""
LETTER_PROMPT = LETTER_PROMPT.replace("{no_real}", C.pick("fictional_world", "不要现实里的国家、城市、学校名；"))


def limit_emoji(text: str, n: int) -> str:
    """emoji 超过 n 个，多出来的去掉（颜文字不算）。"""
    seen = 0

    def keep(m: re.Match[str]) -> str:
        nonlocal seen
        seen += 1
        return m.group(0) if seen <= n else ""

    return _EMOJI.sub(keep, text)


def clean_title(title: str) -> str:
    t = re.sub(r"[《》「」\"'“”]", "", (title or "").strip())
    t = _DATE_IN_TITLE.sub("", t)
    t = re.sub(r"\s+", " ", t).strip(" ，,。.:：-—·")
    return t[:30]


def parse_letter(raw: str) -> tuple[str, str]:
    """从模型的回答里分出 (标题, 正文)。标题缺失时返回空标题。"""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`").split("\n", 1)[-1] if "\n" in text else text
    m = re.match(r"\s*(?:标题|题目)\s*[:：]\s*(.+?)\s*(?:\n|$)", text)
    if m:
        return m.group(1).strip(), re.sub(r"^\s*(?:-{3,}|\*{3,})\s*\n", "", text[m.end():].lstrip("\n")).strip()
    first, _, rest = text.partition("\n")
    if first and len(first) <= 20 and rest.strip() and not re.search(r"[。，,.!！?？]", first):
        return first.strip(), rest.strip()
    return "", text


def local_letter(ym: str, mats: dict[str, list[str]]) -> tuple[str, str]:
    """没有模型 / 反复不合格时：用这个月的记录拼一封朴素的信（不编造）。"""
    m = int(ym[5:])
    paras = [f"写封信给你，说说这个月。"]
    if mats.get("events"):
        paras.append("这个月发生过几件小事：" + "；".join(e.split(" ", 2)[-1].replace("（这件你没跟她说过）", "") for e in mats["events"][-3:]) + "。")
    if mats.get("money"):
        paras.append(mats["money"][0] + "。")
    if mats.get("items"):
        paras.append("房间里多了：" + "、".join(mats["items"][:3]) + "。")
    if mats.get("reading"):
        paras.append("看书的时候记过几句：" + mats["reading"][0] + "。")
    if len(paras) == 1:
        paras.append(f"{m} 月过得平平淡淡的，没什么特别要说的。")
    paras.append("就写到这里。下个月见。")
    return FALLBACK_TITLE, "\n\n".join(paras)


async def make_letter(settings: dict[str, Any], ym: str, mats: dict[str, list[str]]) -> tuple[str, str]:
    """返回 (标题, 正文)。检查：标题不带日期、不带现实地名 / 禁词、不太短；不合格重生成，最后用本地兜底。"""
    from . import guards, reading
    from . import persona as persona_lib

    if llm.is_echo(settings):
        return local_letter(ym, mats)
    lo, hi = int(registry.num("letter_chars_min")), int(registry.num("letter_chars_max"))
    system = LETTER_PROMPT.format(
        name=C.display_name(settings), month=int(ym[5:]), voice=persona_lib.voice_excerpt(settings.get("system_prompt") or "", 1200),
        materials=materials_text(mats), emoji_max=int(registry.num("letter_emoji_max")), lo=min(lo, hi), hi=max(lo, hi))
    best = ""                                                  # 正文合格、只是标题不合格的那一版
    for _ in range(3):
        try:
            raw = await llm.complete(settings, [{"role": "system", "content": system}, {"role": "user", "content": "写吧。"}], temperature=0.95)
        except Exception:  # noqa: BLE001
            break
        title, body = parse_letter(raw)
        title = guards.strip_exclaim(title)
        body = limit_emoji(guards.strip_exclaim(body), int(registry.num("letter_emoji_max")))
        if writing.count_chars(body) < min(lo, hi) * 0.5:
            continue
        if reading.mentions_real_place(title + body) or guards.find_banned(title + body):
            continue
        if not title or _DATE_IN_TITLE.search(title) or not clean_title(title):
            best = best or body                                # 标题没写 / 带了日期：重写，实在不行用兜底标题
            continue
        return clean_title(title), body
    if best:
        return FALLBACK_TITLE, best
    return local_letter(ym, mats)


async def write(settings: dict[str, Any], ym: str) -> dict[str, Any] | None:
    """写这个月的信：汇总 → 写 → 存进「信」分类 → 记事件和记忆。返回 {doc, title}；这个月已经写过就 None。"""
    if ym in done_months():
        return None
    mark_done(ym)
    mats = materials(ym)
    title, body = await make_letter(settings, ym, mats)
    doc = writing.create_letter(title, body.strip() + "\n", last_day(ym))
    db.add_event(last_day(ym), "letter", f"写了一封信《{doc['title']}》", None, shared=True)
    db.add_memory(f"{int(ym[5:])} 月月末 写了一封信《{doc['title']}》给用户，放在「信」里", kind="event", category="phal_fact", source="letter")
    return {"doc": doc, "title": doc["title"]}
