"""启动脚本。

用法::

    python run.py                 # 默认 http://127.0.0.1:8000
    python run.py --port 8123
    python run.py --reload        # 改代码自动重启（开发用）

`data/chat.db` 会在首次启动时自动创建，人设与全部聊天记录都存在里面。
"""

from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _safe_console() -> None:
    """Windows 控制台默认 GBK，遇到无法编码的字符会直接抛异常。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass


def load_env() -> None:
    """先加载 .env，再导入 app.config（它会在 import 时读取环境变量）。"""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_file)
    except ImportError:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            import os

            os.environ.setdefault(key.strip(), value.strip())


def free_port(host: str, port: int, tries: int = 12) -> int:
    for candidate in range(port, port + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, candidate))
                return candidate
            except OSError:
                continue
    return port


def write_state(host: str, port: int) -> None:
    """把实际监听的地址写给外部（快捷方式启动器靠它找到端口 / 关掉服务）。"""
    import json
    import os
    from datetime import datetime

    from app.config import DATA_DIR

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    state = {
        "url": f"http://{host}:{port}",
        "host": host,
        "port": port,
        "pid": os.getpid(),
        "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        (DATA_DIR / "server.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def clear_state() -> None:
    """退出时把状态文件删掉，免得启动器读到一个已经死掉的地址。"""
    try:
        from app.config import DATA_DIR

        state = DATA_DIR / "server.json"
        if state.exists():
            state.unlink()
    except OSError:
        pass


def main() -> int:
    _safe_console()
    parser = argparse.ArgumentParser(description="本地 AI 聊天")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="代码变更自动重启（开发用）")
    parser.add_argument("--no-port-scan", action="store_true", help="端口被占用时直接报错，不自动换端口")
    args = parser.parse_args()

    try:
        import uvicorn
    except ImportError:
        print("缺少依赖，请先执行：python -m pip install -r requirements.txt", file=sys.stderr)
        return 1

    load_env()

    port = args.port
    if not args.no_port_scan:
        port = free_port(args.host, args.port)
        if port != args.port:
            print(f"[提示] 端口 {args.port} 被占用，改用 {port}")

    url = f"http://{args.host}:{port}"
    write_state(args.host, port)
    print("=" * 56)
    print("  本地 AI 聊天已启动")
    print(f"  打开浏览器访问： {url}")
    print(f"  数据库： {ROOT / 'data' / 'chat.db'}")
    print("  第一次用：点右上角「设置」填入 API Key（不填则为离线演示模式）")
    print("=" * 56)

    # 用 uvicorn.Server 而不是 uvicorn.run()，这样网页上的「关闭服务」按钮
    # 能拿到 server 引用、优雅退出（reload 模式下拿不到，会退化成直接退出进程）
    from app.main import app as fastapi_app

    config = uvicorn.Config(
        "app.main:app" if args.reload else fastapi_app,
        host=args.host,
        port=port,
        reload=args.reload,
        log_level="info",
        # 浏览器会保持 keep-alive 连接，不设超时的话优雅退出会一直等下去
        timeout_graceful_shutdown=3,
    )
    server = uvicorn.Server(config)
    if not args.reload:
        fastapi_app.state.uvicorn_server = server

    try:
        server.run()
    finally:
        clear_state()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
