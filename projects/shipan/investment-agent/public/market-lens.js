/**
 * Local handoff UI: receive one frozen Market Lens bundle, ask this computer's
 * existing verified council to analyse it, and return only that run's result.
 * Real inputs: /api/status, /api/market-lens/profile, a bundle from the hosted
 * opener or the file picker. Before: no run. After explicit Start: a local run
 * is saved and the opener receives redacted results. No login/model call occurs
 * automatically. Browser/live-account execution remains unexecuted until the
 * owner starts it; protocol regression examples live in the matching test file.
 */
import { PROTOCOL, readChannel, acceptsMessage, validateBundle, publicRun } from './market-lens-protocol.mjs';

const $ = id => document.getElementById(id);
const pair = readChannel(location.hash);
const openerWindow = window.opener;
const linked = Boolean(pair && openerWindow);
let bundle = null;
let status = null;
let profile = null;
let currentRun = null;
let launchBusy = false;
let checking = false;
let received = false;
let source = null;

/** Render untrusted text as text nodes; never interpret model/material HTML. */
function node(tag, value = '', className = '') {
  const element = document.createElement(tag);
  if (value !== '') element.textContent = String(value);
  if (className) element.className = className;
  return element;
}

/** Send a whitelisted envelope only to the original paired opener. */
function send(type, payload = {}) {
  if (!linked || openerWindow.closed) return;
  openerWindow.postMessage({ protocol: PROTOCOL, channel: pair.channel, type, ...payload }, pair.origin);
}

/** Show an explicit local error and optionally share its user-facing message. */
function error(message, share = true) {
  $('error').hidden = !message;
  $('error').textContent = message || '';
  if (message && share) send('error', { message: String(message) });
}

/** Keep CSRF solely in local memory and attach it only to same-origin mutations. */
async function api(route, body) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 60000);
  try {
    const response = await fetch(route, {
      method: body === undefined ? 'GET' : 'POST',
      headers: body === undefined ? {} : { 'Content-Type': 'application/json', 'X-Expert-CSRF': status?.csrfToken || '' },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      signal: controller.signal,
      credentials: 'same-origin',
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `本地请求失败（${response.status}）`);
    return data;
  } catch (cause) {
    if (cause.name === 'AbortError') throw new Error('本地 Agent 响应超时，请检查程序是否仍在运行。');
    throw cause;
  } finally { clearTimeout(timeout); }
}

/** Format supplied timestamps for display without changing their source semantics. */
function time(value) {
  if (!value) return '源时间未知';
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? date.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) : String(value);
}

/** Describe only the observed data source/status; absent metadata stays explicit. */
function historyDetail(history) {
  const labels = { live: '源数据', real: '源数据', ok: '源数据', cached: '缓存', stale: '源时间较旧', unavailable: '不可用', demo: '合成演示', loading: '尚在加载' };
  return `${history.bars.length} 条 · ${history.source || '来源未标注'} · ${labels[history.status] || history.status || '状态未标注'} · ${time(history.as_of)}`;
}

/** Derive availability of Start from actual local auth/verification plus a loaded bundle. */
function updateControls() {
  const running = currentRun?.status === 'running';
  const occupied = status?.activeRunId && status.activeRunId !== currentRun?.id;
  const ready = status?.auth?.connected && status?.verification?.passed && !status?.verifying;
  $('start').disabled = !bundle || !profile || !ready || occupied || running || launchBusy;
  $('start').textContent = launchBusy ? '正在发起会审…' : running ? '三专家正在会审…' : '开始三专家会审 ↗';
  $('cancel').hidden = !running;
  $('bundle-file').disabled = running || launchBusy;
  $('max-rounds').disabled = running || launchBusy;
}

/** Render the actual installed profile rather than hard-coded claimed expert states. */
function renderProfile() {
  const experts = Array.isArray(profile?.experts) ? profile.experts : [];
  $('experts').replaceChildren(...experts.map((expert, index) => {
    const card = node('article', '', 'expert');
    card.append(node('span', `0${index + 1} / EXPERT`, 'expert-number'), node('h3', expert.name), node('p', expert.focus));
    const count = Array.isArray(expert.files) ? expert.files.filter(file => file.loaded_for_analysis).length : 0;
    card.append(node('small', `Skill ${expert.skill_version || '版本未标注'} · ${count} 份文本已装载`));
    return card;
  }));
  const count = Number.isInteger(profile?.knowledge?.count) ? profile.knowledge.count : 0;
  $('profile-note').textContent = `宏观参考库：${count} 条精选方法摘要。两份看盘 Skill 的重叠来源不重复计票；资料摘要不等于当前行情事实。`;
}

/** Check existing login/verification without initiating login, verification or inference. */
async function checkConnection() {
  if (checking) return;
  checking = true;
  $('recheck').disabled = true;
  try {
    const [nextStatus, nextProfile] = await Promise.all([api('/api/status'), api('/api/market-lens/profile')]);
    if (!Array.isArray(nextProfile.experts) || nextProfile.experts.length !== 3) throw new Error('本地 Agent 尚未加载本次三专家配置，请安装观市配套版本。');
    status = nextStatus;
    profile = nextProfile;
    const connected = Boolean(status.auth?.connected);
    const verified = connected && Boolean(status.verification?.passed) && !status.verifying;
    const occupied = status.activeRunId && status.activeRunId !== currentRun?.id;
    const message = !connected ? '尚未登录。请打开本地圆桌，完成 ChatGPT 登录与连接验证。'
      : status.verifying ? '正在验证连接，请等待验证完成。'
        : !verified ? '已登录，尚未通过连接验证。请在本地圆桌完成验证。'
          : occupied ? '连接已验证；另一个研究任务正在运行，请稍后再开始。'
            : '已通过 ChatGPT 连接验证，可发起三专家会审。';
    $('connection-state').textContent = message;
    send('state', { connected, verified, message });
    renderProfile();
  } catch (cause) {
    status = null;
    profile = null;
    const message = `暂未就绪：${cause.message}`;
    $('connection-state').textContent = message;
    send('state', { connected: false, verified: false, message });
  } finally { checking = false; $('recheck').disabled = false; updateControls(); }
}

/** Freeze a validated received bundle locally; a running task cannot be replaced. */
function acceptBundle(value, fromFile = false) {
  if (launchBusy || currentRun?.status === 'running') throw new Error('本次会审仍在运行，请完成或停止后再接收新资料。');
  const next = validateBundle(value);
  bundle = next;
  currentRun = null;
  received = true;
  error('');
  $('empty-bundle').hidden = true;
  $('bundle-detail').hidden = false;
  $('results-section').hidden = true;
  $('report').hidden = true;
  $('instrument-name').textContent = bundle.selected.name;
  $('instrument-meta').textContent = `${bundle.selected.symbol} · ${bundle.selected.kind === 'etf' ? 'ETF' : '指数'}`;
  $('data-mode').textContent = bundle.mode === 'demo' ? '合成演示 · 非真实市场' : '真实源快照 · 请核对时间';
  $('data-mode').className = bundle.mode === 'demo' ? 'pill demo' : 'pill';
  $('analysis-asof').textContent = time(bundle.analysis_asof);
  $('price-detail').textContent = historyDetail(bundle.price_history);
  $('flow-detail').textContent = historyDetail(bundle.fund_flow_history);
  $('bundle-id').textContent = bundle.bundle_id;
  $('question').textContent = bundle.question;
  const gaps = [];
  if (!bundle.price_history.bars.length) gaps.push('价格历史缺失。');
  if (!bundle.fund_flow_history.bars.length) gaps.push('资金分时缺失。');
  if (bundle.mode === 'demo') gaps.push('本包全部用于演示流程，不能作为真实投资判断。');
  gaps.push('主力资金为行情商成交分类，不代表机构账户身份或 ETF 申赎。');
  gaps.push('宏观与企业基本面仍需独立资料；专家将保留相关证据缺口。');
  $('data-limitations').textContent = gaps.join('\n');
  $('channel-state').textContent = fromFile ? '已从文件载入冻结快照；本地报告可导出。' : '已接收观市冻结快照；本次会审结果将回显到原页面。';
  send('state', { connected: Boolean(status?.auth?.connected), verified: Boolean(status?.auth?.connected && status?.verification?.passed), message: '资料已接收，请在本地窗口点击「开始三专家会审」。' });
  updateControls();
}

/** Build a text-only list for structured report sections. */
function listSection(parent, heading, values, headingTag = 'h5') {
  if (!Array.isArray(values) || !values.length) return;
  parent.append(node(headingTag, heading));
  const list = node('ul');
  for (const value of values) list.append(node('li', value));
  parent.append(list);
}

/** Show structured expert and moderator conclusions with their evidence IDs. */
function renderRun(run) {
  const labels = { running: '会审中', completed: '研究会审完成', disputed: '保留分歧', needs_evidence: '需要补充证据', cancelled: '已停止', failed: '会审未完成', interrupted: '任务中断' };
  const phases = { queued: '等待开始', independent: '独立研究', discussion: '质询与修订', moderating: '主持综合' };
  $('results-section').hidden = false;
  $('run-state').textContent = `${labels[run.status] || run.status} · 第 ${run.round}/${run.maxRounds} 轮 · ${phases[run.phase] || run.phase} · ${run.calls} 次模型调用`;
  $('rounds').replaceChildren(...run.rounds.map(round => {
    const section = node('section', '', 'round');
    section.append(node('h3', `第 ${round.round} 轮`));
    if (!round.opinions.length) section.append(node('p', '专家正在独立研究；结构化意见完成后显示。', 'muted'));
    const grid = node('div', '', 'opinions');
    for (const item of round.opinions) {
      const card = node('article', '', 'opinion');
      const stance = { support: '证据支持', oppose: '保留反对', uncertain: '尚不能判断' }[item.stance] || item.stance;
      card.append(node('h4', item.expertName), node('span', stance, 'stance'), node('p', item.summary));
      if (item.arguments.length) {
        card.append(node('h5', '依据'));
        const list = node('ul');
        for (const argument of item.arguments) {
          const li = node('li', argument.claim);
          li.append(node('small', argument.evidenceIds.length ? `材料：${argument.evidenceIds.join('、')}` : '无材料引用 · 待核验', 'evidence'));
          list.append(li);
        }
        card.append(list);
      }
      listSection(card, '风险与异议', item.risks);
      listSection(card, '缺失资料', item.missing);
      grid.append(card);
    }
    section.append(grid);
    if (round.moderation) {
      const moderation = node('div', '', 'moderation');
      moderation.append(node('h4', '主持人 · 本轮整理'), node('p', round.moderation.summary));
      listSection(moderation, '保留分歧', round.moderation.disagreements);
      listSection(moderation, '缺失证据', round.moderation.missing);
      section.append(moderation);
    }
    return section;
  }));
  $('final').hidden = !run.final;
  $('final').replaceChildren();
  if (run.final) {
    $('final').append(node('h3', `主持总结 · ${labels[run.final.status] || run.final.status}`), node('p', run.final.summary));
    listSection($('final'), '共识', run.final.agreement, 'h4');
    listSection($('final'), '分歧', run.final.disagreements, 'h4');
    listSection($('final'), '证据缺口', run.final.missing, 'h4');
    listSection($('final'), '下一步', run.final.nextSteps, 'h4');
  }
  $('report').hidden = run.status === 'running';
  $('report').href = `/api/runs/${encodeURIComponent(run.id)}/report`;
  if (run.error) error(run.error);
  updateControls();
}

/** Accept only the task this window launched, excluding other local history/SSE runs. */
function acceptRun(value, initial = false) {
  if (!initial && value?.id !== currentRun?.id) return;
  const result = publicRun(value);
  // A reconciliation GET can finish after a newer SSE event. Preserve the newer
  // task state, especially a terminal result, instead of reviving a finished run.
  if (!initial && currentRun) {
    const previousTime = Date.parse(currentRun.updatedAt);
    const nextTime = Date.parse(result.updatedAt);
    if (Number.isFinite(previousTime) && Number.isFinite(nextTime) && nextTime < previousTime) return;
    if (currentRun.status !== 'running' && result.status === 'running') return;
  }
  currentRun = result;
  renderRun(result);
  send('run', { run: result, bundle_id: bundle.bundle_id });
}

/** User action: start a real council run using the model that passed local verification. */
async function startRun() {
  if ($('start').disabled || !bundle) return;
  launchBusy = true;
  error('');
  updateControls();
  try {
    const response = await api('/api/market-lens/runs', { bundle, maxRounds: Number($('max-rounds').value), model: status.verification.model || '' });
    acceptRun(response.run, true);
    // The first SSE frame can arrive before the POST response binds the run ID.
    // Fetch this single ID once to reconcile that race, without exposing history.
    const latest = await api(`/api/runs/${encodeURIComponent(currentRun.id)}`);
    acceptRun(latest.run);
  } catch (cause) { error(cause.message); }
  finally { launchBusy = false; updateControls(); }
}

/** Cancel only the run launched in this popup; no arbitrary run IDs are accepted. */
async function cancelRun() {
  if (!currentRun || currentRun.status !== 'running') return;
  $('cancel').disabled = true;
  try { await api(`/api/runs/${encodeURIComponent(currentRun.id)}/cancel`, {}); }
  catch (cause) { error(cause.message); }
  finally { $('cancel').disabled = false; }
}

/** Reconnectable same-origin event stream; never forwards login or unrelated run payloads. */
function watchEvents() {
  source = new EventSource('/api/events');
  source.onmessage = event => {
    try {
      const data = JSON.parse(event.data);
      if (data.type === 'run' && data.run?.id === currentRun?.id) acceptRun(data.run);
      if (data.type === 'status') checkConnection();
    } catch { /* An invalid frame is not interpreted as a completed result. */ }
  };
  source.onopen = async () => {
    if (!currentRun) return;
    try { const data = await api(`/api/runs/${encodeURIComponent(currentRun.id)}`); acceptRun(data.run); }
    catch (cause) { error(`恢复本次会审进展失败：${cause.message}`); }
  };
  source.onerror = () => {
    if (currentRun?.status === 'running') $('run-state').textContent = '本地事件连接暂时中断，正在重连；任务状态尚未确认。';
  };
}

window.addEventListener('message', event => {
  if (!acceptsMessage(event, pair, openerWindow)) return;
  if (event.data.type === 'bundle') {
    try { acceptBundle(event.data.bundle); } catch (cause) { error(cause.message); }
  } else if (event.data.type === 'cancel' && event.data.bundle_id === bundle?.bundle_id) cancelRun();
});
$('recheck').addEventListener('click', checkConnection);
$('start').addEventListener('click', startRun);
$('cancel').addEventListener('click', cancelRun);
$('bundle-file').addEventListener('change', async event => {
  const file = event.target.files?.[0];
  if (!file) return;
  try {
    // Pretty-printed exports are larger than their compact HTTP representation.
    // Bound the file read first; validateBundle checks the actual compact bytes.
    if (file.size > 1000000) throw new Error('数据包过大，请从观市重新导出当前标的。');
    acceptBundle(JSON.parse(await file.text()), true);
  } catch (cause) { error(`导入失败：${cause.message}`); }
  finally { event.target.value = ''; }
});
window.addEventListener('pagehide', () => source?.close());
document.addEventListener('visibilitychange', () => { if (!document.hidden) checkConnection(); });

$('channel-state').textContent = linked
  ? '已建立与发起页面的专用通道，等待冻结快照。'
  : '当前是独立窗口。可导入观市 JSON 数据包，或从观市重新打开。';
send('ready');
let handshakeAttempts = 0;
const handshake = setInterval(() => {
  if (received || !linked || ++handshakeAttempts > 5) { clearInterval(handshake); return; }
  send('ready');
}, 1500);
checkConnection();
watchEvents();
