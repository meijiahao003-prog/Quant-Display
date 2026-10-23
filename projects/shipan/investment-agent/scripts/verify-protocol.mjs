import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { CodexBridge, CODEX_VERSION, redactError } from '../src/codex-bridge.mjs';

// Run: npm run verify:protocol
// Real input: the pinned, installed Codex binary with a new empty credential directory.
// Output: actual protocol success/failure, never a simulated model answer.
// Changes: creates docs/protocol-verification.json; removes temporary credentials/workspace;
// existing user credentials, database rows and research records are never read or changed.
const projectDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const temp = await mkdtemp(path.join(tmpdir(), 'expert-protocol-'));
const bridge = new CodexBridge({ dataDir: path.join(temp, 'data'), workspaceDir: path.join(temp, 'workspace') });
const report = {
  checkedAt: new Date().toISOString(), expectedVersion: CODEX_VERSION, mode: 'isolated-unauthenticated-real-binary',
  authenticatedInference: 'NOT_EXECUTED: requires the user to sign in and authorize a real model request',
  loginStarted: false, checks: [],
  security: {
    credentials: 'New CODEX_HOME; file credential store; inherited API keys/auth tokens removed',
    execution: 'shell/browser/computer/apps/plugins/code-mode/subagent features disabled; read-only sandbox; approvals rejected',
    limitation: 'This is not OS/container isolation. Version 0.163.0 does not expose restricted read roots in its SandboxPolicy schema. No claim that every possible internal tool is absent.',
  },
};
try {
  await bridge.start();
  report.checks.push({ method: 'initialize', passed: bridge.status().ready, status: bridge.status() });
  const account = await bridge.account();
  report.checks.push({ method: 'account/read', passed: !account.connected && !account.type, connected: account.connected, type: account.type ?? null });
  const models = await bridge.models();
  report.checks.push({ method: 'model/list', passed: Array.isArray(models), count: models.length, models: models.map(({ id, displayName }) => ({ id, displayName })), note: 'Bundled catalog is not entitlement proof.' });
  const configuration = await bridge._request('config/read', { includeLayers: false });
  const cfg = configuration.config ?? {};
  const features = cfg.features ?? {};
  report.checks.push({ method: 'config/read', passed: features.shell_tool === false && cfg.web_search === 'disabled' && cfg.sandbox_mode === 'read-only', effective: { sandbox_mode: cfg.sandbox_mode, web_search: cfg.web_search, forced_login_method: cfg.forced_login_method, cli_auth_credentials_store: cfg.cli_auth_credentials_store, features } });
  try {
    const thread = await bridge._request('thread/start', { cwd: bridge.workspaceDir, sandbox: 'read-only', approvalPolicy: 'never' });
    report.checks.push({ method: 'thread/start', passed: !!thread.thread?.id, threadAllocated: !!thread.thread?.id, persistenceVerified: false, inferenceStarted: false });
    if (thread.thread?.id) {
      const resumed = await bridge._request('thread/resume', { threadId: thread.thread.id, excludeTurns: true, cwd: bridge.workspaceDir, sandbox: 'read-only', approvalPolicy: 'never' });
      report.checks.push({ method: 'thread/resume', passed: resumed.thread?.id === thread.thread.id, inferenceStarted: false });
    }
  } catch (error) { report.checks.push({ method: 'thread/start-or-resume', passed: false, error: redactError(error).replace(/thread id \S+/g, 'thread id [temporary]'), inferenceStarted: false, note: 'An empty thread has no saved rollout yet. Resume after an authenticated completed turn remains unverified.' }); }
} catch (error) {
  report.error = redactError(error);
  process.exitCode = 1;
} finally {
  await bridge.close();
  await rm(temp, { recursive: true, force: true });
  await mkdir(path.join(projectDir, 'docs'), { recursive: true });
  await writeFile(path.join(projectDir, 'docs', 'protocol-verification.json'), `${JSON.stringify(report, null, 2)}\n`);
}
console.log(JSON.stringify(report, null, 2));
