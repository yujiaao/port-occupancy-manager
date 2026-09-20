// 共享工具：DOM 取值、toast、格式化、导出、全局视图状态
// 各 Tab 模块从这里导入，避免重复定义。

export const $ = (id) => document.getElementById(id);

// 跨模块共享的视图状态。curView 由 app.js 的 switchView 更新，
// 各模块据此判断自己是否处于激活 Tab（例如决定是否绘制图表）。
export const state = {
  curView: "ports",
  servicesLoaded: false,
  disksLoaded: false,
  memLoaded: false,
};

export const GB = 1024 ** 3;
export const MB = 1024 ** 2;

let toastTimer = null;
// type: "ok" | "warn" | "err"（兼容历史调用 true/false）
export function toast(msg, type) {
  const t = $("toast");
  t.className = type === true ? "ok" : type === false ? "err" : (type || "ok");
  $("toast-msg").textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 3000);
}

export function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

export function fmtB(n) {
  if (n == null || isNaN(n)) return "--";
  if (n >= GB) return (n / GB).toFixed(2) + " GB";
  if (n >= MB) return Math.round(n / MB) + " MB";
  return Math.round(n) + " B";
}
export function fmtSmart(n) {
  if (n == null || isNaN(n)) return "--";
  if (n >= GB) return (n / GB).toFixed(2) + " GB";
  return Math.round(n / MB) + " MB";
}
export function fmtDur(sec) {
  if (sec == null) return "--";
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
  if (d > 0) return d + " 天 " + h + " 时";
  if (h > 0) return h + " 时 " + m + " 分";
  return m + " 分";
}
export function fmtClock(ms) {
  const d = new Date(ms), p = (x) => String(x).padStart(2, "0");
  return p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
}
export function download(filename, text, mime) {
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename; a.click();
  URL.revokeObjectURL(url);
}

// JSON 语法高亮：返回 HTML（已对 & < > 转义）。用于 JWT / JSON 格式化等工具。
export function syntaxHighlight(json) {
  json = json.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return json.replace(
    /("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g,
    (m) => {
      let cls = "j-num";
      if (/^"/.test(m)) cls = /:$/.test(m) ? "j-key" : "j-str";
      else if (/true|false/.test(m)) cls = "j-bool";
      else if (/null/.test(m)) cls = "j-null";
      return '<span class="' + cls + '">' + m + "</span>";
    });
}

// 全局委托的复制按钮：<button data-copy="元素id"> 点击复制该元素的文本
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-copy]");
  if (!b) return;
  const el = $(b.dataset.copy);
  if (!el) return;
  const txt = el.textContent || "";
  if (!txt) { toast("没有可复制的内容", false); return; }
  navigator.clipboard.writeText(txt).then(() => toast("已复制", true)).catch(() => toast("复制失败", false));
});
