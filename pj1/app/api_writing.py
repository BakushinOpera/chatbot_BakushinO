"""写作：文档卡片、网页编辑器、我的文字。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import books as bk
from . import db, writing
from . import library as lib
from .writing import DocError

router = APIRouter()


class DocNew(BaseModel):
    kind: str = "phal"                 # phal = 给 Phal 的空白文档 | mine = 我的文字
    title: str = Field(min_length=1)
    content: str = ""


class DocPatch(BaseModel):
    title: str | None = None


class DocSave(BaseModel):
    content: str
    base: str | None = None           # 打开 / 上次保存时文档的 updated_at；对不上说明期间被别人（Phal）改过


class Restore(BaseModel):
    version_id: int


def _wrap(fn, *args):
    try:
        return fn(*args)
    except DocError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _reading_card() -> dict[str, Any] | None:
    """Phal 正在读的书：当前状态里的那本，否则书架上「在读」的第一本。"""
    from . import life

    books = lib.list_books()
    st = life.load_state()
    book = None
    if st and st.get("book_id"):
        book = next((b for b in books if b["id"] == st["book_id"]), None)
    if book is None:
        book = next((b for b in books if b["phal_status"] == "reading"), None)
    if book is None:
        return None
    chapters = bk.load_chapters(book["id"])
    ch = min(book["phal_chapter"], max(0, len(chapters) - 1))
    notes = lib.notes_for_book(book["id"], 1)
    return {
        "id": book["id"], "title": book["title"], "author": book["author"],
        "chapter": ch, "chapter_title": chapters[ch]["title"] if chapters else "",
        "n_chapters": book["n_chapters"], "percent": book["phal_percent"],
        "status": book["phal_status_label"], "reading_now": bool(st and st.get("key") == "read" and st.get("book_id") == book["id"]),
        "note": notes[0]["text"] if notes else "",
    }


@router.get("/api/side")
async def side() -> dict[str, Any]:
    """聊天框右侧卡片的内容：她在读的书 + 她的写作文档 + 我的文字。"""
    docs = writing.list_docs("phal")
    return {
        "reading": _reading_card(),
        "docs": docs,
        "mine": writing.list_docs("mine"),
        "letters": writing.list_docs("letter"),
        "limit": writing.doc_limit(),
        "blocked": writing.writing_blocked(),
        "waiting": db.get_settings().get("writing_waiting") == "1",
    }


@router.post("/api/docs")
async def create_doc(payload: DocNew) -> dict[str, Any]:
    if payload.kind not in ("phal", "mine"):
        raise HTTPException(status_code=400, detail="只能建「给 Phal 的空白文档」或「我的文字」（信是她自己月末写的）")
    doc = _wrap(writing.create_doc, payload.kind, payload.title, payload.content)
    from . import life

    resumed = life.on_new_doc(doc)              # 她之前问我要过新文档、在等的话：现在接着写
    return {"doc": doc, "she_resumed": resumed}


@router.get("/api/docs/{doc_id}")
async def get_doc(doc_id: int) -> dict[str, Any]:
    doc, content = _wrap(writing.read_doc, doc_id)
    return {"doc": doc, "content": content}


@router.patch("/api/docs/{doc_id}")
async def patch_doc(doc_id: int, payload: DocPatch) -> dict[str, Any]:
    if payload.title is None:
        raise HTTPException(status_code=400, detail="没有要改的内容")
    return {"doc": _wrap(writing.rename_doc, doc_id, payload.title)}


@router.put("/api/docs/{doc_id}")
async def save_doc(doc_id: int, payload: DocSave) -> dict[str, Any]:
    if payload.base:
        cur = writing.get_doc(doc_id)
        if cur and cur["updated_at"] != payload.base:
            raise HTTPException(status_code=409, detail="文档在你编辑期间被改过（可能是 Phal 刚写了新内容），已经帮你刷新。")
    _, old = _wrap(writing.read_doc, doc_id)
    doc = _wrap(writing.save_content, doc_id, payload.content)
    from . import life

    life.on_user_edit(doc, old, payload.content.replace("\r\n", "\n"))     # 她会读修改记录，可能问原因 / 犟着改回去
    return {"doc": doc}


@router.delete("/api/docs/{doc_id}")
async def delete_doc(doc_id: int) -> dict[str, Any]:
    if not writing.delete_doc(doc_id):
        raise HTTPException(status_code=404, detail="这份文档不存在")
    return {"ok": True}


@router.get("/api/docs/{doc_id}/versions")
async def versions(doc_id: int) -> dict[str, Any]:
    return {"versions": writing.list_versions(doc_id)}


@router.post("/api/docs/{doc_id}/restore")
async def restore(doc_id: int, payload: Restore) -> dict[str, Any]:
    return {"doc": _wrap(writing.restore_version, doc_id, payload.version_id)}


@router.post("/api/docs/import")
async def import_text(request: Request, filename: str = "") -> dict[str, Any]:
    """导入我的文字：请求体就是 .txt / .md 文件本身，文件名放 ?filename=。"""
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    if not name.lower().endswith(writing.EXTS):
        raise HTTPException(status_code=400, detail="只支持 .txt / .md")
    data = await request.body()
    if not data or len(data) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="文件是空的，或超过 5MB")
    text = bk.decode_bytes(data)
    stem = name.rsplit(".", 1)[0]
    return {"doc": writing.create_doc("mine", stem, text)}
