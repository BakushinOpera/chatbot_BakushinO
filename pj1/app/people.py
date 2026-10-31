"""固定的家人和朋友：人物档案。

* 档案：名字、和她的关系、性格一句话、相处方式、最近发生过的事。
* 已有档案：爸爸、妈妈。朋友由 Phal 先生成 3~4 个同龄朋友的**草稿**，你审阅、修改、定稿后才算数（草稿不会进上下文）。
* 她在事件和聊天里提到家人 / 朋友时，只能用档案里的人；路人不用建档。
* 事件发生后，会在对应人物的「最近发生过的事」里追加一条（只留最近几条）。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any

from . import db, llm, registry
from . import character as C
from .planner import _json_object

log = logging.getLogger(__name__)

RECENT_KEEP = 8                 # 每个人物最多留几条「最近发生过的事」
KINDS = ("family", "friend")

SEED = [
    {"name": "爸爸", "kind": "family", "relation": "爸爸",
     "personality": "科技公司高管，很忙，话不多，偶尔难得早回家。",
     "how": "平时见得少；难得早回家会一起吃晚饭；一见面容易被问起学习，她有点烦。"},
    {"name": "妈妈", "kind": "family", "relation": "妈妈",
     "personality": "音效设计师，在家工作，爱捣鼓录音，说话直。",
     "how": "常抓她去当录音效的帮手；会一起做饭；管她吃得好不好。"},
]


def _q(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    with db._lock:
        return [dict(r) for r in db.connect().execute(sql, params).fetchall()]


def _exec(sql: str, params: tuple = ()) -> int:
    with db._lock:
        conn = db.connect()
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid if sql.lstrip().upper().startswith("INSERT") else cur.rowcount


def _row(r: dict[str, Any]) -> dict[str, Any]:
    try:
        recent = json.loads(r.get("recent") or "[]")
    except json.JSONDecodeError:
        recent = []
    return {**r, "recent": recent if isinstance(recent, list) else [], "draft": bool(r["draft"])}


def ensure_seed() -> None:
    """第一次用：建好爸爸、妈妈。只建一次（删掉了就不会再冒出来）。"""
    if db.get_settings().get("people_seeded") == "1":
        return
    if not _q("SELECT id FROM people LIMIT 1"):
        for i, p in enumerate(SEED):
            add(p["name"], p["kind"], p["relation"], p["personality"], p["how"], sort=i)
    db.save_settings({"people_seeded": "1"})


def list_people(drafts: bool = True) -> list[dict[str, Any]]:
    ensure_seed()
    rows = _q("SELECT * FROM people " + ("" if drafts else "WHERE draft = 0 ") + "ORDER BY draft ASC, "
              "CASE kind WHEN 'family' THEN 0 ELSE 1 END, sort ASC, id ASC")
    return [_row(r) for r in rows]


def get(person_id: int) -> dict[str, Any] | None:
    rows = _q("SELECT * FROM people WHERE id = ?", (int(person_id),))
    return _row(rows[0]) if rows else None


def add(name: str, kind: str = "friend", relation: str = "", personality: str = "", how: str = "",
        recent: list[Any] | None = None, draft: bool = False, sort: int | None = None) -> dict[str, Any]:
    name = (name or "").strip()
    if not name:
        raise ValueError("名字不能为空")
    if kind not in KINDS:
        raise ValueError("kind 只能是 family / friend")
    if _q("SELECT id FROM people WHERE name = ? AND draft = ?", (name, int(draft))):
        raise ValueError(f"已经有叫「{name}」的人了")
    if sort is None:
        sort = len(_q("SELECT id FROM people"))
    pid = _exec(
        "INSERT INTO people (name, kind, relation, personality, how, recent, draft, sort, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (name, kind, (relation or "").strip(), (personality or "").strip(), (how or "").strip(),
         json.dumps(recent or [], ensure_ascii=False), int(draft), sort, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    return get(pid)  # type: ignore[return-value]


def update(person_id: int, **fields: Any) -> dict[str, Any] | None:
    allowed = {"name", "kind", "relation", "personality", "how"}
    patch = {k: (str(v).strip() if k != "kind" else v) for k, v in fields.items() if k in allowed and v is not None}
    if "name" in patch and not patch["name"]:
        raise ValueError("名字不能为空")
    if "kind" in patch and patch["kind"] not in KINDS:
        raise ValueError("kind 只能是 family / friend")
    if "recent" in fields and isinstance(fields["recent"], list):
        patch["recent"] = json.dumps(fields["recent"][-RECENT_KEEP:], ensure_ascii=False)
    if not patch:
        return get(person_id)
    cols = ", ".join(f"{k} = ?" for k in patch)
    _exec(f"UPDATE people SET {cols} WHERE id = ?", (*patch.values(), int(person_id)))
    return get(person_id)


def delete(person_id: int) -> bool:
    return bool(_exec("DELETE FROM people WHERE id = ?", (int(person_id),)))


def approve(person_id: int) -> dict[str, Any] | None:
    p = get(person_id)
    if not p:
        return None
    if _q("SELECT id FROM people WHERE name = ? AND draft = 0 AND id <> ?", (p["name"], p["id"])):
        raise ValueError(f"已经有叫「{p['name']}」的人了，先改个名字")
    _exec("UPDATE people SET draft = 0 WHERE id = ?", (int(person_id),))
    return get(person_id)


def add_recent(person_id: int, text: str, day: str | None = None) -> None:
    """事件发生后，给这个人的「最近发生过的事」追加一条。"""
    p = get(person_id)
    if not p or p["draft"] or not (text or "").strip():
        return
    recent = p["recent"] + [{"date": day or datetime.now().strftime("%Y-%m-%d"), "text": text.strip()[:80]}]
    update(person_id, recent=recent[-RECENT_KEEP:])


def find_by_text(text: str) -> list[dict[str, Any]]:
    """文字里提到了哪些（已定稿的）人。"""
    return [p for p in list_people(drafts=False) if p["name"] and p["name"] in (text or "")]


# ───────────────────────── 给上下文 ─────────────────────────
def block() -> str:
    """她身边的人（稳定部分，不含「最近发生的事」，免得每次都变）。"""
    people = list_people(drafts=False)
    if not people:
        return ""
    lines = ["【你身边的人】下面是你家人和固定的朋友。聊天或经历里提到家人、朋友时，**只能用这些人**；"
             "路人随口带过就行，别给路人起名字、也别凭空多出一个新朋友。"]
    for p in people:
        bits = [p["relation"], p["personality"], p["how"]]
        lines.append(f"- {p['name']}" + (f"（{p['relation']}）" if p["relation"] else "") + "：" + " ".join(b for b in bits[1:] if b))
    return "\n".join(lines)


def recent_block(text: str = "", limit: int = 3) -> str:
    """最近发生在这些人身上的事（易变部分）：聊到谁带谁的，不然带最新的几条。"""
    people = list_people(drafts=False)
    mentioned = {p["id"] for p in find_by_text(text)}
    items: list[tuple[str, str, str]] = []
    for p in people:
        for r in p["recent"][-3:]:
            items.append((r.get("date", ""), p["name"], r.get("text", "")))
    if mentioned:
        items = [i for i in items if any(p["name"] == i[1] and p["id"] in mentioned for p in people)] or items
    items.sort(reverse=True)
    items = items[:limit]
    if not items:
        return ""
    return "【他们最近】" + "；".join(f"{d[5:]} {n}：{t}" for d, n, t in items) + "。（只是你知道的近况，不用主动背给她听。）"


# ───────────────────────── 让 Phal 生成朋友草稿 ─────────────────────────
DRAFT_PROMPT = """你在帮一个 AI 角色「{name}」设定她身边固定的几个朋友。她是个十几岁的普通女高中生，住在{where}。
她的人设（只用来决定朋友和她的相处方式，别抄）：
{persona}

已经有的人（不要重复，也不要和他们撞名字）：{existing}

请设计 {n} 个**同龄**的朋友，每个人不同：性格、和她怎么相处、认识多久、最近的一件小事都不一样。
输出 JSON，不要别的：
{{"friends": [{{"name": "中文名字", "relation": "和她的关系，比如：从小一起长大 / 兴趣相投 / 邻居", "personality": "性格一句话，30 字以内", "how": "平时怎么相处，40 字以内", "recent": "最近发生过的一件小事，30 字以内，可以留空"}}]}}

要求：
- 名字是虚构的，不要用名人的名字；
- {no_real}刻意不写他们在哪所学校、哪个班；
- 口吻平实，不要写成小说人物介绍，不要夸张的设定；
{occult_line}"""
DRAFT_PROMPT = (DRAFT_PROMPT.replace("{no_real}", C.pick("fictional_world", "不要出现任何现实里的国家、城市、学校名；"))
                .replace("{occult_line}", C.pick("occult", "\n- 其中最多一个人对神秘学有一点点兴趣，其余都和神秘学无关。"))
                .replace("{where}", C.pick("fictional_world", "一个虚构的城市里", "一个小县城里")))


def _clean_draft(d: dict[str, Any]) -> dict[str, Any] | None:
    name = str(d.get("name") or "").strip()[:12]
    if not name:
        return None
    return {"name": name, "relation": str(d.get("relation") or "").strip()[:30],
            "personality": str(d.get("personality") or "").strip()[:60],
            "how": str(d.get("how") or "").strip()[:80], "recent": str(d.get("recent") or "").strip()[:60]}


async def draft_friends(settings: dict[str, Any], n: int | None = None) -> list[dict[str, Any]]:
    """生成 n 个朋友草稿并存起来（draft=1）。返回新存的草稿。"""
    from . import reading

    if llm.is_echo(settings):
        raise ValueError("还没接入模型：先在设置的「模型与记忆」里填好 API Key")
    ensure_seed()
    n = int(n or registry.num("people_draft_count"))
    existing = "、".join(p["name"] for p in list_people()) or "（还没有）"
    persona = (settings.get("system_prompt") or "")[:800]
    out: list[dict[str, Any]] = []
    for attempt in range(2):
        raw = await llm.complete(
            settings,
            [{"role": "system", "content": DRAFT_PROMPT.format(name=C.display_name(settings), persona=persona, existing=existing, n=n)},
             {"role": "user", "content": "设计吧。"}],
            temperature=1.0,
        )
        items = _json_object(raw).get("friends") or []
        out = []
        for it in items if isinstance(items, list) else []:
            c = _clean_draft(it) if isinstance(it, dict) else None
            if not c:
                continue
            if reading.mentions_real_place(" ".join(c.values())):
                continue                                      # 出现现实地名：这一个丢掉
            if c["name"] in {x["name"] for x in out} or c["name"] in existing:
                continue
            out.append(c)
        if len(out) >= min(n, 3) or attempt == 1:
            break
    saved = []
    for c in out[:n]:
        recent = [{"date": datetime.now().strftime("%Y-%m-%d"), "text": c["recent"]}] if c["recent"] else []
        try:
            saved.append(add(c["name"], "friend", c["relation"], c["personality"], c["how"], recent, draft=True))
        except ValueError:
            continue
    if not saved:
        raise ValueError("这次没生成出可用的草稿，再试一次")
    return saved
