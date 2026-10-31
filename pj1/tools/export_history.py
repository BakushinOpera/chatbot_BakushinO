"""把聊天记录导出来看（每条都带时间戳）。

数据库在哪里、存了什么，用这个脚本一眼就能看到：

    python tools/export_history.py                     # 全部记录，打印到屏幕
    python tools/export_history.py -o 聊天记录.txt      # 写成文件
    python tools/export_history.py --format json       # 结构化 JSON
    python tools/export_history.py --format jsonl      # 每行一条 JSON
    python tools/export_history.py --session <id>      # 只看某一段会话
    python tools/export_history.py --db 别的.db        # 指定数据库

表结构：
    messages  当前会话窗口（短期上下文从这里取）
    archive   全部历史归档（搜索/回忆从这里取）
    memories  长期记忆条目
    settings  人设与配置
每条消息都同时记了两个时间：
    created_at  "2026-09-12 20:49:03"   人能读的本地时间
    ts          1789217343.21            Unix 时间戳（浮点秒），排序/计算用
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "chat.db"

ROLE_LABEL = {"user": "你", "assistant": "AI", "system": "系统"}


def load(db_path: Path) -> tuple[list[dict], list[dict], dict]:
    if not db_path.exists():
        raise SystemExit(f"找不到数据库：{db_path}\n（先启动一次应用，它会自动创建）")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM archive ORDER BY ts ASC, id ASC")]
    memories = [dict(r) for r in conn.execute("SELECT * FROM memories ORDER BY ts ASC")]
    settings = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}
    conn.close()
    return rows, memories, settings


def as_text(rows: list[dict], memories: list[dict], settings: dict, db_path: Path) -> str:
    persona = settings.get("persona_name") or "AI"
    out: list[str] = []
    out.append(f"# 聊天记录导出")
    out.append(f"# 数据库：{db_path}")
    if rows:
        out.append(f"# 共 {len(rows)} 条消息，{rows[0]['created_at']} ~ {rows[-1]['created_at']}")
    else:
        out.append("# 还没有任何聊天记录")
    out.append("")

    current_session = None
    for r in rows:
        if r["session_id"] != current_session:
            current_session = r["session_id"]
            out.append("")
            out.append(f"───── 会话 {current_session} ─────")
        who = ROLE_LABEL.get(r["role"], r["role"])
        if r["role"] == "assistant":
            who = persona
        # 时间戳：本地时间 + Unix 秒
        stamp = f"{r['created_at']}  (ts={int(r['ts'])})"
        if r.get("quote_text"):
            quoted = r["quote_text"].replace("\n", " ")[:60]
            out.append(f"[{stamp}] {who}（引用：{quoted}）")
        else:
            out.append(f"[{stamp}] {who}")
        for line in (r["content"] or "").splitlines() or [""]:
            out.append(f"    {line}")
        out.append("")

    if memories:
        out.append("───── 长期记忆 ─────")
        for m in memories:
            out.append(f"[{m['created_at']}] ({m['kind']}/{m['source']}) {m['content']}")
        out.append("")

    return "\n".join(out)


def as_json(rows: list[dict], memories: list[dict], settings: dict, db_path: Path) -> str:
    payload = {
        "database": str(db_path),
        "persona_name": settings.get("persona_name"),
        "messages": [
            {
                "id": r["id"],
                "session_id": r["session_id"],
                "role": r["role"],
                "content": r["content"],
                "quote_id": r["quote_id"],
                "quote_text": r["quote_text"],
                "created_at": r["created_at"],
                "ts": r["ts"],
            }
            for r in rows
        ],
        "memories": memories,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def as_jsonl(rows: list[dict], memories: list[dict], settings: dict, db_path: Path) -> str:
    lines = []
    for r in rows:
        lines.append(
            json.dumps(
                {
                    "id": r["id"],
                    "session_id": r["session_id"],
                    "role": r["role"],
                    "content": r["content"],
                    "quote_text": r["quote_text"],
                    "created_at": r["created_at"],
                    "ts": r["ts"],
                },
                ensure_ascii=False,
            )
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="导出聊天记录（含时间戳）")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="数据库路径")
    parser.add_argument("--format", choices=["txt", "json", "jsonl"], default="txt")
    parser.add_argument("--session", default=None, help="只导出指定会话 id")
    parser.add_argument("-o", "--out", default=None, help="输出到文件（默认打印到屏幕）")
    args = parser.parse_args()

    db_path = Path(args.db)
    rows, memories, settings = load(db_path)
    if args.session:
        rows = [r for r in rows if r["session_id"] == args.session]

    render = {"txt": as_text, "json": as_json, "jsonl": as_jsonl}[args.format]
    text = render(rows, memories, settings, db_path)

    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"已写出 {args.out}（{len(rows)} 条消息）")
    else:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
