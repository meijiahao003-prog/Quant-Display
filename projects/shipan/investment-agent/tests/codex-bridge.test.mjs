import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { CodexBridge, isolatedEnvironment, redactError } from '../src/codex-bridge.mjs';

// Synthetic protocol fixtures below test failure behavior; they are not model output.
// Run: node --test tests/codex-bridge.test.mjs. The real binary is covered separately.
const makeBridge = () => new CodexBridge({ dataDir: '/tmp/expert-test-data', workspaceDir: '/tmp/expert-test-workspace' });

test('environment never inherits account credentials or Node injection settings', () => {
  const env = isolatedEnvironment('/tmp/isolated', { PATH: '/bin', OPENAI_API_KEY: 'x', CODEX_ACCESS_TOKEN: 'y', CODEX_HOME: '/real', NODE_OPTIONS: '--require evil', HOME: '/real', AWS_SECRET_ACCESS_KEY: 'z' });
  assert.deepEqual(env, { PATH: '/bin', CODEX_HOME: '/tmp/isolated', NO_COLOR: '1' });
});

test('API key auth does not satisfy ChatGPT login', async () => {
  const b = makeBridge(); b.start = async () => {}; b._request = async () => ({ account: { type: 'apiKey' } });
  assert.deepEqual(await b.account(), { connected: false, type: 'apiKey' });
  await assert.rejects(b.generate({ prompt: 'test' }), /ChatGPT/);
});

test('all server approval requests are rejected', () => {
  const b = makeBridge(); const messages = []; b._send = m => messages.push(m);
  b._receive({ id: 1, method: 'item/commandExecution/requestApproval', params: {} });
  b._receive({ id: 2, method: 'item/fileChange/requestApproval', params: {} });
  b._receive({ id: 3, method: 'mcpServer/elicitation/request', params: {} });
  b._receive({ id: 4, method: 'unrecognized/tool', params: {} });
  assert.equal(messages[0].result.decision, 'decline');
  assert.equal(messages[1].result.decision, 'decline');
  assert.equal(messages[2].result.action, 'decline');
  assert.ok(messages[3].error);
});

test('streamed partial output does not turn terminal failure into success', () => {
  const b = makeBridge(); let outcome;
  b._active.set('t', { turnId: 'u', parts: new Map(), finish: error => { outcome = error; } });
  b._receive({ method: 'item/agentMessage/delta', params: { threadId: 't', turnId: 'u', itemId: 'i', delta: 'unfinished' } });
  b._receive({ method: 'turn/completed', params: { threadId: 't', turn: { id: 'u', status: 'failed', error: { message: 'quota exceeded' } } } });
  assert.match(outcome.message, /quota/);
});

test('completed reply selects final answer and excludes commentary', () => {
  const b = makeBridge(); let result;
  b._active.set('t', { turnId: 'u', parts: new Map(), finish: (error, value) => { assert.equal(error, null); result = value; } });
  for (const item of [{ id: 'c', type: 'agentMessage', text: 'Working', phase: 'commentary' }, { id: 'f', type: 'agentMessage', text: 'Answer', phase: 'final_answer' }]) {
    b._receive({ method: 'item/completed', params: { threadId: 't', turnId: 'u', item } });
  }
  b._receive({ method: 'turn/completed', params: { threadId: 't', turn: { id: 'u', status: 'completed' } } });
  assert.deepEqual(result, { threadId: 't', text: 'Answer' });
});

test('abort sends interrupt and rejects the running request', async () => {
  const b = makeBridge(); const calls = []; const abort = new AbortController();
  b.account = async () => ({ connected: true }); b._state.ready = true;
  b._request = async (method, params) => { calls.push({ method, params }); if (method === 'thread/start') return { thread: { id: 't' } }; if (method === 'turn/start') return { turn: { id: 'u' } }; return {}; };
  const promise = b.generate({ prompt: 'test', signal: abort.signal });
  await new Promise(resolve => setImmediate(resolve)); abort.abort();
  await assert.rejects(promise, /cancelled/);
  assert.ok(calls.some(c => c.method === 'turn/interrupt' && c.params.turnId === 'u'));
  assert.equal(b._active.size, 0);
  b._receive({ method: 'turn/completed', params: { threadId: 't', turn: { id: 'u', status: 'interrupted' } } });
  assert.equal(b._cancelling.size, 0);
  await b.close();
});

test('redaction removes bearer credentials and OAuth callback codes', () => {
  assert.equal(redactError('Bearer abc'), 'Bearer [redacted]');
  assert.ok(!redactError('https://x/callback?code=secret&state=ok').includes('secret'));
});

test('async UI callback rejection does not become an unhandled rejection or a protocol failure', async () => {
  const b = makeBridge(); let result;
  b._active.set('t', { turnId: 'u', parts: new Map(), onDelta: async () => { throw new Error('UI disconnected'); }, finish: (error, value) => { assert.equal(error, null); result = value; } });
  b._receive({ method: 'item/agentMessage/delta', params: { threadId: 't', turnId: 'u', itemId: 'i', delta: 'Complete' } });
  await new Promise(resolve => setImmediate(resolve));
  b._receive({ method: 'turn/completed', params: { threadId: 't', turn: { id: 'u', status: 'completed' } } });
  assert.equal(result.text, 'Complete');
});

test('close waits for old process shutdown before concurrent restart', async () => {
  const b = makeBridge();
  const child = spawn(process.execPath, ['-e', "process.stdin.resume();process.stdin.on('end',()=>setTimeout(()=>process.exit(0),80));process.stdout.write('ready');"], { stdio: ['pipe', 'pipe', 'pipe'] });
  await once(child.stdout, 'data');
  b._process = child; b._state.ready = true;
  let restarted = false;
  b._start = async () => {
    assert.ok(child.exitCode !== null || child.signalCode !== null, 'old process must exit before replacement starts');
    assert.equal(b._process, null);
    restarted = true; b._state.ready = true; return b.status();
  };
  const closing = b.close();
  const restarting = b.start();
  assert.equal(restarted, false);
  await closing; await restarting;
  assert.equal(restarted, true);
  assert.equal(b.status().ready, true);
  await b.close();
});

test('close during startup rejects startup and leaves a clean restart state', async () => {
  const b = makeBridge();
  let started = false;
  b._start = async () => {
    await new Promise(resolve => setImmediate(resolve));
    if (b._closing) throw new Error('closed during startup');
    started = true; b._state.ready = true; return b.status();
  };
  const starting = b.start();
  const rejected = assert.rejects(starting, /closed during startup/);
  await b.close(); await rejected;
  assert.equal(started, false);
  assert.equal(b._starting, null);
  assert.equal(b._stopping, null);
  await b.start();
  assert.equal(started, true);
  await b.close();
});
