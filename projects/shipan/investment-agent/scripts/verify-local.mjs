import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { createApplication } from '../src/server.mjs';

// Real, unauthenticated integration smoke check: HTTP server + official Codex.
// No mock model, no existing credentials, no login and no inference calls.
// Run npm run verify:local; output is saved in docs/local-smoke.json.
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const dataDir = await fs.mkdtemp(path.join(os.tmpdir(), 'council-real-local-'));
const report = { checkedAt: new Date().toISOString(), mode: 'real-http-and-codex-unauthenticated', inferenceExecuted: false, checks: [] };
let app;
try {
  app = await createApplication({ dataDir });
  await new Promise(resolve => app.server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${app.server.address().port}`;
  const page = await fetch(base);
  assert.equal(page.status, 200);
  assert.match(await page.text(), /专家圆桌/);
  report.checks.push({ name: 'index.html', passed: true });
  for (const resource of ['/app.js', '/style.css']) {
    const response = await fetch(`${base}${resource}`); assert.equal(response.status, 200); await response.text();
    report.checks.push({ name: resource, passed: true });
  }
  const status = await (await fetch(`${base}/api/status`)).json();
  assert.equal(status.codex.ready, true, status.codex.error || 'Codex was not ready');
  assert.equal(status.auth.connected, false);
  report.checks.push({ name: 'real-codex-status', passed: true, version: status.codex.version, connected: status.auth.connected });
  const catalog = await (await fetch(`${base}/api/models`)).json();
  assert.ok(catalog.models.length > 0);
  report.checks.push({ name: 'model-catalog', passed: true, count: catalog.models.length, entitlementVerified: false });
  const blocked = await fetch(`${base}/api/runs`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Expert-CSRF': status.csrfToken }, body: JSON.stringify({ question: '本地集成检查，不调用模型。', materials: [], maxRounds: 1 }) });
  assert.equal(blocked.status, 409);
  report.checks.push({ name: 'unauthenticated-generation-blocked', passed: true, status: blocked.status, message: (await blocked.json()).error });
  report.passed = true;
} catch (error) {
  report.passed = false; report.error = error.message; process.exitCode = 1;
} finally {
  await app?.close();
  await fs.rm(dataDir, { recursive: true, force: true });
  await fs.writeFile(path.join(root, 'docs/local-smoke.json'), JSON.stringify(report, null, 2) + '\n');
}
console.log(JSON.stringify(report, null, 2));
