"""世界设定：Phal 所在世界的常识……放在 ``data/world/`` 下，每一类一个文件夹，**分开存**。

目录结构（你往里填；每一类就是一个子文件夹，想加第三类、第四类，直接新建文件夹就行）::

    data/world/
      世界常识/
        _总纲.md           ← 总纲：常驻上下文（名字以 _ 开头，或文件名里带「总纲」）
        某国.md             ← 一个国家 / 地点 / 概念一个文件，只有消息里出现关键词才会被带进上下文
        某城.md
      某一类/
        _总纲.md
        _提示.txt           ← 这一类的口吻提示（可选），带条目时会先放在条目前面

条目怎么被找到：
* 文件名（不含扩展名）就是关键词；
* 文件里任意位置（前几行）写一行 ``关键词：某国、首都、货币名``，这些词也算；
* 一个文件里可以用 ``## 条目名`` 分成多个条目，每个条目各自按标题 / 它的「关键词：」行匹配。
你的消息里出现了关键词，才把对应条目（最多 3 条、每条有字数上限）加进上下文。**不会整份文档塞进去。**
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import character as C
from .config import DATA_DIR

WORLD_DIR = DATA_DIR / "world"
PLACES_FILE = WORLD_DIR / "_places.json"     # 地点库（places.py 通过下面的 read_places / write_places 读写）
DEFAULT_CATEGORIES = ["世界常识"]
EXTS = (".md", ".txt")

OVERVIEW_CAP = 1800          # 每一类总纲最多常驻多少字（默认值；设置页 world_overview_cap）
ENTRY_CAP = 1000             # 每个条目最多带多少字（world_entry_cap）
MAX_ENTRIES = 3              # 一次最多带几个条目（world_max_entries）

README = """# 世界设定目录

每一类是一个子文件夹（想加新的一类，直接新建文件夹）。里面放 .md / .txt 文件：

- 名字以 `_` 开头、或文件名里带「总纲」的 = 这一类的**总纲**，常驻上下文（别写太长，超过 1800 字会被截断）。
- `_提示.txt`（可选）= 这一类的口吻提示，带这一类的条目时会放在前面。
- 其它文件 = **条目**：文件名就是关键词；还可以在文件里写一行 `关键词：甲、乙、丙` 补充关键词。
  一个文件里也可以用 `## 条目名` 分成多个条目。
- 只有你的消息里出现了关键词，对应条目才会被带进上下文（最多 3 条，每条 1000 字以内）。

改完保存即可，下一条消息就生效，不用重启。
"""

HINTS: dict[str, str] = {}      # 某一类的默认口吻提示（也可以在那一类的 _提示.txt 里写）

_KW_LINE = re.compile(r"^\s*(?:关键词|关键字|keywords?)\s*[:：]\s*(.+?)\s*$", re.I)
_SPLIT = re.compile(r"[,，、;；/\s]+")


def ensure_dirs() -> None:
    WORLD_DIR.mkdir(parents=True, exist_ok=True)
    readme = WORLD_DIR / "README.md"
    if not readme.exists():
        readme.write_text(README, encoding="utf-8")
    for cat in DEFAULT_CATEGORIES:
        d = WORLD_DIR / cat
        d.mkdir(exist_ok=True)
        hint = HINTS.get(cat)
        if hint and not (d / "_提示.txt").exists():
            (d / "_提示.txt").write_text(hint, encoding="utf-8")


def _read(path: Path) -> str:
    from .books import decode_bytes

    try:
        return decode_bytes(path.read_bytes()).replace("\r\n", "\n").strip()
    except OSError:
        return ""


def _is_overview(name: str) -> bool:
    stem = Path(name).stem
    return (stem.startswith("_") and "提示" not in stem) or "总纲" in stem


def _keywords(title: str, body: str) -> list[str]:
    kws = {title.strip()}
    kws.update(w for w in _SPLIT.split(title) if len(w) >= 2)
    for ln in body.split("\n")[:6]:
        m = _KW_LINE.match(ln)
        if m:
            kws.update(w for w in _SPLIT.split(m.group(1)) if w)
    return sorted((k for k in kws if len(k) >= 2), key=len, reverse=True)


def _entries_of(stem: str, text: str) -> list[tuple[str, str]]:
    parts = re.split(r"(?m)^##\s+(.+?)\s*$", text)
    if len(parts) >= 3:                       # 有 ## 小节：每节一个条目；小节之前的正文（比如后面只跟了一个「## 她不知道」）算一条，标题用文件名
        out = [(stem, parts[0].strip())] if parts[0].strip() else []
        return out + [(parts[i].strip(), parts[i + 1].strip()) for i in range(1, len(parts) - 1, 2) if parts[i + 1].strip()]
    return [(stem, text)] if text else []


def load() -> dict[str, dict[str, Any]]:
    """扫一遍目录（文件不多，每条消息扫一次很便宜；改了文件下一条消息就生效）。"""
    ensure_dirs()
    out: dict[str, dict[str, Any]] = {}
    for cat_dir in sorted(p for p in WORLD_DIR.iterdir() if p.is_dir() and not p.name.startswith(".")):
        cat = {"overview": "", "hint": HINTS.get(cat_dir.name, ""), "entries": []}
        overview_parts: list[str] = []
        for f in sorted(cat_dir.iterdir()):
            if not f.is_file() or f.suffix.lower() not in EXTS or f.name.lower().startswith("readme"):
                continue
            text = _read(f)
            if not text:
                continue
            if f.stem == "_提示":
                cat["hint"] = text
            elif _is_overview(f.name):
                overview_parts.append(text)
            else:
                for title, body in _entries_of(f.stem, text):
                    cat["entries"].append({"title": title, "body": body, "keywords": _keywords(title, body), "file": f.name})
        cat["overview"] = "\n\n".join(overview_parts)
        out[cat_dir.name] = cat
    return out


def overview_block() -> str:
    """各类的总纲，常驻上下文。没有写就不占 token。"""
    parts = []
    for name, cat in load().items():
        text = cat["overview"].strip()
        if text:
            parts.append(f"【世界设定·{name}·总纲】\n" + text[:_cap("world_overview_cap", OVERVIEW_CAP)])
    return "\n\n".join(parts)


def match(text: str, limit: int | None = None) -> list[dict[str, Any]]:
    """消息里出现了哪些条目的关键词。按命中的关键词总长度排序。"""
    text = text or ""
    limit = limit if limit is not None else _cap("world_max_entries", MAX_ENTRIES)
    entry_cap = _cap("world_entry_cap", ENTRY_CAP)
    scored = []
    for name, cat in load().items():
        for e in cat["entries"]:
            if e["title"].startswith("她不知道"):          # 「## 她不知道」小节：只给作者层用（地图 / 校对），不进她的上下文
                continue
            hit = [k for k in e["keywords"] if k in text]
            if hit:
                scored.append((sum(len(k) for k in set(hit)), name, cat, e, hit))
    scored.sort(key=lambda x: -x[0])
    return [{"category": n, "title": e["title"], "body": e["body"][:entry_cap], "hint": c["hint"], "matched": h}
            for _, n, c, e, h in scored[:limit]]


def entries_block(text: str) -> str:
    """消息里提到了某个国家 / 地点 / 概念：把对应条目带进这一轮的上下文。"""
    hits = match(text)
    if not hits:
        return ""
    lines = ["【相关的世界设定】你知道的就这些（是你这个身份该有的了解程度，不是百科全书），"
             "没写到的细节就含糊过去，别编成很确定的事："]
    seen_hint: set[str] = set()
    for h in hits:
        if h["hint"] and h["category"] not in seen_hint:
            lines.append(h["hint"])
            seen_hint.add(h["category"])
        lines.append(f"《{h['category']}·{h['title']}》\n{h['body']}")
    return "\n\n".join(lines)


def _cap(key: str, default: int) -> int:
    from . import registry

    try:
        return int(registry.num(key))
    except (KeyError, TypeError, ValueError):
        return default


def entry_files() -> list[dict[str, str]]:
    """各分类下的条目文件（不含总纲、_提示、README）：[{category, title（文件名）, text}]。小本子初始抄写用。"""
    ensure_dirs()
    out = []
    for cat_dir in sorted(p for p in WORLD_DIR.iterdir() if p.is_dir() and not p.name.startswith(".")):
        for f in sorted(cat_dir.iterdir()):
            if not f.is_file() or f.suffix.lower() not in EXTS or f.name.lower().startswith("readme") or f.stem == "_提示" or _is_overview(f.name):
                continue
            out.append({"category": cat_dir.name, "title": f.stem, "text": _read(f)})
    return out


def category_titles(category: str) -> list[str]:
    """某一类下条目文件的文件名（不含总纲）。地点库用「世界常识」的文件名猜国家。"""
    return [e["title"] for e in entry_files() if e["category"] == category]


def read_places() -> Any:
    """地点库的原始数据（没有文件返回 None）。"""
    if not PLACES_FILE.exists():
        return None
    return json.loads(PLACES_FILE.read_text(encoding="utf-8"))


def write_places(data: Any) -> None:
    PLACES_FILE.parent.mkdir(parents=True, exist_ok=True)
    PLACES_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def summary() -> dict[str, Any]:
    data = load()
    return {
        "dir": str(WORLD_DIR),
        "categories": {
            name: {
                "has_overview": bool(c["overview"]),
                "entries": [{"title": e["title"], "file": e["file"], "keywords": e["keywords"]} for e in c["entries"]],
            }
            for name, c in data.items()
        },
    }


# --------------------------------------------------------------------------
# 文件读写（设置页的「世界」面板用）。路径一律限制在 data/world/ 里面。
# --------------------------------------------------------------------------
_BAD_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _safe(name: str, what: str) -> str:
    name = (name or "").strip()
    if not name or name in (".", "..") or _BAD_NAME.search(name):
        raise ValueError(f"{what}不能为空，也不能包含 \\ / : * ? \" < > |")
    return name


def _file_path(category: str, name: str) -> Path:
    cat, nm = _safe(category, "分类名"), _safe(name, "文件名")
    if not nm.lower().endswith(EXTS):
        nm += ".md"
    path = (WORLD_DIR / cat / nm).resolve()
    if WORLD_DIR.resolve() not in path.parents:
        raise ValueError("路径不合法")
    return path


def list_tree() -> list[dict[str, Any]]:
    """每一类下有哪些文件（总纲排前面）。"""
    ensure_dirs()
    out = []
    for d in sorted(p for p in WORLD_DIR.iterdir() if p.is_dir()):
        files = [f for f in d.iterdir() if f.is_file() and f.suffix.lower() in EXTS]
        files.sort(key=lambda f: (not _is_overview(f.stem), f.name))
        out.append({"name": d.name, "files": [{"name": f.name, "chars": len(_read(f)), "overview": _is_overview(f.stem)} for f in files]})
    return out


def read_file(category: str, name: str) -> str:
    return _read(_file_path(category, name))


def write_file(category: str, name: str, content: str) -> None:
    path = _file_path(category, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text((content or "").replace("\r\n", "\n"), encoding="utf-8")


def delete_file(category: str, name: str) -> None:
    path = _file_path(category, name)
    if path.exists():
        path.unlink()


def create_category(name: str) -> None:
    (WORLD_DIR / _safe(name, "分类名")).mkdir(parents=True, exist_ok=True)
