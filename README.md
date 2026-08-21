# Port Occupancy Manager · 端口占用管理器

> 一个零依赖的本地端口占用查看与进程终止工具。双击即用，玻璃拟态深色 UI，按 PID 或端口号一键终止进程，全程二次确认。

[![Release](https://img.shields.io/github/v/release/yujiaao/port-occupancy-manager)](https://github.com/yujiaao/port-occupancy-manager/releases/tag/v1.0.0)
[![Python](https://img.shields.io/badge/python-3.8%2B-blue)](https://www.python.org)
[![Platform](https://img.shields.io/badge/platform-Windows-lightgrey)](https://www.microsoft.com/windows)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

## 为什么需要它

开发时经常遇到「端口被占用」「Address already in use」，但系统自带工具要么信息零散（`netstat`）、要么没有图形界面（`taskkill`）。本工具把「看端口」和「杀进程」整合到一个清爽的本地网页里 —— 无需安装、不依赖任何第三方包。

## 功能特性

- **实时查看**：本机所有端口占用一览（协议 / 本地地址 / 状态 / PID / 进程名）
- **统计卡片**：总连接数、TCP、监听数、进程数，一眼掌握全局
- **即时搜索**：按端口号、地址、PID、进程名过滤；TCP / UDP 一键切换
- **按 PID 终止**：每行「终止」按钮，二次确认后强杀整个进程树（`taskkill /F /T`）
- **按端口号终止**：输入端口（如 `8080`），自动找出占用该端口的全部进程，批量强杀
- **导出**：当前列表一键导出为 CSV（UTF-8 BOM，Excel 直接打开不乱码）或 JSON
- **自动刷新**：可开关，默认每 3 秒拉取一次
- **双击即用**：打包成单文件 `PortInspector.exe`，运行即自动打开浏览器

## 界面预览

![Port Occupancy Manager 界面预览](preview.svg)

## 快速开始

### 方式一：下载 exe（推荐，零安装）

1. 下载 `PortInspector.exe`
   - 仓库文件页：<https://github.com/yujiaao/port-occupancy-manager/blob/main/dist/PortInspector.exe>
   - 或直接下载：<https://raw.githubusercontent.com/yujiaao/port-occupancy-manager/main/dist/PortInspector.exe>
2. 双击运行，浏览器自动打开 <http://127.0.0.1:8765>

可选命令行参数：

```bash
PortInspector.exe 9000          # 指定监听端口
PortInspector.exe --no-browser  # 不自动打开浏览器
```

### 方式二：从源码运行（需 Python 3.8+）

```bash
git clone https://github.com/yujiaao/port-occupancy-manager.git
cd port-occupancy-manager
python server.py
# 浏览器打开 http://127.0.0.1:8765
```

## 使用说明

### 查看

打开页面即自动列出所有端口占用。使用顶部搜索框按端口、地址、PID 或进程名过滤；点击「TCP / UDP」标签切换协议。

### 终止进程（按 PID）

点击某行右侧「终止」→ 弹窗显示进程名、PID、占用端口 → 确认后终止整个进程树。

### 终止进程（按端口）

在顶部「按端口终止」面板输入端口号（如 `8080`）→ 自动查找占用该端口的全部进程 → 弹窗列出并确认 → 批量终止。

### 导出

工具栏「导出 CSV」/「导出 JSON」导出当前视图（含过滤结果）。

## 安全设计

- **禁止误杀**：拒绝终止系统关键进程（PID 0 / 4）与工具自身，避免把系统搞崩。
- **不卡界面**：`taskkill` 设 10 秒超时并先做进程存活预检 —— 进程不存在则秒回，存在则终止，绝不卡死 UI。
- **兜底查询**：进程名以 `tasklist` 为主、`PowerShell` 兜底，兼容性更好。

## 技术架构

| 层 | 技术 | 说明 |
| --- | --- | --- |
| 后端 | Python 标准库 `http.server` | 零第三方依赖，跨平台（Windows 为主，Unix 预留 `lsof` 路径） |
| 前端 | 原生 HTML / CSS / JavaScript | 玻璃拟态深色主题，无前端框架 |
| 打包 | PyInstaller | 单文件 exe，前端已一同打进可执行文件 |

## 项目结构

```
port-occupancy-manager/
├── server.py              # 后端：端口查询 + 进程终止 API
├── index.html             # 前端：玻璃拟态深色 UI
├── PortInspector.spec     # PyInstaller 打包配置
├── dist/
│   └── PortInspector.exe  # 单文件可执行（双击即用）
├── preview.svg            # 界面预览图
├── LICENSE
└── README.md
```

## 常见问题

**Q：进程名显示为空？**
在受限制的环境（如某些带安全软件的沙箱）中，进程枚举命令可能被拦截。在正常的 Windows 本机上，`tasklist` 能正常返回进程名。

**Q：杀进程没反应？**
部分企业终端防护软件可能拦截 `taskkill`。工具已做超时保护，最多等待 10 秒并给出明确反馈，不会卡死界面。

**Q：支持 macOS / Linux 吗？**
后端已为 Unix 预留 `lsof` 兼容路径，主测试环境为 Windows。欢迎提交 PR 完善跨平台支持。

## 许可证

本项目基于 [MIT 许可证](LICENSE) 开源。
