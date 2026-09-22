# -*- coding: utf-8 -*-
"""系统服务 —— 通过平台抽象层获取服务列表与执行操作。"""
from .platform import backend


def services_snapshot(force=False):
    """带缓存的服务快照。"""
    return backend.services_snapshot(force=force)


def service_action(name, action, mode=""):
    """启动 / 停止 / 重启服务，或修改启动类型。"""
    return backend.service_action(name, action, mode)
