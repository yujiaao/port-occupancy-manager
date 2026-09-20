# -*- coding: utf-8 -*-
"""代码搜索 —— 在本地目录中按内容查找文本，自动跳过版本库/压缩包/二进制，
并把命中的文本文件按 代码 / 配置文件 / 其他文本 分组；附带搜索历史持久化。

约定：code_search 是「生成器」，边扫描边 yield 事件，便于前端实时展示与中断。
  进度事件：{"type":"progress","dir":当前目录,"scanned":N,"found":M,"groups":{},"skipped":{}}
  命中事件：{"type":"hit","file":{...},"scanned":N,"found":M}
  结果事件：{"type":"result", ...}  /  出错：{"ok":False,"error":"..."}
"""
import json
import os
import re
import sys
import tempfile
import threading
import time

# 跳过的目录：版本管理目录 + 体积巨大且通常无需搜索的依赖/构建目录
SKIP_DIRS = {
    ".git", ".svn", ".hg", ".bzr", ".idea", ".vscode",
    "node_modules", "__pycache__", ".venv", "venv", "env", ".tox",
    "dist", "build", "target", ".gradle", ".next", ".nuxt", "out",
    "bin", "obj", "vendor", "site-packages",
}
# 压缩包 / 归档（显式跳过，满足"跳过压缩包"要求）
ARCHIVE_EXT = {
    "zip", "rar", "7z", "tar", "gz", "tgz", "bz2", "xz", "lz4", "zst",
    "jar", "war", "ear", "iso", "img", "dmg", "cab", "ace", "arj", "lzh",
    "zoo", "apk", "deb", "rpm", "msi", "wim", "esd", "pak", "crx",
}
# 其它常见二进制（读取无意义；NUL 嗅探会兜底，这里显式列出避免大文件读盘）
BINARY_EXT = {
    "exe", "dll", "so", "dylib", "sys", "drv", "bin", "dat", "pdb", "obj",
    "o", "a", "lib", "pyd", "class", "node", "wasm",
    "png", "jpg", "jpeg", "gif", "bmp", "ico", "webp", "tiff", "tif", "heic",
    "mp3", "mp4", "avi", "mov", "mkv", "webm", "wav", "flac", "ogg", "m4a", "aac",
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "pub", "odt", "ods", "odp",
    "ttf", "otf", "woff", "woff2", "eot", "db", "sqlite", "sqlite3", "mdb", "accdb",
}
# 大文件阈值：正文超过此体积的文件，命中一处即停止继续读该文件
BIG_FILE_FIRST_HIT_BYTES = 100 * 1024

# 文件分类（按扩展名）
CODE_EXT = {
    "c", "h", "cpp", "cc", "cxx", "hpp", "hxx", "hh", "c++", "h++",
    "py", "pyw", "js", "jsx", "mjs", "cjs", "ts", "tsx",
    "java", "kt", "kts", "go", "rs", "rb", "rbw", "php", "php3", "php4", "phtml",
    "cs", "vb", "vbs", "swift", "m", "mm", "scala", "sc", "groovy", "gradle",
    "sh", "bash", "zsh", "fish", "bat", "cmd", "ps1", "psm1",
    "sql", "lua", "pl", "pm", "r", "dart", "vue", "svelte",
    "html", "htm", "xhtml", "css", "scss", "sass", "less", "styl",
    "json", "json5", "jsonc", "xml", "xsl", "xslt", "xsd", "wsdl", "svg",
    "graphql", "gql", "proto", "asm", "s", "tex", "ipynb", "jl",
    "ex", "exs", "erl", "hrl", "hs", "lhs", "clj", "cljs", "cljc",
    "tf", "tfvars", "tpl", "tmpl", "ejs", "pug", "jade", "haml", "mustache",
    "coffee", "rkt", "nim", "cr", "zig", "d", "f", "f90", "f95", "ada", "adb", "ads",
}
CONFIG_EXT = {
    "ini", "cfg", "conf", "config", "toml", "env", "properties", "cnf", "inf",
    "prop", "yaml", "yml", "editorconfig",
    "gitignore", "gitattributes", "gitmodules", "dockerignore", "npmrc", "yarnrc",
    "babelrc", "eslintrc", "prettierrc", "npmignore", "pylintrc", "flake8",
    "git-blame-ignore-revs", "htpasswd", "netrc", "service", "socket", "mount",
}
OTHER_EXT = {
    "txt", "text", "md", "markdown", "rst", "adoc", "asciidoc", "log", "csv",
    "tsv", "rtf", "org", "1st", "textile", "texinfo",
}
# 无扩展名但有明确含义的文件（按文件名归类）
NAME_MAP = {
    "makefile": "code", "dockerfile": "code", "cmakelists.txt": "code",
    "rakefile": "code", "gemfile": "code", "vagrantfile": "code",
    "procfile": "code", "build.gradle": "code", "build.gradle.kts": "code",
    "pom.xml": "code", "meson.build": "code", "justfile": "code",
    ".gitignore": "config", ".gitattributes": "config", ".gitmodules": "config",
    ".dockerignore": "config", ".npmrc": "config", ".editorconfig": "config",
    ".babelrc": "config", ".eslintrc": "config", ".npmignore": "config",
    ".pylintrc": "config", ".flake8": "config",
    "license": "other", "licence": "other", "readme": "other", "readme.md": "other",
    "changelog": "other", "copying": "other", "authors": "other", "contributors": "other",
}


def categorize(ext, name):
    """把文件归入 code / config / other 三类。"""
    nl = name.lower()
    if nl in NAME_MAP:
        return NAME_MAP[nl]
    if ext in CODE_EXT:
        return "code"
    if ext in CONFIG_EXT:
        return "config"
    return "other"


def detect_encoding(path):
    """探测文本编码：utf-8(-sig) / gbk；都失败视为二进制，返回 None。"""
    try:
        with open(path, "rb") as f:
            chunk = f.read(32768)
    except OSError:
        return None
    if not chunk:
        return "utf-8"
    if chunk.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    try:
        chunk.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        chunk.decode("gbk")
        return "gbk"
    except UnicodeDecodeError:
        return None


def _skipped_payload(stats):
    return {
        "binary": stats["skippedBinary"], "archive": stats["skippedArchive"],
        "large": stats["skippedLarge"], "ext": stats["skippedExt"],
        "vcsDirs": stats["vcsDirs"], "scope": stats["skippedScope"],
    }


def code_search(directory, query, mode="text", case=False, name_only=False,
                ext_filter=None, recursive=True, max_files=500,
                max_per_file=200, max_file_mb=8, time_limit=60.0, scope=None):
    """边扫描边 yield 进度 / 命中事件，最后 yield 完整结果。

    scope 为允许搜索的文件类别集合（'code'/'config'/'other'），为空表示全部。
    """
    directory = os.path.abspath(directory)
    if not os.path.isdir(directory):
        yield {"ok": False, "error": "目录不存在或不可访问：" + directory}
        return
    if not query:
        yield {"ok": False, "error": "请输入要搜索的关键词"}
        return

    flags = 0 if case else re.IGNORECASE
    rx = None
    if mode == "regex":
        try:
            rx = re.compile(query, flags)
        except re.error as e:
            yield {"ok": False, "error": "正则表达式无效：" + str(e)}
            return
    # 普通文本模式预转小写，避免每行 lower()
    qlow = (query.lower() if (mode == "text" and not case) else None)
    ext_set = None
    if ext_filter:
        ext_set = set(e.lower().lstrip(".") for e in str(ext_filter).split(",") if e.strip())

    stats = {"scannedFiles": 0, "skippedBinary": 0, "skippedArchive": 0,
             "skippedLarge": 0, "skippedExt": 0, "vcsDirs": 0, "skippedScope": 0}
    groups = {"code": 0, "config": 0, "other": 0}
    results = []
    total_matches = 0
    start = time.time()
    deadline = start + time_limit
    truncated = False

    def walk_files():
        if recursive:
            for root, dirs, names in os.walk(directory, onerror=lambda e: None):
                # 剪枝：跳过版本库 / 构建产物等巨型目录
                pruned = [d for d in dirs if d in SKIP_DIRS]
                stats["vcsDirs"] += len(pruned)
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
                for f in names:
                    yield os.path.join(root, f)
        else:
            try:
                with os.scandir(directory) as it:
                    for e in it:
                        if e.is_file():
                            yield e.path
            except OSError:
                return

    def content_hits(fpath, enc, file_limit):
        """读取文件内容找命中；返回 (matches, stopped_early)。"""
        matches = []
        stopped_early = False   # 命中数触顶即停，文件里可能还有更多命中
        try:
            with open(fpath, "r", encoding=enc, errors="replace") as fh:
                for i, line in enumerate(fh, 1):
                    if len(matches) >= file_limit:
                        stopped_early = True
                        break
                    text = line.rstrip("\n").rstrip("\r")
                    if qlow is not None:
                        hit = qlow in text.lower()
                    elif rx is not None:
                        hit = bool(rx.search(text))
                    else:
                        hit = query in text
                    if hit:
                        matches.append({"line": i, "text": text[:1000]})
        except OSError:
            return [], False
        return matches, stopped_early

    cur_dir = None
    last_progress = start
    for fpath in walk_files():
        if time.time() > deadline or len(results) >= max_files:
            truncated = True
            break
        now = time.time()
        d = os.path.dirname(fpath)
        if d != cur_dir or now - last_progress >= 0.25:
            cur_dir = d
            last_progress = now
            yield {"type": "progress", "dir": d,
                   "scanned": stats["scannedFiles"], "found": len(results),
                   "groups": dict(groups), "skipped": _skipped_payload(stats)}
        ext = os.path.splitext(fpath)[1].lower().lstrip(".")
        if ext_set is not None and ext not in ext_set:
            stats["skippedExt"] += 1
            continue
        if ext in ARCHIVE_EXT:
            stats["skippedArchive"] += 1
            continue
        if ext in BINARY_EXT:
            stats["skippedBinary"] += 1
            continue
        try:
            size = os.path.getsize(fpath)
        except OSError:
            continue
        if max_file_mb and size > max_file_mb * 1024 * 1024:
            stats["skippedLarge"] += 1
            continue
        # 二进制嗅探：含 NUL 字节即视为二进制
        try:
            with open(fpath, "rb") as fb:
                head = fb.read(8192)
        except OSError:
            continue
        if b"\x00" in head:
            stats["skippedBinary"] += 1
            continue
        base = os.path.basename(fpath)
        grp = categorize(ext, base)
        if scope and grp not in scope:
            stats["skippedScope"] += 1
            continue

        # 仅按文件名匹配
        if name_only:
            stats["scannedFiles"] += 1
            if qlow is not None:
                hit = qlow in base.lower()
            elif rx is not None:
                hit = bool(rx.search(base))
            else:
                hit = query in base
            if hit:
                groups[grp] += 1
                rec = {"path": fpath, "rel": os.path.relpath(fpath, directory),
                       "group": grp, "size": size, "matches": [], "matchCount": 0}
                results.append(rec)
                yield {"type": "hit", "file": rec,
                       "scanned": stats["scannedFiles"], "found": len(results)}
            continue

        # 内容搜索
        enc = detect_encoding(fpath)
        if enc is None:
            stats["skippedBinary"] += 1
            continue
        stats["scannedFiles"] += 1   # 真正读取并检索的文本文件
        # 大文件（>100KB）命中一处即停：全量扫描代价高，通常只需知道"这个文件里有"
        file_limit = 1 if size > BIG_FILE_FIRST_HIT_BYTES else max_per_file
        matches, stopped_early = content_hits(fpath, enc, file_limit)
        if not matches:
            continue
        groups[grp] += 1
        total_matches += len(matches)
        rec = {"path": fpath, "rel": os.path.relpath(fpath, directory),
               "group": grp, "size": size,
               "matches": matches, "matchCount": len(matches),
               "moreMatches": stopped_early}
        results.append(rec)
        yield {"type": "hit", "file": rec,
               "scanned": stats["scannedFiles"], "found": len(results)}

    yield {
        "type": "result",
        "ok": True, "dir": directory, "query": query, "mode": mode, "case": case,
        "nameOnly": name_only, "elapsed": round(time.time() - start, 3),
        "scannedFiles": stats["scannedFiles"], "totalMatches": total_matches,
        "skipped": _skipped_payload(stats),
        "groups": groups, "totalFiles": len(results), "truncated": truncated,
        "results": results,
    }


# ---- 搜索历史（记录用户最近查询的目录，持久化到本地 JSON）----
# 文件名沿用旧名，避免改名/重构后用户的搜索历史丢失
HISTORY_FILENAME = "port_inspector_search_history.json"
HISTORY_MAX = 20

_history_lock = threading.Lock()


def _history_file():
    """返回可写的搜索历史文件路径；按可写性依次尝试 exe 同目录 / 用户主目录 / 临时目录。"""
    candidates = []
    try:
        candidates.append(os.path.dirname(os.path.abspath(sys.argv[0])))
    except Exception:
        pass
    try:
        candidates.append(os.path.expanduser("~"))
    except Exception:
        pass
    candidates.append(tempfile.gettempdir())
    for c in candidates:
        if not c or not os.path.isdir(c):
            continue
        p = os.path.join(c, HISTORY_FILENAME)
        try:
            with open(p, "a", encoding="utf-8"):
                pass
            return p
        except OSError:
            continue
    return candidates[-1]


def load_history():
    """读取最近查询目录列表（最多 HISTORY_MAX 条）。"""
    try:
        with open(_history_file(), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [str(x) for x in data if x][:HISTORY_MAX]
    except Exception:
        pass
    return []


def record_history(directory):
    """记录一次查询目录（去重并置顶），返回最新的历史列表。"""
    d = os.path.abspath(directory)
    with _history_lock:
        lst = [x for x in load_history() if x != d]
        lst.insert(0, d)
        lst = lst[:HISTORY_MAX]
        try:
            with open(_history_file(), "w", encoding="utf-8") as f:
                json.dump(lst, f, ensure_ascii=False)
        except Exception:
            pass
        return lst
