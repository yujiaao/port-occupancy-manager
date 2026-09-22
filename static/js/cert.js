// HTTPS 证书检测 Tab：连接目标端口读取 TLS 证书，展示信任状态 / 有效期 / 域名匹配与算法细节。
import { $, toast, esc } from "./common.js";

const HIST_KEY = "onekit_cert_history_v1";
const HIST_MAX = 12;

let lastPem = "";

// ---------- 最近查询（本地保存，仅用于输入框下拉） ----------
function histLoad() {
  try {
    const v = JSON.parse(localStorage.getItem(HIST_KEY) || "[]");
    return Array.isArray(v) ? v.filter((x) => typeof x === "string") : [];
  } catch (e) {
    return [];
  }
}
function renderHist() {
  const dl = $("certHistory");
  if (!dl) return;
  dl.innerHTML = "";
  for (const v of histLoad()) {
    const o = document.createElement("option");
    o.value = v;
    dl.appendChild(o);
  }
}
function histPush(target) {
  const list = [target, ...histLoad().filter((x) => x !== target)].slice(0, HIST_MAX);
  try { localStorage.setItem(HIST_KEY, JSON.stringify(list)); } catch (e) { /* 隐私模式忽略 */ }
  renderHist();
}

// ---------- 渲染 ----------
function setBadge(cls, txt) {
  $("certBadge").className = "badge" + (cls ? " " + cls : "");
  $("certBadgeTxt").textContent = txt;
}
// value 需为已转义文本或受控 HTML 片段
function kvRow(label, value, danger) {
  return "<dt>" + esc(label) + "</dt><dd" + (danger ? ' class="big"' : "") + ">" + value + "</dd>";
}
function chips(items) {
  return '<span class="cert-san">' + items.map((s) => "<i>" + esc(s) + "</i>").join("") + "</span>";
}
function hideResults() {
  $("certStats").style.display = "none";
  $("certDetail").style.display = "none";
  $("certPemPanel").style.display = "none";
}
function showError(msg) {
  const err = $("certErr");
  err.style.display = "block";
  err.textContent = msg;
}

function render(d) {
  const trusted = !!d.trusted;
  setBadge(trusted ? "st-ok" : "st-crit", trusted ? "证书受信任" : "证书不受信任");

  const days = d.daysLeft;
  $("certStats").style.display = "grid";
  $("certDays").textContent =
    days == null ? "—" : (days < 0 ? "已过期" : days + " 天");
  $("certDaysSub").textContent =
    days == null ? "有效期解析失败"
      : (days < 0 ? Math.abs(days) + " 天前到期"
        : (d.notYetValid ? "尚未生效 · " + d.notBefore : "至 " + d.notAfter));
  $("certTrust").textContent = trusted ? "受信任" : "不受信任";
  $("certTrustSub").textContent = trusted ? "系统 CA 校验通过" : (d.verifyError || "校验未通过");
  $("certProto").textContent = d.protocol || "—";
  $("certCipher").textContent = d.cipher || "—";
  $("certKey").textContent = (d.keyAlg || "未知") + (d.keyBits ? " " + d.keyBits + " 位" : "");
  $("certSig").textContent = d.sigAlg || "—";

  const rows = [];
  rows.push(kvRow("Subject", esc(d.subjectText || "—")));
  rows.push(kvRow("Issuer", esc(d.issuerText || "—")));
  rows.push(kvRow("有效期起", esc(d.notBefore || "—")));
  rows.push(kvRow("有效期止", esc(d.notAfter || "—"), !!d.expired));
  rows.push(kvRow("序列号", esc(d.serial || "—")));
  rows.push(kvRow("版本", "v" + (d.version || 3)));
  rows.push('<dd class="sep"></dd>');
  const sigTxt = esc(d.sigAlg || "—") +
    (d.sigAlgOid ? ' <span style="opacity:.55">(' + esc(d.sigAlgOid) + ")</span>" : "");
  rows.push(kvRow("签名算法" + (d.weakSig ? " ⚠" : ""), sigTxt, !!d.weakSig));
  rows.push(kvRow("公钥算法", esc(d.keyAlg || "—") + (d.curve ? " · " + esc(d.curve) : "")));
  rows.push(kvRow("密钥长度", d.keyBits ? d.keyBits + " 位" : "—"));
  rows.push(kvRow("域名匹配", d.hostnameMatch ? "匹配" : "不匹配", !d.hostnameMatch));
  rows.push('<dd class="sep"></dd>');
  rows.push(kvRow("指纹 SHA-256", esc(d.fingerprintSha256 || "—")));
  rows.push(kvRow("指纹 SHA-1", esc(d.fingerprintSha1 || "—")));
  if (d.ocsp && d.ocsp.length) rows.push(kvRow("OCSP", d.ocsp.map(esc).join("<br>")));
  if (d.caIssuers && d.caIssuers.length) rows.push(kvRow("CA Issuers", d.caIssuers.map(esc).join("<br>")));
  if (d.crl && d.crl.length) rows.push(kvRow("CRL", d.crl.map(esc).join("<br>")));
  const san = d.san || [];
  rows.push(kvRow("SAN（" + san.length + "）", san.length ? chips(san) : "—"));
  $("certKv").innerHTML = rows.join("");
  $("certDetail").style.display = "block";

  lastPem = d.pem || "";
  $("certPem").textContent = lastPem;
  $("certPemPanel").style.display = lastPem ? "block" : "none";

  // 风险汇总
  const warns = [];
  if (d.expired) warns.push("证书已过期");
  if (d.notYetValid) warns.push("证书尚未生效");
  if (!trusted) warns.push("信任校验未通过：" + (d.verifyError || "未知原因"));
  if (!d.hostnameMatch) warns.push("证书不覆盖当前域名（SAN 与 CN 均未匹配）");
  if (d.weakSig) warns.push("签名算法已过时（" + d.sigAlg + "），建议服务端更换");
  if (d.keyAlg === "RSA" && d.keyBits && d.keyBits < 2048) warns.push("RSA 密钥不足 2048 位，强度偏低");
  if (days != null && days >= 0 && days <= 15) warns.push("仅剩 " + days + " 天到期，建议尽快续期");
  const err = $("certErr");
  if (warns.length) {
    showError(warns.map((w) => "• " + w).join("\n"));
  } else {
    err.style.display = "none";
  }
}

// ---------- 主流程 ----------
async function doVerify() {
  const target = ($("certTarget").value || "").trim();
  if (!target) { toast("请输入域名或 URL", false); return; }

  const btn = $("certBtn");
  btn.disabled = true;
  btn.innerHTML = '<span class="spin"></span> 检测';
  setBadge("st-warn", "连接中…");
  $("certErr").style.display = "none";

  try {
    const body = { target, timeout: parseFloat($("certTimeout").value) || 10 };
    const p = parseInt($("certPort").value, 10);
    if (p) body.port = p;
    const resp = await fetch("/api/cert", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const d = await resp.json();
    if (!d.ok) {
      setBadge("st-crit", "检测失败");
      hideResults();
      showError(d.error || "检测失败");
      toast(d.error || "检测失败", "err");
      return;
    }
    render(d);
    histPush(d.host + (d.port === 443 ? "" : ":" + d.port));
    toast(d.trusted ? "证书校验通过" : "证书校验未通过", d.trusted ? "ok" : "warn");
  } catch (e) {
    setBadge("st-crit", "检测失败");
    hideResults();
    showError("请求失败：" + (e.message || e));
    toast("请求失败", "err");
  } finally {
    btn.disabled = false;
    btn.textContent = "验证";
  }
}

function clearAll() {
  $("certTarget").value = "";
  $("certPort").value = "";
  $("certKv").innerHTML = "";
  lastPem = "";
  hideResults();
  $("certErr").style.display = "none";
  setBadge("", "待检测");
}

$("certBtn").onclick = doVerify;
$("certTarget").addEventListener("keydown", (e) => { if (e.key === "Enter") doVerify(); });
$("certPort").addEventListener("keydown", (e) => { if (e.key === "Enter") doVerify(); });
$("certClearBtn").onclick = clearAll;
$("certCopyPem").onclick = () => {
  if (!lastPem) { toast("没有可复制的证书", false); return; }
  navigator.clipboard.writeText(lastPem)
    .then(() => toast("已复制 PEM", "ok"))
    .catch(() => toast("复制失败", false));
};

export function loadCert() {
  renderHist();
  const el = $("certTarget");
  if (el) el.focus();
}
