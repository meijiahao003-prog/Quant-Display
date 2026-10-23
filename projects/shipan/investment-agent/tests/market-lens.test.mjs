import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import { EventEmitter } from 'node:events';
import { loadMarketLensProfile, validateMarketLensBundle, createMarketLensResearch, compactMarketHistory } from '../src/market-lens.mjs';
import { createApplication } from '../src/server.mjs';
import { createRun, executeCouncil } from '../src/council.mjs';
import { renderReport } from '../src/report.mjs';

// TEST ONLY. Synthetic values test protocol handling; they are not real market or model results.
export function demoBundle() {
  return { schema_version: 'market-lens.council.v1', bundle_id: 'test-only-bundle', analysis_asof: '2026-10-21T03:00:00Z', timezone: 'Asia/Shanghai', mode: 'demo', selected: { symbol: '510300', kind: 'etf', name: '协议测试ETF' }, question: '仅测试：所给演示价格是否得到交易资金支持？', snapshot: { mode: 'demo', selected: { symbol: '510300', kind: 'etf', as_of: '2026-10-21T10:00:00+08:00' } }, price_history: { symbol: '510300', kind: 'etf', period: 'intraday', source: 'TEST ONLY 合成演示', status: 'demo', as_of: '2026-10-21T10:00:00+08:00', bars: [{ time: '09:31', close: 4.2 }, { time: '2026-10-21 10:00:00', close: 4.3 }] }, fund_flow_history: { symbol: '510300', kind: 'etf', source: 'TEST ONLY 合成演示', status: 'demo', unit: 'CNY', as_of: '2026-10-21T10:00:00+08:00', bars: [{ time: '2026-10-21T09:31:00+08:00', main_net: 100, super_net: 60, large_net: 40 }, { time: '2026-10-21T10:00:00+08:00', main_net: -20, super_net: -10, large_net: -10 }] } };
}

// Fake bridge exercises real server gates only. It never reads account credentials or starts Codex.
class FixtureBridge extends EventEmitter {
  constructor(connected = false) { super(); this.connected = connected; this.calls = []; }
  async start() {}
  status() { return { ready: true, testOnly: true }; }
  async account() { if (this.accountGate) await this.accountGate; return { connected: this.connected, type: 'chatgpt', email: 'test-only@example.invalid' }; }
  async close() { this.release?.(); }
  async generate(args) {
    this.calls.push(args);
    if (this.hold) await new Promise(resolve => { this.release = resolve; args.signal?.addEventListener('abort', resolve, { once: true }); });
    return { threadId: `test-only-${args.role}`, text: JSON.stringify(args.role === 'moderator'
      ? { summary: 'TEST ONLY 演示综合', agreement: [], disagreements: [], missing: ['TEST ONLY 没有公司基本面'], nextSteps: ['TEST ONLY 补充材料'], status: 'needs_evidence' }
      : { stance: 'uncertain', summary: `TEST ONLY ${args.role}`, arguments: [{ claim: 'TEST ONLY 所给合成快照', evidenceIds: ['ML-CONTEXT'] }], risks: [], missing: ['TEST ONLY 没有公司基本面'], confidence: 0 }) };
  }
}

/** Send a bounded HTTP request to this test's isolated loopback service; real HTTP status is asserted. */
function request(port, route, { method = 'GET', body, headers = {} } = {}) {
  return new Promise((resolve, reject) => {
    const req = http.request({ hostname: '127.0.0.1', port, path: route, method, headers: { ...(body ? { 'Content-Type': 'application/json' } : {}), ...headers } }, res => {
      const chunks = []; res.on('data', chunk => chunks.push(chunk));
      res.on('end', () => { const text = Buffer.concat(chunks).toString(); resolve({ status: res.statusCode, text, json: res.headers['content-type']?.startsWith('application/json') ? JSON.parse(text) : null }); });
    });
    req.on('error', reject); req.setTimeout(5000, () => req.destroy(new Error('TEST ONLY request timeout')));
    req.end(body ? JSON.stringify(body) : undefined);
  });
}

/** Create disposable test state with explicit synthetic verification, never a user's .local directory. */
async function harness(t, { connected = true, verified = true } = {}) {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'market-lens-test-'));
  if (verified) await fs.writeFile(path.join(dir, 'verification.json'), JSON.stringify({ passed: true, model: null, testOnly: true }));
  const bridge = new FixtureBridge(connected);
  const app = await createApplication({ dataDir: dir, bridge });
  await new Promise(resolve => app.server.listen(0, '127.0.0.1', resolve));
  const port = app.server.address().port;
  t.after(async () => { await app.close(); await fs.rm(dir, { recursive: true, force: true }); });
  const status = await request(port, '/api/status');
  return { app, bridge, port, headers: { 'X-Expert-CSRF': status.json.csrfToken } };
}

test('real uploaded Skill bytes load in separate namespaces with 15 verified investment summaries', () => {
  const profile = loadMarketLensProfile();
  assert.deepEqual(profile.experts.map(x => x.id), ['macro_environment', 'market_structure', 'company_fundamentals']);
  assert.match(profile.experts[0].instructions, /两套视角分别判断/);
  assert.match(profile.experts[1].instructions, /先识别环境，再选方法/);
  assert.match(profile.experts[2].instructions, /八个研究习惯/);
  assert.equal(profile.sources.length, 15);
  assert.ok(profile.sources.every(x => x.verification_level === 'content_verified' && ['dalio', 'druckenmiller'].includes(x.person)));
  assert.ok(profile.publicProfile.experts.every(x => x.files.every(f => /^[a-f0-9]{64}$/.test(f.sha256))));
  assert.equal(profile.publicProfile.experts[0].original_skill_name, profile.publicProfile.experts[1].original_skill_name);
  assert.notEqual(profile.publicProfile.experts[0].files[0].path, profile.publicProfile.experts[1].files[0].path);
  assert.ok(profile.experts[1].instructions.length > 22000);
  assert.ok(profile.experts[2].instructions.includes('基本面证据不足'));
});

test('changing one Skill byte fails closed instead of fallback to names or rules', async t => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'market-lens-pack-')); t.after(() => fs.rm(dir, { recursive: true, force: true }));
  await fs.cp(new URL('../expert-packages/', import.meta.url), dir, { recursive: true });
  await fs.appendFile(path.join(dir, 'macro-v1/dalio-druckenmiller-market/SKILL.md'), '\nTEST ONLY tampered\n');
  assert.throws(() => loadMarketLensProfile(dir), /专家包校验失败/);
});

test('frozen data is detached, no fabricated bars, source dates and demo marker preserved', () => {
  const original = demoBundle();
  const checked = validateMarketLensBundle(original);
  assert.deepEqual(checked.bundle, original);
  assert.ok(checked.notes.some(x => /合成演示/.test(x)));
  assert.deepEqual(checked.dates.price_history.days, ['2026-10-21']);
  original.price_history.bars[0].close = 999;
  assert.equal(checked.bundle.price_history.bars[0].close, 4.2);
  const missing = demoBundle(); missing.fund_flow_history.bars = []; missing.fund_flow_history.status = 'unavailable';
  assert.equal(validateMarketLensBundle(missing).bundle.fund_flow_history.bars.length, 0);
});

test('reject mismatched symbols, kinds, dates and units rather than silently joining', () => {
  const cases = [
    b => { b.price_history.symbol = '000300'; },
    b => { b.fund_flow_history.kind = 'index'; },
    b => { b.fund_flow_history.bars[0].time = '2026-10-20T09:31:00+08:00'; },
    b => { b.fund_flow_history.unit = '亿元'; },
    b => { b.price_history.as_of = '2026-10-20T10:00:00+08:00'; },
    b => { b.snapshot.selected.as_of = '2026-10-20T10:00:00+08:00'; },
    b => { b.snapshot.mode = 'real'; },
    b => { delete b.bundle_id; },
    b => { b.price_history.bars[0].time = '2026-13-40'; },
    b => { b.price_history.bars[0].time = '25:00'; },
  ];
  for (const mutate of cases) { const b = demoBundle(); mutate(b); assert.throws(() => validateMarketLensBundle(b), error => error.status === 400); }
});

test('reject duplicates and oversize evidence; daily history dates remain separate from fund minute date', () => {
  const duplicate = demoBundle(); duplicate.price_history.bars.push(duplicate.price_history.bars[0]);
  assert.throws(() => validateMarketLensBundle(duplicate), /重复时间/);
  const aliases = demoBundle(); aliases.price_history.bars.push({ time: '2026-10-21T09:31:00+08:00', close: 4.2 });
  assert.throws(() => validateMarketLensBundle(aliases), /重复时间/);
  const large = demoBundle(); large.snapshot.description = '过大'.repeat(50000);
  assert.throws(() => validateMarketLensBundle(large), error => error.status === 413);
  const daily = demoBundle(); daily.price_history.period = 'day'; daily.price_history.bars = [{ date: '2026-10-19', close: 4 }, { date: '2026-10-20', close: 4.1 }];
  assert.deepEqual(validateMarketLensBundle(daily).dates.price_history.days, ['2026-10-19', '2026-10-20']);
});

test('deterministic actual methods and source IDs are frozen into research; no quarantined legacy enters', () => {
  const profile = loadMarketLensProfile();
  const input = createMarketLensResearch({ bundle: demoBundle(), maxRounds: 1 }, profile);
  assert.equal(input.materials.length, 18);
  assert.deepEqual(input.materials.slice(0, 3).map(x => x.id), ['ML-CONTEXT', 'ML-PRICE', 'ML-FLOW']);
  assert.equal(input.marketLens.knowledge.count, 15);
  assert.equal(input.marketLens.bundle_sha256, createMarketLensResearch({ bundle: demoBundle() }, profile).marketLens.bundle_sha256);
  const kb = input.materials.slice(3).map(x => JSON.parse(x.text));
  assert.ok(kb.every(x => x.verification_level === 'content_verified' && !Object.hasOwn(x, 'legacy_records')));
  const huge = demoBundle(); huge.snapshot.notes = 'a'.repeat(99000);
  assert.throws(() => createMarketLensResearch({ bundle: huge }, profile), /100000字/);
});

test('real Skill text reaches each role prompt and shared-source warning reaches moderator', async () => {
  const input = createMarketLensResearch({ bundle: demoBundle(), maxRounds: 1 }, loadMarketLensProfile());
  const run = createRun(input); run.marketLens = input.marketLens;
  const bridge = new FixtureBridge(true);
  await executeCouncil(run, { bridge });
  assert.equal(run.status, 'needs_evidence');
  assert.equal(bridge.calls.length, 4);
  assert.match(bridge.calls.find(x => x.role === 'market_structure').prompt, /方法与适用边界/);
  assert.match(bridge.calls.find(x => x.role === 'company_fundamentals').prompt, /S1/);
  assert.match(bridge.calls.find(x => x.role === 'moderator').prompt, /同源证据和相似方法不能算独立投票/);
  const report = renderReport(run);
  assert.match(report, /合成演示，非真实行情/); assert.match(report, /快照SHA-256/); assert.match(report, /15条/);
});

test('profile GET returns real metadata only and never account, CSRF or private instructions', async t => {
  const h = await harness(t);
  const response = await request(h.port, '/api/market-lens/profile');
  assert.equal(response.status, 200); assert.equal(response.json.knowledge.count, 15);
  assert.doesNotMatch(response.text, /csrfToken|test-only@example.invalid|codex-home|"instructions"/);
  assert.equal((await request(h.port, '/api/market-lens/profile', { headers: { Origin: 'https://evil.invalid' } })).status, 403);
});

test('new run endpoint preserves CSRF, login, verification and model gates', async t => {
  const body = { bundle: demoBundle(), maxRounds: 1 };
  const unauth = await harness(t, { connected: false });
  assert.equal((await request(unauth.port, '/api/market-lens/runs', { method: 'POST', body })).status, 403);
  assert.equal((await request(unauth.port, '/api/market-lens/runs', { method: 'POST', body, headers: unauth.headers })).status, 409);
  const unverified = await harness(t, { verified: false });
  assert.equal((await request(unverified.port, '/api/market-lens/runs', { method: 'POST', body, headers: unverified.headers })).status, 409);
  const verified = await harness(t);
  assert.equal((await request(verified.port, '/api/market-lens/runs', { method: 'POST', body: { ...body, model: 'different-test-only-model' }, headers: verified.headers })).status, 409);
  assert.equal(unauth.bridge.calls.length + unverified.bridge.calls.length + verified.bridge.calls.length, 0);
});

test('new endpoint forces actual method profile while ordinary expert settings remain untouched', async t => {
  const h = await harness(t);
  const before = (await request(h.port, '/api/experts')).json;
  const response = await request(h.port, '/api/market-lens/runs', { method: 'POST', headers: h.headers, body: { bundle: demoBundle(), maxRounds: 1, experts: [{ name: 'INJECTED fake expert' }] } });
  assert.equal(response.status, 202);
  assert.deepEqual(response.json.run.experts.map(x => x.id), ['macro_environment', 'market_structure', 'company_fundamentals']);
  assert.equal(response.json.run.marketLens.knowledge.count, 15);
  assert.deepEqual((await request(h.port, '/api/experts')).json, before);
  assert.doesNotMatch(JSON.stringify(response.json.run.experts), /INJECTED/);
});

test('normal and imported run launches share the same concurrency lock', async t => {
  const h = await harness(t);
  let release; h.bridge.accountGate = new Promise(resolve => { release = resolve; });
  const first = request(h.port, '/api/market-lens/runs', { method: 'POST', headers: h.headers, body: { bundle: demoBundle(), maxRounds: 1 } });
  await new Promise(resolve => setTimeout(resolve, 20));
  const second = await request(h.port, '/api/runs', { method: 'POST', headers: h.headers, body: { question: 'TEST ONLY normal', materials: [], maxRounds: 1 } });
  assert.equal(second.status, 409); release(); assert.equal((await first).status, 202);
});

test('only user top-level navigation may cross site; API and asset requests remain same-origin', async t => {
  const h = await harness(t);
  const navigation = { 'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Dest': 'document', 'Sec-Fetch-User': '?1' };
  assert.equal((await request(h.port, '/api/status', { headers: navigation })).status, 403);
  assert.equal((await request(h.port, '/market-lens.js', { headers: navigation })).status, 403);
  assert.equal((await request(h.port, '/market-lens.html', { headers: { ...navigation, 'Sec-Fetch-User': '?0' } })).status, 403);
  const page = await request(h.port, '/market-lens.html', { headers: navigation });
  assert.equal(page.status, 200);
  assert.match(page.text, /投资|Agent|会审/);
});

/** Restore the explicit columnar protocol in this test only, to prove field-for-field preservation. */
function expandHistory(history) {
  if (Array.isArray(history.bars)) return structuredClone(history);
  const { columns, rows, presence } = history.bars;
  return { ...history, bars: rows.map((row, index) => Object.fromEntries(columns.flatMap((key, col) => !presence || presence[index][col] === '1' ? [[key, row[col]]] : []))) };
}

test('lossless columnar history retains all observations, zero, negative, null and absent fields', () => {
  const history = demoBundle().fund_flow_history;
  history.bars = Array.from({ length: 240 }, (_, index) => ({ time: `2026-10-21T${String(9 + Math.floor(index / 60)).padStart(2, '0')}:${String(index % 60).padStart(2, '0')}:00+08:00`, main_net: index === 0 ? 0 : -index, super_net: index % 2 ? null : index, large_net: index * 10, source_time: '2026-10-21T13:00:00+08:00', ...(index % 3 ? { optional_value: 0 } : {}) }));
  const encoded = compactMarketHistory(history);
  assert.equal(encoded.bars.encoding, 'market-lens.columnar.v1');
  assert.ok(encoded.bars.presence.some(row => row.includes('0')));
  assert.deepEqual(expandHistory(encoded), history);
  assert.ok(JSON.stringify(encoded).length < JSON.stringify(history).length);
});

test('a full 240-point pair fits with all real method summaries and no evidence truncation', () => {
  const bundle = demoBundle();
  bundle.price_history.bars = Array.from({ length: 240 }, (_, index) => ({ time: `2026-10-21 ${String(9 + Math.floor(index / 60)).padStart(2, '0')}:${String(index % 60).padStart(2, '0')}:00`, open: 4.20001, high: 4.30001, low: 4.10001, close: 4.25001, amount: 123456789.123, volume: 1234567, source_time: '2026-10-21T13:00:00+08:00' }));
  bundle.fund_flow_history.bars = bundle.price_history.bars.map(bar => ({ time: bar.time, main_net: -123456789.123, super_net: 12345678.123, large_net: -135802467.246, medium_net: 1234567.89, small_net: -1234567.89, source_time: '2026-10-21T13:00:00+08:00' }));
  const input = createMarketLensResearch({ bundle, maxRounds: 1 }, loadMarketLensProfile());
  assert.equal(input.materials.length, 18);
  assert.deepEqual(expandHistory(JSON.parse(input.materials[1].text)), bundle.price_history);
  assert.deepEqual(expandHistory(JSON.parse(input.materials[2].text)), bundle.fund_flow_history);
  assert.ok(input.materials.reduce((sum, material) => sum + material.text.length, 0) < 100000);
});

test('captured real production bundle imports all 187 price and 185 fund observations losslessly', async () => {
  const bundle = JSON.parse(await fs.readFile(new URL('../docs/market-lens-real-input.json', import.meta.url), 'utf8'));
  const input = createMarketLensResearch({ bundle, maxRounds: 1 }, loadMarketLensProfile());
  assert.equal(bundle.mode, 'real');
  assert.equal(bundle.price_history.bars.length, 187);
  assert.equal(bundle.fund_flow_history.bars.length, 185);
  assert.deepEqual(expandHistory(JSON.parse(input.materials[1].text)), bundle.price_history);
  assert.deepEqual(expandHistory(JSON.parse(input.materials[2].text)), bundle.fund_flow_history);
  assert.equal(input.materials.length, 18);
  assert.equal(input.marketLens.bundle_id, bundle.bundle_id);
});
