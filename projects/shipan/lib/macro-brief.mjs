import { shanghaiDate } from '../public/market-time.mjs';

export const MACRO_SOURCE_PAGE = '/macro-source.html';
export const MACRO_HANDBOOK_REF = '/macro-handbook.html';
export const MACRO_MAX_BYTES = 128 * 1024;
const LIMITS = { title: 160, stance: 200, summary: 8000, dimension: 4000, item: 2000, url: 2048 };

/** Shared unattended contract. Read current protocol before each run; all dates
 * are data, never source constants. Real-input examples: docs/MACRO_API.md. */
export const MACRO_PROTOCOL = Object.freeze({
  version: 1,
  timezone: 'Asia/Shanghai',
  purpose: '先更新宏观资料，再依研究手册生成有出处的每日宏观先导，最后同步观市行情并分别读回验证。',
  sourcePageUrl: MACRO_SOURCE_PAGE,
  sourcePackageId: 'configure-local-macro-data',
  handbookId: 'configure-local-handbook',
  workflowPath: '/macro-workflow.json',
  workflowUrl: '/api/refresh',
  write: { method: 'POST', path: '/api/macro', body: '{"brief":{"reportDate":"YYYY-MM-DD","generatedAt":"ISO timestamp with timezone","title":"...","stance":"...","summary":"...","dimensions":[{"name":"...","signal":"...","evidence":"..."}],"watchpoints":["..."],"risks":["..."],"sources":[{"title":"...","url":"https://... or library-file:...","asOf":"source date/period or null"}],"dataStatus":"complete or partial","sourcePageUrl":"macro Page URL","handbookUrl":"/macro-handbook.html"}}' },
  failure: { method: 'POST', path: '/api/macro', body: '{"failure":{"reportDate":"YYYY-MM-DD","attemptedAt":"ISO timestamp with timezone","message":"actual failed steps and preserved data"}}' },
  readback: { method: 'GET', path: '/api/macro', check: ['brief.reportDate', 'brief.generatedAt', 'brief.sources[].asOf', 'latestAttempt', 'freshness'] },
  requirements: [
    '经 Sites get_site 取得当次服务访问权限，仅向本站发送；不把凭据写入文件、日志或消息。',
    '每次读取资料空间、Agent Instructions和最新版数据包；按Library流程更新同一数据包，先核验真实来源日期、缺口和修订。',
    '读取研究手册，区分事实、推断、支持和反证；结论必须有来源，不用固定模板伪造经济判断。',
    '报告日期和生成时间不等于源数据日期；月度季度指标未新发布时保持原所属期，不当成当日新增证据。',
    '盘前行情沿用最近真实交易日，盘中值不标成收盘；源失败、未发布、过期、零值分别表述。',
    '部分来源缺失但有充分事实可形成报告时写 dataStatus=partial 并列出缺口；无法形成结论时只写 failure，保留上次报告。',
    'POST 完成后独立 GET 读回，确认 reportDate、generatedAt 和 sources；HTTP 200 不代表报告已更新。',
    '同一报告日期只接受更晚的生成时间，重试同一正文不增加记录；普通运行不修改源码或发布网站。',
  ],
});

/** Strict plain-text normalization. Arguments and executable examples follow
 * every exported operation in docs/MACRO_API.md. Throws a field-specific error. */
function text(value, field, max) {
  if (typeof value !== 'string' || !value.trim() || value.trim().length > max) throw new Error(`${field} 必须为 1—${max} 字符的文本`);
  return value.trim();
}

function date(value, field, today) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value) || shanghaiDate(value) !== value || value > today) throw new Error(`${field} 必须为有效且不晚于今天的北京时间日期`);
  return value;
}

function timestamp(value, field, now, reportDate) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?(?:Z|[+-]\d{2}:\d{2})$/.test(value) || !shanghaiDate(value)) throw new Error(`${field} 必须带时区`);
  const epoch = Date.parse(value);
  if (!Number.isFinite(epoch) || epoch > now || shanghaiDate(value) < reportDate) throw new Error(`${field} 不能在未来或早于报告日期`);
  return new Date(epoch).toISOString();
}

function link(value, field) {
  const result = text(value, field, LIMITS.url);
  if (/^library-file:libfile_[a-zA-Z0-9]+$/.test(result) || /^\/(?!\/)[\w./?=&%#-]*$/.test(result)) return result;
  let parsed;
  try { parsed = new URL(result); } catch { throw new Error(`${field} 必须为有效来源链接`); }
  if (parsed.protocol !== 'https:' || parsed.username || parsed.password) throw new Error(`${field} 仅支持 HTTPS、本站路径或 Library 文件引用`);
  return result;
}

function items(value, field, max = 20) {
  if (!Array.isArray(value) || value.length > max) throw new Error(`${field} 必须为最多 ${max} 项的数组`);
  return value.map((item, index) => text(item, `${field}[${index}]`, LIMITS.item));
}

function sourcePeriod(value, field, today, now) {
  if (value === null) return null;
  const result = text(value, field, 80);
  // Preserve source period, rather than borrowing the report generation day.
  if (/^\d{4}-\d{2}-\d{2}$/.test(result)) return date(result, field, today);
  if (/^\d{4}-(0[1-9]|1[0-2])$/.test(result) && result <= today.slice(0, 7)) return result;
  if (/^\d{4}-Q[1-4]$/.test(result) && result <= `${today.slice(0, 4)}-Q${Math.ceil(Number(today.slice(5, 7)) / 3)}`) return result;
  if (/^\d{4}$/.test(result) && result <= today.slice(0, 4)) return result;
  if (/T.*(?:Z|[+-]\d{2}:\d{2})$/.test(result) && shanghaiDate(result) && Date.parse(result) <= now) return result;
  throw new Error(`${field} 必须为实际源日期、时间、年月、年季度、年份或 null`);
}

/** Validate and normalize one brief or failed attempt; no arbitrary model calls,
 * URLs are references only. Empty writes never reach durable storage. */
export function normalizeMacroWrite(body, now = Date.now()) {
  if (!body || typeof body !== 'object' || Array.isArray(body) || Boolean(body.brief) === Boolean(body.failure)) throw new Error('必须且只能提供 brief 或 failure');
  const today = shanghaiDate(now);
  if (body.failure) {
    const failure = body.failure;
    const reportDate = date(failure.reportDate, 'failure.reportDate', today);
    return { failure: { reportDate, attemptedAt: timestamp(failure.attemptedAt, 'failure.attemptedAt', now, reportDate), status: 'failed', message: text(failure.message, 'failure.message', 2000) } };
  }
  const input = body.brief;
  const reportDate = date(input.reportDate, 'brief.reportDate', today);
  const generatedAt = timestamp(input.generatedAt, 'brief.generatedAt', now, reportDate);
  if (!['complete', 'partial'].includes(input.dataStatus)) throw new Error('brief.dataStatus 必须为 complete 或 partial');
  if (!Array.isArray(input.dimensions) || input.dimensions.length < 1 || input.dimensions.length > 12) throw new Error('brief.dimensions 必须包含 1—12 个维度');
  if (!Array.isArray(input.sources) || input.sources.length < 1 || input.sources.length > 30) throw new Error('brief.sources 必须包含 1—30 个来源');
  const sourcePageUrl = link(input.sourcePageUrl ?? MACRO_SOURCE_PAGE, 'brief.sourcePageUrl');
  if (sourcePageUrl !== MACRO_SOURCE_PAGE) throw new Error('brief.sourcePageUrl 必须指向已关联宏观资料空间');
  const brief = {
    reportDate, generatedAt,
    title: text(input.title, 'brief.title', LIMITS.title),
    stance: text(input.stance, 'brief.stance', LIMITS.stance),
    summary: text(input.summary, 'brief.summary', LIMITS.summary),
    dimensions: input.dimensions.map((item, index) => ({ name: text(item?.name, `dimensions[${index}].name`, 100), signal: text(item?.signal, `dimensions[${index}].signal`, 200), evidence: text(item?.evidence, `dimensions[${index}].evidence`, LIMITS.dimension) })),
    watchpoints: items(input.watchpoints, 'brief.watchpoints'),
    risks: items(input.risks, 'brief.risks'),
    sources: input.sources.map((item, index) => ({ title: text(item?.title, `sources[${index}].title`, 200), url: link(item?.url, `sources[${index}].url`), asOf: sourcePeriod(item?.asOf, `sources[${index}].asOf`, today, now) })),
    dataStatus: input.dataStatus, sourcePageUrl,
    handbookUrl: link(input.handbookUrl ?? '/macro-handbook.html', 'brief.handbookUrl'),
  };
  return { brief };
}

/** Compute response freshness from reportDate, not generatedAt or source dates.
 * Stored sources and text pass through unchanged across midnight.
 * @param {Record<string, any> | null} brief
 * @param {Record<string, any> | null} latestAttempt
 */
export function macroResponse(brief, latestAttempt = null, now = Date.now()) {
  const today = shanghaiDate(now);
  return { ok: true, brief: brief ?? null, freshness: !brief ? 'missing' : brief.reportDate === today ? 'today' : 'stale', today,
    checkedAt: new Date(now).toISOString(), latestAttempt, protocol: MACRO_PROTOCOL };
}

/** Bound actual bytes even when Content-Length is absent or incorrect. */
export async function readMacroBody(request) {
  if (!request.body) throw new Error('缺少 JSON 正文');
  if (Number(request.headers.get('content-length')) > MACRO_MAX_BYTES) throw new Error('正文超过 128 KiB');
  const reader = request.body.getReader(), chunks = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MACRO_MAX_BYTES) { await reader.cancel(); throw new Error('正文超过 128 KiB'); }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  try { return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)); } catch { throw new Error('正文必须为有效 UTF-8 JSON'); }
}
