"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  status: null,
  statusError: null,
  models: [],
  experts: [],
  runs: [],
  selectedRun: null,
  materials: [],
  login: null,
  selectedRound: null,
  streamRole: null,
  pending: new Set(),
  eventSource: null,
  loading: true,
};
const terminalStatuses = new Set(["completed", "disputed", "needs_evidence", "failed", "cancelled"]);
const statusLabels = { queued: "等待开始", running: "讨论中", completed: "已形成结论", disputed: "保留分歧", needs_evidence: "待补充证据", failed: "运行失败", cancelled: "已停止" };
const phaseLabels = { queued: "准备研究", preparing: "准备研究", independent: "独立分析", initial: "独立分析", analyzing: "独立分析", analysis: "独立分析", discussion: "交叉质询", debate: "交叉质询", revision: "修订意见", revising: "修订意见", moderation: "综合审议", moderating: "综合审议", synthesis: "综合结论", final: "综合结论", finalizing: "综合结论", completed: "研究结束", done: "研究结束" };
const stanceLabels = { support: "支持", oppose: "反对", uncertain: "尚不确定" };
const dateFormatter = new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
const timeFormatter = new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
let toastTimer;
let statusRequest;
let runsRequest;

function node(tag, className, content) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (content !== undefined && content !== null) item.textContent = String(content);
  return item;
}

function icon(kind) {
  const paths = {
    file: ["M5 2h7l4 4v12H5V2z", "M12 2v5h4M8 10h5M8 13h5"],
  };
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 20 20");
  svg.setAttribute("aria-hidden", "true");
  for (const d of paths[kind] || paths.file) {
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", d);
    svg.append(path);
  }
  return svg;
}

function text(value, fallback = "") {
  if (value === null || value === undefined) return fallback;
  if (typeof value === "string" || typeof value === "number") return String(value);
  if (typeof value === "object") return value.text || value.claim || value.summary || value.description || value.message || JSON.stringify(value);
  return String(value);
}

function array(value) { return Array.isArray(value) ? value : []; }
function date(value, formatter = dateFormatter) {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? "" : formatter.format(parsed);
}
function isRunning(run) { return !!run && !terminalStatuses.has(run.status); }
function currentActiveRun() { return state.status?.activeRunId || state.runs.find(isRunning)?.id; }
function isPreview() { return !!state.status?.preview; }
function isVerifiedForModel() { return !!state.status?.verification?.passed && (state.status.verification.model || "") === ($("model-select").value || ""); }
function isVerifying() { return state.pending.has("verify") || !!state.status?.verifying; }

function toast(message, error = false) {
  clearTimeout(toastTimer);
  $("toast").textContent = message;
  $("toast").className = `toast${error ? " error" : ""}`;
  $("toast").hidden = false;
  toastTimer = setTimeout(() => { $("toast").hidden = true; }, error ? 8500 : 4500);
}

async function api(path, options = {}) {
  const method = options.method || "GET";
  const headers = { Accept: "application/json" };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") {
    if (!state.status?.csrfToken) await refreshStatus();
    if (!state.status?.csrfToken) throw new Error("无法取得本机安全会话，请确认本地服务已启动并刷新页面。");
    headers["X-Expert-CSRF"] = state.status.csrfToken;
  }
  let response;
  try {
    response = await fetch(path, {
      method,
      headers,
      credentials: "same-origin",
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
  } catch {
    throw new Error("暂时无法连接本地服务。请检查启动终端，再重试。");
  }
  const raw = await response.text();
  let data;
  try { data = raw ? JSON.parse(raw) : {}; } catch { throw new Error("本地服务返回了无法识别的响应，请检查服务日志。"); }
  if (!response.ok) throw new Error(text(data.error, `请求未完成（${response.status}）`));
  return data;
}

function setPending(name, pending) {
  if (pending) state.pending.add(name); else state.pending.delete(name);
  renderAvailability();
  renderConnectionControls();
}

async function refreshStatus() {
  if (statusRequest) return statusRequest;
  statusRequest = (async () => {
    try {
      const oldConnected = state.status?.auth?.connected;
      state.status = await api("/api/status");
      state.statusError = null;
      if (state.status.auth?.connected && state.login) {
        state.login = null;
        if (!oldConnected) toast("ChatGPT 登录成功。现在可以验证真实连接。");
      }
      renderStatus();
    } catch (error) {
      state.statusError = error.message;
      renderStatus();
    } finally { statusRequest = null; }
  })();
  return statusRequest;
}

async function refreshRuns() {
  if (runsRequest) return runsRequest;
  runsRequest = (async () => {
    try {
      const result = await api("/api/runs");
      state.runs = array(result.runs).sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt));
      if (state.selectedRun) {
        const latest = state.runs.find((run) => run.id === state.selectedRun.id);
        if (latest) { state.selectedRun = latest; renderRun(); }
      }
      renderHistory();
      renderAvailability();
    } finally { runsRequest = null; }
  })();
  return runsRequest;
}

function renderStatus() {
  const status = state.status;
  const auth = status?.auth || {};
  const verified = isVerifiedForModel();
  const label = $("connection-label");
  const badge = $("connection-badge");
  badge.className = "connection-badge";
  if (state.statusError) label.textContent = "服务未连接";
  else if (!status) label.textContent = "检测连接中";
  else if (isPreview()) label.textContent = "界面预览";
  else if (!status.codex?.installed) label.textContent = "Codex 未就绪";
  else if (!status.codex?.ready) label.textContent = "服务待就绪";
  else if (verified && auth.connected) { label.textContent = "真实连接已验证"; badge.classList.add("verified"); }
  else if (auth.connected) { label.textContent = isVerifying() ? "正在验证连接" : "已登录 · 待验证"; badge.classList.add("connected"); }
  else label.textContent = "尚未连接";

  const notice = $("service-notice");
  let noticeText = "";
  if (state.statusError) noticeText = state.statusError;
  else if (isPreview()) noticeText = "当前为界面预览。真实登录、模型验证与专家讨论请在下载项目后，于本机启动服务使用。这里不会模拟研究结果。";
  else if (status && !status.codex?.installed) noticeText = "本机 Codex 尚未就绪。请按照项目启动说明安装依赖，再重新启动服务。";
  else if (status?.codex?.error) noticeText = `本机服务需要处理：${status.codex.error}`;
  notice.textContent = noticeText;
  notice.hidden = !noticeText;
  $("version-label").textContent = status?.version ? `v${status.version}` : "LOCAL";

  const readiness = $("readiness-title");
  const description = $("readiness-description");
  const action = $("readiness-action");
  if (isPreview()) {
    readiness.textContent = "在本机开始真实研究";
    description.textContent = "此页展示工作台。下载项目并启动服务后，连接你的 ChatGPT 账户。";
    action.textContent = "查看连接说明 →";
  } else if (verified && auth.connected) {
    readiness.textContent = "连接已准备就绪";
    description.textContent = "真实调用与会话恢复已通过验证。填写议题即可开始研究。";
    action.textContent = "查看验证结果 →";
  } else if (auth.connected) {
    readiness.textContent = "再验证一次真实连接";
    description.textContent = "账户已登录。完成真实调用与会话恢复检查后，就能开始讨论。";
    action.textContent = "进行连接验证 →";
  } else {
    readiness.textContent = "连接你的研究模型";
    description.textContent = "使用 ChatGPT 登录，再完成一次真实连接验证。";
    action.textContent = "设置连接 →";
  }
  renderAvailability();
  renderConnectionControls();
}

function renderAvailability() {
  const status = state.status;
  const authReady = status?.auth?.connected && isVerifiedForModel() && status?.codex?.ready && !isPreview() && !state.statusError && !isVerifying();
  const active = currentActiveRun();
  const question = $("question").value.trim();
  const start = $("start-research");
  start.disabled = !authReady || !question || !state.experts.length || !!active || state.pending.has("start");
  start.classList.toggle("busy", state.pending.has("start"));
  start.querySelector("span").textContent = state.pending.has("start") ? "正在创建" : "开始讨论";
  let hint = "填写一个研究问题，即可开始。";
  if (state.statusError) hint = "请先恢复本机服务连接。";
  else if (!status) hint = "正在检测本机连接状态…";
  else if (isPreview()) hint = "预览模式 · 请在本机启动项目后使用。";
  else if (!status.codex?.ready) hint = "请先在连接设置中检查本机 Codex 状态。";
  else if (!status.auth?.connected) hint = "请先连接 ChatGPT，并完成真实连接验证。";
  else if (isVerifying()) hint = "真实连接正在验证中，请稍候。";
  else if (status.verification?.passed && !isVerifiedForModel()) hint = "所选模型已更换，请先验证当前模型后再开始。";
  else if (!isVerifiedForModel()) hint = "已登录 · 请先完成一次真实连接验证。";
  else if (active) hint = "已有研究正在进行。完成或停止后可发起新研究。";
  else if (question) hint = `最多 ${$("round-select").value} 轮 · 仅使用你提供的材料 · 可随时停止`;
  $("launch-hint").textContent = hint;
  $("add-material").disabled = !$("material-text").value.trim();
  $("question-count").textContent = `${$("question").value.length.toLocaleString()} / 6,000`;
  $("save-experts").disabled = isPreview() || state.pending.has("experts") || !state.experts.length;
}

function statusRow(label, value, bad = false) {
  const row = node("div", "connection-status-row");
  row.append(node("span", "", label), node("strong", bad ? "bad" : "", value));
  return row;
}

function renderConnectionControls() {
  const status = state.status;
  const codex = status?.codex || {};
  const connected = !!status?.auth?.connected;
  const blocked = isPreview() || !codex.ready || !!state.statusError || state.pending.has("auth") || isVerifying();
  const details = $("connection-status-details");
  details.replaceChildren();
  details.append(statusRow("运行环境", isPreview() ? "界面预览" : "本机服务"));
  details.append(statusRow("Codex", codex.installed ? (codex.version || "已安装") : (status ? "尚未安装" : "检测中"), !codex.installed));
  details.append(statusRow("服务状态", codex.ready ? "就绪" : "未就绪", !codex.ready));
  details.append(statusRow("账户登录", connected ? "已登录" : "未登录", !connected));
  details.append(statusRow("真实调用", status?.verification?.passed ? "已验证" : "尚未通过验证", !status?.verification?.passed));
  if (status?.verification?.passed) details.append(statusRow("已验证模型", status.verification.model || "服务默认模型"));
  if (status?.verification?.passed && !isVerifiedForModel()) details.append(statusRow("当前选择", "模型已更换，需重新验证", true));
  if (state.statusError || codex.error) details.append(node("p", "connection-status-error", state.statusError || codex.error));
  $("signed-out-controls").hidden = connected;
  $("signed-in-controls").hidden = !connected;
  const auth = status?.auth || {};
  $("account-detail").textContent = [auth.email, auth.planType, auth.type].filter(Boolean).join(" · ") || "ChatGPT 账户";
  $("login-device").disabled = blocked || !!state.login;
  $("login-browser").disabled = blocked || !!state.login;
  $("login-device").classList.toggle("busy", state.pending.has("auth"));
  $("logout").disabled = blocked || !!currentActiveRun();
  $("logout").title = currentActiveRun() ? "请先停止正在进行的研究" : "退出本机 Codex 登录";
  $("cancel-login").disabled = state.pending.has("auth");
  $("verify-connection").disabled = !connected || blocked || !!currentActiveRun();
  $("verify-connection").classList.toggle("busy", isVerifying());
  $("verify-connection").querySelector("span").textContent = isVerifying() ? "正在验证调用与会话恢复…" : "一键验证真实连接";

  const login = state.login;
  $("login-instructions").hidden = !login;
  if (login) {
    $("login-instructions-text").textContent = login.code ? "打开登录页面，输入以下设备代码，完成账户授权。" : "请在浏览器完成登录。本页会持续检测账户状态。";
    $("device-code-row").hidden = !login.code;
    $("device-code").textContent = login.code || "";
    const url = safeLoginUrl(login.url);
    $("login-link").hidden = !url;
    if (url) $("login-link").href = url;
  }
  renderVerification();
}

function safeLoginUrl(value) {
  try {
    const url = new URL(value);
    if (url.protocol === "https:" || (url.protocol === "http:" && ["localhost", "127.0.0.1"].includes(url.hostname))) return url.href;
  } catch {}
  return null;
}

function renderVerification() {
  const target = $("verification-result");
  target.replaceChildren();
  if (isVerifying()) {
    const box = node("div", "verification-box running");
    box.append(node("p", "", "正在执行真实请求，并检查重启后的会话恢复。这可能需要几分钟，请保持本地服务运行。"));
    target.append(box);
    return;
  }
  const verification = state.status?.verification;
  if (!verification) return;
  const box = node("div", `verification-box${verification.passed ? "" : " error"}`);
  box.append(node("p", "", verification.passed ? "验证通过：真实模型调用与会话恢复均已完成。" : `验证尚未通过：${text(verification.error, "请检查本地服务和账户权限后重试。")}`));
  if (verification.checkedAt) box.append(node("small", "", `检查时间 ${date(verification.checkedAt)}`));
  if (verification.firstReply || verification.resumedReply || verification.threadId) {
    const detail = node("details");
    detail.append(node("summary", "", "查看实际验证返回"));
    const lines = [];
    if (verification.threadId) lines.push(`会话：${verification.threadId}`);
    if (verification.firstReply) lines.push(`首次回复：\n${text(verification.firstReply)}`);
    if (verification.resumedReply) lines.push(`恢复后回复：\n${text(verification.resumedReply)}`);
    detail.append(node("pre", "", lines.join("\n\n")));
    box.append(detail);
  }
  target.append(box);
}

function renderModels() {
  const select = $("model-select");
  const previous = select.value;
  select.replaceChildren();
  const defaultOption = node("option", "", "服务默认模型");
  defaultOption.value = "";
  select.append(defaultOption);
  for (const model of state.models) {
    if (!model?.id) continue;
    const option = node("option", "", model.displayName || model.id);
    option.value = model.id;
    option.title = model.description || "";
    select.append(option);
  }
  if ([...select.options].some((option) => option.value === previous)) select.value = previous;
}

function renderExperts() {
  $("expert-count").textContent = String(state.experts.length);
  const target = $("expert-pills");
  target.replaceChildren();
  state.experts.forEach((expert, index) => {
    const pill = node("span", "expert-pill");
    pill.title = expert.focus || "";
    pill.append(node("span", "expert-pill-index", String(index + 1).padStart(2, "0")), node("span", "", expert.name));
    target.append(pill);
  });
  if (!state.experts.length) target.append(node("span", "loading-note", "未能读取专家配置。请检查本地服务。"));
  renderAvailability();
}

function renderExpertEditors() {
  const target = $("expert-editors");
  target.replaceChildren();
  state.experts.forEach((expert, index) => {
    const editor = node("section", "expert-editor");
    editor.dataset.expertId = expert.id;
    const heading = node("div", "expert-editor-heading");
    heading.append(node("span", "expert-editor-index", String(index + 1).padStart(2, "0")));
    const name = node("input");
    name.name = "name";
    name.value = expert.name || "";
    name.required = true;
    name.maxLength = 80;
    name.setAttribute("aria-label", `专家 ${index + 1} 的名称`);
    heading.append(name);
    editor.append(heading);
    const focusLabel = node("label", "", "研究侧重点");
    focusLabel.htmlFor = `expert-focus-${index}`;
    const focus = node("input");
    focus.id = `expert-focus-${index}`;
    focus.name = "focus";
    focus.value = expert.focus || "";
    focus.maxLength = 500;
    focus.required = true;
    const instructionsLabel = node("label", "", "方法与判断规则");
    instructionsLabel.htmlFor = `expert-instructions-${index}`;
    const instructions = node("textarea");
    instructions.id = `expert-instructions-${index}`;
    instructions.name = "instructions";
    instructions.value = expert.instructions || "";
    instructions.rows = 6;
    instructions.required = true;
    instructions.maxLength = 8000;
    editor.append(focusLabel, focus, instructionsLabel, instructions);
    target.append(editor);
  });
}

function renderMaterials() {
  const list = $("material-list");
  list.replaceChildren();
  state.materials.forEach((material, index) => {
    const row = node("div", "material-item");
    const title = node("span", "material-item-title", material.title);
    title.title = material.title;
    const remove = node("button", "material-remove", "×");
    remove.type = "button";
    remove.setAttribute("aria-label", `移除材料：${material.title}`);
    remove.addEventListener("click", () => { state.materials.splice(index, 1); renderMaterials(); });
    row.append(icon("file"), title, node("span", "material-item-size", `${material.text.length.toLocaleString()} 字符`), remove);
    list.append(row);
  });
}

function addPastedMaterial() {
  const content = $("material-text").value.trim();
  if (!content) return;
  const title = $("material-title").value.trim() || `研究材料 ${state.materials.length + 1}`;
  if (!canAddMaterial(content)) return false;
  state.materials.push({ title, text: content });
  $("material-text").value = "";
  $("material-title").value = "";
  renderMaterials();
  renderAvailability();
  return true;
}

function canAddMaterial(content) {
  if (state.materials.length >= 12) { toast("每项研究最多添加 12 份材料。", true); return false; }
  if (state.materials.reduce((sum, material) => sum + material.text.length, 0) + content.length > 60000) { toast("参考材料合计最多 60,000 字符，请精简后再添加。", true); return false; }
  return true;
}

function renderHistory() {
  const list = $("history-list");
  const scroll = list.scrollTop;
  list.replaceChildren();
  $("history-count").textContent = String(state.runs.length);
  if (!state.runs.length) {
    const empty = node("p", "history-empty");
    empty.append(document.createTextNode("还没有研究会话"), node("br"), node("span", "", "从你的第一个问题开始。"));
    list.append(empty);
  }
  for (const run of state.runs) {
    const button = node("button", `history-item${state.selectedRun?.id === run.id ? " selected" : ""}`);
    button.type = "button";
    if (state.selectedRun?.id === run.id) button.setAttribute("aria-current", "true");
    button.title = run.question || "研究会话";
    button.append(node("span", "history-item-title", run.question || "研究会话"));
    const meta = node("span", "history-item-meta");
    meta.append(node("span", `history-item-status ${run.status || ""}`), node("span", "", statusLabels[run.status] || run.status || "研究会话"), node("span", "", date(run.createdAt)));
    button.append(meta);
    button.addEventListener("click", () => selectRun(run.id));
    list.append(button);
  }
  list.scrollTop = scroll;
}

async function selectRun(id, scroll = true) {
  try {
    const result = await api(`/api/runs/${encodeURIComponent(id)}`);
    state.selectedRun = result.run;
    state.selectedRound = null;
    state.streamRole = null;
    upsertRun(result.run);
    renderRun();
    renderHistory();
    closeSidebar();
    if (scroll) $("results-section").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) { toast(error.message, true); }
}

function upsertRun(run) {
  if (!run?.id) return;
  const index = state.runs.findIndex((item) => item.id === run.id);
  if (index >= 0) state.runs[index] = run; else state.runs.unshift(run);
  state.runs.sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt));
}

function appendList(target, title, values, className = "detail-list", emptyText = "") {
  if (!array(values).length && !emptyText) return;
  if (title) target.append(node("h5", "detail-heading", title));
  if (!array(values).length) { target.append(node("p", "empty-detail", emptyText)); return; }
  const list = node("ul", className);
  for (const value of values) list.append(node("li", "", text(value)));
  target.append(list);
}

function makeOpinionCard(expert, opinion) {
  const card = node("article", "opinion-card");
  const header = node("div", "opinion-card-header");
  const nameRow = node("div", "opinion-name-row");
  nameRow.append(node("h4", "opinion-name", opinion?.expertName || expert.name || expert.id));
  if (opinion) nameRow.append(node("span", `stance-tag ${opinion.stance || "uncertain"}`, stanceLabels[opinion.stance] || "尚不确定"));
  header.append(nameRow);
  if (opinion?.confidence !== undefined && opinion?.confidence !== null) {
    const value = Number(opinion.confidence);
    const confidence = Number.isFinite(value) ? `${value <= 1 ? Math.round(value * 100) : value}%` : text(opinion.confidence);
    header.append(node("div", "opinion-confidence", `模型自评信心 ${confidence} · 非校准概率`));
  } else if (expert.focus) header.append(node("div", "opinion-confidence", expert.focus));
  card.append(header);
  if (!opinion) { card.append(node("p", "expert-waiting", "尚未收到该专家本轮的结构化意见。")); return card; }
  const body = node("div", "opinion-body");
  body.append(node("p", "opinion-summary", opinion.summary || ""));
  if (array(opinion.arguments).length) {
    body.append(node("h5", "detail-heading", "判断依据"));
    const list = node("ul", "claim-list");
    for (const argument of opinion.arguments) {
      const item = node("li", "", text(argument.claim, text(argument)));
      if (array(argument.evidenceIds).length) item.append(node("span", "evidence-ids", `材料引用：${argument.evidenceIds.join("、")}`));
      list.append(item);
    }
    body.append(list);
  }
  appendList(body, "风险与反例", opinion.risks);
  appendList(body, "缺少的证据", opinion.missing);
  card.append(body);
  return card;
}

function renderRun() {
  const run = state.selectedRun;
  const root = $("run-results");
  const previousStream = root.querySelector(".stream-content");
  const sourceState = new Set([...root.querySelectorAll(".source-detail[open]")].map((item) => item.dataset.sourceId));
  const sourcesOpen = root.querySelector(".sources-details")?.open || false;
  const previousScroll = previousStream?.scrollTop || 0;
  const streamAtBottom = !previousStream || previousStream.scrollHeight - previousStream.clientHeight - previousScroll < 45;
  const eventsOpen = root.querySelector(".run-events")?.open || false;
  $("empty-results").hidden = !!run;
  root.hidden = !run;
  root.replaceChildren();
  $("cancel-run").hidden = !isRunning(run);
  $("cancel-run").disabled = state.pending.has("cancel") || isPreview();
  $("cancel-run").classList.toggle("busy", state.pending.has("cancel"));
  $("export-report").disabled = !run?.final;
  if (!run) return;
  const header = node("div", "run-header");
  const headerTop = node("div", "run-header-top");
  headerTop.append(node("h3", "", run.question || "研究会话"), node("span", `run-status ${run.status || ""}`, statusLabels[run.status] || run.status));
  header.append(headerTop);
  const meta = node("div", "run-meta");
  for (const value of [date(run.createdAt), run.model || "服务默认模型", `${array(run.experts).length || state.experts.length} 位专家`, `第 ${run.round || 0} / ${run.maxRounds || "—"} 轮`, `${run.calls || 0}${run.maxCalls ? ` / ${run.maxCalls}` : ""} 次模型调用`]) meta.append(node("span", "", value));
  header.append(meta);
  const progress = node("div", "run-progress");
  let stageIndex = 0;
  if (["debate", "discussion", "revision", "revising"].includes(run.phase) || run.round > 1) stageIndex = 1;
  if (["moderation", "moderating", "synthesis", "final", "finalizing", "completed", "done"].includes(run.phase) || terminalStatuses.has(run.status)) stageIndex = 2;
  ["独立分析", "交叉质询", "综合结论"].forEach((label, index) => {
    if (index) progress.append(node("span", "progress-divider"));
    progress.append(node("span", `progress-stage${index === stageIndex ? " current" : ""}`, label));
  });
  if (isRunning(run)) progress.append(node("span", "progress-stage", phaseLabels[run.phase] || "处理中"));
  header.append(progress);
  root.append(header);
  if (run.error) root.append(node("div", "run-error", text(run.error)));
  if (array(run.materials).length) {
    const sources = node("details", "sources-details events-details");
    sources.open = sourcesOpen;
    sources.append(node("summary", "", `本次参考材料 · ${run.materials.length} 份 · 点击查看引用原文`));
    sources.append(node("p", "source-note", "材料编号可追溯到原文；引用编号存在，不代表原文已经自动核验支持该结论。"));
    for (const material of run.materials) {
      const detail = node("details", "source-detail");
      detail.dataset.sourceId = material.id;
      detail.open = sourceState.has(material.id);
      detail.append(node("summary", "", `${material.id} · ${material.title}`), node("pre", "source-text", material.text));
      sources.append(detail);
    }
    root.append(sources);
  }

  const liveEntries = Object.entries(run.live || {}).filter(([, value]) => value && (value.text || value.status === "running"));
  if (liveEntries.length && isRunning(run)) {
    const stream = node("section", "stream-panel");
    const heading = node("div", "stream-heading");
    heading.append(node("span", "stream-light"), node("span", "", "实时讨论输出"), node("span", "", "· 正在生成，尚未形成最终结论"));
    stream.append(heading);
    const tabs = node("div", "stream-tabs");
    const roleExists = liveEntries.some(([role]) => role === state.streamRole);
    if (!roleExists) state.streamRole = liveEntries.find(([, value]) => value.status === "running")?.[0] || liveEntries[0][0];
    for (const [role, value] of liveEntries) {
      const expert = array(run.experts).find((item) => item.id === role) || state.experts.find((item) => item.id === role);
      const label = expert?.name || ({ moderator: "主持人", final: "综合结论", synthesizer: "综合结论" }[role]) || role;
      const button = node("button", `stream-tab${state.streamRole === role ? " selected" : ""}`, `${label}${value.status === "running" ? " · 生成中" : ""}`);
      button.type = "button";
      button.setAttribute("aria-pressed", String(state.streamRole === role));
      button.addEventListener("click", () => { state.streamRole = role; renderRun(); });
      tabs.append(button);
    }
    stream.append(tabs);
    const value = run.live[state.streamRole];
    const content = node("pre", "stream-content", value?.text || "已发起真实模型请求，等待首个输出…");
    content.tabIndex = 0;
    content.setAttribute("aria-label", "正在生成的专家输出");
    stream.append(content);
    root.append(stream);
    requestAnimationFrame(() => { content.scrollTop = streamAtBottom ? content.scrollHeight : previousScroll; });
  }

  const rounds = array(run.rounds);
  if (rounds.length) {
    if (!rounds.some((round) => round.round === state.selectedRound)) state.selectedRound = rounds[rounds.length - 1].round;
    const tabs = node("div", "round-tabs");
    for (const round of rounds) {
      const tab = node("button", `round-tab${state.selectedRound === round.round ? " selected" : ""}`, `第 ${round.round} 轮${round.round === 1 ? " · 独立观点" : " · 修订意见"}`);
      tab.setAttribute("aria-pressed", String(state.selectedRound === round.round));
      tab.addEventListener("click", () => { state.selectedRound = round.round; renderRun(); });
      tabs.append(tab);
    }
    root.append(tabs);
    const round = rounds.find((item) => item.round === state.selectedRound);
    const opinions = array(round?.opinions);
    const experts = array(run.experts).length ? run.experts : state.experts;
    const grid = node("div", "opinion-grid");
    for (const expert of experts) grid.append(makeOpinionCard(expert, opinions.find((opinion) => opinion.expertId === expert.id)));
    for (const opinion of opinions.filter((item) => !experts.some((expert) => expert.id === item.expertId))) grid.append(makeOpinionCard({ id: opinion.expertId, name: opinion.expertName }, opinion));
    root.append(grid);
    if (round?.moderation) {
      const moderation = round.moderation;
      const panel = node("div", "moderation-panel");
      panel.append(node("h4", "", "本轮主持人整理"));
      if (typeof moderation === "string") panel.append(node("p", "", moderation));
      else {
        if (moderation.summary) panel.append(node("p", "", moderation.summary));
        appendList(panel, "共同判断", moderation.agreement || moderation.agreements || moderation.consensus);
        appendList(panel, "仍有分歧", moderation.disagreements || moderation.disputes);
        appendList(panel, "待补充", moderation.missing || moderation.missingEvidence);
        appendList(panel, "下一轮关注", moderation.questions || moderation.nextQuestions);
        if (panel.children.length === 1) panel.append(node("p", "", JSON.stringify(moderation, null, 2)));
      }
      root.append(panel);
    }
  } else if (isRunning(run) && !liveEntries.length) {
    const pending = node("div", "stream-panel");
    const heading = node("div", "stream-heading");
    heading.append(node("span", "stream-light"), node("span", "", "研究任务已创建，正在准备独立分析…"));
    pending.append(heading);
    root.append(pending);
  }

  if (run.final) root.append(makeFinalReport(run.final, run.status));
  if (array(run.events).length) {
    const details = node("details", "events-details run-events");
    details.open = eventsOpen;
    details.append(node("summary", "", `运行记录 · ${run.events.length} 条事件`));
    const list = node("ol", "event-list");
    for (const event of run.events) {
      const item = node("li");
      item.append(node("span", "event-time", date(event.at, timeFormatter)), node("span", "event-message", event.message || event.type));
      list.append(item);
    }
    details.append(list);
    root.append(details);
  }
}

function makeFinalReport(final, runStatus) {
  const report = node("article", "final-report");
  const heading = node("div", "final-heading");
  const interrupted = ["failed", "cancelled"].includes(runStatus);
  heading.append(node("h3", "", interrupted ? "已保存的阶段结论" : "圆桌研究结论"), node("span", "", "EVIDENCE BEFORE CONSENSUS"));
  const content = node("div", "final-content");
  if (interrupted) content.append(node("p", "run-error", "本次研究未完整结束。以下是中断前已保存的结论，不代表全部讨论已经完成。"));
  content.append(node("p", "final-summary", final.summary || ""));
  const grid = node("div", "final-grid");
  const sections = [
    ["形成的共识", final.agreement, "没有形成明确共识。"],
    ["保留的分歧", final.disagreements, "本次输出未列出保留分歧。"],
    ["仍需补充的证据", final.missing, "本次输出未列出待补充材料。"],
    ["下一步研究", final.nextSteps, "本次输出未列出后续步骤。"],
  ];
  for (const [title, values, empty] of sections) {
    const section = node("section", "final-section");
    const titleNode = node("h4");
    titleNode.append(node("span"), document.createTextNode(title));
    section.append(titleNode);
    appendList(section, "", values, "detail-list", empty);
    grid.append(section);
  }
  content.append(grid);
  report.append(heading, content);
  return report;
}

function openDialog(id) {
  closeSidebar();
  if (id === "experts-dialog") renderExpertEditors();
  if (id === "connection-dialog") refreshStatus();
  const dialog = $(id);
  if (!dialog.open) dialog.showModal();
}

function closeSidebar() {
  document.body.classList.remove("sidebar-open");
  $("menu-button").setAttribute("aria-expanded", "false");
  $("sidebar-scrim").hidden = true;
}

async function startLogin(type) {
  if (isPreview()) return toast("请在本机启动项目后连接 ChatGPT。", true);
  setPending("auth", true);
  try {
    state.login = await api("/api/auth/start", { method: "POST", body: { type } });
    await refreshStatus();
    renderConnectionControls();
  } catch (error) { toast(error.message, true); }
  finally { setPending("auth", false); }
}

async function cancelLogin() {
  if (!state.login) return;
  setPending("auth", true);
  try {
    await api("/api/auth/cancel", { method: "POST", body: { loginId: state.login.loginId } });
    state.login = null;
    await refreshStatus();
  } catch (error) { toast(error.message, true); }
  finally { setPending("auth", false); }
}

async function verifyConnection() {
  setPending("verify", true);
  try {
    const model = $("model-select").value;
    const verification = await api("/api/verify", { method: "POST", body: model ? { model } : {} });
    if (state.status) state.status.verification = verification.verification || verification;
    await refreshStatus();
    toast(state.status?.verification?.passed ? "真实连接与会话恢复验证通过，可以开始研究了。" : "验证尚未通过，请查看返回信息。", !state.status?.verification?.passed);
    const models = await api("/api/models");
    state.models = array(models.models);
    renderModels();
  } catch (error) {
    if (state.status) state.status.verification = { passed: false, error: error.message };
    toast(error.message, true);
  } finally { setPending("verify", false); renderStatus(); }
}

async function startResearch(event) {
  event.preventDefault();
  if ($("start-research").disabled) return;
  if ($("material-text").value.trim() && addPastedMaterial() === false) return;
  setPending("start", true);
  try {
    const model = $("model-select").value;
    const result = await api("/api/runs", {
      method: "POST",
      body: {
        question: $("question").value.trim(),
        materials: state.materials.map((material) => ({ title: material.title, text: material.text })),
        maxRounds: Number($("round-select").value),
        ...(model ? { model } : {}),
      },
    });
    state.selectedRun = result.run;
    state.selectedRound = null;
    state.streamRole = null;
    upsertRun(result.run);
    if (state.status) state.status.activeRunId = result.run.id;
    renderRun();
    renderHistory();
    $("results-section").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) { toast(error.message, true); }
  finally { setPending("start", false); }
}

async function cancelRun() {
  const run = state.selectedRun;
  if (!run || !isRunning(run)) return;
  setPending("cancel", true);
  renderRun();
  try {
    const result = await api(`/api/runs/${encodeURIComponent(run.id)}/cancel`, { method: "POST", body: {} });
    if (result.run) { state.selectedRun = result.run; upsertRun(result.run); }
    else {
      const latest = await api(`/api/runs/${encodeURIComponent(run.id)}`);
      state.selectedRun = latest.run;
      upsertRun(latest.run);
    }
    await refreshStatus();
    renderRun();
    renderHistory();
    toast("停止请求已提交，已完成的研究内容会保留。");
  } catch (error) { toast(error.message, true); }
  finally { setPending("cancel", false); renderRun(); }
}

async function saveExperts(event) {
  event.preventDefault();
  setPending("experts", true);
  try {
    const experts = [...$("expert-editors").querySelectorAll(".expert-editor")].map((editor) => ({
      id: editor.dataset.expertId,
      name: editor.querySelector('[name="name"]').value.trim(),
      focus: editor.querySelector('[name="focus"]').value.trim(),
      instructions: editor.querySelector('[name="instructions"]').value.trim(),
    }));
    const result = await api("/api/experts", { method: "PUT", body: { experts } });
    state.experts = array(result.experts).length ? result.experts : experts;
    renderExperts();
    $("experts-dialog").close();
    toast("专家方法已保存，将用于下一项研究。");
  } catch (error) { toast(error.message, true); }
  finally { setPending("experts", false); }
}

function newSession() {
  state.selectedRun = null;
  state.selectedRound = null;
  state.streamRole = null;
  state.materials = [];
  $("question").value = "";
  $("material-text").value = "";
  $("material-title").value = "";
  renderMaterials();
  renderHistory();
  renderRun();
  renderAvailability();
  closeSidebar();
  window.scrollTo({ top: 0, behavior: "smooth" });
  $("question").focus({ preventScroll: true });
}

function connectEvents() {
  if (state.eventSource) state.eventSource.close();
  const source = new EventSource("/api/events");
  state.eventSource = source;
  source.onmessage = (event) => {
    let payload;
    try { payload = JSON.parse(event.data); } catch { return; }
    if (payload.type === "status") { refreshStatus(); return; }
    if (payload.type === "run" && payload.run) {
      const previousStatus = state.runs.find((run) => run.id === payload.run.id)?.status;
      upsertRun(payload.run);
      if (state.selectedRun?.id === payload.run.id) { state.selectedRun = payload.run; renderRun(); }
      if (terminalStatuses.has(payload.run.status) && previousStatus !== payload.run.status) {
        refreshStatus();
        toast(payload.run.status === "failed" ? "研究未完成，请查看运行记录。" : `研究状态：${statusLabels[payload.run.status]}`, payload.run.status === "failed");
      }
      renderHistory();
      renderAvailability();
    }
    if (payload.type === "delta" && payload.runId === state.selectedRun?.id && payload.delta) {
      const run = state.selectedRun;
      run.live ||= {};
      const role = payload.role || "moderator";
      run.live[role] ||= { text: "", status: "running" };
      run.live[role].text += payload.delta;
      renderRun();
    }
  };
  source.onopen = () => { refreshStatus(); refreshRuns().catch(() => {}); };
}

$("research-form").addEventListener("submit", startResearch);
$("question").addEventListener("input", renderAvailability);
$("material-text").addEventListener("input", renderAvailability);
$("round-select").addEventListener("change", renderAvailability);
$("model-select").addEventListener("change", renderStatus);
$("add-material").addEventListener("click", addPastedMaterial);
$("new-session").addEventListener("click", newSession);
$("nav-workbench").addEventListener("click", () => { closeSidebar(); window.scrollTo({ top: 0, behavior: "smooth" }); });
$("open-experts").addEventListener("click", () => openDialog("experts-dialog"));
$("edit-experts-inline").addEventListener("click", () => openDialog("experts-dialog"));
for (const id of ["open-connection", "open-connection-nav", "readiness-action"]) $(id).addEventListener("click", () => openDialog("connection-dialog"));
for (const button of document.querySelectorAll("[data-close]")) button.addEventListener("click", () => $(button.dataset.close).close());
for (const dialog of document.querySelectorAll("dialog")) {
  dialog.addEventListener("click", (event) => {
    if (event.target !== dialog) return;
    const rect = dialog.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
  });
}
$("login-device").addEventListener("click", () => startLogin("chatgptDeviceCode"));
$("login-browser").addEventListener("click", () => startLogin("chatgpt"));
$("cancel-login").addEventListener("click", cancelLogin);
$("verify-connection").addEventListener("click", verifyConnection);
$("experts-form").addEventListener("submit", saveExperts);
$("cancel-run").addEventListener("click", cancelRun);
$("export-report").addEventListener("click", () => {
  if (!state.selectedRun?.final) return;
  const link = node("a");
  link.href = `/api/runs/${encodeURIComponent(state.selectedRun.id)}/report`;
  link.download = `研究报告-${state.selectedRun.id}.md`;
  document.body.append(link);
  link.click();
  link.remove();
});
$("logout").addEventListener("click", async () => {
  setPending("auth", true);
  try { await api("/api/auth/logout", { method: "POST", body: {} }); await refreshStatus(); toast("已退出本机 Codex 登录。"); }
  catch (error) { toast(error.message, true); }
  finally { setPending("auth", false); }
});
$("copy-code").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText(state.login?.code || ""); toast("设备代码已复制。"); }
  catch { toast("无法访问剪贴板，请手动复制显示的设备代码。", true); }
});
$("material-upload").addEventListener("change", async (event) => {
  for (const file of event.target.files) {
    if (!/\.(txt|md|markdown)$/i.test(file.name)) { toast(`仅支持 TXT / MD 文本：${file.name}`, true); continue; }
    if (file.size > 1024 * 1024) { toast(`单份材料请控制在 1 MB 以内：${file.name}`, true); continue; }
    try {
      const content = (await file.text()).trim();
      if (!content) { toast(`这份材料没有文本内容：${file.name}`, true); continue; }
      if (!canAddMaterial(content)) continue;
      state.materials.push({ title: file.name.slice(0, 200), text: content });
    } catch { toast(`无法读取文件：${file.name}`, true); }
  }
  event.target.value = "";
  renderMaterials();
});
$("menu-button").addEventListener("click", () => {
  const open = !document.body.classList.contains("sidebar-open");
  document.body.classList.toggle("sidebar-open", open);
  $("menu-button").setAttribute("aria-expanded", String(open));
  $("sidebar-scrim").hidden = !open;
});
$("sidebar-scrim").addEventListener("click", closeSidebar);
document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeSidebar(); });

async function initialize() {
  const results = await Promise.allSettled([
    refreshStatus(),
    api("/api/experts").then((result) => { state.experts = array(result.experts); renderExperts(); }),
    api("/api/models").then((result) => { state.models = array(result.models); renderModels(); }),
    refreshRuns(),
  ]);
  for (const [index, result] of results.entries()) {
    if (result.status === "rejected") {
      if (index === 1) renderExperts();
      if (index !== 2) console.warn("工作台初始化未完成：", result.reason?.message || result.reason);
    }
  }
  state.loading = false;
  if (state.status?.activeRunId) await selectRun(state.status.activeRunId, false);
  renderAvailability();
  connectEvents();
}

initialize();
setInterval(() => {
  if (document.visibilityState === "hidden" && !state.login) return;
  if (state.login || $("connection-dialog").open) refreshStatus();
}, 2500);
setInterval(() => {
  if (document.visibilityState === "hidden") return;
  refreshStatus();
  if (currentActiveRun()) refreshRuns().catch(() => {});
}, 12000);
window.addEventListener("beforeunload", () => state.eventSource?.close());
