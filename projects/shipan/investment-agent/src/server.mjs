import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomBytes, timingSafeEqual } from 'node:crypto';
import { Store } from './store.mjs';
import { renderReport } from './report.mjs';
import { CodexBridge } from './codex-bridge.mjs';
import { DEFAULT_EXPERTS, createRun, executeCouncil } from './council.mjs';
import { loadMarketLensProfile, createMarketLensResearch } from './market-lens.mjs';

const projectDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const VERSION = '0.1.0';
const fail = (message, status = 400) => Object.assign(new Error(message), { status });

/** 校验研究输入并生成固定证据编号；不接受任意文件路径或执行指令。 */
export function validateResearch(body) {
  if (!body || typeof body !== 'object') throw fail('需要 JSON 请求。');
  if (typeof body.question !== 'string' || !body.question.trim() || body.question.length > 6000) throw fail('研究问题必填，最多 6000 字。');
  const maxRounds = body.maxRounds ?? 2;
  if (!Number.isInteger(maxRounds) || maxRounds < 1 || maxRounds > 3) throw fail('讨论轮数必须是 1–3。');
  if (body.model != null && (typeof body.model !== 'string' || body.model.length > 100)) throw fail('模型名称无效。');
  if (!Array.isArray(body.materials) || body.materials.length > 12) throw fail('最多添加 12 份文字材料。');
  let total = 0;
  const materials = body.materials.map((m, i) => {
    if (!m || typeof m.text !== 'string' || !m.text.trim() || typeof m.title !== 'string' || !m.title.trim() || m.title.length > 200) throw fail('材料需要标题和非空正文。');
    total += m.text.length;
    return { id: `E${i + 1}`, title: m.title.trim(), text: m.text };
  });
  if (total > 60000) throw fail('所有材料合计最多 60000 字，请先提取与问题相关的段落。');
  return { question: body.question.trim(), materials, maxRounds, model: body.model || undefined };
}

/** 固定三个角色 ID，允许用户替换名称、关注点和方法；不是训练模型权重。 */
export function validateExperts(value) {
  if (!Array.isArray(value) || value.length !== DEFAULT_EXPERTS.length) throw fail('需要三位专家配置。');
  return DEFAULT_EXPERTS.map(base => {
    const input = value.find(item => item && item.id === base.id);
    if (!input || typeof input.instructions !== 'string' || !input.instructions.trim() || input.instructions.length > 8000) throw fail('每位专家需要 1–8000 字方法说明。');
    const name = input.name ?? base.name;
    const focus = input.focus ?? base.focus;
    if (typeof name !== 'string' || !name.trim() || name.length > 80) throw fail('专家名称需要 1–80 字。');
    if (typeof focus !== 'string' || !focus.trim() || focus.length > 500) throw fail('研究侧重点需要 1–500 字。');
    return { ...base, name: name.trim(), focus: focus.trim(), instructions: input.instructions.trim() };
  });
}

async function bodyJSON(req) {
  if (!String(req.headers['content-type'] || '').startsWith('application/json')) throw fail('需要 application/json。', 415);
  let size = 0; const chunks = [];
  for await (const chunk of req) { size += chunk.length; if (size > 350000) throw fail('请求内容过大。', 413); chunks.push(chunk); }
  let parsed;
  try { parsed = JSON.parse(Buffer.concat(chunks).toString('utf8')); } catch { throw fail('JSON 格式无效。'); }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw fail('请求必须是 JSON 对象。');
  return parsed;
}

/** 可注入桥接器进行无账号协议测试；实际启动始终使用官方 Codex。 */
export async function createApplication({ dataDir = path.join(projectDir, '.local'), bridge: suppliedBridge, preview = false } = {}) {
  fs.mkdirSync(path.join(dataDir, 'workspace'), { recursive: true, mode: 0o700 });
  const store = new Store(dataDir);
  // Read verified method files on demand. No rule-analysis fallback when files are unavailable.
  let marketLensProfile;
  const getMarketLensProfile = () => marketLensProfile ??= loadMarketLensProfile();
  store.recoverInterrupted();
  const bridge = suppliedBridge || new CodexBridge({ dataDir, workspaceDir: path.join(dataDir, 'workspace') });
  const csrfToken = randomBytes(32).toString('hex');
  let active = null;
  let verifying = false;
  let authBusy = false;
  let verification = store.read('verification');
  let auth = { connected: false };
  let stopping = false;
  const streams = new Set();
  const tasks = new Set();
  const track = promise => { tasks.add(promise); promise.finally(() => tasks.delete(promise)).catch(() => {}); return promise; };
  const emit = value => {
    const frame = `data: ${JSON.stringify(value)}\n\n`;
    for (const res of streams) if (!res.write(frame)) { res.end(); streams.delete(res); }
  };
  const resetVerification = () => { verification = null; store.write('verification', null); };
  // 同步占位发生在任何身份查询 await 之前，避免双击或并发请求启动多个任务。
  const withLaunchLock = async action => {
    if (stopping) throw fail('平台正在关闭。', 503);
    if (active || verifying || authBusy) throw fail('已有任务运行中，请等待完成。', 409);
    authBusy = true;
    try { return await action(); } finally { authBusy = false; }
  };
  const readStatus = async () => {
    if (!verifying && !stopping) {
      try { auth = await bridge.account(); } catch { auth = { connected: false }; }
      if (!auth.connected && verification?.passed) resetVerification();
    }
    return { version: VERSION, codex: bridge.status(), auth, verification, verifying, activeRunId: active?.id || null, csrfToken, preview };
  };
  bridge.on('state', () => emit({ type: 'status' }));
  const ready = bridge.start().catch(() => {});
  const heartbeat = setInterval(() => { for (const res of streams) res.write(': heartbeat\n\n'); }, 15000);
  heartbeat.unref();

  const server = http.createServer(async (req, res) => {
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('Referrer-Policy', 'no-referrer');
    res.setHeader('Cross-Origin-Resource-Policy', 'same-origin');
    res.setHeader('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'");
    const json = (value, status = 200) => { res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' }); res.end(JSON.stringify(value)); };
    try {
      const host = req.headers.host || '';
      const permitted = /^(localhost|127\.0\.0\.1|\[::1\])(?::\d+)?$/.test(host) || (preview && /^terminal\.local(?::\d+)?$/.test(host));
      // The hosted dashboard may open only this document via a user gesture. APIs remain same-origin.
      const isMarketLensNavigation = req.method === 'GET' && String(req.url).split('?')[0] === '/market-lens.html'
        && req.headers['sec-fetch-mode'] === 'navigate' && req.headers['sec-fetch-dest'] === 'document'
        && req.headers['sec-fetch-user'] === '?1';
      if (!permitted || (req.headers['sec-fetch-site'] === 'cross-site' && !isMarketLensNavigation)) throw fail('仅允许本机同源访问。', 403);
      if (req.headers.origin && req.headers.origin !== `http://${host}`) throw fail('拒绝跨站请求。', 403);
      const url = new URL(req.url, `http://${host}`);
      const route = url.pathname;
      const method = req.method;
      if (!['GET', 'POST', 'PUT'].includes(method)) throw fail('不支持的请求方法。', 405);
      if (method !== 'GET') {
        const token = Buffer.from(String(req.headers['x-expert-csrf'] || ''));
        const expectedToken = Buffer.from(csrfToken);
        if (token.length !== expectedToken.length || !timingSafeEqual(token, expectedToken)) throw fail('页面会话失效，请刷新后重试。', 403);
      }
      if (method === 'GET' && route === '/api/status') { await ready; return json(await readStatus()); }
      if (method === 'GET' && route === '/api/models') { await ready; return json({ models: await bridge.models() }); }
      if (method === 'GET' && route === '/api/events') {
        res.writeHead(200, { 'Content-Type': 'text/event-stream; charset=utf-8', Connection: 'keep-alive', 'X-Accel-Buffering': 'no' });
        res.write(': connected\n\n'); streams.add(res); req.on('close', () => streams.delete(res)); return;
      }
      if (method === 'GET' && route === '/api/market-lens/profile') return json(getMarketLensProfile().publicProfile);
      if (method === 'GET' && route === '/api/experts') return json({ experts: store.read('experts', DEFAULT_EXPERTS) });
      if (method === 'PUT' && route === '/api/experts') {
        const experts = validateExperts((await bodyJSON(req)).experts); store.write('experts', experts); return json({ experts });
      }
      if (method === 'GET' && route === '/api/runs') return json({ runs: store.runs() });
      if (method === 'POST' && route.startsWith('/api/auth/')) {
        const body = await bodyJSON(req);
        return await withLaunchLock(async () => {
        if (route === '/api/auth/start') {
          if (!['chatgpt', 'chatgptDeviceCode'].includes(body.type)) throw fail('不支持的登录方式。');
          resetVerification();
          const result = await bridge.login(body.type); emit({ type: 'status' }); return json(result);
        }
        if (route === '/api/auth/cancel') { await bridge.cancelLogin(body.loginId); return json({ ok: true }); }
        if (route === '/api/auth/logout') { resetVerification(); await bridge.logout(); emit({ type: 'status' }); return json({ ok: true }); }
        throw fail('未找到该地址。', 404);
        });
      }
      if (method === 'POST' && route === '/api/verify') {
        const body = await bodyJSON(req);
        return await withLaunchLock(async () => {
        auth = await bridge.account(); if (!auth.connected) throw fail('请先用 ChatGPT 登录 Codex。', 409);
        if (body.model != null && (typeof body.model !== 'string' || body.model.length > 100)) throw fail('模型名称无效。');
        verifying = true; resetVerification(); emit({ type: 'status' });
        const verifyTask = (async () => {
          let first; const marker = `COUNCIL-${randomBytes(6).toString('hex')}`;
          try {
            first = await bridge.generate({ prompt: `连接测试。请记住此校验标识：${marker}。不要使用任何工具。请仅回复：连接验证成功`, model: body.model, role: 'connection_check' });
            // 必须重启进程后恢复，才能验证持久化历史，而非同一进程内的缓存。
            await bridge.close();
            if (stopping) throw new Error('平台正在关闭，连接验证已中止。');
            await bridge.start();
            const second = await bridge.generate({ threadId: first.threadId, prompt: '不要使用工具。请仅回复我上一条消息提供的完整校验标识，不加标点或解释。', model: body.model, role: 'connection_check' });
            verification = { passed: first.text.trim() === '连接验证成功' && second.text.trim() === marker, checkedAt: new Date().toISOString(), model: body.model || null, threadId: first.threadId, firstReply: first.text, resumedReply: second.text, processRestarted: true };
            if (!verification.passed) verification.error = '已收到模型回复，但未通过精确回答和会话恢复检查。';
          } catch (error) {
            verification = { passed: false, checkedAt: new Date().toISOString(), threadId: first?.threadId || null, firstReply: first?.text || '', error: error.message };
          } finally { verifying = false; store.write('verification', verification); emit({ type: 'status' }); }
          return verification;
        })();
        return json(await track(verifyTask));
        });
      }
      if (method === 'POST' && (route === '/api/runs' || route === '/api/market-lens/runs')) {
        const isMarketLens = route === '/api/market-lens/runs';
        const body = await bodyJSON(req);
        const input = isMarketLens ? createMarketLensResearch(body, getMarketLensProfile()) : validateResearch(body);
        return await withLaunchLock(async () => {
        auth = await bridge.account();
        if (!auth.connected) throw fail('请先完成 ChatGPT 登录。', 409);
        if (!verification?.passed) throw fail('请先完成真实连接验证。', 409);
        if ((input.model || null) !== (verification.model || null)) throw fail('模型已更换，请先验证当前选择的模型。', 409);
        const run = createRun({ ...input, experts: isMarketLens ? input.experts : store.read('experts', DEFAULT_EXPERTS) });
        if (isMarketLens) run.marketLens = input.marketLens;
        const controller = new AbortController(); active = { id: run.id, controller };
        store.write(`runs/${run.id}`, run); emit({ type: 'run', run });
        let lastSave = 0;
        const task = executeCouncil(run, { bridge, signal: controller.signal, onUpdate: async current => {
          const now = Date.now();
          if (now - lastSave > 200 || current.status !== 'running') { store.write(`runs/${run.id}`, current); emit({ type: 'run', run: current }); lastSave = now; }
        } }).catch(error => { run.status = controller.signal.aborted ? 'cancelled' : 'failed'; run.error = error.message; })
          .finally(() => { store.write(`runs/${run.id}`, run); active = null; emit({ type: 'run', run }); emit({ type: 'status' }); });
        track(task);
        return json({ run }, 202);
        });
      }
      const match = route.match(/^\/api\/runs\/([a-zA-Z0-9-]+)(?:\/(cancel|report))?$/);
      if (match) {
        const run = store.read(`runs/${match[1]}`);
        if (!run) throw fail('未找到该会话。', 404);
        if (method === 'POST' && match[2] === 'cancel') {
          await bodyJSON(req);
          if (active?.id === run.id) active.controller.abort();
          return json({ ok: true });
        }
        if (method === 'GET' && match[2] === 'report') {
          res.writeHead(200, { 'Content-Type': 'text/markdown; charset=utf-8', 'Content-Disposition': `attachment; filename="council-${run.id}.md"` });
          return res.end(renderReport(run));
        }
        if (method === 'GET' && !match[2]) return json({ run });
      }
      const assets = { '/': ['index.html','text/html'], '/app.js':['app.js','text/javascript'], '/style.css':['style.css','text/css'], '/market-lens.html':['market-lens.html','text/html'], '/market-lens.js':['market-lens.js','text/javascript'], '/market-lens.css':['market-lens.css','text/css'], '/market-lens-protocol.mjs':['market-lens-protocol.mjs','text/javascript'] };
      if (method === 'GET' && assets[route]) {
        const [name, mime] = assets[route]; res.writeHead(200, { 'Content-Type': `${mime}; charset=utf-8` });
        return res.end(fs.readFileSync(path.join(projectDir, 'public', name)));
      }
      throw fail('未找到该地址。', 404);
    } catch (error) { if (!res.headersSent) json({ error: error.message || '请求失败。' }, error.status || 500); else res.end(); }
  });
  server.requestTimeout = 30000; server.headersTimeout = 10000;
  return { server, bridge, store, ready, async close() {
    if (stopping) return; stopping = true; active?.controller.abort(); clearInterval(heartbeat);
    for (const stream of streams) stream.end();
    await bridge.close(); await Promise.allSettled([...tasks]); await bridge.close();
    if (server.listening) await new Promise(resolve => server.close(resolve));
  } };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const preview = process.argv.includes('--preview');
  const port = Number(process.env.EXPERT_PORT || (preview ? 4173 : 4317));
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('EXPERT_PORT 无效。');
  const app = await createApplication({ preview });
  app.server.listen(port, preview ? '0.0.0.0' : '127.0.0.1', () => console.log(`专家圆桌： http://127.0.0.1:${port}`));
  for (const event of ['SIGINT','SIGTERM']) process.on(event, async () => { await app.close(); process.exit(0); });
}
