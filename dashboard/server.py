"""只读监控面板 server — stdlib http.server，零依赖，与 agent 进程解耦。

运行：  python -m dashboard.server  [--host 127.0.0.1] [--port 8765]
浏览器：http://127.0.0.1:8765
agent 随便重启，本进程读盘上的 artifact，互不牵连（无共享生命周期）。
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dashboard.collectors import (
    build_evolution,
    build_memory,
    build_overview,
    build_safety,
)

_HTML_PATH = Path(__file__).resolve().parent / "index.html"

# 四视图四接口：每个接口数据源见 collectors 各 build_* docstring。
_ROUTES = {
    "/api/overview": build_overview,
    "/api/safety": build_safety,
    "/api/memory": build_memory,
    "/api/evolution": build_evolution,
}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 — http.server 接口约定
        for prefix, builder in _ROUTES.items():
            if self.path.startswith(prefix):
                body = json.dumps(builder(), ensure_ascii=False).encode("utf-8")
                self._send(200, body, "application/json; charset=utf-8")
                return
        if self.path in ("/", "/index.html"):
            # 每次读盘：改 index.html 后刷新即生效，无需重启 server
            body = _HTML_PATH.read_text(encoding="utf-8").encode("utf-8")
            self._send(200, body, "text/html; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    def log_message(self, *args) -> None:  # noqa: ARG002 — 静默访问日志
        pass


def main() -> None:
    ap = argparse.ArgumentParser(description="CTGents 只读监控面板")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"监控面板: http://{args.host}:{args.port}  (Ctrl+C 退出)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出。")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
