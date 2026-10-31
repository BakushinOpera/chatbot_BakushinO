"""PDF → 纯文本（只用标准库：re + zlib）。

只取文字层：支持 Flate 压缩流、对象流（ObjStm）、简单字体（WinAnsi / MacRoman / Differences）
和带 ToUnicode 的复合字体（中日韩 PDF 基本都是这种）。不支持：加密、扫描版（只有图片）、
没有 ToUnicode 的 Identity-H 字体——遇到这些会明确报错，而不是吐一堆乱码。

输出和 epub / txt 一样：段落之间一个 "\\n"，交给 books.py 的章节识别去切章。
"""

from __future__ import annotations

import re
import zlib
from typing import Any


class PdfError(ValueError):
    pass


class NoTextError(PdfError):
    """没有文字层（扫描版）：可以改走「一页一张图」。"""


class Name(str):
    pass


class Ref:
    __slots__ = ("num",)

    def __init__(self, num: int) -> None:
        self.num = num


_WS = b"\x00\t\n\x0c\r "
_DELIM = b"()<>[]{}/%"
_NUM_RE = re.compile(rb"[+-]?(?:\d+\.?\d*|\.\d+)")


# --------------------------------------------------------------------------
# 词法 / 对象解析
# --------------------------------------------------------------------------
class Lexer:
    def __init__(self, buf: bytes, pos: int = 0) -> None:
        self.buf = buf
        self.pos = pos

    def _skip(self) -> None:
        b, n = self.buf, len(self.buf)
        while self.pos < n:
            c = b[self.pos:self.pos + 1]
            if c in (b"\x00", b"\t", b"\n", b"\x0c", b"\r", b" "):
                self.pos += 1
            elif c == b"%":
                while self.pos < n and b[self.pos:self.pos + 1] not in (b"\n", b"\r"):
                    self.pos += 1
            else:
                break

    def token(self) -> Any:
        """下一个记号：数字 / Name / bytes（字符串）/ '[' ']' '<<' '>>' / 关键字（str）/ None（结束）。"""
        self._skip()
        b, n = self.buf, len(self.buf)
        if self.pos >= n:
            return None
        c = b[self.pos:self.pos + 1]
        if c == b"/":
            self.pos += 1
            start = self.pos
            while self.pos < n and b[self.pos] not in _WS and b[self.pos] not in _DELIM:
                self.pos += 1
            raw = b[start:self.pos]
            raw = re.sub(rb"#([0-9A-Fa-f]{2})", lambda m: bytes([int(m.group(1), 16)]), raw)
            return Name(raw.decode("latin-1"))
        if c == b"(":
            return self._literal()
        if c == b"<":
            if b[self.pos:self.pos + 2] == b"<<":
                self.pos += 2
                return "<<"
            end = b.find(b">", self.pos)
            if end < 0:
                end = n
            hexs = re.sub(rb"[^0-9A-Fa-f]", b"", b[self.pos + 1:end])
            if len(hexs) % 2:
                hexs += b"0"
            self.pos = end + 1
            return bytes.fromhex(hexs.decode("ascii"))
        if c == b">":
            if b[self.pos:self.pos + 2] == b">>":
                self.pos += 2
                return ">>"
            self.pos += 1
            return ">"
        if c in (b"[", b"]", b"{", b"}"):
            self.pos += 1
            return c.decode()
        if c == b")":
            self.pos += 1
            return ")"
        m = _NUM_RE.match(b, self.pos)
        if m and (m.end() >= n or b[m.end()] in _WS or b[m.end()] in _DELIM):
            self.pos = m.end()
            txt = m.group(0)
            return float(txt) if b"." in txt else int(txt)
        start = self.pos
        while self.pos < n and b[self.pos] not in _WS and b[self.pos] not in _DELIM:
            self.pos += 1
        if self.pos == start:
            self.pos += 1
        return b[start:self.pos].decode("latin-1")

    def _literal(self) -> bytes:
        b, n = self.buf, len(self.buf)
        self.pos += 1
        depth = 1
        out = bytearray()
        while self.pos < n:
            ch = b[self.pos]
            self.pos += 1
            if ch == 0x5C:  # 反斜杠
                if self.pos >= n:
                    break
                e = b[self.pos]
                self.pos += 1
                if e in b"nrtbf":
                    out.append({"n": 10, "r": 13, "t": 9, "b": 8, "f": 12}[chr(e)])
                elif 0x30 <= e <= 0x37:
                    v = e - 0x30
                    for _ in range(2):
                        if self.pos < n and 0x30 <= b[self.pos] <= 0x37:
                            v = v * 8 + b[self.pos] - 0x30
                            self.pos += 1
                        else:
                            break
                    out.append(v & 0xFF)
                elif e in (10, 13):
                    if e == 13 and self.pos < n and b[self.pos] == 10:
                        self.pos += 1
                else:
                    out.append(e)
            elif ch == 0x28:
                depth += 1
                out.append(ch)
            elif ch == 0x29:
                depth -= 1
                if depth == 0:
                    break
                out.append(ch)
            else:
                out.append(ch)
        return bytes(out)


def parse_obj(lex: Lexer, first: Any = ...) -> Any:
    tok = lex.token() if first is ... else first
    if tok == "<<":
        d: dict[str, Any] = {}
        while True:
            k = lex.token()
            if k is None or k == ">>":
                break
            if not isinstance(k, Name):
                continue
            d[str(k)] = parse_obj(lex)
        return d
    if tok == "[":
        arr: list[Any] = []
        while True:
            t = lex.token()
            if t is None or t == "]":
                break
            arr.append(parse_obj(lex, t))
        return arr
    if isinstance(tok, int) and not isinstance(tok, bool):
        save = lex.pos
        t2 = lex.token()
        if isinstance(t2, int):
            t3 = lex.token()
            if t3 == "R":
                return Ref(tok)
        lex.pos = save
        return tok
    if tok == "true":
        return True
    if tok == "false":
        return False
    if tok == "null":
        return None
    return tok


# --------------------------------------------------------------------------
# 文件结构
# --------------------------------------------------------------------------
_OBJ_RE = re.compile(rb"(?<![0-9])(\d+)\s+(\d+)\s+obj\b")


class Pdf:
    def __init__(self, data: bytes) -> None:
        if not data.lstrip()[:5] == b"%PDF-":
            raise PdfError("这不是有效的 PDF 文件")
        if re.search(rb"/Encrypt\s*(?:\d+\s+\d+\s+R|<<)", data):
            raise PdfError("这个 PDF 是加密的，读不了文字")
        self.data = data
        self.offsets: dict[int, int] = {}
        self.cache: dict[int, Any] = {}
        self.compressed: dict[int, Any] = {}
        objstms: list[int] = []
        for m in _OBJ_RE.finditer(data):
            num = int(m.group(1))
            self.offsets[num] = m.end()
            if b"/ObjStm" in data[m.end():m.end() + 300]:
                objstms.append(num)
        for num in objstms:
            self._load_objstm(num)

    # —— 对象读取 ——
    def _read_at(self, pos: int) -> tuple[Any, bytes | None]:
        lex = Lexer(self.data, pos)
        obj = parse_obj(lex)
        stream = None
        if isinstance(obj, dict):
            save = lex.pos
            tok = lex.token()
            if tok == "stream":
                p = lex.pos
                if self.data[p:p + 2] == b"\r\n":
                    p += 2
                elif self.data[p:p + 1] in (b"\n", b"\r"):
                    p += 1
                length = obj.get("Length")
                if isinstance(length, Ref):
                    length = self.get(length.num)
                end = None
                if isinstance(length, int) and length >= 0:
                    tail = self.data[p + length:p + length + 20].lstrip(_WS)
                    if tail.startswith(b"endstream"):
                        end = p + length
                if end is None:
                    end = self.data.find(b"endstream", p)
                    if end < 0:
                        end = len(self.data)
                    while end > p and self.data[end - 1:end] in (b"\n", b"\r"):
                        end -= 1
                stream = self.data[p:end]
            else:
                lex.pos = save
        return obj, stream

    def _load_objstm(self, num: int) -> None:
        try:
            d, raw = self._read_at(self.offsets[num])
            body = self.decode_stream(d, raw)
            n, first = int(d.get("N", 0)), int(d.get("First", 0))
            lex = Lexer(body)
            pairs = []
            for _ in range(n):
                a, b = lex.token(), lex.token()
                if isinstance(a, int) and isinstance(b, int):
                    pairs.append((a, b))
            for onum, off in pairs:
                self.compressed[onum] = (body, first + off)
        except Exception:  # noqa: BLE001 —— 一个坏的对象流不该拖垮整本
            return

    def get(self, num: int) -> Any:
        if num in self.cache:
            return self.cache[num]
        val: Any = None
        if num in self.compressed:
            body, pos = self.compressed[num]
            try:
                val = parse_obj(Lexer(body, pos))
            except Exception:  # noqa: BLE001
                val = None
        elif num in self.offsets:
            try:
                val, _ = self._read_at(self.offsets[num])
            except Exception:  # noqa: BLE001
                val = None
        self.cache[num] = val
        return val

    def stream_of(self, num: int) -> bytes:
        if num not in self.offsets:
            return b""
        try:
            d, raw = self._read_at(self.offsets[num])
        except Exception:  # noqa: BLE001
            return b""
        return self.decode_stream(d, raw) if raw is not None else b""

    def resolve(self, v: Any) -> Any:
        depth = 0
        while isinstance(v, Ref) and depth < 20:
            v = self.get(v.num)
            depth += 1
        return v

    def decode_stream(self, d: dict[str, Any], raw: bytes | None) -> bytes:
        if raw is None:
            return b""
        filters = self.resolve(d.get("Filter"))
        if filters is None:
            return raw
        if not isinstance(filters, list):
            filters = [filters]
        data = raw
        for f in filters:
            f = str(self.resolve(f))
            if f in ("FlateDecode", "Fl"):
                try:
                    data = zlib.decompress(data)
                except zlib.error:
                    try:
                        data = zlib.decompressobj().decompress(data)     # 截断 / 末尾有杂质的流：能解多少算多少
                    except zlib.error:
                        return b""
            elif f in ("ASCIIHexDecode", "AHx"):
                data = bytes.fromhex(re.sub(rb"[^0-9A-Fa-f]", b"", data.split(b">")[0]).decode() or "")
            else:
                return b""        # LZW / ASCII85 / 图片滤镜：不处理
        return data

    # —— 页面树 ——
    def pages(self) -> list[dict[str, Any]]:
        root = None
        m = None
        for m in re.finditer(rb"/Root\s+(\d+)\s+\d+\s+R", self.data):
            pass
        if m:
            root = self.get(int(m.group(1)))
        out: list[dict[str, Any]] = []
        seen: set[int] = set()

        def walk(node: Any, inherited: dict[str, Any], depth: int) -> None:
            node = self.resolve(node)
            if not isinstance(node, dict) or depth > 30:
                return
            res = dict(inherited)
            if "Resources" in node:
                res["Resources"] = node["Resources"]
            kids = self.resolve(node.get("Kids"))
            if isinstance(kids, list) and node.get("Type") != "Page":
                for k in kids:
                    if isinstance(k, Ref):
                        if k.num in seen:
                            continue
                        seen.add(k.num)
                    walk(k, res, depth + 1)
            else:
                page = dict(node)
                if "Resources" not in page and "Resources" in res:
                    page["Resources"] = res["Resources"]
                out.append(page)

        if isinstance(root, dict):
            walk(root.get("Pages"), {}, 0)
        if not out:                         # 页面树坏了：按文件里出现的顺序捞 /Type /Page
            for num in sorted(self.offsets):
                head = self.data[self.offsets[num]:self.offsets[num] + 400]
                if re.search(rb"/Type\s*/Page\b(?!s)", head):
                    obj = self.get(num)
                    if isinstance(obj, dict):
                        out.append(obj)
        return out

    def info(self) -> dict[str, str]:
        m = None
        for m in re.finditer(rb"/Info\s+(\d+)\s+\d+\s+R", self.data):
            pass
        res: dict[str, str] = {}
        if m:
            d = self.get(int(m.group(1)))
            if isinstance(d, dict):
                for k in ("Title", "Author"):
                    v = self.resolve(d.get(k))
                    if isinstance(v, bytes):
                        res[k] = _decode_text_string(v)
        return res


def _decode_text_string(b: bytes) -> str:
    if b.startswith(b"\xfe\xff"):
        return b[2:].decode("utf-16-be", "replace").strip()
    if b.startswith(b"\xff\xfe"):
        return b[2:].decode("utf-16-le", "replace").strip()
    if b.startswith(b"\xef\xbb\xbf"):
        return b[3:].decode("utf-8", "replace").strip()
    return b.decode("cp1252", "replace").strip()


# --------------------------------------------------------------------------
# 字体：字节 → 文字
# --------------------------------------------------------------------------
_GLYPHS = {
    "space": " ", "hyphen": "-", "period": ".", "comma": ",", "colon": ":", "semicolon": ";", "quotesingle": "'",
    "quotedbl": '"', "quoteleft": "‘", "quoteright": "’", "quotedblleft": "“", "quotedblright": "”",
    "endash": "–", "emdash": "—", "bullet": "•", "ellipsis": "…", "fi": "fi", "fl": "fl", "ff": "ff",
    "parenleft": "(", "parenright": ")", "exclam": "!", "question": "?", "slash": "/", "ampersand": "&",
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
    "eight": "8", "nine": "9",
}


def _glyph_to_char(name: str) -> str | None:
    if name in _GLYPHS:
        return _GLYPHS[name]
    m = re.fullmatch(r"uni([0-9A-Fa-f]{4})", name) or re.fullmatch(r"u([0-9A-Fa-f]{4,6})", name)
    if m:
        return chr(int(m.group(1), 16))
    if len(name) == 1:
        return name
    return None


def _hexchars(b: bytes) -> str:
    if len(b) == 1:
        return chr(b[0])
    try:
        return b.decode("utf-16-be")
    except UnicodeDecodeError:
        return b.decode("utf-16-be", "replace")


def parse_cmap(data: bytes) -> tuple[dict[int, str], int]:
    """ToUnicode CMap → ({字节码: 文字}, 每个码几个字节)。"""
    mapping: dict[int, str] = {}
    width = 0
    text = data
    for blk in re.findall(rb"begincodespacerange(.*?)endcodespacerange", text, re.S):
        hexes = re.findall(rb"<([0-9A-Fa-f]+)>", blk)
        if hexes:
            width = max(width, len(hexes[0]) // 2)
    for blk in re.findall(rb"beginbfchar(.*?)endbfchar", text, re.S):
        for src, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]*)>", blk):
            mapping[int(src, 16)] = _hexchars(bytes.fromhex(dst.decode() or "00")) if dst else ""
            width = width or len(src) // 2
    for blk in re.findall(rb"beginbfrange(.*?)endbfrange", text, re.S):
        for m in re.finditer(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(\[[^\]]*\]|<[0-9A-Fa-f]*>)", blk):
            lo, hi = int(m.group(1), 16), int(m.group(2), 16)
            width = width or len(m.group(1)) // 2
            tgt = m.group(3)
            if hi - lo > 70000:
                continue
            if tgt.startswith(b"["):
                for i, d in enumerate(re.findall(rb"<([0-9A-Fa-f]*)>", tgt)):
                    if lo + i <= hi:
                        mapping[lo + i] = _hexchars(bytes.fromhex(d.decode() or "00"))
            else:
                base = bytes.fromhex(tgt[1:-1].decode() or "00")
                s = _hexchars(base)
                for i in range(hi - lo + 1):
                    if len(s) == 1:
                        mapping[lo + i] = chr(ord(s) + i)
                    else:
                        mapping[lo + i] = s[:-1] + chr(ord(s[-1]) + i)
    return mapping, width or 1


class Font:
    def __init__(self, pdf: Pdf, d: dict[str, Any]) -> None:
        self.cmap: dict[int, str] = {}
        self.width = 1
        self.table: list[str] | None = None
        self.usable = True
        sub = str(pdf.resolve(d.get("Subtype")))
        tu = d.get("ToUnicode")
        self.widths: dict[int, float] = {}
        self.dw = 500.0                      # 没写字宽的字体（标准 14 字体）：取个平均值
        self._load_widths(pdf, d, sub)
        if isinstance(tu, Ref):
            self.cmap, self.width = parse_cmap(pdf.stream_of(tu.num))
        if sub == "Type0":
            if not self.cmap:
                enc = str(pdf.resolve(d.get("Encoding")))
                self.usable = False if enc.startswith("Identity") else True
                self.width = 2
            return
        if not self.cmap:
            self.table = self._simple_table(pdf, d)

    def _simple_table(self, pdf: Pdf, d: dict[str, Any]) -> list[str]:
        enc = pdf.resolve(d.get("Encoding"))
        codec = "cp1252"
        diffs: list[Any] = []
        if isinstance(enc, dict):
            base = str(pdf.resolve(enc.get("BaseEncoding")) or "")
            codec = "mac_roman" if base.startswith("MacRoman") else "cp1252"
            diffs = pdf.resolve(enc.get("Differences")) or []
        elif isinstance(enc, str):
            codec = "mac_roman" if enc.startswith("MacRoman") else "cp1252"
        table = []
        for i in range(256):
            try:
                table.append(bytes([i]).decode(codec))
            except UnicodeDecodeError:
                table.append(chr(i) if i < 128 else "")
        code = 0
        for item in diffs:
            item = pdf.resolve(item)
            if isinstance(item, int):
                code = item
            elif isinstance(item, str):
                ch = _glyph_to_char(str(item))
                if ch is not None and 0 <= code < 256:
                    table[code] = ch
                code += 1
        return table

    def _load_widths(self, pdf: Pdf, d: dict[str, Any], sub: str) -> None:
        try:
            if sub == "Type0":
                kids = pdf.resolve(d.get("DescendantFonts")) or []
                cf = pdf.resolve(kids[0]) if kids else None
                if not isinstance(cf, dict):
                    return
                self.dw = float(pdf.resolve(cf.get("DW")) or 1000)
                arr = pdf.resolve(cf.get("W")) or []
                i = 0
                while i < len(arr):
                    c = pdf.resolve(arr[i])
                    nxt = pdf.resolve(arr[i + 1]) if i + 1 < len(arr) else None
                    if isinstance(nxt, list):
                        for k, w in enumerate(nxt):
                            self.widths[int(c) + k] = float(pdf.resolve(w))
                        i += 2
                    elif i + 2 < len(arr):
                        w = float(pdf.resolve(arr[i + 2]))
                        for code in range(int(c), min(int(nxt), int(c) + 70000) + 1):
                            self.widths[code] = w
                        i += 3
                    else:
                        break
            else:
                first = int(pdf.resolve(d.get("FirstChar")) or 0)
                ws = pdf.resolve(d.get("Widths")) or []
                for k, w in enumerate(ws):
                    self.widths[first + k] = float(pdf.resolve(w))
                fd = pdf.resolve(d.get("FontDescriptor"))
                if isinstance(fd, dict) and fd.get("MissingWidth") is not None:
                    self.dw = float(pdf.resolve(fd.get("MissingWidth")))
        except Exception:  # noqa: BLE001 —— 字宽读不出来就用默认值
            return

    def advance(self, raw: bytes) -> tuple[float, int]:
        """这段字节排出来的宽度（千分之一 em 的单位）和字数。"""
        w = self.width if self.cmap else (2 if not self.table else 1)
        w = w if w in (1, 2, 3, 4) else 1
        total, n = 0.0, 0
        for i in range(0, len(raw) - w + 1, w):
            code = int.from_bytes(raw[i:i + w], "big")
            total += self.widths.get(code, self.dw)
            n += 1
        return total, n

    def decode(self, raw: bytes) -> str:
        if not self.usable:
            return ""
        if self.cmap:
            w = self.width if self.width in (1, 2, 3, 4) else 1
            out = []
            for i in range(0, len(raw) - w + 1, w):
                code = int.from_bytes(raw[i:i + w], "big")
                out.append(self.cmap.get(code, ""))
            return "".join(out)
        if self.table is not None:
            return "".join(self.table[b] for b in raw)
        # 复合字体、编码不是 Identity 又没有 ToUnicode：两字节按 UTF-16 碰碰运气
        try:
            return raw.decode("utf-16-be")
        except UnicodeDecodeError:
            return ""


# --------------------------------------------------------------------------
# 内容流 → 行
# --------------------------------------------------------------------------
class _Page:
    def __init__(self) -> None:
        self.lines: list[list[Any]] = []      # [y, 文字, 字号]
        self.last_y: float | None = None
        self.end_x = 0.0

    def add(self, y: float, text: str, size: float, x: float, adv: float) -> None:
        """adv = 这段文字排出来的宽度（页面单位）。同一行里两段之间的空隙够大，才补一个空格。"""
        if not text:
            return
        if self.lines and self.last_y is not None and abs(y - self.last_y) <= max(1.0, size * 0.35):
            cur = self.lines[-1]
            gap = x - self.end_x
            if (gap > size * 0.18 and cur[1] and cur[1][-1].isascii() and cur[1][-1] != " "
                    and text[0].isascii() and text[0] != " "):
                cur[1] += " "
            cur[1] += text
        else:
            self.lines.append([y, text, size])
        self.last_y = y
        self.end_x = x + adv


def _content_text(pdf: Pdf, data: bytes, resources: dict[str, Any], page: _Page, fonts: dict[str, Font], depth: int = 0) -> None:
    lex = Lexer(data)
    stack: list[Any] = []
    font: Font | None = None
    size = 10.0
    lx = ly = 0.0           # 当前行起点
    leading = 0.0
    xobjs = pdf.resolve(resources.get("XObject")) if isinstance(resources, dict) else None
    fdict = pdf.resolve(resources.get("Font")) if isinstance(resources, dict) else None
    cx = 0.0                # 当前横向位置
    tc = 0.0                # 字间距
    ta, td = 1.0, 1.0       # 文本矩阵的横 / 纵缩放（很多 PDF 用 Tf 240 + Tm 0.05 这种写法）
    ctm = [(1.0, 1.0)]      # q / Q / cm 的缩放栈

    def load_font(name: str) -> Font | None:
        if not isinstance(fdict, dict) or name not in fdict:
            return None
        ref = fdict[name]
        key = f"{id(fdict)}:{name}"
        if key not in fonts:
            d = pdf.resolve(ref)
            fonts[key] = Font(pdf, d) if isinstance(d, dict) else None  # type: ignore[assignment]
        return fonts[key]

    def show(raw: bytes) -> None:
        nonlocal cx
        if font is None:
            return
        units, count = font.advance(raw)
        kx, ky = ta * ctm[-1][0], td * ctm[-1][1]
        adv = (units / 1000.0 * size + count * tc) * kx
        page.add(ly, font.decode(raw), size * ky, cx, adv)
        cx += adv

    while True:
        tok = lex.token()
        if tok is None:
            break
        if isinstance(tok, (int, float, bytes, Name)) or tok in ("[", "<<"):
            stack.append(parse_obj(lex, tok))
            continue
        if tok in ("]", ">>", ")", ">", "{", "}"):
            continue
        op = tok
        try:
            if op == "BT":
                lx = ly = cx = 0.0
                ta = td = 1.0
            elif op == "q":
                ctm.append(ctm[-1])
            elif op == "Q":
                if len(ctm) > 1:
                    ctm.pop()
            elif op == "cm" and len(stack) >= 6:
                ctm[-1] = (ctm[-1][0] * abs(float(stack[-6])), ctm[-1][1] * abs(float(stack[-3])))
            elif op == "Tf" and len(stack) >= 2:
                font = load_font(str(stack[-2]))
                size = float(stack[-1]) or size
            elif op == "TL" and stack:
                leading = float(stack[-1])
            elif op in ("Td", "TD") and len(stack) >= 2:
                tx, ty = float(stack[-2]) * ta * ctm[-1][0], float(stack[-1]) * td * ctm[-1][1]
                lx += tx
                ly += ty
                cx = lx
                if op == "TD":
                    leading = -ty
            elif op == "Tm" and len(stack) >= 6:
                lx, ly = float(stack[-2]), float(stack[-1])
                ta, td = abs(float(stack[-6])) or 1.0, abs(float(stack[-3])) or 1.0
                cx = lx
            elif op == "Tc" and stack:
                tc = float(stack[-1])
            elif op == "T*":
                ly -= leading or size * td * ctm[-1][1] * 1.2
                cx = lx
            elif op == "Tj" and stack and isinstance(stack[-1], bytes):
                show(stack[-1])
            elif op in ("'", '"') and stack and isinstance(stack[-1], bytes):
                ly -= leading or size * td * ctm[-1][1] * 1.2
                cx = lx
                show(stack[-1])
            elif op == "TJ" and stack and isinstance(stack[-1], list):
                for el in stack[-1]:
                    if isinstance(el, bytes):
                        show(el)
                    elif isinstance(el, (int, float)):
                        cx -= el / 1000.0 * size * ta * ctm[-1][0]
            elif op == "Do" and stack and depth < 3 and isinstance(xobjs, dict):
                ref = xobjs.get(str(stack[-1]))
                if isinstance(ref, Ref):
                    d, raw = pdf._read_at(pdf.offsets[ref.num]) if ref.num in pdf.offsets else (None, None)
                    if isinstance(d, dict) and str(pdf.resolve(d.get("Subtype"))) == "Form":
                        sub_res = pdf.resolve(d.get("Resources")) or resources
                        _content_text(pdf, pdf.decode_stream(d, raw), sub_res, page, fonts, depth + 1)
            elif op == "BI":                       # 行内图片：跳到 EI
                end = re.compile(rb"\sEI(?=[\s]|$)").search(lex.buf, lex.pos)
                lex.pos = end.end() if end else len(lex.buf)
        except Exception:  # noqa: BLE001 —— 一个坏操作符不影响后面的字
            pass
        stack.clear()


# --------------------------------------------------------------------------
# 页面 → 段落
# --------------------------------------------------------------------------
_END_PUNCT = "。！？.!?”’\"」』）)…"
_PAGE_NO = re.compile(r"^\s*[-—–]?\s*(?:第\s*)?\d{1,4}\s*(?:页)?\s*[-—–]?\s*$")


def _join(a: str, b: str) -> str:
    if a.endswith("-") and len(a) > 1 and a[-2].isascii() and a[-2].isalpha() and b[:1].isascii() and b[:1].isalpha():
        return a[:-1] + b
    if a[-1:].isascii() and a[-1:] != " " and b[:1].isascii() and b[:1] != " ":
        return a + " " + b
    return a + b


def _page_paragraphs(lines: list[list[Any]]) -> list[str]:
    """一页的行 → 段落：行间距明显变大、或者上一行像一段的结尾又明显没写满，就另起一段。"""
    if not lines:
        return []
    texts = [re.sub(r"[ \t]+", " ", ln[1]).strip() for ln in lines]
    gaps = [abs(lines[i][0] - lines[i - 1][0]) for i in range(1, len(lines))]
    pos = sorted(g for g in gaps if g > 0.5)
    med = pos[len(pos) // 2] if pos else 0.0
    maxlen = max((len(t) for t in texts), default=0)
    paras: list[str] = []
    cur = ""
    for i, t in enumerate(texts):
        if not t:
            continue
        if not cur:
            cur = t
            continue
        gap = gaps[i - 1] if i - 1 < len(gaps) else 0
        brk = (med and gap > med * 1.45) or (cur[-1] in _END_PUNCT and len(texts[i - 1]) < maxlen * 0.78)
        if brk:
            paras.append(cur)
            cur = t
        else:
            cur = _join(cur, t)
    if cur:
        paras.append(cur)
    return paras


def _strip_running(pages: list[list[list[Any]]]) -> None:
    """去掉每页重复的页眉页脚和单独成行的页码。"""
    def norm(s: str) -> str:
        return re.sub(r"\d+", "#", s.strip())

    n = len(pages)
    counts: dict[str, int] = {}
    for lines in pages:
        keys = {norm(ln[1]) for ln in lines[:2] + lines[-2:] if ln[1].strip()}
        for k in keys:
            counts[k] = counts.get(k, 0) + 1
    common = {k for k, c in counts.items() if n >= 3 and c >= max(3, int(n * 0.4)) and len(k) <= 60}
    for lines in pages:
        keep = []
        for i, ln in enumerate(lines):
            edge = i < 2 or i >= len(lines) - 2
            t = ln[1].strip()
            if edge and (norm(t) in common or _PAGE_NO.match(t)):
                continue
            keep.append(ln)
        lines[:] = keep


def extract_text(data: bytes) -> dict[str, Any]:
    """返回 {"title", "author", "text"}。"""
    pdf = Pdf(data)
    pages = pdf.pages()
    if not pages:
        raise PdfError("没能读出这个 PDF 的页面")
    fonts: dict[str, Font] = {}
    page_lines: list[list[list[Any]]] = []
    unusable = False
    for pg in pages:
        res = pdf.resolve(pg.get("Resources")) or {}
        contents = pdf.resolve(pg.get("Contents"))
        refs = contents if isinstance(contents, list) else [pg.get("Contents")]
        blob = b""
        for r in refs:
            if isinstance(r, Ref):
                blob += pdf.stream_of(r.num) + b"\n"
        page = _Page()
        try:
            _content_text(pdf, blob, res, page, fonts)
        except Exception:  # noqa: BLE001
            pass
        page_lines.append(page.lines)
    unusable = any(f is not None and not f.usable for f in fonts.values())
    _strip_running(page_lines)
    paras: list[str] = []
    for lines in page_lines:
        ps = _page_paragraphs(lines)
        if not ps:
            continue
        if paras and paras[-1] and paras[-1][-1] not in _END_PUNCT and len(paras[-1]) > 12:
            paras[-1] = _join(paras[-1], ps[0])         # 一句话跨页：接上
            ps = ps[1:]
        paras.extend(ps)
    text = "\n".join(p for p in paras if p.strip())
    if len(re.sub(r"\s", "", text)) < 20:
        if unusable:
            raise NoTextError("这个 PDF 的字体没有文字映射，提取不出可读的文字")
        raise NoTextError("没能从这个 PDF 里读出文字（可能是扫描版 / 纯图片）")
    info = pdf.info()
    return {"title": info.get("Title", ""), "author": info.get("Author", ""), "text": text, "pages": len(pages)}


# --------------------------------------------------------------------------
# 图片版 PDF（扫描件）：每页取出那张图，一页一张
# --------------------------------------------------------------------------
import struct  # noqa: E402


def _png(width: int, height: int, color: int, bpc: int, rows: bytes, palette: bytes | None = None) -> bytes:
    """rows：已经带好每行滤镜字节的未压缩数据。"""
    def chunk(tag: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)

    out = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, bpc, color, 0, 0, 0))
    if palette is not None:
        out += chunk(b"PLTE", palette)
    return out + chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b"")


def _image_to_file(pdf: Pdf, d: dict[str, Any], raw: bytes) -> tuple[str, bytes]:
    filters = pdf.resolve(d.get("Filter"))
    filters = [str(pdf.resolve(f)) for f in (filters if isinstance(filters, list) else ([filters] if filters else []))]
    if filters and filters[-1] in ("DCTDecode", "DCT"):
        data = raw
        for f in filters[:-1]:
            if f not in ("FlateDecode", "Fl"):
                raise PdfError("这个 PDF 的图片用了暂不支持的压缩格式，没法显示")
            data = zlib.decompress(data)
        return "jpg", data
    if any(f not in ("FlateDecode", "Fl") for f in filters):
        name = next(f for f in filters if f not in ("FlateDecode", "Fl"))
        raise PdfError(f"这个 PDF 的图片用了暂不支持的压缩格式（{name}），没法显示")
    data = zlib.decompress(raw) if filters else raw
    w, h = int(pdf.resolve(d.get("Width")) or 0), int(pdf.resolve(d.get("Height")) or 0)
    bpc = int(pdf.resolve(d.get("BitsPerComponent")) or 8)
    if pdf.resolve(d.get("ImageMask")) is True:
        bpc, cs = 1, "DeviceGray"
    else:
        cs = pdf.resolve(d.get("ColorSpace"))
    palette = None
    if isinstance(cs, list) and cs:
        kind = str(pdf.resolve(cs[0]))
        if kind == "ICCBased":
            n = None
            ref = cs[1] if len(cs) > 1 else None
            if isinstance(ref, Ref) and ref.num in pdf.offsets:
                pd, _ = pdf._read_at(pdf.offsets[ref.num])
                n = pd.get("N") if isinstance(pd, dict) else None
            n = int(n or 3)
            cs = "DeviceRGB" if n == 3 else "DeviceGray" if n == 1 else "DeviceCMYK"
        elif kind in ("Indexed", "I"):
            lookup = pdf.resolve(cs[3]) if len(cs) > 3 else b""
            if len(cs) > 3 and isinstance(cs[3], Ref):
                lookup = pdf.stream_of(cs[3].num)
            palette = bytes(lookup) if isinstance(lookup, (bytes, bytearray)) else None
            cs = "Indexed"
        elif kind == "CalRGB":
            cs = "DeviceRGB"
        elif kind == "CalGray":
            cs = "DeviceGray"
        else:
            cs = kind
    cs = str(cs)
    if cs in ("DeviceGray", "G"):
        color, ncomp = 0, 1
    elif cs in ("DeviceRGB", "RGB"):
        color, ncomp = 2, 3
    elif cs == "Indexed" and palette is not None and bpc <= 8:
        color, ncomp = 3, 1
    else:
        raise PdfError(f"这个 PDF 的图片色彩模式（{cs}）暂不支持，没法显示")
    if not w or not h:
        raise PdfError("这个 PDF 的图片尺寸信息缺失，没法显示")
    stride = (w * ncomp * bpc + 7) // 8
    parms = pdf.resolve(d.get("DecodeParms") or d.get("DP"))
    if isinstance(parms, list):
        parms = pdf.resolve(parms[-1]) if parms else None
    pred = int(pdf.resolve(parms.get("Predictor")) or 1) if isinstance(parms, dict) else 1
    if pred >= 10:
        if len(data) < h * (stride + 1):
            raise PdfError("这个 PDF 的图片数据不完整")
        rows = data[: h * (stride + 1)]
    elif pred == 1:
        if len(data) < h * stride:
            raise PdfError("这个 PDF 的图片数据不完整")
        rows = b"".join(b"\x00" + data[i * stride:(i + 1) * stride] for i in range(h))
    else:
        raise PdfError("这个 PDF 的图片用了暂不支持的预测编码，没法显示")
    dec = pdf.resolve(d.get("Decode"))
    if color == 0 and pred == 1 and isinstance(dec, list) and dec and float(pdf.resolve(dec[0])) == 1.0:
        rows = b"".join(b"\x00" + bytes(x ^ 0xFF for x in rows[i * (stride + 1) + 1:(i + 1) * (stride + 1)]) for i in range(h))
    return "png", _png(w, h, color, bpc, rows, palette if color == 3 else None)


def extract_page_images(data: bytes, max_pages: int = 3000) -> dict[str, Any]:
    """每页取面积最大的那张图（扫描件一页就是一张图）。返回 {"title", "author", "pages": [(扩展名, 字节)]}。"""
    pdf = Pdf(data)
    pages = pdf.pages()[:max_pages]
    if not pages:
        raise PdfError("没能读出这个 PDF 的页面")
    out: list[tuple[str, bytes]] = []
    first_err: PdfError | None = None
    for pg in pages:
        res = pdf.resolve(pg.get("Resources")) or {}
        xobj = pdf.resolve(res.get("XObject")) if isinstance(res, dict) else None
        best = None
        for ref in (xobj.values() if isinstance(xobj, dict) else []):
            if not isinstance(ref, Ref) or ref.num not in pdf.offsets:
                continue
            try:
                d, raw = pdf._read_at(pdf.offsets[ref.num])
            except Exception:  # noqa: BLE001
                continue
            if not isinstance(d, dict) or str(pdf.resolve(d.get("Subtype"))) != "Image" or raw is None:
                continue
            area = int(pdf.resolve(d.get("Width")) or 0) * int(pdf.resolve(d.get("Height")) or 0)
            if best is None or area > best[0]:
                best = (area, d, raw)
        if best is None:
            continue
        try:
            out.append(_image_to_file(pdf, best[1], best[2]))
        except PdfError as exc:
            first_err = first_err or exc
        except zlib.error:
            first_err = first_err or PdfError("这个 PDF 的图片数据损坏，没法显示")
    if not out:
        raise first_err or PdfError("这个 PDF 里没有能显示的图片")
    info = pdf.info()
    return {"title": info.get("Title", ""), "author": info.get("Author", ""), "pages": out, "total": len(pages)}
