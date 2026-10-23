/**
 * Runnable: node --test tests/market-lens-protocol.test.mjs
 * Inputs are explicitly synthetic protocol fixtures; no account/model calls.
 * Expected output: all assertions pass. Before/after: no persistent state changes.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { PROTOCOL, SITE_ORIGIN, allowedOrigin, readChannel, acceptsMessage, validateBundle, publicRun } from '../public/market-lens-protocol.mjs';

const channel = 'b9100d23-6400-4200-b0c7-f9f6bc030b79';
const fixture = () => ({
  schema_version: PROTOCOL, bundle_id: 'synthetic-test-001', timezone: 'Asia/Shanghai', mode: 'demo',
  analysis_asof: '2026-10-21T07:00:00Z', selected: { symbol: '510300', kind: 'etf', name: '合成 ETF' },
  question: '仅用于协议验证，不是投资结论。', snapshot: { status: 'demo' },
  price_history: { symbol: '510300', kind: 'etf', bars: [{ time: '09:31', close: 1 }] },
  fund_flow_history: { symbol: '510300', kind: 'etf', bars: [] },
});

test('origin whitelist rejects lookalikes, credentials, paths and non-loopback addresses', () => {
  for (const origin of [SITE_ORIGIN, 'http://127.0.0.1:4173', 'http://localhost:4317', 'http://[::1]:4317']) assert.equal(allowedOrigin(origin), true);
  for (const origin of [`${SITE_ORIGIN}.evil.test`, 'http://localhost.evil.test', 'http://127.0.0.2', 'http://user@localhost:4317', 'http://localhost:4317/path', 'https://localhost:4317', 'null']) assert.equal(allowedOrigin(origin), false);
});

test('channel verifies exact original opener, origin, nonce and allowed message type', () => {
  const pair = readChannel(`#origin=${encodeURIComponent(SITE_ORIGIN)}&channel=${channel}`);
  assert.deepEqual(pair, { origin: SITE_ORIGIN, channel });
  const opener = {};
  const event = { source: opener, origin: SITE_ORIGIN, data: { protocol: PROTOCOL, channel, type: 'bundle' } };
  assert.equal(acceptsMessage(event, pair, opener), true);
  assert.equal(acceptsMessage({ ...event, source: {} }, pair, opener), false);
  assert.equal(acceptsMessage({ ...event, origin: 'https://evil.test' }, pair, opener), false);
  assert.equal(acceptsMessage({ ...event, data: { ...event.data, channel: 'different' } }, pair, opener), false);
  assert.equal(acceptsMessage({ ...event, data: { ...event.data, type: 'login' } }, pair, opener), false);
  assert.equal(readChannel('#origin=https://evil.test&channel=aaaaaaaaaaaaaaaa'), null);
});

test('bundle validation preserves exact numeric data and detaches sender object', () => {
  const input = fixture();
  const result = validateBundle(input);
  assert.deepEqual(result, input);
  input.price_history.bars[0].close = 2;
  assert.equal(result.price_history.bars[0].close, 1);
  assert.equal(result.mode, 'demo');
});

test('bundle validation rejects mixed instruments, oversize history and missing schema', () => {
  const wrong = fixture(); wrong.fund_flow_history.symbol = '510500';
  assert.throws(() => validateBundle(wrong), /不一致/);
  const many = fixture(); many.price_history.bars = Array.from({ length: 261 }, () => ({ close: 1 }));
  assert.throws(() => validateBundle(many), /260/);
  assert.throws(() => validateBundle({ ...fixture(), schema_version: 'old' }), /数据包/);
  assert.throws(() => validateBundle({ ...fixture(), question: '' }), /研究问题/);
});

test('public run removes credentials, materials, raw streams, prompts and unrelated nested fields', () => {
  const result = publicRun({
    id: 'test-run-123', status: 'needs_evidence', question: 'synthetic', round: 1, maxRounds: 2, calls: 4,
    csrfToken: 'must-not-leave', auth: { email: 'must-not-leave' }, materials: [{ text: 'must-not-leave' }],
    experts: [{ instructions: 'must-not-leave' }], threads: { a: 'must-not-leave' }, rawResponses: ['must-not-leave'],
    rounds: [{ round: 1, secret: 'must-not-leave', opinions: [{ expertId: 'macro_environment', expertName: '宏观', stance: 'uncertain', summary: '证据不足',
      arguments: [{ claim: '缺资料', evidenceIds: ['E1'], secret: 'must-not-leave' }], risks: [], missing: ['资料'], prompt: 'must-not-leave' }],
      moderation: { summary: '等待资料', agreement: [], disagreements: [], missing: ['资料'], nextSteps: [], status: 'needs_evidence', secret: 'must-not-leave' } }],
    marketLens: { analysis_asof: '2026-10-21T07:00:00Z', selected: { symbol: '510300', kind: 'etf', name: '合成', secret: 'must-not-leave' }, experts: ['must-not-leave'] },
  });
  assert.equal(JSON.stringify(result).includes('must-not-leave'), false);
  assert.equal(result.rounds[0].opinions[0].summary, '证据不足');
  assert.deepEqual(result.rounds[0].opinions[0].arguments[0].evidenceIds, ['E1']);
  assert.equal(result.marketLens.selected.symbol, '510300');
});
