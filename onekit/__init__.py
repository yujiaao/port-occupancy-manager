# -*- coding: utf-8 -*-
"""OneKit 后端包。

按功能域拆分，便于扩展与维护：

  config   —— 路径定位与全局常量
  shell    —— 外部命令 / PowerShell 调用封装
  winapi   —— Windows 原生 API（内存、磁盘、管理员、运行时长）
  ports    —— 端口占用查询与进程终止
  memory   —— 系统内存（提交空间）监控与内存大户进程
  services —— Windows 服务列表与启停
  disks    —— 分区容量 / 垃圾清理 / 大文件
  search   —— 目录全文搜索与搜索历史
  http_app —— HTTP 路由层（Handler）

新增一个后端接口的标准姿势：
  1) 在对应功能域模块里写好业务函数（纯逻辑、不碰 HTTP）；
  2) 在 http_app.Handler 里加一个 `_get_xxx` / `_post_xxx` 方法；
  3) 在 GET_ROUTES / POST_ROUTES 路由表里登记路径。
"""

__all__ = [
    "config", "shell", "winapi",
    "ports", "memory", "services", "disks", "search",
    "http_app",
]
