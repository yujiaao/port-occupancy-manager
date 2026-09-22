# -*- coding: utf-8 -*-
"""平台后端抽象基类：定义所有功能域需要的接口契约。

每个方法都有默认返回值（None / [] / {}），未实现的方法不会崩溃，
只是对应功能在前端显示为「不支持当前平台」。
"""
from abc import ABC, abstractmethod


class PlatformBackend(ABC):
    """平台后端接口。"""

    # ── 系统信息 ──────────────────────────────────────────────

    @abstractmethod
    def memory_snapshot(self) -> dict | None:
        """系统内存快照；返回格式见 win32.py 实现。"""

    @abstractmethod
    def uptime_seconds(self) -> int | None:
        """系统运行时长（秒）。"""

    @abstractmethod
    def is_admin(self) -> bool:
        """是否以管理员 / root 权限运行。"""

    # ── 端口与进程 ────────────────────────────────────────────

    @abstractmethod
    def get_connections(self) -> list[dict]:
        """本机全部端口占用连接。"""

    @abstractmethod
    def kill_pid(self, pid: int) -> dict:
        """终止单个进程（含子进程树）。"""

    @abstractmethod
    def proc_name_map(self) -> dict[int, str]:
        """{pid: name} 映射。"""

    # ── 系统服务 ──────────────────────────────────────────────

    @abstractmethod
    def services_snapshot(self, force: bool = False) -> dict | None:
        """服务列表快照。"""

    @abstractmethod
    def service_action(self, name: str, action: str, mode: str = "") -> dict:
        """启动 / 停止 / 重启服务或修改启动类型。"""

    # ── 磁盘 ──────────────────────────────────────────────────

    @abstractmethod
    def disk_snapshot(self) -> dict | None:
        """各分区容量。"""

    @abstractmethod
    def clean_catalogs(self) -> list[tuple]:
        """清理类别列表：(id, 显示名, 路径, 默认勾选)。"""

    @abstractmethod
    def is_protected_path(self, path: str) -> bool:
        """是否为受保护的系统路径。"""

    # ── 进程探针（内存大户）──────────────────────────────────

    @abstractmethod
    def proc_snapshot(self) -> dict | None:
        """内存大户进程 Top N + OS 信息。"""
