# -*- coding: utf-8 -*-
"""磁盘 —— 分区容量 / 垃圾扫描清理 / 大文件扫描与删除。

清理类别与保护规则都集中在文件顶部，新增一类缓存只要往 CATALOGS 里加一行。
"""
import os
import time

from .config import IS_WINDOWS
from .winapi import DRIVE_TYPES, logical_drives, pct, volume_info


def clean_catalogs():
    """清理类别：(id, 显示名, 路径, 默认是否勾选)。全部为可安全删除的缓存/临时目录。"""
    env = os.environ
    local = env.get("LOCALAPPDATA", "")
    roaming = env.get("APPDATA", "")
    windir = env.get("windir", r"C:\Windows")
    temp = env.get("TEMP") or (os.path.join(local, "Temp") if local else "")
    return [
        ("user_temp", "用户临时文件", temp, True),
        ("win_temp", "Windows 临时文件", os.path.join(windir, "Temp"), True),
        ("thumbcache", "缩略图缓存", os.path.join(local, "Microsoft", "Windows", "Explorer"), True),
        ("crash_dumps", "崩溃转储文件", os.path.join(local, "CrashDumps"), True),
        ("wer", "Windows 错误报告", os.path.join(local, "Microsoft", "Windows", "WER"), True),
        ("npm_cache", "npm 缓存", os.path.join(roaming, "npm-cache"), True),
        ("pip_cache", "pip 缓存", os.path.join(local, "pip", "cache"), True),
        ("yarn_cache", "Yarn 缓存", os.path.join(local, "Yarn", "Cache"), True),
        ("go_build", "Go 构建缓存", os.path.join(local, "go-build"), True),
        ("prefetch", "Windows 预取文件", os.path.join(windir, "Prefetch"), False),
        ("wu_cache", "Windows Update 缓存", os.path.join(windir, "SoftwareDistribution", "Download"), False),
        ("recycle", "回收站", "", False),
    ]


def disk_snapshot():
    """各分区容量（GetDiskFreeSpaceExW / GetVolumeInformationW）。"""
    if not IS_WINDOWS:
        return None
    out = []
    for drive in logical_drives():
        ready, dtype, label, fs, total_v, free_v = volume_info(drive)
        # 未就绪的盘（空光驱、离线网络盘）查询会失败：仍列出，只是不显示容量
        used = max(0, total_v - free_v) if ready else 0
        if not ready:
            total_v = free_v = 0
        out.append({
            "drive": drive,
            "label": label,
            "fs": fs,
            "type": DRIVE_TYPES.get(dtype, "未知"),
            "total": total_v, "used": used, "free": free_v,
            "usedPct": pct(used, total_v),
            "ready": ready,
        })
    return {"disks": out} if out else None


def _recycle_dirs():
    dirs = []
    for d in logical_drives():
        p = os.path.join(d, "$RECYCLE.BIN")
        try:
            if os.path.isdir(p):
                dirs.append(p)
        except OSError:
            pass
    return dirs


def _catalog_paths(cid):
    """返回该类别对应的真实目录列表（回收站为各盘 $RECYCLE.BIN）。"""
    if cid == "recycle":
        return _recycle_dirs()
    for i, _n, path, _r in clean_catalogs():
        if i == cid:
            return [path] if path else []
    return []


def _walk_stats(path, deadline, max_items=40000):
    """统计目录总大小与文件数；无权限的文件跳过。"""
    total = count = 0
    for root, _dirs, files in os.walk(path, onerror=lambda e: None):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
            count += 1
            if count >= max_items or time.time() > deadline:
                return total, count
    return total, count


def clean_scan(time_budget=18.0):
    """扫描各清理类别占用；受总时间预算约束，避免卡住请求。"""
    deadline = time.time() + time_budget
    items = []
    for cid, name, path, rec in clean_catalogs():
        paths = [p for p in _catalog_paths(cid) if p and os.path.isdir(p)]
        size = files = 0
        for p in paths:
            s, c = _walk_stats(p, deadline)
            size += s
            files += c
        items.append({
            "id": cid, "name": name,
            "path": " ; ".join(paths) if paths else (path or "—"),
            "size": size, "files": files,
            "exists": bool(paths), "recommended": rec,
        })
        if time.time() > deadline:
            break
    return {"items": items, "truncated": time.time() > deadline}


def _purge_dir(path, deadline):
    """清空目录内容（文件 + 空子目录），保留目录本身。"""
    removed = freed = failed = 0
    for root, dirs, files in os.walk(path, topdown=False, onerror=lambda e: None):
        for f in files:
            if time.time() > deadline:
                return removed, freed, failed
            fp = os.path.join(root, f)
            try:
                size = os.path.getsize(fp)
                os.remove(fp)
                freed += size
                removed += 1
            except OSError:
                failed += 1
        for d in dirs:
            try:
                os.rmdir(os.path.join(root, d))   # 只删已空的目录
            except OSError:
                pass
    return removed, freed, failed


def clean_run(ids, time_budget=90.0):
    """清理指定类别；被占用/无权限的文件计入 failed。"""
    deadline = time.time() + time_budget
    details = []
    removed = freed = failed = 0
    for cid in ids:
        s_removed = s_freed = s_failed = 0
        for p in _catalog_paths(cid):
            if not p or not os.path.isdir(p) or time.time() > deadline:
                continue
            r, f, e = _purge_dir(p, deadline)
            s_removed += r
            s_freed += f
            s_failed += e
        name = next((n for i, n, _p, _r in clean_catalogs() if i == cid), cid)
        details.append({"id": cid, "name": name, "removed": s_removed,
                        "freed": s_freed, "failed": s_failed})
        removed += s_removed
        freed += s_freed
        failed += s_failed
    return {"success": removed > 0, "removed": removed, "freed": freed,
            "failed": failed, "details": details}


def scan_big_files(root, min_mb=100, limit=50, time_limit=25.0):
    """扫描大文件（只读），返回 Top N。"""
    if not root or not os.path.isdir(root):
        return {"files": [], "scanned": 0, "error": "路径不存在或不可访问"}
    min_bytes = max(1, int(min_mb)) * 1024 * 1024
    found = []
    scanned = 0
    end = time.time() + time_limit
    for cur, _dirs, files in os.walk(root, onerror=lambda e: None):
        for f in files:
            scanned += 1
            if scanned % 2000 == 0 and time.time() > end:
                found.sort(key=lambda x: -x["size"])
                return {"files": found[:limit], "scanned": scanned, "truncated": True}
            p = os.path.join(cur, f)
            try:
                st = os.stat(p)
            except OSError:
                continue
            if st.st_size >= min_bytes:
                found.append({"path": p, "size": st.st_size, "mtime": int(st.st_mtime)})
    found.sort(key=lambda x: -x["size"])
    return {"files": found[:limit], "scanned": scanned, "truncated": False}


def is_protected_path(path):
    """系统目录（Windows / Program Files）下的文件禁止删除。"""
    low = os.path.abspath(path).lower()
    guards = [
        os.environ.get("windir", r"C:\Windows").lower(),
        os.environ.get("ProgramFiles", r"C:\Program Files").lower(),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)").lower(),
    ]
    return any(g and (low == g or low.startswith(g + os.sep)) for g in guards)


def delete_paths(paths):
    """删除指定文件（大文件清理）；受保护目录拒绝删除。"""
    removed = freed = failed = 0
    details = []
    for p in paths or []:
        try:
            if not os.path.isfile(p):
                failed += 1
                details.append({"path": p, "ok": False, "error": "不是文件或不存在"})
                continue
            if is_protected_path(p):
                failed += 1
                details.append({"path": p, "ok": False, "error": "受保护的系统目录，拒绝删除"})
                continue
            size = os.path.getsize(p)
            os.remove(p)
            freed += size
            removed += 1
            details.append({"path": p, "ok": True, "size": size})
        except Exception as e:
            failed += 1
            details.append({"path": p, "ok": False, "error": str(e)})
    return {"success": removed > 0, "removed": removed, "freed": freed,
            "failed": failed, "details": details}
