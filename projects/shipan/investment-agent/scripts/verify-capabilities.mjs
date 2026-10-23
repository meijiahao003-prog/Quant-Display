import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { CodexBridge, CODEX_VERSION, redactError } from '../src/codex-bridge.mjs';

// Real, unauthenticated protocol regression. Run: npm run verify:capabilities
// Capture request bodies by executing the production generate() in a stub-only
// harness. Never replace its approval/sandbox fields with hand-written examples.
// Real turn/start probes use only a nonexistent UUID, so cannot run a model.
const projectDir = fileURLToPath(new URL('../', import.meta.url));
const reportPath = path.join(projectDir, 'docs', 'capability-regression.json');
const missingThreadId = '00000000-0000-0000-0000-000000000000';
const syntheticThreadId = '11111111-1111-4111-8111-111111111111';
const syntheticTurnId = '22222222-2222-4222-8222-222222222222';
const gateError = /askForApproval\.granular requires experimentalApi capability/;
const failures = [];
const report = {
  checkedAt: new Date().toISOString(),
  expectedCodexVersion: CODEX_VERSION,
  scope: 'Unauthenticated protocol capability gate only; not an account, entitlement, or model-generation verification.',
  requestSource: 'Actual CodexBridge.generate() calls, captured by a test-only account/RPC stub; production account detection is unchanged.',
  isolation: {
    freshTemporaryCodexHomePerMode: true,
    inheritedCredentialsRemovedByProductionBridge: true,
    loginRequested: false,
    userAccountRead: false,
    realTurnRequestsOnlyUseNonexistentThreadId: true,
  },
  cases: [],
};

function check(condition, message) {
  if (!condition) failures.push(message);
  return Boolean(condition);
}

async function captureGenerateRequests({ dataDir, workspaceDir }) {
  const harness = new CodexBridge({ dataDir, workspaceDir });
  const requests = [];
  // This instance never starts a process and cannot access an account or model.
  harness.start = async () => { throw new Error('Capture harness must not start Codex'); };
  harness.account = async () => ({ connected: true });
  harness._request = async (method, params) => {
    requests.push({ method, params: structuredClone(params) });
    if (method === 'thread/start' || method === 'thread/resume') {
      return { thread: { id: syntheticThreadId } };
    }
    if (method === 'turn/start') {
      setImmediate(() => {
        harness._receive({ method: 'item/completed', params: {
          threadId: syntheticThreadId, turnId: syntheticTurnId,
          item: { id: 'synthetic-capture-item', type: 'agentMessage', phase: 'final_answer', text: 'SYNTHETIC PARAMETER CAPTURE ONLY' },
        } });
        harness._receive({ method: 'turn/completed', params: {
          threadId: syntheticThreadId, turn: { id: syntheticTurnId, status: 'completed' },
        } });
      });
      return { turn: { id: syntheticTurnId, status: 'inProgress' } };
    }
    throw new Error(`Unexpected capture-harness RPC: ${method}`);
  };
  try {
    const args = { prompt: 'Protocol capability probe. This prompt must never be sent to a valid thread.', role: 'Capability regression parameter capture.' };
    await harness.generate(args);
    await harness.generate({ ...args, threadId: syntheticThreadId });
    assert.deepEqual(requests.map(request => request.method), ['thread/start', 'turn/start', 'thread/resume', 'turn/start']);
    assert.deepEqual(requests[1].params, requests[3].params);
    return [requests[0], requests[2], requests[1]];
  } finally {
    await harness.close();
  }
}

async function runMode(baseDir, experimentalApi) {
  const mode = experimentalApi ? 'enabled' : 'disabled-negative-control';
  const dirs = { dataDir: path.join(baseDir, mode, 'data'), workspaceDir: path.join(baseDir, mode, 'workspace') };
  const captured = await captureGenerateRequests(dirs);
  const bridge = new CodexBridge(dirs);
  const originalRequest = bridge._request.bind(bridge);
  const result = { experimentalApi, installedVersion: null, productionInitializeOptIn: null, accountConnected: null, checks: [] };
  report.cases.push(result);
  bridge._request = (method, params, timeout) => {
    if (!['initialize', 'config/read', 'account/read', 'thread/start', 'thread/resume', 'turn/start'].includes(method)) {
      throw new Error(`Disallowed RPC in unauthenticated capability probe: ${method}`);
    }
    if (method === 'initialize') {
      result.productionInitializeOptIn = params.capabilities?.experimentalApi === true;
      params = { ...params, capabilities: { ...params.capabilities, experimentalApi } };
    }
    if (['thread/resume', 'turn/start'].includes(method) && params.threadId !== missingThreadId) {
      throw new Error('Refusing a real resume/turn probe against any potentially valid thread');
    }
    return originalRequest(method, params, timeout);
  };
  try {
    await bridge.start();
    result.installedVersion = bridge.status().version;
    check(result.productionInitializeOptIn, `${mode}: production initialize must opt into experimentalApi`);
    const account = await bridge.account();
    result.accountConnected = account.connected;
    assert.equal(account.connected, false, 'Probe must use a fresh unauthenticated profile');
    assert.equal(account.type, undefined, 'Probe must not inherit any credential type');
    for (const capturedRequest of captured) {
      const { method } = capturedRequest;
      const params = structuredClone(capturedRequest.params);
      // This is the sole alteration to generate() request bodies. An absent
      // target stops resume/turn before inference after field gating succeeds.
      if (method !== 'thread/start') params.threadId = missingThreadId;
      const detail = {
        method,
        source: 'Captured from production generate()',
        changedFields: method === 'thread/start' ? [] : ['threadId'],
        parameterFields: Object.keys(params).sort(),
        approvalPolicy: params.approvalPolicy,
        approvalsReviewer: params.approvalsReviewer,
        ...(params.sandbox ? { sandbox: params.sandbox } : {}),
        ...(params.sandboxPolicy ? { sandboxPolicy: params.sandboxPolicy } : {}),
      };
      result.checks.push(detail);
      let response;
      try {
        response = await bridge._request(method, params);
        detail.outcome = 'accepted';
      } catch (error) {
        detail.outcome = 'rejected';
        detail.error = redactError(error);
      }
      if (!experimentalApi) {
        detail.passed = check(detail.outcome === 'rejected' && gateError.test(detail.error), `${mode}: ${method} must reject the granular field at the capability gate`);
      } else if (method === 'thread/start') {
        detail.passed = check(detail.outcome === 'accepted' && Boolean(response?.thread?.id) && response.thread.id !== missingThreadId, `${mode}: thread/start must accept the actual generate() parameters`);
      } else {
        const missingError = method === 'thread/resume' ? /no rollout found for thread id/ : /thread not found/;
        detail.passed = check(detail.outcome === 'rejected' && !gateError.test(detail.error) && missingError.test(detail.error), `${mode}: ${method} must pass field gating and reject the nonexistent thread`);
      }
    }
  } finally {
    await bridge.close();
  }
}

let tempDir;
try {
  report.bridgeSourceSha256 = createHash('sha256').update(await readFile(new URL('../src/codex-bridge.mjs', import.meta.url))).digest('hex');
  tempDir = await mkdtemp(path.join(os.tmpdir(), 'expert-capability-regression-'));
  await runMode(tempDir, false);
  await runMode(tempDir, true);
} catch (error) {
  failures.push(redactError(error));
} finally {
  if (tempDir) {
    try { await rm(tempDir, { recursive: true, force: true }); }
    catch (error) { failures.push(`Temporary-profile cleanup failed: ${redactError(error)}`); }
  }
  report.passed = failures.length === 0;
  report.failures = failures;
  report.modelInferenceVerified = false;
  report.noModelTurnStarted = report.cases.length === 2 && report.cases.every(entry => entry.checks.some(check => check.method === 'turn/start' && check.passed && check.outcome === 'rejected'));
  await mkdir(path.dirname(reportPath), { recursive: true });
  await writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`);
}

console.log(`Capability regression: ${report.passed ? 'PASS' : 'FAIL'}; ${report.cases.reduce((count, entry) => count + entry.checks.filter(check => check.passed).length, 0)}/6 expected protocol outcomes.`);
console.log('No login or successful model turn; this checks the protocol gate only.');
console.log(`Report: ${reportPath}`);
if (!report.passed) {
  for (const failure of failures) console.error(failure);
  process.exitCode = 1;
}
