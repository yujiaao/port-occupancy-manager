# -*- coding: utf-8 -*-
"""端口占用查询与进程终止 —— 通过平台抽象层实现。"""
from .platform import backend


def local_port(local):
    """从 '0.0.0.0:8080' / '[::]:8080' 中提取端口号（int），失败返回 None。"""
    if not local or ":" not in local:
        return None
    port_part = local.rsplit(":", 1)[1].strip("[]")
    try:
        return int(port_part)
    except ValueError:
        return None


def get_connections():
    """本机全部端口占用连接。"""
    return backend.get_connections()


def proc_name_map():
    """{pid: name} 映射。"""
    return backend.proc_name_map()


def kill_pid(pid):
    """终止单个进程（含子进程树）；受保护进程直接拒绝。"""
    return backend.kill_pid(pid)


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
