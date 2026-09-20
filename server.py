#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OneKit — 本地开发运维工具箱（启动入口）。

十个 Tab 一站式：端口占用 / 系统内存监控 / 系统服务 / 磁盘清理 / 代码搜索 /
JWT 解密 / JSON 格式化 / 时间戳转换 / Base64 编解码 / UTF-8 转义。

运行:  python server.py [端口] [--no-browser]   默认 8765
访问:  http://127.0.0.1:8765
打包:  pyinstaller OneKit.spec

后端实现按功能域拆分在 onekit/ 包中（见 onekit/__init__.py），本文件只负责启动服务。
"""
import sys
import webbrowser
from http.server import ThreadingHTTPServer

from onekit.config import DEFAULT_PORT
from onekit.http_app import Handler


def main():
    port = DEFAULT_PORT
    no_browser = False
    for a in sys.argv[1:]:
        if a == "--no-browser":
            no_browser = True
        elif a.isdigit():
            port = int(a)

    # 多线程：进程快照（PowerShell）较慢时不会阻塞端口查询等其它请求
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"OneKit 已启动： {url}")
    print("功能：端口占用 / 内存监控 / 系统服务 / 磁盘清理 / 代码搜索 / JWT 解密 / "
          "JSON 格式化 / 时间戳转换 / Base64 编解码 / UTF-8 转义（页面顶部 Tab 切换）")
    print("按 Ctrl+C 停止")

    if not no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
