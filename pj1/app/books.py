"""书籍解析：epub / txt / pdf → 章节列表。只用标准库（zipfile + html.parser + xml.etree + zlib）。

约定：每章是一段纯文本，段落之间用单个 "\\n" 分隔。
**所有注释、书签、进度的位置，都是这个章节文本里的字符偏移**（再配一小段原文片段当兜底），
所以只要这里的规整规则不变，分页、字号怎么变，位置都能找回来。
"""

from __future__ import annotations

import io
import json
import posixpath
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from xml.etree import ElementTree as ET

from .config import DATA_DIR

BOOKS_DIR = DATA_DIR / "books"
MAX_UPLOAD_BYTES = 80 * 1024 * 1024
MIN_CHAPTER_CHARS = 150          # epub 里太短的页（封面、版权页）并进相邻章节
TXT_CHUNK_CHARS = 6000           # txt 识别不到章节时，按这个长度分

_BLOCK = {
    "p", "div", "br", "li", "tr", "blockquote", "section", "article", "pre",
    "h1", "h2", "h3", "h4", "h5", "h6", "dt", "dd", "figcaption", "table",
}
_HEADINGS = {"h1", "h2", "h3"}


class BookError(ValueError):
    pass


# --------------------------------------------------------------------------
# 文本规整
# --------------------------------------------------------------------------
def decode_bytes(data: bytes) -> str:
    """UTF-8 / GBK（含 GB18030）自动识别。"""
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    for enc in ("gb18030", "big5"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode("utf-8", "replace")


def normalize_text(text: str) -> str:
    """去掉空行和行首尾空白；段落之间只留一个 \\n。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("　", " ").replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n")]
    return "\n".join(ln for ln in lines if ln)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0
        self.heading: str | None = None
        self._in_heading = False
        self._heading_buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in ("script", "style", "head", "svg"):
            self.skip += 1
            return
        if tag in _BLOCK:
            self.parts.append("\n")
        if tag in _HEADINGS and self.heading is None:
            self._in_heading = True
            self._heading_buf = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "head", "svg"):
            self.skip = max(0, self.skip - 1)
            return
        if tag in _BLOCK:
            self.parts.append("\n")
        if self._in_heading and tag in _HEADINGS:
            self._in_heading = False
            title = re.sub(r"\s+", " ", "".join(self._heading_buf)).strip()
            if title:
                self.heading = title

    def handle_data(self, data: str) -> None:
        if self.skip:
            return
        self.parts.append(data)
        if self._in_heading:
            self._heading_buf.append(data)


def html_to_text(html: str) -> tuple[str, str | None]:
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 —— 坏掉的 html 尽量多捞一点
        pass
    return normalize_text("".join(parser.parts)), parser.heading


# --------------------------------------------------------------------------
# epub
# --------------------------------------------------------------------------
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _find_all(root: ET.Element, name: str) -> list[ET.Element]:
    return [e for e in root.iter() if _local(e.tag) == name]


def parse_epub(data: bytes) -> dict[str, Any]:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise BookError("这不是有效的 epub 文件") from exc
    names = set(z.namelist())

    try:
        container = ET.fromstring(z.read("META-INF/container.xml"))
        opf_path = next(e.get("full-path") for e in _find_all(container, "rootfile") if e.get("full-path"))
    except (KeyError, StopIteration, ET.ParseError) as exc:
        raise BookError("epub 缺少目录信息（container.xml）") from exc
    opf_dir = posixpath.dirname(opf_path)
    try:
        opf = ET.fromstring(z.read(opf_path))
    except (KeyError, ET.ParseError) as exc:
        raise BookError("epub 的 OPF 文件损坏") from exc

    def meta(name: str) -> str:
        for e in _find_all(opf, name):
            if e.text and e.text.strip():
                return e.text.strip()
        return ""

    manifest: dict[str, dict[str, str]] = {}
    for item in _find_all(opf, "item"):
        manifest[item.get("id", "")] = {
            "href": item.get("href", ""),
            "type": item.get("media-type", ""),
            "props": item.get("properties", ""),
        }

    def resolve(href: str) -> str:
        return posixpath.normpath(posixpath.join(opf_dir, unquote(href.split("#", 1)[0])))

    # 目录标题：优先 nav.xhtml，其次 toc.ncx
    toc_titles: dict[str, str] = {}
    for it in manifest.values():
        if "nav" in it["props"].split():
            try:
                nav_path = resolve(it["href"])
                nav_dir = posixpath.dirname(nav_path)
                html = decode_bytes(z.read(nav_path))
                for m in re.finditer(r'<a[^>]*href="([^"#]*)[^"]*"[^>]*>(.*?)</a>', html, re.S):
                    title = re.sub(r"<[^>]+>", "", m.group(2))
                    title = re.sub(r"\s+", " ", title).strip()
                    if title and m.group(1):
                        key = posixpath.normpath(posixpath.join(nav_dir, unquote(m.group(1))))
                        toc_titles.setdefault(key, title)
            except KeyError:
                pass
    if not toc_titles:
        for it in manifest.values():
            if it["type"] == "application/x-dtbncx+xml":
                try:
                    ncx_path = resolve(it["href"])
                    ncx_dir = posixpath.dirname(ncx_path)
                    ncx = ET.fromstring(z.read(ncx_path))
                    for point in _find_all(ncx, "navPoint"):
                        label = next((t.text for t in _find_all(point, "text") if t.text), "")
                        content = next(iter(_find_all(point, "content")), None)
                        if label and content is not None and content.get("src"):
                            key = posixpath.normpath(posixpath.join(ncx_dir, unquote(content.get("src").split("#", 1)[0])))
                            toc_titles.setdefault(key, re.sub(r"\s+", " ", label).strip())
                except (KeyError, ET.ParseError):
                    pass

    raw: list[dict[str, str]] = []
    for ref in _find_all(opf, "itemref"):
        it = manifest.get(ref.get("idref", ""))
        if not it or "html" not in it["type"] and "xml" not in it["type"]:
            continue
        path = resolve(it["href"])
        if path not in names:
            continue
        text, heading = html_to_text(decode_bytes(z.read(path)))
        if not text:
            continue
        raw.append({"title": toc_titles.get(path) or heading or "", "text": text, "in_toc": path in toc_titles})

    if not raw:
        raise BookError("没能从这个 epub 里读出文字（可能是纯图片书）")

    # 太短的页并进下一章（封面、扉页、版权页）；最后一页太短就并进前一章
    chapters: list[dict[str, str]] = []
    carry = ""
    for ch in raw:
        if len(ch["text"]) < MIN_CHAPTER_CHARS and not (ch["in_toc"] and toc_titles):
            carry = (carry + "\n" + ch["text"]).strip("\n") if carry else ch["text"]
            continue
        if carry:
            ch = {"title": ch["title"], "text": carry + "\n" + ch["text"]}
            carry = ""
        chapters.append(ch)
    if carry:
        if chapters:
            chapters[-1]["text"] += "\n" + carry
        else:
            chapters.append({"title": "", "text": carry})
    chapters = [{"title": c["title"], "text": c["text"]} for c in chapters]
    for i, ch in enumerate(chapters, 1):
        if not ch["title"]:
            ch["title"] = f"第 {i} 节"
    return {"title": meta("title"), "author": meta("creator"), "chapters": chapters}


# --------------------------------------------------------------------------
# txt
# --------------------------------------------------------------------------
_CN_NUM = "0-9０-９零〇一二三四五六七八九十百千万两壹贰叁肆伍陆柒捌玖拾佰仟"
_CHAPTER_MARK = re.compile(rf"^第\s*[{_CN_NUM}]+\s*[章节回卷部集篇幕]")
_EN_CHAPTER = re.compile(r"^(?:chapter|CHAPTER|Chapter)\s+(?:[0-9]+|[IVXLCivxlc]+|[A-Za-z]+)\b.{0,60}$")
_SPECIAL = re.compile(r"^(?:序章|序言|序|楔子|引子|前言|尾声|后记|终章|番外.{0,20})\s*$")
_SENTENCE_PUNCT = re.compile(r"[。，！？；,!?;]")


def is_chapter_heading(line: str) -> bool:
    """像不像一个章节标题。「第3章 重逢」「Chapter 3」「序章」算；
    正文里恰好以「第1章的……」开头的整句话（带逗号句号）不算。"""
    line = line.strip()
    if not line or len(line) > 60:
        return False
    if _SPECIAL.match(line) or _EN_CHAPTER.match(line):
        return True
    m = _CHAPTER_MARK.match(line)
    if not m:
        return False
    rest = line[m.end():]
    if not rest:
        return True
    if rest[0] in " \t　:：、.．-—_·":
        return len(rest) <= 45 and not _SENTENCE_PUNCT.search(rest.rstrip("。.!！"))
    return len(rest) <= 20 and not _SENTENCE_PUNCT.search(rest)    # 「第一章重逢」这种没写空格的


def parse_txt(data: bytes, filename: str = "") -> dict[str, Any]:
    text = normalize_text(decode_bytes(data))
    if not text:
        raise BookError("文件是空的")
    return {"title": Path(filename).stem if filename else "", "author": "", "chapters": chapters_from_text(text)}


def parse_pdf(data: bytes, filename: str = "") -> dict[str, Any]:
    """PDF：有文字层就取文字（和 txt 走同一套章节识别，阅读器里的呈现和别的书一样）；
    扫描版 / 图片版就一页一张图（阅读器里左右翻页；Phal 看不了字，所以不会去读它）。"""
    from . import pdftext

    got = None
    text_err: pdftext.PdfError | None = None
    try:
        got = pdftext.extract_text(data)
    except pdftext.PdfError as exc:
        text_err = exc
    if got and len(got["text"]) / max(1, got["pages"]) >= 40:
        text = normalize_text(got["text"])
        if text:
            return {"title": got["title"], "author": got["author"], "chapters": chapters_from_text(text)}
    if text_err is not None and not isinstance(text_err, pdftext.NoTextError):
        raise BookError(str(text_err)) from text_err        # 不是 PDF / 加密：图片也读不了
    try:
        imgs = pdftext.extract_page_images(data)
    except pdftext.PdfError as exc:
        if got:
            text = normalize_text(got["text"])
            return {"title": got["title"], "author": got["author"], "chapters": chapters_from_text(text)}
        raise BookError(str(exc)) from exc
    pages = imgs["pages"]
    return {
        "title": imgs["title"], "author": imgs["author"], "pages": pages,
        "chapters": [{"title": f"第 {i + 1} 页", "text": ""} for i in range(len(pages))],
    }


def chapters_from_text(text: str) -> list[dict[str, str]]:
    lines = text.split("\n")
    heads = [i for i, ln in enumerate(lines) if is_chapter_heading(ln)]

    chapters: list[dict[str, str]] = []
    if len(heads) >= 2:
        if heads[0] > 0:
            pre = "\n".join(lines[: heads[0]])
            if len(pre) >= MIN_CHAPTER_CHARS:
                chapters.append({"title": "前言", "text": pre})
            else:  # 书名、作者之类的短前缀，并进第一章
                lines = lines[heads[0]:]
                heads = [h - heads[0] for h in heads]
        for k, h in enumerate(heads):
            end = heads[k + 1] if k + 1 < len(heads) else len(lines)
            body = "\n".join(lines[h + 1 : end])
            chapters.append({"title": lines[h].strip(), "text": body or lines[h]})
    else:
        # 识别不到章节：按长度在段落边界切开
        buf: list[str] = []
        size = 0
        for ln in lines:
            buf.append(ln)
            size += len(ln)
            if size >= TXT_CHUNK_CHARS:
                chapters.append({"title": f"第 {len(chapters) + 1} 部分", "text": "\n".join(buf)})
                buf, size = [], 0
        if buf:
            if chapters and size < MIN_CHAPTER_CHARS:
                chapters[-1]["text"] += "\n" + "\n".join(buf)
            else:
                chapters.append({"title": f"第 {len(chapters) + 1} 部分", "text": "\n".join(buf)})

    return chapters


# --------------------------------------------------------------------------
# 入口 + 存储
# --------------------------------------------------------------------------
def parse_book(data: bytes, filename: str) -> dict[str, Any]:
    if len(data) > MAX_UPLOAD_BYTES:
        raise BookError("文件太大了（上限 80MB）")
    ext = Path(filename).suffix.lower()
    if ext == ".epub":
        parsed = parse_epub(data)
    elif ext in (".txt", ".text"):
        parsed = parse_txt(data, filename)
    elif ext == ".pdf":
        parsed = parse_pdf(data, filename)
    else:
        raise BookError("只支持 epub、txt 和 pdf")
    if not parsed["title"].strip():
        parsed["title"] = Path(filename).stem or "未命名"
    parsed["fmt"] = "pdfimg" if parsed.get("pages") else {".epub": "epub", ".pdf": "pdf"}.get(ext, "txt")
    parsed["chars"] = sum(len(c["text"]) for c in parsed["chapters"])
    return parsed


def book_dir(book_id: int) -> Path:
    return BOOKS_DIR / str(int(book_id))


def page_path(book_id: int, n: int) -> Path | None:
    """图片版书籍的第 n 页（从 0 开始）的图片文件。"""
    for p in (book_dir(book_id) / "pages").glob(f"{int(n)}.*"):
        return p
    return None


def save_book_files(book_id: int, original: bytes, filename: str, chapters: list[dict[str, str]],
                    pages: list[tuple[str, bytes]] | None = None) -> None:
    d = book_dir(book_id)
    d.mkdir(parents=True, exist_ok=True)
    if pages:
        (d / "pages").mkdir(exist_ok=True)
        for i, (ext, blob) in enumerate(pages):
            (d / "pages" / f"{i}.{ext}").write_bytes(blob)
    (d / ("original" + (Path(filename).suffix.lower() or ".bin"))).write_bytes(original)
    (d / "chapters.json").write_text(json.dumps(chapters, ensure_ascii=False), encoding="utf-8")
    _cache.pop(int(book_id), None)


_cache: dict[int, list[dict[str, str]]] = {}


def load_chapters(book_id: int) -> list[dict[str, str]]:
    book_id = int(book_id)
    if book_id not in _cache:
        path = book_dir(book_id) / "chapters.json"
        if not path.exists():
            return []
        if len(_cache) > 6:
            _cache.clear()
        _cache[book_id] = json.loads(path.read_text(encoding="utf-8"))
    return _cache[book_id]


SEARCH_MAX_HITS = 300            # 一次最多返回多少处（总数照实报，超出的不带上下文）
SEARCH_CONTEXT = (18, 30)        # 命中前后各带多少字的上下文


def search_text(book_id: int, query: str, chapter: int | None = None) -> dict[str, Any]:
    """在书的正文里找一段话（默认搜全书；给了 chapter 就只搜那一章）。不分大小写，空白按原样匹配（换行不参与）。

    返回的位置和阅读器一致：章节 + 章节文本里的字符偏移，点一下就能跳过去。
    """
    q = re.sub(r"[\r\n]+", " ", query or "").strip()
    if not q:
        raise ValueError("搜什么呢？")
    chapters = load_chapters(book_id)
    if chapter is not None and not 0 <= int(chapter) < len(chapters):
        raise ValueError("没有这一章")
    pat = re.compile(re.escape(q), re.IGNORECASE)
    pre_n, post_n = SEARCH_CONTEXT
    hits: list[dict[str, Any]] = []
    total = 0
    in_chapters = 0
    for n, ch in enumerate(chapters):
        if chapter is not None and n != int(chapter):
            continue
        text = ch["text"]
        found = False
        for m in pat.finditer(text):
            total += 1
            found = True
            if len(hits) < SEARCH_MAX_HITS:
                s, e = m.start(), m.end()
                clean = lambda t: re.sub(r"\s*\n\s*", " ", t)  # noqa: E731
                hits.append({"chapter": n, "chapter_title": ch["title"], "offset": s, "length": e - s,
                             "pre": clean(text[max(0, s - pre_n):s]), "hit": clean(text[s:e]), "post": clean(text[e:e + post_n])})
        if found:
            in_chapters += 1
    return {"query": q, "scope": "chapter" if chapter is not None else "book", "chapter": chapter, "hits": hits, "total": total,
            "chapters": in_chapters, "truncated": total > len(hits)}


def remove_book_files(book_id: int) -> None:
    import shutil

    _cache.pop(int(book_id), None)
    shutil.rmtree(book_dir(book_id), ignore_errors=True)
