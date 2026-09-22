// 应用入口：Tab 切换、懒加载各视图数据。
// 各视图模块在加载时已自行绑定事件并发起初始请求（端口、内存监控），
// 服务 / 磁盘视图则在此处按需懒加载（首次切到该 Tab 才请求）。
import { $, state } from "./common.js";
import { fetchPorts } from "./ports.js";
import { sysTick, drawChart } from "./sysmon.js";
import { fetchServices } from "./services.js";
import { fetchDisks } from "./disks.js";
import { loadSearchHistory } from "./search.js";
import { loadJwt } from "./jwt.js";
import { loadJsonFmt } from "./jsonfmt.js";
import { loadTsConv } from "./tsconv.js";
import { loadB64 } from "./b64.js";
import { loadUtf8 } from "./utf8.js";
import { loadCert } from "./cert.js";

function switchView(v) {
  state.curView = v;
  document.querySelectorAll("#tabs .tab").forEach((b) => b.classList.toggle("active", b.dataset.view === v));
  $("view-ports").hidden = v !== "ports";
  $("view-sysmon").hidden = v !== "sysmon";
  $("view-services").hidden = v !== "services";
  $("view-disks").hidden = v !== "disks";
  $("view-search").hidden = v !== "search";
  $("view-jwt").hidden = v !== "jwt";
  $("view-jsonfmt").hidden = v !== "jsonfmt";
  $("view-tsconv").hidden = v !== "tsconv";
  $("view-b64").hidden = v !== "b64";
  $("view-utf8").hidden = v !== "utf8";
  $("view-cert").hidden = v !== "cert";
  if (v === "ports") fetchPorts();
  else if (v === "services") { if (!state.servicesLoaded) fetchServices(); }
  else if (v === "disks") { if (!state.disksLoaded) fetchDisks(); }
  else if (v === "search") { loadSearchHistory(); }
  else if (v === "jwt") { loadJwt(); }
  else if (v === "jsonfmt") { loadJsonFmt(); }
  else if (v === "tsconv") { loadTsConv(); }
  else if (v === "b64") { loadB64(); }
  else if (v === "utf8") { loadUtf8(); }
  else if (v === "cert") { loadCert(); }
  else { requestAnimationFrame(drawChart); if (!state.memLoaded) sysTick(); }
}

$("tabs").addEventListener("click", (e) => {
  const b = e.target.closest(".tab");
  if (b) switchView(b.dataset.view);
});
