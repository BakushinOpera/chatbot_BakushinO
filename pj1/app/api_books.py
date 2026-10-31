"""书架 / 阅读器相关的接口。"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import books as bk
from . import library as lib

router = APIRouter()


class ProgressIn(BaseModel):
    chapter: int = Field(ge=0)
    offset: int = Field(ge=0)


class BookmarkIn(BaseModel):
    chapter: int = Field(ge=0)
    offset: int = Field(ge=0)
    snippet: str = ""


class InkIn(BaseModel):
    key: str = Field(min_length=1, max_length=60)
    strokes: list[dict[str, Any]] = Field(default_factory=list, max_length=2000)


class BookPatch(BaseModel):
    title: str | None = None
    author: str | None = None


@router.patch("/api/books/{book_id}")
async def patch_book(book_id: int, payload: BookPatch) -> dict[str, Any]:
    """改书名 / 作者。"""
    try:
        book = lib.update_book(book_id, **payload.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not book:
        raise HTTPException(status_code=404, detail="这本书不存在")
    return {"book": book}


def _book_or_404(book_id: int) -> dict[str, Any]:
    book = lib.get_book(book_id)
    if not book:
        raise HTTPException(status_code=404, detail="这本书不存在")
    return book


@router.post("/api/books")
async def upload_book(request: Request, filename: str = "") -> dict[str, Any]:
    """上传一本书。请求体就是文件本身（不用 multipart，免装依赖），文件名放在 ?filename= 里。"""
    filename = (filename or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
    if not filename:
        raise HTTPException(status_code=400, detail="缺少文件名")
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="文件是空的")
    try:
        # 解析大文件会卡一会儿，放到线程里，别堵住别的请求
        parsed = await asyncio.get_running_loop().run_in_executor(None, bk.parse_book, data, filename)
    except bk.BookError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    book = lib.add_book(parsed, data, filename)
    return {"book": book, "chapters": len(parsed["chapters"])}


@router.get("/api/books")
async def list_books() -> dict[str, Any]:
    return {"books": lib.list_books()}


@router.get("/api/books/{book_id}")
async def get_book(book_id: int) -> dict[str, Any]:
    book = _book_or_404(book_id)
    chapters = bk.load_chapters(book_id)
    book["phal_status_label"] = lib.STATUS_LABEL.get(book["phal_status"], "")
    return {
        "book": book,
        "toc": [{"n": i, "title": c["title"], "chars": len(c["text"])} for i, c in enumerate(chapters)],
        "bookmarks": lib.list_bookmarks(book_id),
    }


@router.get("/api/books/{book_id}/chapters/{n}")
async def get_chapter(book_id: int, n: int) -> dict[str, Any]:
    _book_or_404(book_id)
    chapters = bk.load_chapters(book_id)
    if not 0 <= n < len(chapters):
        raise HTTPException(status_code=404, detail="没有这一章")
    ch = chapters[n]
    return {
        "n": n,
        "total": len(chapters),
        "title": ch["title"],
        "text": ch["text"],
        "annotations": lib.annotations_in_chapter(book_id, n),
        "bookmarks": [b for b in lib.list_bookmarks(book_id) if b["chapter"] == n],
    }


@router.get("/api/books/{book_id}/search")
async def search_book(book_id: int, q: str = "", chapter: int | None = None) -> dict[str, Any]:
    """搜书的正文：默认搜全书，带 chapter 就只搜那一章。"""
    book = _book_or_404(book_id)
    if book["fmt"] == "pdfimg":
        raise HTTPException(status_code=400, detail="这是图片版（扫描 pdf），没有文字，搜不了。")
    try:
        return bk.search_text(book_id, q, chapter)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/books/{book_id}/pages/{n}")
async def get_page_image(book_id: int, n: int) -> FileResponse:
    """图片版书籍的第 n 页（从 0 开始）。"""
    _book_or_404(book_id)
    path = bk.page_path(book_id, n)
    if path is None:
        raise HTTPException(status_code=404, detail="没有这一页")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})


@router.get("/api/books/{book_id}/ink")
async def get_ink(book_id: int) -> dict[str, Any]:
    _book_or_404(book_id)
    return {"ink": lib.get_ink(book_id)}


@router.put("/api/books/{book_id}/ink")
async def put_ink(book_id: int, payload: InkIn) -> dict[str, Any]:
    _book_or_404(book_id)
    lib.set_ink(book_id, payload.key, payload.strokes)
    return {"ok": True}


@router.put("/api/books/{book_id}/progress")
async def put_progress(book_id: int, payload: ProgressIn) -> dict[str, Any]:
    _book_or_404(book_id)
    lib.save_my_progress(book_id, payload.chapter, payload.offset)
    return {"ok": True}


@router.post("/api/books/{book_id}/bookmarks")
async def add_bookmark(book_id: int, payload: BookmarkIn) -> dict[str, Any]:
    _book_or_404(book_id)
    return {"bookmark": lib.add_bookmark(book_id, payload.chapter, payload.offset, payload.snippet)}


@router.delete("/api/bookmarks/{bookmark_id}")
async def delete_bookmark(bookmark_id: int) -> dict[str, Any]:
    if not lib.delete_bookmark(bookmark_id):
        raise HTTPException(status_code=404, detail="书签不存在")
    return {"ok": True}


@router.delete("/api/books/{book_id}")
async def delete_book(book_id: int) -> dict[str, Any]:
    if not lib.delete_book(book_id):
        raise HTTPException(status_code=404, detail="这本书不存在")
    return {"ok": True}
