// 代码搜索 Tab：目录内按内容查找文本，结果按 代码 / 配置文件 / 其他文本 分组。
import { $, toast, esc, fmtB } from "./common.js";

let searchMode = "text";
let searchAbort = null; // 非空表示正在搜索，可被「停止」按钮中断
let lastScanned = 0;    // 最近一次进度上报的已扫描文件数

// 拉取最近查询目录，填充目录输入框的 datalist
export async function loadSearchHistory() {
  try {
    const d = await (await fetch("/api/search/history", { cache: "no-store" })).json();
    const list = $("dirHistory");
    if (!list) return;
    list.innerHTML = "";
    for (const p of (d.history || [])) {
      const o = document.createElement("option");
      o.value = p; o.textContent = p;
      list.appendChild(o);
    }
  } catch (e) { /* 失败忽略，不影响主流程 */ }
}

function setMode(m) {
  searchMode = m;
  document.querySelectorAll("#searchModeSeg button").forEach((b) =>
    b.classList.toggle("active", b.dataset.mode === m));
}

// 仅普通文本模式做关键词高亮，避免正则转义复杂度
function highlight(text) {
  const q = ($("searchQuery").value || "").trim();
  if (searchMode !== "text" || !q) return esc(text);
  const safe = q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  try {
    const rx = new RegExp(safe, $("searchCase").checked ? "g" : "ig");
    return esc(text).replace(rx, (m) => "<mark>" + m + "</mark>");
  } catch (e) {
    return esc(text);
  }
}

async function doSearch() {
  if (searchAbort) return; // 已有搜索在进行
  const dir = ($("searchDir").value || "").trim();
  const query = ($("searchQuery").value || "").trim();
  if (!dir) { toast("请输入要搜索的目录", false); return; }
  if (!query) { toast("请输入要搜索的关键词", false); return; }

  const btn = $("searchBtn");
  const ctrl = new AbortController();
  searchAbort = ctrl;
  lastScanned = 0;
  const partial = [];
  prepareResults();
  btn.className = "btn danger";
  btn.innerHTML = '<span class="spin"></span> 停止';
  $("searchBadge").className = "badge st-warn";
  $("searchBadgeTxt").textContent = "搜索中…";

  try {
    const scopeEls = document.querySelectorAll(".scope-cb:checked");
    const scope = Array.from(scopeEls).map((e) => e.value).join(",");
    let t = parseInt($("searchTimeout").value, 10);
    if (!t || t < 1) t = 600;
    const body = {
      dir, query, mode: searchMode,
      case: $("searchCase").checked,
      nameOnly: $("searchNameOnly").checked,
      ext: ($("searchExt").value || "").trim(),
      recursive: $("searchSubdir").checked,
      scope, timeLimit: t,
    };
    const resp = await fetch("/api/search", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body), signal: ctrl.signal,
    });
    if (!resp.ok) {
      const msg = (await resp.text()) || "搜索失败";
      throw new Error(msg);
    }
    // 消费 NDJSON 流：实时显示当前扫描目录，并逐条渲染命中的文件
    const reader = resp.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buf = "";
    let d = null;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, idx).trim();
        buf = buf.slice(idx + 1);
        if (!line) continue;
        let ev;
        try { ev = JSON.parse(line); } catch (e) { continue; }
        if (ev.type === "progress") {
          lastScanned = ev.scanned || 0;
          applyStats(ev);
          const full = ev.dir || "";
          const short = full.length > 50 ? (full.slice(0, 12) + "…" + full.slice(-38)) : full;
          $("searchBadgeTxt").textContent =
            `搜索中… ${short}（已扫 ${ev.scanned} · 命中 ${ev.found}）`;
        } else if (ev.type === "hit") {
          partial.push(ev.file);
          if (ev.scanned != null) lastScanned = ev.scanned;
          appendHit(ev.file);
        } else if (ev.type === "result") {
          d = ev;
        } else if (ev.ok === false) {
          d = ev;
        }
      }
    }
    if (!d) {
      toast("搜索未返回结果", false);
      hideEmptyResults(partial);
      $("searchBadge").className = "badge st-warn";
      $("searchBadgeTxt").textContent = "失败";
      return;
    }
    if (!d.ok) {
      toast(d.error || "搜索失败", false);
      hideEmptyResults(partial);
      $("searchBadge").className = "badge st-warn";
      $("searchBadgeTxt").textContent = "失败";
      return;
    }
    renderResults(d);
    // 用返回的最新历史刷新下拉
    if (d.history) {
      const list = $("dirHistory");
      list.innerHTML = "";
      for (const p of d.history) {
        const o = document.createElement("option");
        o.value = p; o.textContent = p;
        list.appendChild(o);
      }
    }
    $("searchBadge").className = "badge st-ok";
    $("searchBadgeTxt").textContent =
      `命中 ${d.totalFiles} 个文件 / ${d.totalMatches} 处` + (d.truncated ? "（已超时截断）" : "");
  } catch (e) {
    if (e.name === "AbortError") {
      // 用户主动中断：保留已扫到的部分结果
      $("searchBadge").className = "badge st-warn";
      if (partial.length) {
        $("searchBadgeTxt").textContent =
          `已中断 · 已找到 ${partial.length} 个文件（部分结果）`;
        $("ss-hit").textContent = partial.length;
        $("ss-scanned").textContent = lastScanned;
      } else {
        $("searchBadgeTxt").textContent = "已中断";
        $("searchEmpty").style.display = "block";
      }
      toast("已中断搜索", "warn");
      return;
    }
    toast("搜索失败：" + e.message, false);
    hideEmptyResults(partial);
    $("searchBadge").className = "badge st-warn";
    $("searchBadgeTxt").textContent = "失败";
  } finally {
    searchAbort = null;
    btn.className = "btn primary";
    btn.textContent = "搜索";
  }
}

// 跳过统计文案：二进制/压缩/VCS[ +大文件N][ +范围N]
function skippedText(sk) {
  let t = (sk.binary || 0) + "/" + (sk.archive || 0) + "/" + (sk.vcsDirs || 0);
  if (sk.large) t += " +大文件" + sk.large;
  if (sk.scope) t += " +范围" + sk.scope;
  return t;
}

// 用进度事件中的统计快照刷新统计面板（扫描数 / 命中数 / 分组 / 跳过）
function applyStats(ev) {
  $("ss-scanned").textContent = ev.scanned || 0;
  $("ss-hit").textContent = ev.found || 0;
  const g = ev.groups || {};
  for (const grp of ["code", "config", "other"]) {
    $("cnt-" + grp).textContent = g[grp] || 0;
  }
  $("ss-groups").textContent =
    (g.code || 0) + "/" + (g.config || 0) + "/" + (g.other || 0);
  if (ev.skipped) $("ss-skip").textContent = skippedText(ev.skipped);
}

// 搜索彻底失败且未渲染任何结果时，收起空的结果面板
function hideEmptyResults(partial) {
  if (!partial.length) {
    $("searchResults").style.display = "none";
    $("searchStats").style.display = "none";
  }
}

// 搜索开始时清空上一次结果并显示结果区（随后边搜边填充）
function prepareResults() {
  $("searchStats").style.display = "grid";
  $("searchResults").style.display = "block";
  $("ss-scanned").textContent = "0";
  $("ss-hit").textContent = "0";
  $("ss-groups").textContent = "0/0/0";
  $("ss-skip").textContent = "0/0/0";
  for (const grp of ["code", "config", "other"]) {
    $("list-" + grp).innerHTML = "";
    $("cnt-" + grp).textContent = "0";
  }
  $("searchEmpty").style.display = "none";
}

// 把单个命中文件追加到对应分组列表，并同步计数（用于边搜边渲染）
function appendHit(f) {
  const grp = ["code", "config", "other"].includes(f.group) ? f.group : "other";
  const wrap = $("list-" + grp) || $("list-other");
  wrap.appendChild(makeResultItem(f));
  const cnt = $("cnt-" + grp);
  cnt.textContent = String((parseInt(cnt.textContent, 10) || 0) + 1);
  const hit = $("ss-hit");
  hit.textContent = String((parseInt(hit.textContent, 10) || 0) + 1);
  $("ss-groups").textContent = ["code", "config", "other"]
    .map((k) => $("cnt-" + k).textContent).join("/");
}

// 构建单个命中文件的 DOM 节点
function makeResultItem(f) {
  const det = document.createElement("details");
  det.className = "res-item";
  const sum = document.createElement("summary");
  sum.className = "res-sum";
  sum.innerHTML =
    '<span class="fpath" title="' + esc(f.path) + '">' + esc(f.rel) + '</span>' +
    '<span class="rc">匹配 ' + f.matchCount + ' 处 · ' + fmtB(f.size) + '</span>';
  det.appendChild(sum);

  if (f.matches && f.matches.length) {
    const box = document.createElement("div");
    box.className = "res-matches";
    for (const m of f.matches) {
      const line = document.createElement("div");
      line.className = "mline";
      line.innerHTML = '<span class="ln">' + m.line + '</span>' +
        '<span class="lt">' + highlight(m.text) + '</span>';
      box.appendChild(line);
    }
    det.appendChild(box);
  } else {
    const p = document.createElement("div");
    p.className = "res-nameonly";
    p.textContent = "（按文件名命中）";
    det.appendChild(p);
  }
  return det;
}

function renderResults(d) {
  prepareResults();
  $("ss-scanned").textContent = d.scannedFiles;
  $("ss-hit").textContent = d.totalFiles;
  const g = d.groups || {};
  $("ss-groups").textContent = (g.code || 0) + "/" + (g.config || 0) + "/" + (g.other || 0);
  $("ss-skip").textContent = skippedText(d.skipped || {});

  const lists = {
    code: $("list-code"), config: $("list-config"), other: $("list-other"),
  };
  let any = false;
  for (const f of (d.results || [])) {
    any = true;
    const wrap = lists[f.group] || lists.other;
    wrap.appendChild(makeResultItem(f));
  }

  for (const grp of ["code", "config", "other"]) {
    $("cnt-" + grp).textContent = (d.groups && d.groups[grp]) || 0;
  }
  $("searchEmpty").style.display = any ? "none" : "block";
}

// 事件绑定
$("searchModeSeg").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-mode]");
  if (b) setMode(b.dataset.mode);
});
$("searchBtn").onclick = () => {
  if (searchAbort) { searchAbort.abort(); return; } // 中断当前搜索
  doSearch();
};
$("searchQuery").addEventListener("keydown", (e) => { if (e.key === "Enter") doSearch(); });
$("searchDir").addEventListener("keydown", (e) => { if (e.key === "Enter") doSearch(); });

// 模块加载时拉取一次历史
loadSearchHistory();
