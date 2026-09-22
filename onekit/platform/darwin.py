# -*- coding: utf-8 -*-
"""macOS / Linux 平台后端：用标准命令替代 Windows API。

覆盖范围：
- 内存：vm_stat + sysctl（macOS）/ /proc/meminfo（Linux）
- 端口：lsof -i -P -n
- 进程终止：SIGKILL
- 服务：launchctl（macOS）/ systemctl（Linux）
- 磁盘：os.statvfs + /Volumes（macOS）或 df（Linux）
- 清理类别：~/Library/Caches、/tmp 等
"""
import os
import platform
import re
import signal
import subprocess
import sys
import threading
import time

from .base import PlatformBackend
from ..config import PROTECTED_PIDS, SELF_PID


def _run(args, timeout=20, encoding=None, errors="replace"):
    try:
        return subprocess.run(
            args, capture_output=True, text=True,
            encoding=encoding, errors=errors, timeout=timeout,
        )
    except Exception:
        return None


_IS_MACOS = sys.platform == "darwin"


class DarwinBackend(PlatformBackend):
    """macOS / Linux 平台实现。"""

    # ── 系统信息 ──────────────────────────────────────────────

    def memory_snapshot(self):
        if _IS_MACOS:
            return self._macos_memory()
        return self._linux_memory()

    def _macos_memory(self):
        """macOS: vm_stat + sysctl hw.memsize。"""
        page_size = 4096
        out = _run(["sysctl", "-n", "hw.memsize"], timeout=5)
        if out is None or not out.stdout.strip():
            return None
        try:
            total_phys = int(out.stdout.strip())
        except ValueError:
            return None

        out = _run(["vm_stat"], timeout=5)
        if out is None:
            return None
        stats = {}
        for line in out.stdout.splitlines():
            m = re.match(r"^(.+?):\s+([\d]+)", line)
            if m:
                stats[m.group(1).strip()] = int(m.group(2))

        free = stats.get("Pages free", 0) * page_size
        inactive = stats.get("Pages inactive", 0) * page_size
        avail_phys = free + inactive
        used_phys = max(0, total_phys - avail_phys)

        # macOS 没有 commit limit 概念，用 swap 代替
        swap_out = _run(["sysctl", "-n", "vm.swapusage"], timeout=5)
        swap_total = swap_avail = 0
        if swap_out and swap_out.stdout:
            m = re.search(r"total\s*=\s*([\d.]+)\s*(\w+)", swap_out.stdout)
            if m:
                val, unit = float(m.group(1)), m.group(2).lower()
                mult = {"g": 1 << 30, "m": 1 << 20, "k": 1 << 10}.get(unit[0], 1)
                swap_total = int(val * mult)
            m2 = re.search(r"avail\s*=\s*([\d.]+)\s*(\w+)", swap_out.stdout)
            if m2:
                val, unit = float(m2.group(1)), m2.group(2).lower()
                mult = {"g": 1 << 30, "m": 1 << 20, "k": 1 << 10}.get(unit[0], 1)
                swap_avail = int(val * mult)

        def pct(p, t):
            return round(p * 100.0 / t, 1) if t > 0 else 0.0

        return {
            "memoryLoad": round(used_phys * 100 / total_phys, 1) if total_phys else 0,
            "physical": {
                "total": total_phys, "avail": avail_phys,
                "used": used_phys, "usedPct": pct(used_phys, total_phys),
            },
            "commit": {
                "total": swap_total, "avail": swap_avail,
                "used": max(0, swap_total - swap_avail),
                "usedPct": pct(swap_total - swap_avail, swap_total),
            },
            "virtual": {
                "total": total_phys, "avail": avail_phys,
                "used": used_phys, "usedPct": pct(used_phys, total_phys),
            },
        }

    def _linux_memory(self):
        """Linux: /proc/meminfo。"""
        try:
            with open("/proc/meminfo", "r") as f:
                lines = f.readlines()
        except OSError:
            return None
        info = {}
        for line in lines:
            parts = line.split()
            if len(parts) >= 2:
                key = parts[0].rstrip(":")
                val = int(parts[1]) * 1024  # kB → bytes
                info[key] = val

        total = info.get("MemTotal", 0)
        avail = info.get("MemAvailable", info.get("MemFree", 0))
        used = max(0, total - avail)
        swap_total = info.get("SwapTotal", 0)
        swap_free = info.get("SwapFree", 0)

        def pct(p, t):
            return round(p * 100.0 / t, 1) if t > 0 else 0.0

        return {
            "memoryLoad": pct(used, total),
            "physical": {
                "total": total, "avail": avail,
                "used": used, "usedPct": pct(used, total),
            },
            "commit": {
                "total": swap_total, "avail": swap_free,
                "used": max(0, swap_total - swap_free),
                "usedPct": pct(swap_total - swap_free, swap_total),
            },
            "virtual": {
                "total": total, "avail": avail,
                "used": used, "usedPct": pct(used, total),
            },
        }

    def uptime_seconds(self):
        if _IS_MACOS:
            out = _run(["sysctl", "-n", "kern.boottime"], timeout=5)
            if out and out.stdout:
                m = re.search(r"sec\s*=\s*(\d+)", out.stdout)
                if m:
                    return int(time.time()) - int(m.group(1))
        else:
            try:
                with open("/proc/uptime", "r") as f:
                    return int(float(f.read().split()[0]))
            except Exception:
                pass
        return None

    def is_admin(self):
        return os.getuid() == 0

    # ── 端口与进程 ────────────────────────────────────────────

    def proc_name_map(self):
        out = _run(["ps", "-eo", "pid,comm"], timeout=10)
        if out is None:
            return {}
        mapping = {}
        for line in out.stdout.splitlines()[1:]:
            parts = line.strip().split(None, 1)
            if len(parts) == 2:
                try:
                    mapping[int(parts[0])] = parts[1]
                except ValueError:
                    pass
        return mapping

    def get_connections(self):
        out = _run(["lsof", "-i", "-P", "-n"], timeout=20)
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
            name_field = " ".join(parts[8:])
            m = re.match(r"(TCP|UDP)\s+([^:]+):(\d+)(?:\s+\((\w+)\))?", name_field)
            if m:
                proto, addr, port, state = m.group(1), m.group(2), m.group(3), m.group(4) or ""
            else:
                m2 = re.search(r"([\d.]+|\*|\S+):(\d+)", name_field)
                if not m2:
                    continue
                proto = "TCP" if "TCP" in name_field else "UDP"
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

    def kill_pid(self, pid):
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            return {"success": False, "error": "无效的 PID", "pid": pid}
        if pid <= 0 or pid in PROTECTED_PIDS or pid == SELF_PID:
            return {"success": False, "error": "受保护的进程，禁止终止", "pid": pid}
        try:
            os.kill(pid, signal.SIGKILL)
            return {"success": True, "pid": pid, "message": "已发送 SIGKILL"}
        except ProcessLookupError:
            return {"success": False, "error": "进程不存在", "pid": pid}
        except PermissionError:
            return {"success": False, "error": "权限不足，请用 sudo 运行", "pid": pid}

    # ── 系统服务 ──────────────────────────────────────────────

    _svc_lock = threading.Lock()
    _svc_cache = {"data": None, "ts": 0.0}
    _SERVICES_CACHE_TTL = 20.0

    def services_snapshot(self, force=False):
        now = time.monotonic()
        with self._svc_lock:
            if (not force and self._svc_cache["data"] is not None
                    and now - self._svc_cache["ts"] < self._SERVICES_CACHE_TTL):
                return self._svc_cache["data"]
        data = self._services_probe()
        if data is not None:
            with self._svc_lock:
                self._svc_cache["data"] = data
                self._svc_cache["ts"] = time.monotonic()
        return data

    def _services_probe(self):
        if _IS_MACOS:
            return self._macos_services()
        return self._linux_services()

    def _macos_services(self):
        """macOS: launchctl list。"""
        out = _run(["launchctl", "list"], timeout=15)
        if out is None:
            return None
        services = []
        for line in out.stdout.splitlines()[1:]:
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            pid_str, exit_str, label = parts[0], parts[1], parts[2]
            try:
                pid = int(pid_str) if pid_str != "-" else 0
            except ValueError:
                pid = 0
            state = "Running" if pid > 0 else "Stopped"
            services.append({
                "name": label,
                "display": label,
                "state": state,
                "mode": "Auto",
                "pid": pid,
                "protected": False,
            })
        if not services:
            return None
        return {"services": services, "admin": self.is_admin()}

    def _linux_services(self):
        """Linux: systemctl list-units。"""
        out = _run(["systemctl", "list-units", "--type=service", "--all",
                     "--no-pager", "--no-legend"], timeout=15)
        if out is None:
            return None
        services = []
        for line in out.stdout.splitlines():
            parts = line.split()
            if len(parts) < 4:
                continue
            name = parts[0].removesuffix(".service")
            load_state = parts[1]
            active = parts[2]
            sub = parts[3]
            state = "Running" if active == "active" and sub == "running" else "Stopped"
            services.append({
                "name": name,
                "display": name,
                "state": state,
                "mode": load_state,
                "pid": 0,
                "protected": False,
            })
        if not services:
            return None
        return {"services": services, "admin": self.is_admin()}

    def service_action(self, name, action, mode=""):
        name = (name or "").strip()
        if not name:
            return {"success": False, "error": "无效的服务名", "name": name}
        if action not in ("start", "stop", "restart", "mode"):
            return {"success": False, "error": "不支持的操作", "name": name}

        if _IS_MACOS:
            cmd_map = {"start": ["launchctl", "load"],
                       "stop": ["launchctl", "unload"],
                       "restart": ["launchctl", "kickstart", "-k"]}
            if action == "mode":
                return {"success": False, "error": "macOS 不支持修改启动类型", "name": name}
            cmd = cmd_map.get(action)
            if not cmd:
                return {"success": False, "error": "不支持的操作", "name": name}
            res = _run(cmd + [name], timeout=30)
        else:
            if action == "mode":
                res = _run(["systemctl", "enable" if mode != "Disabled" else "disable",
                            name], timeout=30)
            else:
                res = _run(["systemctl", action, name], timeout=30)

        if res is None:
            return {"success": False, "error": "操作超时", "name": name}
        ok = res.returncode == 0
        msg = (res.stderr or res.stdout or "").strip()
        result = {"success": ok, "name": name}
        if ok:
            result["state"] = "Running" if action in ("start", "restart") else "Stopped"
            self.services_snapshot(force=True)
        else:
            result["error"] = msg[:500] or "操作失败"
        return result

    # ── 磁盘 ──────────────────────────────────────────────────

    def disk_snapshot(self):
        if _IS_MACOS:
            return self._macos_disks()
        return self._linux_disks()

    def _macos_disks(self):
        """macOS: 遍历 /Volumes + statvfs。"""
        disks = []
        try:
            volumes = os.listdir("/Volumes")
        except OSError:
            return None
        for vol in volumes:
            path = os.path.join("/Volumes", vol)
            if not os.path.ismount(path):
                continue
            try:
                st = os.statvfs(path)
            except OSError:
                continue
            total = st.f_frsize * st.f_blocks
            free = st.f_frsize * st.f_bavail
            used = max(0, total - free)

            def pct(p, t):
                return round(p * 100.0 / t, 1) if t > 0 else 0.0

            disks.append({
                "drive": path,
                "label": vol,
                "fs": "",
                "type": "固定磁盘" if vol != "Macintosh HD" else "系统盘",
                "total": total, "used": used, "free": free,
                "usedPct": pct(used, total),
                "ready": True,
            })
        return {"disks": disks} if disks else None

    def _linux_disks(self):
        """Linux: df 输出解析。"""
        out = _run(["df", "-B1", "--output=target,fstype,size,used,avail,pcent"],
                   timeout=10)
        if out is None:
            return None
        disks = []
        for line in out.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 6:
                continue
            mount, fstype = parts[0], parts[1]
            try:
                total = int(parts[2])
                used = int(parts[3])
                free = int(parts[4])
            except ValueError:
                continue
            pct_str = parts[5].rstrip("%")
            try:
                used_pct = float(pct_str)
            except ValueError:
                used_pct = 0.0

            disks.append({
                "drive": mount,
                "label": os.path.basename(mount) or "/",
                "fs": fstype,
                "type": "固定磁盘",
                "total": total, "used": used, "free": free,
                "usedPct": used_pct,
                "ready": True,
            })
        return {"disks": disks} if disks else None

    def clean_catalogs(self):
        home = os.environ.get("HOME", "")
        if _IS_MACOS:
            return [
                ("user_caches", "用户缓存", os.path.join(home, "Library", "Caches"), True),
                ("logs", "日志文件", os.path.join(home, "Library", "Logs"), True),
                ("tmp", "临时文件", "/tmp", True),
                ("npm_cache", "npm 缓存", os.path.join(home, ".npm", "_cacache"), True),
                ("pip_cache", "pip 缓存", os.path.join(home, "Library", "Caches", "pip"), True),
                ("brew_cache", "Homebrew 缓存", os.path.join(home, "Library", "Caches", "Homebrew"), True),
                ("yarn_cache", "Yarn 缓存", os.path.join(home, "Library", "Caches", "Yarn"), True),
                ("go_build", "Go 构建缓存", os.path.join(home, "Library", "Caches", "go-build"), True),
                ("trash", "废纸篓", os.path.join(home, ".Trash"), False),
            ]
        # Linux
        return [
            ("user_caches", "用户缓存", os.path.join(home, ".cache"), True),
            ("tmp", "临时文件", "/tmp", True),
            ("npm_cache", "npm 缓存", os.path.join(home, ".npm", "_cacache"), True),
            ("pip_cache", "pip 缓存", os.path.join(home, ".cache", "pip"), True),
            ("yarn_cache", "Yarn 缓存", os.path.join(home, ".cache", "yarn"), True),
            ("go_build", "Go 构建缓存", os.path.join(home, ".cache", "go-build"), True),
            ("trash", "回收站", os.path.join(home, ".local", "share", "Trash"), False),
        ]

    def is_protected_path(self, path):
        low = os.path.abspath(path)
        guards = ["/System", "/usr", "/bin", "/sbin", "/private/var"]
        return any(low == g or low.startswith(g + "/") for g in guards)

    # ── 进程探针 ──────────────────────────────────────────────

    _proc_lock = threading.Lock()
    _proc_cache = {"data": None, "ts": 0.0}
    _PROC_CACHE_TTL = 15.0

    def proc_snapshot(self):
        now = time.monotonic()
        with self._proc_lock:
            if (self._proc_cache["data"] is not None
                    and now - self._proc_cache["ts"] < self._PROC_CACHE_TTL):
                return self._proc_cache["data"]
        data = self._proc_probe()
        if data is not None:
            with self._proc_lock:
                self._proc_cache["data"] = data
                self._proc_cache["ts"] = time.monotonic()
        return data

    def _proc_probe(self):
        """ps aux 按 RSS 排序取 Top 20。"""
        out = _run(["ps", "aux", "-r"], timeout=10)
        if out is None:
            return None
        top = []
        for line in out.stdout.splitlines()[1:21]:
            parts = line.split(None, 10)
            if len(parts) < 11:
                continue
            try:
                pid = int(parts[1])
                rss_kb = int(parts[5])
            except (ValueError, IndexError):
                continue
            name = parts[10].split("/")[-1] if parts[10] else ""
            top.append({
                "pid": pid,
                "name": name[:60],
                "commit": rss_kb * 1024,  # macOS 无 PageFileUsage，用 RSS 近似
                "ws": rss_kb * 1024,
                "peak": 0,
            })

        os_info = {
            "caption": f"{platform.system()} {platform.release()}",
            "version": platform.version(),
            "hypervisorPresent": False,
            "bootTime": "",
        }
        boot = self.uptime_seconds()
        if boot is not None:
            import datetime
            bt = datetime.datetime.now() - datetime.timedelta(seconds=boot)
            os_info["bootTime"] = bt.isoformat()

        return {"top": top, "os": os_info}
