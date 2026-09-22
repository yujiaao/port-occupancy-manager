# -*- coding: utf-8 -*-
"""HTTP 路由层。

只做三件事：解析请求 → 调用功能域模块 → 输出 JSON。
新增接口：写一个 `_get_xxx` / `_post_xxx` 方法，然后在 GET_ROUTES / POST_ROUTES 登记一行。
"""
import json
import mimetypes
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler

from . import certinfo, disks, memory, ports, search, services
from .config import INDEX_PATH, STATIC_DIR
from .winapi import is_admin


class Handler(BaseHTTPRequestHandler):
    # ------------------------------------------------------------------ 路由表
    GET_ROUTES = {
        "/api/ports": "_get_ports",
        "/api/stats": "_get_stats",
        "/api/services": "_get_services",
        "/api/disks": "_get_disks",
        "/api/search/history": "_get_search_history",
    }
    POST_ROUTES = {
        "/api/kill": "_post_kill",
        "/api/kill_port": "_post_kill_port",
        "/api/service": "_post_service",
        "/api/clean_scan": "_post_clean_scan",
        "/api/clean": "_post_clean",
        "/api/bigfiles": "_post_bigfiles",
        "/api/delete_paths": "_post_delete_paths",
        "/api/search": "_post_search",
        "/api/cert": "_post_cert",
    }

    # -------------------------------------------------------------- 基础输出
    def _send(self, code, payload, content_type="application/json; charset=utf-8"):
        data = payload if isinstance(payload, bytes) else payload.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def _serve_index(self):
        try:
            with open(INDEX_PATH, "r", encoding="utf-8") as f:
                html = f.read()
        except FileNotFoundError:
            html = "<h1>index.html 未找到，请将其放在 server.py 同目录</h1>"
        self._send(200, html, "text/html; charset=utf-8")

    def _serve_static(self, rel_path):
        """托管 static 目录下的资源（CSS / JS 模块）。禁止路径穿越。"""
        full = os.path.normpath(os.path.join(STATIC_DIR, rel_path))
        root = os.path.normpath(STATIC_DIR)
        if not full.startswith(root + os.sep) and full != root:
            self._json(403, {"error": "forbidden"})
            return
        if not os.path.isfile(full):
            self._json(404, {"error": "not found"})
            return
        ctype, _ = mimetypes.guess_type(full)
        ctype = ctype or "application/octet-stream"
        if ctype.startswith("text/") or ctype in (
                "application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        try:
            with open(full, "rb") as f:
                data = f.read()
        except OSError:
            self._json(404, {"error": "not found"})
            return
        self._send(200, data, ctype)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw)
        except Exception:
            return None

    # ------------------------------------------------------------ GET 分发
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path.startswith("/static/"):
            self._serve_static(path[len("/static/"):])
            return
        if path in ("/", "/index.html"):
            self._serve_index()
            return
        name = self.GET_ROUTES.get(path)
        if name is None:
            self._json(404, {"error": "not found"})
            return
        getattr(self, name)(parsed.query)

    def _get_ports(self, query):
        conns = ports.get_connections()
        self._json(200, {"ports": conns, "count": len(conns)})

    def _get_stats(self, query):
        self._json(200, memory.build_stats())

    def _get_services(self, query):
        force = urllib.parse.parse_qs(query).get("force", [""])[0] == "1"
        snap = services.services_snapshot(force=force)
        if snap is None:
            self._json(200, {
                "ok": False, "services": [], "admin": is_admin(),
                "error": "无法获取服务列表（PowerShell 被禁用或非 Windows）",
            })
        else:
            self._json(200, dict(ok=True, **snap))

    def _get_disks(self, query):
        snap = disks.disk_snapshot()
        if snap is None:
            self._json(200, {"ok": False, "disks": [],
                             "error": "无法获取磁盘信息（仅支持 Windows）"})
        else:
            self._json(200, dict(ok=True, **snap))

    def _get_search_history(self, query):
        self._json(200, {"ok": True, "history": search.load_history()})

    # ------------------------------------------------------------ POST 分发
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        name = self.POST_ROUTES.get(path)
        if name is None:
            self._json(404, {"error": "not found"})
            return
        data = self._read_json_body()
        if data is None:
            self._json(400, {"success": False, "error": "无效的请求体"})
            return
        getattr(self, name)(data)

    def _post_kill(self, data):
        try:
            result = ports.kill_pid(data.get("pid"))
        except Exception as e:
            result = {"success": False, "error": f"服务器异常：{e}"}
        self._json(200, result)

    def _post_kill_port(self, data):
        try:
            result = ports.kill_port(data.get("port"))
        except Exception as e:
            result = {"success": False, "error": f"服务器异常：{e}"}
        self._json(200, result)

    def _post_service(self, data):
        try:
            result = services.service_action(data.get("name"), data.get("action"),
                                             data.get("mode") or "")
        except Exception as e:
            result = {"success": False, "error": f"服务器异常：{e}"}
        self._json(200, result)

    def _post_clean_scan(self, data):
        try:
            result = disks.clean_scan()
        except Exception as e:
            result = {"items": [], "error": f"扫描失败：{e}"}
        self._json(200, result)

    def _post_clean(self, data):
        try:
            result = disks.clean_run(data.get("ids") or [])
        except Exception as e:
            result = {"success": False, "error": f"清理失败：{e}"}
        self._json(200, result)

    def _post_bigfiles(self, data):
        try:
            result = disks.scan_big_files(data.get("path") or "",
                                          data.get("min_mb") or 100,
                                          int(data.get("limit") or 50))
        except Exception as e:
            result = {"files": [], "error": f"扫描失败：{e}"}
        self._json(200, result)

    def _post_delete_paths(self, data):
        try:
            result = disks.delete_paths(data.get("paths") or [])
        except Exception as e:
            result = {"success": False, "error": f"删除失败：{e}"}
        self._json(200, result)

    def _post_cert(self, data):
        try:
            result = certinfo.inspect(data.get("target") or data.get("host") or "",
                                      data.get("port"), data.get("timeout") or 10)
        except Exception as e:
            result = {"ok": False, "error": f"检测失败：{e}"}
        self._json(200, result)

    def _post_search(self, data):
        """目录全文搜索：NDJSON 流式输出（进度 + 命中 + 结果），支持客户端中断。"""
        try:
            directory = (data.get("dir") or "").strip()
            query = (data.get("query") or "").strip()
            mode = data.get("mode") or "text"
            case = bool(data.get("case"))
            name_only = bool(data.get("nameOnly"))
            ext = data.get("ext") or ""
            recursive = data.get("recursive", True)
            try:
                max_files = int(data.get("maxFiles") or 500)
            except (TypeError, ValueError):
                max_files = 500
            try:
                time_limit = float(data.get("timeLimit") or 60.0)
            except (TypeError, ValueError):
                time_limit = 60.0
            scope_raw = data.get("scope") or ""
            scope = set(s.strip().lower() for s in str(scope_raw).split(",") if s.strip()) or None

            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            gen = search.code_search(
                directory, query, mode=mode, case=case, name_only=name_only,
                ext_filter=ext, recursive=recursive, max_files=max_files,
                time_limit=time_limit, scope=scope,
            )
            try:
                for ev in gen:
                    if ev.get("type") == "result" and ev.get("ok"):
                        ev["history"] = search.record_history(directory)
                    try:
                        self.wfile.write((json.dumps(ev, ensure_ascii=False) + "\n").encode("utf-8"))
                        self.wfile.flush()
                    except (BrokenPipeError, OSError):
                        break  # 客户端已断开（用户中断），跳出后关闭生成器
            finally:
                gen.close()  # 立即终止扫描，不再遍历剩余文件
        except Exception as e:
            try:
                self.wfile.write(
                    (json.dumps({"ok": False, "error": f"搜索失败：{e}"}) + "\n").encode("utf-8"))
                self.wfile.flush()
            except Exception:
                pass

    def log_message(self, *args):
        """静音默认的访问日志（控制台保持干净）。"""
