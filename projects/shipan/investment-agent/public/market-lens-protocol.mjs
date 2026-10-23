/**
 * Market Lens popup protocol. Input: browser MessageEvents and frozen JSON bundles.
 * Output: validated channel/bundle or an explicit error; outgoing runs contain only
 * review results. No credentials, source prompts, saved history or thread IDs leave
 * the local app. Runnable examples and observed assertions: tests/market-lens-protocol.test.mjs.
 * These pure functions never mutate their input or perform network/model calls.
 */
export const PROTOCOL = 'market-lens.council.v1';
export const SITE_ORIGIN = 'https://market-lens-ivy.glad-lamp-2848.chatgpt.site';

/** Accept the deployed owner-private Site or an exact HTTP loopback origin. */
export function allowedOrigin(value) {
  if (value === SITE_ORIGIN) return true;
  try {
    const url = new URL(value);
    return url.origin === value && url.protocol === 'http:' &&
      ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) &&
      !url.username && !url.password;
  } catch { return false; }
}

/** Read the one-use channel from a popup hash; invalid/unpaired pages stay file-only. */
export function readChannel(hash) {
  const params = new URLSearchParams(String(hash).replace(/^#/, ''));
  const origin = params.get('origin');
  const channel = params.get('channel');
  return allowedOrigin(origin) && /^[A-Za-z0-9_-]{16,128}$/.test(channel || '')
    ? { origin, channel } : null;
}

/** Bind messages to the original opener, exact origin and per-opening random nonce. */
export function acceptsMessage(event, pair, opener) {
  return Boolean(pair && opener && event.source === opener && event.origin === pair.origin &&
    event.data && typeof event.data === 'object' && !Array.isArray(event.data) &&
    event.data.protocol === PROTOCOL && event.data.channel === pair.channel &&
    ['bundle', 'cancel'].includes(event.data.type));
}

const object = value => value && typeof value === 'object' && !Array.isArray(value);
const strings = value => Array.isArray(value) ? value.filter(item => typeof item === 'string') : [];
const text = value => typeof value === 'string' ? value : '';

/** Validate a received/file-imported bundle before displaying it; server validates again. */
export function validateBundle(value) {
  if (!object(value) || value.schema_version !== PROTOCOL) throw new Error('请导入观市导出的投资 Agent 数据包。');
  let serialized;
  try { serialized = JSON.stringify(value); } catch { throw new Error('数据包不是有效的 JSON 对象。'); }
  if (new TextEncoder().encode(serialized).length > 250000) throw new Error('数据包过大，请从观市重新导出当前标的。');
  if (typeof value.bundle_id !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(value.bundle_id)) throw new Error('数据包缺少有效的批次编号。');
  if (value.timezone !== 'Asia/Shanghai' || !['real', 'demo'].includes(value.mode)) throw new Error('数据模式或时区无效。');
  if (typeof value.analysis_asof !== 'string' || !Number.isFinite(Date.parse(value.analysis_asof))) throw new Error('数据包缺少有效的分析截至时间。');
  if (!object(value.selected) || !/^\d{6}$/.test(value.selected.symbol) || !['index', 'etf'].includes(value.selected.kind) || typeof value.selected.name !== 'string') throw new Error('当前观察标的无效。');
  if (typeof value.question !== 'string' || !value.question.trim() || value.question.length > 6000) throw new Error('研究问题需为 1–6000 字。');
  if (!object(value.snapshot)) throw new Error('数据包缺少行情快照。');
  for (const key of ['price_history', 'fund_flow_history']) {
    const history = value[key];
    if (!object(history) || !Array.isArray(history.bars) || history.bars.length > 260 || history.bars.some(bar => !object(bar))) throw new Error('价格与资金历史必须使用最多 260 条原始记录。');
    if (history.symbol && history.symbol !== value.selected.symbol) throw new Error('历史记录与当前标的不一致，请重新导出。');
    if (history.kind && history.kind !== value.selected.kind) throw new Error('历史记录与当前品种不一致，请重新导出。');
  }
  return JSON.parse(serialized);
}

/** Copy only the structured moderator output; do not forward arbitrary nested fields. */
function moderation(value) {
  if (!object(value)) return null;
  return { summary: text(value.summary), agreement: strings(value.agreement), disagreements: strings(value.disagreements),
    missing: strings(value.missing), nextSteps: strings(value.nextSteps), status: text(value.status) };
}

/** Copy only structured expert output, excluding raw streams/prompts/account metadata. */
function opinion(value) {
  return { expertId: text(value.expertId), expertName: text(value.expertName), stance: text(value.stance),
    summary: text(value.summary), arguments: (Array.isArray(value.arguments) ? value.arguments : []).filter(object).map(argument => ({
      claim: text(argument.claim), evidenceIds: strings(argument.evidenceIds),
    })), risks: strings(value.risks), missing: strings(value.missing),
    confidence: Number.isFinite(value.confidence) ? value.confidence : null };
}

/** Sanitize the single run initiated in this popup; other runs are ignored by the caller. */
export function publicRun(run) {
  if (!object(run) || typeof run.id !== 'string' || !/^[A-Za-z0-9-]+$/.test(run.id)) throw new Error('研究任务编号无效。');
  const result = {};
  for (const key of ['id', 'status', 'phase', 'createdAt', 'updatedAt', 'question']) result[key] = text(run[key]);
  for (const key of ['round', 'maxRounds', 'calls']) result[key] = Number.isFinite(run[key]) ? run[key] : 0;
  result.rounds = (Array.isArray(run.rounds) ? run.rounds : []).filter(object).map(round => ({
    round: Number.isFinite(round.round) ? round.round : 0,
    opinions: (Array.isArray(round.opinions) ? round.opinions : []).filter(object).map(opinion),
    moderation: moderation(round.moderation),
  }));
  result.final = moderation(run.final);
  result.error = typeof run.error === 'string' ? run.error : null;
  if (object(run.marketLens)) {
    const meta = run.marketLens;
    result.marketLens = {};
    for (const key of ['bundle_id', 'analysis_asof', 'timezone', 'mode', 'profile_version']) {
      if (typeof meta[key] === 'string') result.marketLens[key] = meta[key];
    }
    if (object(meta.selected)) result.marketLens.selected = {
      symbol: text(meta.selected.symbol), kind: text(meta.selected.kind), name: text(meta.selected.name),
    };
  }
  return result;
}
