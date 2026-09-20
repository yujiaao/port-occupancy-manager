# -*- coding: utf-8 -*-
"""端口占用查询与进程终止。

数据来源：Windows 用 netstat -ano + tasklist 补进程名；Unix 走 lsof。
终止进程：Windows 用 taskkill /F /T（连子进程树），Unix 用 SIGKILL。
"""
import csv
import io
import json
import os
import platform
import re
import signal

from .config import KILL_TIMEOUT, PROTECTED_PIDS, SELF_PID
from .shell import run

PS_GBK = {"encoding": "gbk", "errors": "ignore"}


def proc_name_map():
    """返回 {pid: name} 映射，用于补全进程名。优先 tasklist，失败用 PowerShell 兜底。"""
    out = run(["tasklist", "/FO", "CSV", "/NH"], timeout=15, **PS_GBK)
    if out is not None:
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
    # PowerShell 兜底
    out = run(["powershell", "-NoProfile", "-Command",
               "Get-CimInstance Win32_Process | Select-Object ProcessId,Name | ConvertTo-Json -Compress"],
              timeout=15, encoding="utf-8", errors="ignore")
    if out is None:
        return {}
    try:
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


def pid_exists(pid):
    """进程是否存活。无法判断（命令被限制/无输出）时返回 None，由调用方兜底。"""
    out = run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], timeout=6, **PS_GBK)
    if out is None or not out.stdout.strip():
        return None
    return str(pid) in out.stdout


def local_port(local):
    """从 '0.0.0.0:8080' / '[::]:8080' 中提取端口号（int），失败返回 None。"""
    if not local or ":" not in local:
        return None
    # 去掉 IPv6 可能的括号残留
    port_part = local.rsplit(":", 1)[1].strip("[]")
    try:
        return int(port_part)
    except ValueError:
        return None


def _windows_connections():
    out = run(["netstat", "-ano"], timeout=20, **PS_GBK)
    if out is None:
        return []
    procs = proc_name_map()
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
    out = run(["lsof", "-i", "-P", "-n"], timeout=20)
    if out is None or out.returncode != 0:
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
    """本机全部端口占用连接。"""
    if platform.system() == "Windows":
        return _windows_connections()
    return _unix_connections()


def kill_pid(pid):
    """终止单个进程（含子进程树）；受保护进程直接拒绝。"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return {"success": False, "error": "无效的 PID", "pid": pid}
    if pid <= 0 or pid in PROTECTED_PIDS or pid == SELF_PID:
        return {"success": False, "error": "受保护的进程，禁止终止", "pid": pid}

    if platform.system() == "Windows":
        exists = pid_exists(pid)
        if exists is False:
            return {"success": False, "error": f"进程 {pid} 不存在"}
        res = run(["taskkill", "/PID", str(pid), "/F", "/T"],
                  timeout=KILL_TIMEOUT, **PS_GBK)
        if res is None:
            return {"success": False,
                    "error": "终止命令超时（可能被安全软件拦截或进程无响应）", "pid": pid}
        ok = res.returncode == 0
        msg = (res.stdout or res.stderr).strip()
        return {"success": ok, "pid": pid,
                "message": msg or ("已终止" if ok else "taskkill 返回非零")}

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
    pids = set()
    for c in get_connections():
        if local_port(c.get("local", "")) == port:
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
        return {"success": False, "error": f"端口 {port} 上未发现任何连接",
                "port": port, "pids": []}
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
