#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Port Inspector — 端口占用查看与进程终止工具（零依赖，仅用标准库）。

运行:  python server.py [端口] [--no-browser]   默认 8765
访问:  http://127.0.0.1:8765
打包:  pyinstaller --onefile --add-data "index.html;." server.py
"""
import csv
import io
import json
import os
import platform
import re
import signal
import subprocess
import sys
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

SELF_PID = os.getpid()
# Windows 系统关键进程（System / PID 4 等），禁止终止，避免把系统搞崩
PROTECTED_PIDS = {0, 4}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PORT = 8765
# taskkill 超时：某些安全软件会拦截并挂起该命令，必须设上限，避免 UI 永久卡死
KILL_TIMEOUT = 10


def resource_path(rel):
    """定位随附资源（index.html）。PyInstaller 打包后用 _MEIPASS 临时目录。"""
    base = getattr(sys, "_MEIPASS", BASE_DIR)
    return os.path.join(base, rel)


INDEX_PATH = resource_path("index.html")


def _win_proc_map():
    """返回 {pid: name} 映射，用于补全进程名。优先 tasklist，失败用 PowerShell 兜底。"""
    # 1) tasklist CSV
    try:
        out = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, encoding="gbk", errors="ignore", timeout=15,
        )
        mapping = {}
        for line in out.stdout.splitlines():
            row = next(csv.reader(io.StringIO(line)), [])
            if len(row) >= 2:
                try:
                    mapping[int(row[1])] = row[0]
                except ValueError:
                    pass
        if mapping:
            return mapping
    except Exception:
        pass
    # 2) PowerShell 兜底
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process | Select-Object ProcessId,Name | ConvertTo-Json -Compress"],
            capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=15,
        )
        data = json.loads(out.stdout)
        if isinstance(data, dict):
            data = [data]
        mapping = {}
        for p in data:
            try:
                mapping[int(p.get("ProcessId"))] = p.get("Name", "") or ""
            except (TypeError, ValueError):
                pass
        return mapping
    except Exception:
        return {}


def _pid_exists(pid):
    """进程是否存活。无法判断（命令被限制/无输出）时返回 None，由调用方兜底。"""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, encoding="gbk", errors="ignore", timeout=6,
        )
    except Exception:
        return None
    if not out.stdout.strip():
        return None
    return str(pid) in out.stdout


def _local_port(local):
    """从 '0.0.0.0:8080' / '[::]:8080' 中提取端口号（int），失败返回 None。"""
    if not local or ":" not in local:
        return None
    port_part = local.rsplit(":", 1)[1]
    # 去掉 IPv6 可能的括号残留
    port_part = port_part.strip("[]")
    try:
        return int(port_part)
    except ValueError:
        return None


def _windows_connections():
    try:
        out = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True, text=True, encoding="gbk", errors="ignore", timeout=20,
        )
    except Exception:
        return []
    procs = _win_proc_map()
    conns = []
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        proto = parts[0]
        if proto not in ("TCP", "UDP"):
            continue
        local = parts[1]
        state = parts[3] if proto == "TCP" else ""
        try:
            pid = int(parts[-1])
        except ValueError:
            continue
        conns.append({
            "protocol": proto,
            "local": local,
            "state": state,
            "pid": pid,
            "name": procs.get(pid, ""),
        })
    return conns


def _unix_connections():
    try:
        out = subprocess.run(
            ["lsof", "-i", "-P", "-n"],
            capture_output=True, text=True, errors="ignore", timeout=20,
        )
    except Exception:
        return []
    if out.returncode != 0:
        return []
    conns = []
    for line in out.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 9:
            continue
        try:
            pid = int(parts[1])
        except ValueError:
            continue
        name = " ".join(parts[8:])
        m = re.match(r"(TCP|UDP)\s+([^:]+):(\d+)(?:\s+\((\w+)\))?", name)
        if m:
            proto, addr, port, state = m.group(1), m.group(2), m.group(3), m.group(4) or ""
        else:
            m2 = re.search(r"([\d.]+|\*|\S+):(\d+)", name)
            if not m2:
                continue
            proto = "TCP" if "TCP" in name else "UDP"
            addr, port = m2.group(1), m2.group(2)
            state = ""
        conns.append({
            "protocol": proto,
            "local": f"{addr}:{port}",
            "state": state,
            "pid": pid,
            "name": parts[0],
        })
    return conns


def get_connections():
    if platform.system() == "Windows":
        return _windows_connections()
    return _unix_connections()


def kill_pid(pid):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return {"success": False, "error": "无效的 PID", "pid": pid}
    if pid <= 0 or pid in PROTECTED_PIDS or pid == SELF_PID:
        return {"success": False, "error": "受保护的进程，禁止终止", "pid": pid}

    if platform.system() == "Windows":
        exists = _pid_exists(pid)
        if exists is False:
            return {"success": False, "error": f"进程 {pid} 不存在"}
        try:
            res = subprocess.run(
                ["taskkill", "/PID", str(pid), "/F", "/T"],
                capture_output=True, text=True, encoding="gbk", errors="ignore", timeout=KILL_TIMEOUT,
            )
            ok = res.returncode == 0
            msg = (res.stdout or res.stderr).strip()
            return {"success": ok, "pid": pid, "message": msg or ("已终止" if ok else "taskkill 返回非零")}
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "终止命令超时（可能被安全软件拦截或进程无响应）", "pid": pid}
        except Exception as e:
            return {"success": False, "error": f"终止失败：{e}", "pid": pid}

    # Unix
    try:
        os.kill(pid, signal.SIGKILL)
        return {"success": True, "pid": pid, "message": "已发送 SIGKILL"}
    except ProcessLookupError:
        return {"success": False, "error": "进程不存在", "pid": pid}
    except PermissionError:
        return {"success": False, "error": "权限不足，请用管理员/root 权限运行", "pid": pid}


def find_pids_by_port(port):
    """返回占用指定本地端口的所有 PID 集合（去重）。"""
    try:
        port = int(port)
    except (TypeError, ValueError):
        return set()
    conns = get_connections()
    pids = set()
    for c in conns:
        if _local_port(c.get("local", "")) == port:
            pids.add(c["pid"])
    return pids


def kill_port(port):
    """终止占用指定端口的全部进程，返回汇总结果。"""
    try:
        port = int(port)
    except (TypeError, ValueError):
        return {"success": False, "error": "无效的端口号", "port": port, "pids": []}
    pids = find_pids_by_port(port)
    if not pids:
        return {"success": False, "error": f"端口 {port} 上未发现任何连接", "port": port, "pids": []}
    results = []
    for pid in sorted(pids):
        r = kill_pid(pid)
        r["pid"] = pid
        results.append(r)
    killed = [r["pid"] for r in results if r.get("success")]
    skipped = [r["pid"] for r in results if not r.get("success")]
    return {
        "success": len(killed) > 0,
        "port": port,
        "pids": sorted(pids),
        "results": results,
        "killed": killed,
        "skipped": skipped,
    }


class Handler(BaseHTTPRequestHandler):
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

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            try:
                with open(INDEX_PATH, "r", encoding="utf-8") as f:
                    html = f.read()
            except FileNotFoundError:
                html = "<h1>index.html 未找到，请将其放在 server.py 同目录</h1>"
            self._send(200, html, "text/html; charset=utf-8")
        elif path == "/api/ports":
            conns = get_connections()
            self._json(200, {"ports": conns, "count": len(conns)})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ("/api/kill", "/api/kill_port"):
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                data = json.loads(raw)
            except Exception:
                self._json(400, {"success": False, "error": "无效的请求体"})
                return
            try:
                if path == "/api/kill":
                    result = kill_pid(data.get("pid"))
                else:
                    result = kill_port(data.get("port"))
            except Exception as e:
                result = {"success": False, "error": f"服务器异常：{e}"}
            self._json(200, result)
        else:
            self._json(404, {"error": "not found"})

    def log_message(self, *args):
        pass


def main():
    port = DEFAULT_PORT
    no_browser = False
    for a in sys.argv[1:]:
        if a == "--no-browser":
            no_browser = True
        elif a.isdigit():
            port = int(a)
    server = HTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"端口占用管理器已启动： {url}")
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
