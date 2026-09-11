# Port Inspector · 本地系统工具箱

> 一个零依赖的本地运维小工具，三个 Tab 一站式：**端口占用查看与进程终止**、**Windows 服务启停**、**系统内存（提交空间）监控报警**。双击即用，玻璃拟态深色 UI，所有危险操作全程二次确认。

[![Release](https://img.shields.io/github/v/release/yujiaao/port-occupancy-manager)](https://github.com/yujiaao/port-occupancy-manager/releases/tag/v1.0.0)
[![Python](https://img.shields.io/badge/python-3.8%2B-blue)](https://www.python.org)
[![Platform](https://img.shields.io/badge/platform-Windows-lightgrey)](https://www.microsoft.com/windows)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

## 为什么需要它

- **端口冲突**：开发时经常遇到「端口被占用」「Address already in use」，系统自带工具要么信息零散（`netstat`）、要么没有图形界面（`taskkill`）。
- **内存崩溃**：Windows 上「提交空间（页面文件 commit）耗尽」会让任何进程的内存分配失败 —— 典型表现为 IntelliJ 系工具弹出 `GitAskPassApp` 崩溃、`hs_err_pid*.log` 里写着 `AvailPageFile size 63M`。这类问题在崩溃前往往没有任何提示。
- **服务启停**：为了停一个服务去开 `services.msc` 再翻几百行列表，效率太低。

本工具把这三件事整合到一个清爽的本地网页里 —— 无需安装、不依赖任何第三方包、数据不出本机。

## 功能特性

### Tab 1 · 端口占用

- **实时查看**：本机所有端口占用一览（协议 / 本地地址 / 状态 / PID / 进程名）
- **统计卡片**：总连接数、TCP、监听数、进程数，一眼掌握全局
- **即时搜索**：按端口号、地址、PID、进程名过滤；TCP / UDP 一键切换
- **按 PID 终止**：每行「终止」按钮，二次确认后强杀整个进程树（`taskkill /F /T`）
- **按端口号终止**：输入端口（如 `8080`），自动找出占用该端口的全部进程，批量强杀
- **导出**：当前列表一键导出为 CSV（UTF-8 BOM，Excel 直接打开不乱码）或 JSON
- **自动刷新**：可开关，默认每 3 秒拉取一次

### Tab 2 · 系统内存监控

- **核心指标**：物理内存、**提交空间（页面文件 commit）**占用与剩余、内存负载、系统运行时长 —— 采用 `GlobalMemoryStatusEx`，与 JVM `hs_err` 日志里的 `TotalPageFile` / `AvailPageFile` **同源**，可直接与崩溃日志逐项对照
- **8 分钟趋势图**：提交剩余 / 物理剩余双曲线 + 严重阈值参考线
- **内存大户 Top 20**：按「提交大小」排序（对应任务管理器的“提交大小”），一眼看出谁是元凶（`vmmem` = WSL2 / Docker）
- **阈值报警**：命中阈值时**页面弹窗 + 系统通知 + 声音 + 标签页标题闪烁**；默认阈值 —— 提交剩余 < 0.5GB 或占用 ≥ 95% 判严重，< 2GB 或占用 ≥ 85% 判警告；另含「按趋势预测多久耗尽」提醒
- 阈值可在页面「报警设置」中调整，保存在浏览器本地

### Tab 3 · 系统服务

- **服务列表**：显示名 / 服务名、状态（运行中 · 已停止）、启动类型（自动 · 手动 · 禁用）、PID
- **启停与配置**：支持**启动**、**停止**，以及**修改启动类型**（自动 / 手动 / 禁用），全部二次确认
- **筛选**：按名称搜索，按状态、启动类型过滤；可开自动刷新（10 秒）
- **安全边界**：受保护的系统关键服务（RPC / LSASS / Winlogon / WMI 等）禁止操作

### 通用

- **双击即用**：打包成单文件 `PortInspector.exe`，运行即自动打开浏览器
- **跨 Tab 报警**：在端口页或服务页时，内存监控仍在后台轮询，异常时内存 Tab 亮红点并照常弹窗

## 界面预览

![Port Inspector 界面预览（端口占用页面）](preview.svg)

> 预览图为早期版本（端口占用页面）。新增的「系统内存监控」「系统服务」两个 Tab 风格与之一致。

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

> **要用「系统服务」启停功能时，请右键以管理员身份运行**；非管理员也能查看列表，但启停会被系统拒绝（Access denied）。

### 方式二：从源码运行（需 Python 3.8+）

```bash
git clone https://github.com/yujiaao/port-occupancy-manager.git
cd port-occupancy-manager
python server.py
# 浏览器打开 http://127.0.0.1:8765
```

### 重新打包 exe

```bash
pip install pyinstaller
pyinstaller PortInspector.spec --noconfirm   # 产物： dist/PortInspector.exe
```

## 使用说明

### 端口占用

打开页面即自动列出所有端口占用。用顶部搜索框按端口、地址、PID 或进程名过滤；点击「TCP / UDP」标签切换协议。

- **按 PID 终止**：点击某行右侧「终止」→ 弹窗显示进程名、PID、占用端口 → 确认后终止整个进程树
- **按端口终止**：在「按端口号一键终止」面板输入端口（如 `8080`）→ 自动找出占用该端口的全部进程 → 弹窗列出并确认 → 批量终止
- **导出**：工具栏「导出 CSV」/「导出 JSON」导出当前视图（含过滤结果）

### 系统服务

切换到「系统服务」Tab 即加载列表（首次约 1–3 秒）。

- 每行右侧：**启动 / 停止**按钮（按当前状态自动禁用其一）+ **启动类型**下拉（自动 / 手动 / 禁用）
- 任何变更都会弹窗二次确认，并提示影响面（如“停止服务可能导致依赖它的功能不可用”）
- 顶部徽章显示**管理员模式** / **受限模式**；受限模式下确认弹窗会额外提醒可能失败
- 标注「受保护」的服务（RPC、LSASS、Winlogon、`Winmgmt` 等）按钮置灰，后端也会直接拒绝

### 系统内存监控

切换到「系统内存监控」Tab，默认每 2 秒刷新。

- **提交空间剩余（AvailPageFile）** 是最关键的指标：它见底时，Windows 上任何进程申请内存（哪怕 1MB）都可能失败
- 命中严重阈值时弹窗报警，列出触发原因与当前内存大户，并给出处置建议
- 点「我知道了」静默 5 分钟；指标恢复正常后自动复位，再次异常会重新报警
- 声音与系统通知依赖浏览器策略：需先在页面上点一下（解锁音频），通知需在浏览器里允许一次

## 安全设计

- **禁止误杀进程**：拒绝终止系统关键进程（PID 0 / 4）与工具自身
- **不卡界面**：`taskkill` 设 10 秒超时并先做进程存活预检；服务操作设 45 秒超时
- **服务防护**：受保护服务名单后端硬拒绝；服务名做注入字符校验（拒绝 `' " ; | & $ < >` 等）；启动类型使用白名单
- **兜底查询**：进程名以 `tasklist` 为主、`PowerShell` 兜底，兼容性更好
- **只读监控**：内存监控只读取系统指标，不修改任何系统设置
- **仅本地**：服务只绑定 `127.0.0.1`，不对外暴露、不上传任何数据

## 技术架构

| 层 | 技术 | 说明 |
| --- | --- | --- |
| 后端 | Python 标准库 `http.server`（`ThreadingHTTPServer`） | 零第三方依赖；多线程避免慢查询互相阻塞 |
| 系统指标 | `ctypes` 调用 `GlobalMemoryStatusEx` / `GetTickCount64` / `IsUserAnAdmin` | 无需 WMI 轮询即可拿到与 `hs_err` 同源的内存数据 |
| 进程 / 服务 | `tasklist`、`taskkill`、PowerShell CIM（`Win32_Process` / `Win32_Service`） | 进程快照 15 秒缓存，服务列表 20 秒缓存，操作后主动刷新 |
| 前端 | 原生 HTML / CSS / JavaScript | 玻璃拟态深色主题，无前端框架，Canvas 手绘趋势图 |
| 打包 | PyInstaller | 单文件 exe，前端已一同打进可执行文件 |

### 本地 API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/ports` | 端口占用列表 |
| POST | `/api/kill` | 按 PID 终止进程（含子进程树） |
| POST | `/api/kill_port` | 按端口号批量终止 |
| GET | `/api/stats` | 系统内存指标 + 内存大户 Top 20 |
| GET | `/api/services` | 系统服务列表（支持 `?force=1` 跳过缓存） |
| POST | `/api/service` | 服务操作：`start` / `stop` / `restart` / `mode`（改启动类型） |

## 项目结构

```
port-inspector/
├── server.py              # 后端：端口查询 / 进程终止 / 系统服务 / 内存采集 API
├── index.html             # 前端：三个 Tab 的玻璃拟态深色 UI（单文件）
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

**Q：服务启停提示 Access denied / 操作失败？**
启停服务需要管理员权限。请关闭本工具后**右键以管理员身份运行**再试。

**Q：为什么有些服务不能停？**
RPC、LSASS、Winlogon、WMI（`Winmgmt`）这类关键服务被列入保护名单 —— 停止它们会直接导致系统崩溃或让本工具失效。列表中会标注「受保护」，后端也会拒绝操作。

**Q：内存监控页提示“仅支持 Windows”？**
内存指标依赖 Windows 的 `GlobalMemoryStatusEx`，其它平台暂不支持（端口功能仍可正常使用）。

**Q：报警没声音 / 没收到系统通知？**
浏览器要求先有用户交互才允许播放声音，请在页面上点一下；系统通知需要点击页面上的「启用系统通知」并允许权限。页面内弹窗不受影响。

**Q：支持 macOS / Linux 吗？**
后端已为 Unix 预留 `lsof` 兼容路径（端口功能），服务与内存监控为 Windows 实现。主测试环境为 Windows，欢迎提交 PR 完善跨平台支持。

## 许可证

本项目基于 [MIT 许可证](LICENSE) 开源。
