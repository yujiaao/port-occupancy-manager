#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OneKit — 本地开发运维工具箱（启动入口）。

十个 Tab 一站式：端口占用 / 系统内存监控 / 系统服务 / 磁盘清理 / 代码搜索 /
JWT 解密 / JSON 格式化 / 时间戳转换 / Base64 编解码 / UTF-8 转义。

运行:  python server.py [端口] [--no-browser]   默认 8765
访问:  http://127.0.0.1:8765
打包:  pyinstaller OneKit.spec          （Windows）
      pyinstaller OneKit-macos.spec   （macOS，产物 dist/OneKit.app）

后端实现按功能域拆分在 onekit/ 包中（见 onekit/__init__.py），本文件只负责启动服务。
"""
import signal
import subprocess
import sys
import threading
import webbrowser
from http.server import ThreadingHTTPServer

from onekit.config import DEFAULT_PORT
from onekit.http_app import Handler


def _alert(title, message):
    """窗口程序没有终端时，用系统对话框把启动失败告诉用户。"""
    if sys.platform != "darwin":
        return
    safe = message.replace("\\", "\\\\").replace('"', '\\"')
    subprocess.run(
        ["osascript", "-e", f'display alert "{title}" message "{safe}"'],
        check=False,
    )


def main():
    port = DEFAULT_PORT
    no_browser = False
    for a in sys.argv[1:]:
        if a == "--no-browser":
            no_browser = True
        elif a.isdigit():
            port = int(a)

    # 多线程：进程快照较慢时不会阻塞端口查询等其它请求
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as e:
        msg = f"无法监听 127.0.0.1:{port}（{e}）。请换一个端口，或关闭已占用该端口的程序。"
        print(msg, file=sys.stderr)
        _alert("OneKit 启动失败", msg)
        return 1
    url = f"http://127.0.0.1:{port}"
    print(f"OneKit 已启动： {url}")
    print("功能：端口占用 / 内存监控 / 系统服务 / 磁盘清理 / 代码搜索 / JWT 解密 / "
          "JSON 格式化 / 时间戳转换 / Base64 编解码 / UTF-8 转义（页面顶部 Tab 切换）")
    print("按 Ctrl+C 停止；在 macOS 上也可从程序坞退出")

    def _stop(signum, _frame):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    if not no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    print("\n已停止")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
