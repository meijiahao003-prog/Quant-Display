import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { EventEmitter } from 'node:events';
import { setTimeout as delay } from 'node:timers/promises';
import { createApplication } from '../src/server.mjs';
import { createRun, DEFAULT_EXPERTS } from '../src/council.mjs';

// TEST ONLY: deterministic protocol fixture. It never invokes Codex, a model,
// account credentials, login pages, or a user's data directory.
class FakeBridge extends EventEmitter {
  constructor({ connected = false, persistedThreads = new Map() } = {}) {
    super();
    this.connected = connected;
    this.persistedThreads = persistedThreads;
    this.calls = [];
    this.starts = 0;
    this.closes = 0;
    this.pending = new Set();
    this.accountCalls = 0;
    this.running = false;
  }
  async start() { this.running = true; this.starts += 1; }
  async close() {
    this.running = false;
    this.closes += 1;
    for (const reject of [...this.pending]) reject(new Error('TEST ONLY: bridge closed'));
  }
  status() { return { state: this.running ? 'ready' : 'stopped', testOnly: true }; }
  async account() {
    this.accountCalls += 1;
    if (this.accountGate) await this.accountGate;
    return { connected: this.connected, type: this.connected ? 'chatgpt' : null };
  }
  async models() { return [{ id: 'test-only-model', displayName: 'TEST ONLY' }]; }
  async login(type) { this.loginType = type; return { loginId: 'test-only-login', type }; }
  async cancelLogin(loginId) { this.cancelledLogin = loginId; }
  async logout() { this.connected = false; }
  async generate(options) {
    assert.equal(this.running, true, 'fixture bridge must be started before generation');
    this.calls.push({ ...options, epoch: this.starts });
    if (options.role === 'connection_check') {
      if (this.verifyError) throw new Error(this.verifyError);
      if (!options.threadId) {
        const marker = options.prompt.match(/COUNCIL-[a-f0-9]+/)?.[0];
        assert.ok(marker, 'verification must use a nonempty random marker');
        const threadId = `test-only-check-${this.calls.length}`;
        this.persistedThreads.set(threadId, marker);
        return { threadId, text: this.firstReply ?? '连接验证成功' };
      }
      assert.ok(this.persistedThreads.has(options.threadId), 'resume must reference a saved thread');
      return { threadId: options.threadId, text: this.resumeReply ?? this.persistedThreads.get(options.threadId) };
    }
    if (this.holdCouncil) {
      await new Promise((resolve, reject) => {
        const fail = error => {
          this.pending.delete(fail);
          options.signal?.removeEventListener('abort', abort);
          reject(error);
        };
        const abort = () => fail(new DOMException('TEST ONLY: cancelled', 'AbortError'));
        this.pending.add(fail);
        if (options.signal?.aborted) abort();
        else options.signal?.addEventListener('abort', abort, { once: true });
      });
    }
    if (this.councilError) throw new Error(this.councilError);
    const value = options.role === 'moderator'
      ? { summary: 'TEST ONLY: 综合结论', agreement: ['TEST ONLY: 一致'], disagreements: [], missing: [], nextSteps: ['TEST ONLY: 下一步'], status: 'completed' }
      : { stance: 'support', summary: `TEST ONLY: ${options.role} 观点`, arguments: [{ claim: 'TEST ONLY: 示例材料中的主张', evidenceIds: ['E1'] }], risks: [], missing: [], confidence: 70 };
    return { threadId: options.threadId || `test-only-${options.role}-${this.calls.length}`, text: this.invalidCouncilJSON ? 'TEST ONLY: not JSON' : JSON.stringify(value) };
  }
}

const research = (overrides = {}) => ({
  question: 'TEST ONLY: 请分析虚构测试材料',
  materials: [{ title: 'TEST ONLY: 虚构材料', text: 'TEST ONLY: 不含真实证券信息，仅用于协议测试。' }],
  maxRounds: 1,
  ...overrides,
});

function request(port, route, { method = 'GET', body, rawBody, headers = {} } = {}) {
  const payload = rawBody ?? (body === undefined ? undefined : JSON.stringify(body));
  return new Promise((resolve, reject) => {
    const req = http.request({
      hostname: '127.0.0.1', port, path: route, method,
      headers: { ...(payload === undefined ? {} : { 'content-type': 'application/json' }), ...headers },
    }, res => {
      const chunks = [];
      res.on('data', chunk => chunks.push(chunk));
      res.on('end', () => {
        const text = Buffer.concat(chunks).toString('utf8');
        let json = null;
        if (String(res.headers['content-type']).startsWith('application/json')) json = JSON.parse(text);
        resolve({ status: res.statusCode, headers: res.headers, text, json });
      });
    });
    req.on('error', reject);
    req.setTimeout(5000, () => req.destroy(new Error('Test HTTP request timed out')));
    req.end(payload);
  });
}

async function harness(t, options = {}) {
  const dataDir = options.dataDir || await fs.mkdtemp(path.join(os.tmpdir(), 'expert-http-test-'));
  const bridge = options.bridge || new FakeBridge({ connected: options.connected });
  const app = await createApplication({ dataDir, bridge, preview: options.preview });
  await new Promise(resolve => app.server.listen(0, '127.0.0.1', resolve));
  const port = app.server.address().port;
  t.after(async () => {
    await app.close();
    if (!options.dataDir) await fs.rm(dataDir, { recursive: true, force: true });
  });
  const status = await request(port, '/api/status');
  assert.equal(status.status, 200);
  const token = status.json.csrfToken;
  const call = (route, options = {}) => request(port, route, { ...options, headers: { 'x-expert-csrf': token, ...options.headers } });
  return { app, bridge, dataDir, port, token, call };
}

async function verified(t, options = {}) {
  const h = await harness(t, { connected: true, ...options });
  const response = await h.call('/api/verify', { method: 'POST', body: {} });
  assert.equal(response.status, 200);
  assert.equal(response.json.passed, true);
  return h;
}

async function finished(h, id) {
  const deadline = Date.now() + 4000;
  while (Date.now() < deadline) {
    const response = await h.call(`/api/runs/${id}`);
    assert.equal(response.status, 200);
    if (response.json.run.status !== 'running') return response.json.run;
    await delay(10);
  }
  assert.fail('test run did not finish');
}

test('HTTP: switching models requires a fresh successful verification', async t => {
  const h = await verified(t);
  const blocked = await h.call('/api/runs', { method: 'POST', body: research({ model: 'test-only-model' }) });
  assert.equal(blocked.status, 409);
  assert.match(blocked.json.error, /模型已更换/);
  assert.equal(h.bridge.calls.length, 2);
  const check = await h.call('/api/verify', { method: 'POST', body: { model: 'test-only-model' } });
  assert.equal(check.json.passed, true);
  const accepted = await h.call('/api/runs', { method: 'POST', body: research({ model: 'test-only-model' }) });
  assert.equal(accepted.status, 202);
  await finished(h, accepted.json.run.id);
});

test('HTTP: only loopback Host, same Origin and non-cross-site requests are admitted', async t => {
  const h = await harness(t);
  for (const headers of [
    { host: 'attacker.example' },
    { host: 'localhost.attacker.example' },
    { host: '127.0.0.1.attacker.example' },
    { host: 'terminal.local' },
    { origin: 'https://attacker.example' },
    { origin: 'null' },
    { origin: `http://localhost:${h.port}` },
    { 'sec-fetch-site': 'cross-site' },
  ]) {
    const response = await request(h.port, '/api/status', { headers });
    assert.equal(response.status, 403, JSON.stringify(headers));
  }
  const allowed = await request(h.port, '/api/status', { headers: { origin: `http://127.0.0.1:${h.port}` } });
  assert.equal(allowed.status, 200);
  assert.equal(allowed.headers['access-control-allow-origin'], undefined);
  assert.match(allowed.headers['content-security-policy'], /frame-ancestors 'none'/);
  assert.equal(allowed.headers['cache-control'], 'no-store');
});

test('HTTP: terminal.local is admitted only in explicit preview mode', async t => {
  const h = await harness(t, { preview: true });
  const response = await request(h.port, '/api/status', { headers: { host: 'terminal.local', origin: 'http://terminal.local' } });
  assert.equal(response.status, 200);
  assert.equal(response.json.preview, true);
});

test('HTTP: every mutation requires exact session CSRF token, including Unicode malformed tokens', async t => {
  const h = await harness(t);
  for (const value of [undefined, '', 'wrong', 'f'.repeat(64), 'é'.repeat(64)]) {
    const response = await request(h.port, '/api/auth/start', {
      method: 'POST', body: { type: 'chatgpt' }, headers: value === undefined ? {} : { 'x-expert-csrf': value },
    });
    assert.equal(response.status, 403);
  }
  assert.equal(h.bridge.loginType, undefined);
  const response = await h.call('/api/auth/start', { method: 'POST', body: { type: 'chatgptDeviceCode' } });
  assert.equal(response.status, 200);
  assert.equal(h.bridge.loginType, 'chatgptDeviceCode');
});

test('HTTP: unauthenticated and unverified accounts cannot start research', async t => {
  const h = await harness(t);
  assert.equal((await h.call('/api/verify', { method: 'POST', body: {} })).status, 409);
  assert.equal((await h.call('/api/runs', { method: 'POST', body: research() })).status, 409);
  h.bridge.connected = true;
  assert.equal((await h.call('/api/runs', { method: 'POST', body: research() })).status, 409);
  assert.equal(h.bridge.calls.length, 0);
  assert.deepEqual((await h.call('/api/runs')).json.runs, []);
});

test('HTTP: verification performs two exact responses across process restart, resumes and persists', async t => {
  const h = await verified(t);
  assert.equal(h.bridge.calls.length, 2);
  assert.equal(h.bridge.starts, 2);
  assert.equal(h.bridge.closes, 1);
  const [first, second] = h.bridge.calls;
  assert.equal(first.threadId, undefined);
  assert.equal(first.epoch, 1);
  assert.equal(second.epoch, 2);
  assert.ok(h.bridge.persistedThreads.has(second.threadId));
  const persisted = JSON.parse(await fs.readFile(path.join(h.dataDir, 'verification.json'), 'utf8'));
  assert.equal(persisted.passed, true);
  assert.equal(persisted.processRestarted, true);
  assert.equal(persisted.threadId, second.threadId);
  assert.equal(persisted.resumedReply, h.bridge.persistedThreads.get(second.threadId));
  await h.app.close();
  const restored = await harness(t, { dataDir: h.dataDir, connected: true });
  assert.deepEqual((await restored.call('/api/status')).json.verification, persisted);
  assert.equal(restored.bridge.calls.length, 0);
});

test('HTTP: incorrect verification and bridge errors never become a successful connection', async t => {
  for (const scenario of ['firstReply', 'resumeReply', 'verifyError']) {
    await t.test(scenario, async t => {
      const h = await harness(t, { connected: true });
      h.bridge[scenario] = 'TEST ONLY: simulated verification failure';
      const response = await h.call('/api/verify', { method: 'POST', body: {} });
      assert.equal(response.status, 200);
      assert.equal(response.json.passed, false);
      assert.ok(response.json.error);
      assert.equal((await h.call('/api/status')).json.verification.passed, false);
      assert.equal((await h.call('/api/runs', { method: 'POST', body: research() })).status, 409);
      assert.deepEqual((await h.call('/api/runs')).json.runs, []);
    });
  }
});

test('HTTP: disconnect invalidates saved verification and requires verifying again', async t => {
  const h = await verified(t);
  h.bridge.connected = false;
  const status = await h.call('/api/status');
  assert.equal(status.json.verification, null);
  h.bridge.connected = true;
  assert.equal((await h.call('/api/runs', { method: 'POST', body: research() })).status, 409);
});

test('HTTP: malformed JSON, null objects, unsupported media and excessive bodies are rejected', async t => {
  const h = await harness(t, { connected: true });
  for (const [route, method] of [['/api/verify', 'POST'], ['/api/auth/start', 'POST'], ['/api/experts', 'PUT'], ['/api/runs', 'POST']]) {
    for (const rawBody of ['null', '[]', '42', '"x"', '{']) {
      const response = await h.call(route, { method, rawBody });
      assert.equal(response.status, 400, `${method} ${route} ${rawBody}: ${response.text}`);
    }
  }
  assert.equal((await h.call('/api/verify', { method: 'POST', rawBody: '{}', headers: { 'content-type': 'text/plain' } })).status, 415);
  assert.equal((await h.call('/api/verify', { method: 'POST', body: { padding: 'x'.repeat(350001) } })).status, 413);
  assert.equal((await h.call('/api/status', { method: 'DELETE' })).status, 405);
  assert.equal(h.bridge.calls.length, 0);
});

test('HTTP: invalid research and malformed expert configurations return 400, never 500', async t => {
  const h = await harness(t, { connected: true });
  for (const body of [
    research({ question: '' }), research({ question: 'x'.repeat(6001) }), research({ question: 3 }),
    research({ maxRounds: 0 }), research({ maxRounds: 4 }), research({ maxRounds: 1.5 }),
    research({ materials: null }), research({ materials: [null] }),
    research({ materials: Array.from({ length: 13 }, () => ({ title: 'x', text: 'x' })) }),
    research({ materials: [{ title: 'x', text: 'x'.repeat(60001) }] }),
    research({ model: {} }), research({ model: 'x'.repeat(101) }),
  ]) assert.equal((await h.call('/api/runs', { method: 'POST', body })).status, 400);
  for (const experts of [null, [], [null, null, null], DEFAULT_EXPERTS.map(x => ({ ...x, instructions: '' }))]) {
    const response = await h.call('/api/experts', { method: 'PUT', body: { experts } });
    assert.equal(response.status, 400, response.text);
  }
  assert.equal(h.bridge.calls.length, 0);
});

test('HTTP: research history, expert settings, raw replies and report survive application restart', async t => {
  const h = await verified(t);
  const experts = DEFAULT_EXPERTS.map(x => ({ ...x, instructions: `${x.instructions} TEST ONLY: saved setting` }));
  assert.equal((await h.call('/api/experts', { method: 'PUT', body: { experts } })).status, 200);
  const input = research();
  const started = await h.call('/api/runs', { method: 'POST', body: input });
  assert.equal(started.status, 202);
  const run = await finished(h, started.json.run.id);
  assert.equal(run.status, 'completed');
  assert.equal(run.calls, 4);
  assert.equal(run.rounds[0].opinions.length, 3);
  assert.equal(run.rawResponses.length, 4);
  assert.equal(run.materials[0].id, 'E1');
  assert.equal(run.materials[0].text, input.materials[0].text);
  assert.deepEqual(run.experts, experts);
  const report = await h.call(`/api/runs/${run.id}/report`);
  assert.equal(report.status, 200);
  assert.match(report.headers['content-type'], /^text\/markdown/);
  assert.match(report.headers['content-disposition'], /attachment; filename="council-/);
  for (const text of [input.question, input.materials[0].text, 'TEST ONLY: 综合结论', '同一模型的不同角色', '不等同事实核验']) assert.ok(report.text.includes(text), text);
  await h.app.close();
  const restored = await harness(t, { dataDir: h.dataDir, connected: true });
  assert.deepEqual((await restored.call(`/api/runs/${run.id}`)).json.run, run);
  assert.deepEqual((await restored.call('/api/experts')).json.experts, experts);
  assert.equal((await restored.call('/api/runs')).json.runs[0].id, run.id);
  assert.equal((await restored.call(`/api/runs/${run.id}/report`)).text, report.text);
});

test('HTTP: model errors and invalid JSON persist as failure with truthful downloadable reports', async t => {
  for (const scenario of ['councilError', 'invalidCouncilJSON']) {
    await t.test(scenario, async t => {
      const h = await verified(t);
      h.bridge[scenario] = scenario === 'councilError' ? 'TEST ONLY: model quota error' : true;
      const started = await h.call('/api/runs', { method: 'POST', body: research() });
      assert.equal(started.status, 202);
      const run = await finished(h, started.json.run.id);
      assert.equal(run.status, 'failed');
      assert.equal(run.final, null);
      assert.ok(run.error);
      const report = await h.call(`/api/runs/${run.id}/report`);
      assert.match(report.text, /状态：failed/);
      assert.match(report.text, /尚未生成最终结论/);
      assert.ok(report.text.includes(run.error.message || run.error));
      assert.doesNotMatch(report.text, /\[object Object\]/);
      assert.equal((await h.call('/api/status')).json.activeRunId, null);
    });
  }
});

test('HTTP: cancelling an in-flight run persists cancelled status and preserves audit data', async t => {
  const h = await verified(t);
  h.bridge.holdCouncil = true;
  const started = await h.call('/api/runs', { method: 'POST', body: research() });
  assert.equal(started.status, 202);
  const id = started.json.run.id;
  assert.equal((await h.call(`/api/runs/${id}/cancel`, { method: 'POST', body: {} })).status, 200);
  const run = await finished(h, id);
  assert.equal(run.status, 'cancelled');
  assert.equal(run.final, null);
  assert.ok(run.events.length);
  assert.equal((await h.call('/api/status')).json.activeRunId, null);
});

test('HTTP: restart turns interrupted work into explicit failed history while retaining opinions', async t => {
  const h = await harness(t);
  const run = createRun({ question: 'TEST ONLY: interrupted case', materials: [{ id: 'E1', title: 'TEST ONLY', text: 'TEST ONLY' }], maxRounds: 1 });
  run.phase = 'independent';
  run.round = 1;
  run.rounds = [{ round: 1, opinions: [{ expertName: 'TEST ONLY: saved expert', stance: 'uncertain', summary: 'TEST ONLY: saved partial opinion', arguments: [], risks: [], missing: [] }] }];
  h.app.store.write(`runs/${run.id}`, run);
  await h.app.close();
  const restored = await harness(t, { dataDir: h.dataDir });
  const recovered = (await restored.call(`/api/runs/${run.id}`)).json.run;
  assert.equal(recovered.status, 'failed');
  assert.equal(recovered.phase, 'interrupted');
  assert.deepEqual(recovered.rounds, run.rounds);
  assert.match(recovered.error, /中断/);
  assert.equal(restored.bridge.calls.length, 0, 'startup must not silently generate a replacement result');
  const report = await restored.call(`/api/runs/${run.id}/report`);
  assert.match(report.text, /saved partial opinion/);
  assert.match(report.text, /尚未生成最终结论/);
});

test('HTTP: simultaneous research starts cannot both pass an awaited account check', async t => {
  const h = await verified(t);
  h.bridge.holdCouncil = true;
  let release;
  h.bridge.accountGate = new Promise(resolve => { release = resolve; });
  t.after(() => release());
  const first = h.call('/api/runs', { method: 'POST', body: research({ question: 'TEST ONLY: concurrent first' }) });
  const second = h.call('/api/runs', { method: 'POST', body: research({ question: 'TEST ONLY: concurrent second' }) });
  await delay(25);
  release();
  const results = await Promise.all([first, second]);
  assert.deepEqual(results.map(x => x.status).sort(), [202, 409]);
  assert.equal((await h.call('/api/runs')).json.runs.length, 1);
  const id = results.find(x => x.status === 202).json.run.id;
  await h.call(`/api/runs/${id}/cancel`, { method: 'POST', body: {} });
  assert.equal((await finished(h, id)).status, 'cancelled');
});

test('HTTP: simultaneous verify requests are serialized before awaited account checks', async t => {
  const h = await harness(t, { connected: true });
  let release;
  h.bridge.accountGate = new Promise(resolve => { release = resolve; });
  t.after(() => release());
  const first = h.call('/api/verify', { method: 'POST', body: {} });
  const second = h.call('/api/verify', { method: 'POST', body: {} });
  await delay(25);
  release();
  const results = await Promise.all([first, second]);
  assert.deepEqual(results.map(x => x.status).sort(), [200, 409]);
  assert.equal(results.find(x => x.status === 200).json.passed, true);
  assert.equal(h.bridge.calls.length, 2);
});

test('HTTP: research startup excludes simultaneous verification and account changes', async t => {
  const h = await verified(t);
  h.bridge.holdCouncil = true;
  let release;
  h.bridge.accountGate = new Promise(resolve => { release = resolve; });
  t.after(() => release());
  const accountCalls = h.bridge.accountCalls;
  const start = h.call('/api/runs', { method: 'POST', body: research() });
  const deadline = Date.now() + 1000;
  while (h.bridge.accountCalls === accountCalls && Date.now() < deadline) await delay(2);
  assert.ok(h.bridge.accountCalls > accountCalls, 'run request must be inside its awaited account lookup');
  const [verify, logout] = await Promise.all([
    h.call('/api/verify', { method: 'POST', body: {} }),
    h.call('/api/auth/logout', { method: 'POST', body: {} }),
  ]);
  release();
  assert.equal(verify.status, 409);
  assert.equal(logout.status, 409);
  const started = await start;
  assert.equal(started.status, 202);
  const id = started.json.run.id;
  await h.call(`/api/runs/${id}/cancel`, { method: 'POST', body: {} });
  assert.equal((await finished(h, id)).status, 'cancelled');
  assert.equal(h.bridge.calls.filter(x => x.role === 'connection_check').length, 2);
});

test('HTTP: logout and reverification are blocked during active research', async t => {
  const h = await verified(t);
  h.bridge.holdCouncil = true;
  const started = await h.call('/api/runs', { method: 'POST', body: research() });
  const id = started.json.run.id;
  assert.equal((await h.call('/api/auth/logout', { method: 'POST', body: {} })).status, 409);
  assert.equal((await h.call('/api/verify', { method: 'POST', body: {} })).status, 409);
  assert.equal((await h.call('/api/runs', { method: 'POST', body: research() })).status, 409);
  await h.call(`/api/runs/${id}/cancel`, { method: 'POST', body: {} });
  await finished(h, id);
  assert.equal((await h.call('/api/auth/logout', { method: 'POST', body: {} })).status, 200);
  const status = await h.call('/api/status');
  assert.equal(status.json.auth.connected, false);
  assert.equal(status.json.verification, null);
});

test('HTTP: unknown and traversal-like run identifiers do not disclose arbitrary files', async t => {
  const h = await harness(t);
  for (const route of ['/api/runs/nonexistent', '/api/runs/nonexistent/report', '/api/runs/%2e%2e%2fverification', '/api/runs/%2e%2e%2fverification/report']) {
    assert.equal((await h.call(route)).status, 404, route);
  }
});
