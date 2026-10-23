import test from 'node:test';
import assert from 'node:assert/strict';
import { createRun, executeCouncil, DEFAULT_EXPERTS } from '../src/council.mjs';

// 协议测试专用脚本stub：以下内容是明确的虚构测试夹具，不是真实专家推理或市场数据。
const material = { id: 'E1', title: '虚构协议测试材料', text: '本材料仅用于测试消息协议，不包含市场行情。' };
const opinion = (overrides = {}) => ({ stance: 'support', summary: '虚构测试结论', arguments: [{ claim: '虚构测试主张', evidenceIds: ['E1'] }], risks: [], missing: [], confidence: 70, ...overrides });
const moderation = (overrides = {}) => ({ summary: '虚构测试综合', agreement: ['协议测试共识'], disagreements: [], missing: [], nextSteps: [], status: 'completed', ...overrides });

function scriptBridge(script) {
  const calls = [];
  return {
    calls,
    async generate(args) {
      calls.push(args);
      const answer = await script(args, calls);
      const text = typeof answer === 'string' ? answer : JSON.stringify(answer);
      await args.onDelta?.(text.slice(0, 10));
      return { threadId: args.threadId || `test-thread-${args.role}`, text };
    },
  };
}

test('首轮三个独立线程；专家prompt不包含他人首轮输出', async () => {
  const run = createRun({ question: '虚构测试问题', materials: [material] });
  const bridge = scriptBridge(args => args.role === 'moderator' ? moderation() : opinion({ summary: `独立标记-${args.role}` }));
  let updates = 0;
  await executeCouncil(run, { bridge, onUpdate: async () => { updates += 1; } });
  assert.equal(run.status, 'completed');
  assert.equal(run.calls, 4);
  assert.equal(run.rounds[0].opinions.length, 3);
  for (const call of bridge.calls.slice(0, 3)) {
    assert.equal(call.threadId, undefined);
    assert.doesNotMatch(call.prompt, /独立标记-/);
  }
  assert.match(bridge.calls[3].prompt, /独立标记-fundamental/);
  assert.equal(new Set(Object.values(run.threads)).size, 4);
  assert.equal(run.evidenceVerification, 'reference_ids_only');
  assert.ok(updates > 5);
});

test('主持人声称一致也不能抹去立场冲突，最多3轮12调用', async () => {
  const run = createRun({ question: '虚构分歧测试', materials: [material], maxRounds: 3 });
  const bridge = scriptBridge(args => args.role === 'moderator' ? moderation() : opinion({ stance: args.role === 'risk' ? 'oppose' : 'support' }));
  await executeCouncil(run, { bridge });
  assert.equal(run.status, 'disputed');
  assert.equal(run.rounds.length, 3);
  assert.equal(run.calls, 12);
  assert.ok(run.final.disagreements.some(item => item.includes('风控专家=oppose')));
  assert.ok(run.events.some(event => event.type === 'budget_reached'));
  const secondFundamental = bridge.calls.filter(call => call.role === 'fundamental')[1];
  assert.equal(secondFundamental.threadId, 'test-thread-fundamental');
  assert.match(secondFundamental.prompt, /上一轮会审数据/);
  assert.match(secondFundamental.prompt, /风控专家/);
});

test('不存在的材料引用强制needs_evidence，不视为有效事实', async () => {
  const run = createRun({ question: '虚构引用测试', materials: [material] });
  const bridge = scriptBridge(args => args.role === 'moderator' ? moderation() : opinion({ arguments: [{ claim: '虚构无效引用主张', evidenceIds: ['E999'] }] }));
  await executeCouncil(run, { bridge });
  assert.equal(run.status, 'needs_evidence');
  assert.equal(run.calls, 4);
  assert.match(run.final.missing.join('\n'), /不存在的材料：E999/);
});

test('即便stance相同，主持人也不能删除专家明确登记的异议', async () => {
  const run = createRun({ question: '虚构同立场分歧测试', materials: [material], maxRounds: 1 });
  const bridge = scriptBridge(args => args.role === 'moderator' ? moderation() : opinion({ risks: args.role === 'risk' ? ['异议：其他专家的情景假设尚未解决'] : ['风险：一般测试风险'] }));
  await executeCouncil(run, { bridge });
  assert.equal(run.status, 'disputed');
  assert.ok(run.final.disagreements.includes('风控专家：异议：其他专家的情景假设尚未解决'));
  assert.ok(run.final.disagreements.every(item => !item.includes('一般测试风险')));
});

test('专家报告的缺口不能被主持人删除', async () => {
  const run = createRun({ question: '虚构缺口测试', materials: [material] });
  const bridge = scriptBridge(args => args.role === 'moderator' ? moderation() : opinion({ missing: args.role === 'quant' ? ['尚无真实样本'] : [] }));
  await executeCouncil(run, { bridge });
  assert.equal(run.status, 'needs_evidence');
  assert.ok(run.final.missing.includes('量化专家：尚无真实样本'));
});

test('无材料时即便模型一致，也只能needs_evidence', async () => {
  const run = createRun({ question: '无资料测试' });
  const bridge = scriptBridge(args => args.role === 'moderator' ? moderation() : opinion({ arguments: [] }));
  await executeCouncil(run, { bridge });
  assert.equal(run.status, 'needs_evidence');
  assert.match(run.final.missing.join('\n'), /未提供可追溯/);
});

test('自报高置信度不能将uncertain变成completed', async () => {
  const run = createRun({ question: '虚构置信度测试', materials: [material] });
  const bridge = scriptBridge(args => args.role === 'moderator' ? moderation() : opinion({ stance: 'uncertain', confidence: 100 }));
  await executeCouncil(run, { bridge });
  assert.equal(run.status, 'needs_evidence');
});

test('格式失败明确failed，保留原始文本和其他专家成果', async () => {
  const run = createRun({ question: '虚构格式测试', materials: [material] });
  const bridge = scriptBridge(args => args.role === 'quant' ? '不是JSON的协议测试输出' : opinion());
  await executeCouncil(run, { bridge });
  assert.equal(run.status, 'failed');
  assert.equal(run.rounds[0].opinions.length, 2);
  assert.equal(run.rawResponses.length, 3);
  assert.equal(run.final, null);
  assert.match(run.error.message, /不符合JSON协议/);
  assert.equal(bridge.calls.length, 3);
});

test('取消时保留已完成意见与流式部分', async () => {
  const controller = new AbortController();
  const run = createRun({ question: '虚构取消测试', materials: [material] });
  const bridge = {
    async generate(args) {
      if (args.role === 'fundamental') return { threadId: 'saved', text: JSON.stringify(opinion()) };
      await args.onDelta('虚构流式片段');
      return new Promise((resolve, reject) => {
        if (args.signal.aborted) return reject(new DOMException('cancelled', 'AbortError'));
        args.signal.addEventListener('abort', () => reject(new DOMException('cancelled', 'AbortError')), { once: true });
      });
    },
  };
  await executeCouncil(run, {
    bridge, signal: controller.signal,
    onUpdate: async state => { if (state.rounds[0]?.opinions.length === 1) controller.abort(); },
  });
  assert.equal(run.status, 'cancelled');
  assert.equal(run.rounds[0].opinions.length, 1);
  assert.equal(run.threads.fundamental, 'saved');
  assert.match(run.live.quant.text, /虚构流式片段/);
});

test('启动前取消不会调用模型', async () => {
  const controller = new AbortController();
  controller.abort();
  const run = createRun({ question: '虚构预取消测试' });
  const bridge = scriptBridge(() => { throw new Error('不应调用'); });
  await executeCouncil(run, { bridge, signal: controller.signal });
  assert.equal(run.status, 'cancelled');
  assert.equal(run.calls, 0);
});

test('限制配置、ID和严格字段', async () => {
  assert.throws(() => createRun({ question: '测试', maxRounds: 4 }), /maxRounds/);
  assert.throws(() => createRun({ question: '测试', materials: [material, material] }), /不能重复/);
  assert.throws(() => createRun({ question: '测试', experts: DEFAULT_EXPERTS.slice(1) }), /3位专家/);
  const run = createRun({ question: '测试', materials: [material] });
  const bridge = scriptBridge(() => opinion({ unexpected: true }));
  await executeCouncil(run, { bridge });
  assert.equal(run.status, 'failed');
  assert.match(run.error.message, /字段必须恰好/);
});
