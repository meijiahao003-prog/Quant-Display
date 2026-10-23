import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { DatabaseSync } from 'node:sqlite';
import { normalizeMacroWrite, macroResponse, readMacroBody, MACRO_MAX_BYTES, MACRO_SOURCE_PAGE } from '../lib/macro-brief.mjs';

// All values below are synthetic boundary fixtures, not investment conclusions.
const now = Date.parse('2026-10-23T08:40:00+08:00');
const fixture = () => ({ reportDate: '2026-10-23', generatedAt: '2026-10-23T08:30:00+08:00',
  title: 'Synthetic report', stance: 'Pending confirmation', summary: 'Synthetic validation fixture only.',
  dimensions: [{ name: 'Source coverage', signal: 'Partial', evidence: 'Fixture data has a documented missing source.' }],
  watchpoints: ['Check the next publication'], risks: ['Source gap'],
  sources: [{ title: 'Macro source page', url: MACRO_SOURCE_PAGE, asOf: '2026-09' }], dataStatus: 'partial' });

test('brief normalization preserves real source periods independently of report generation and does not mutate input', () => {
  const input = fixture(), before = JSON.stringify(input), result = normalizeMacroWrite({ brief: input }, now).brief;
  assert.equal(result.generatedAt, '2026-10-23T00:30:00.000Z');
  assert.equal(result.reportDate, '2026-10-23'); assert.equal(result.sources[0].asOf, '2026-09');
  assert.equal(result.sourcePageUrl, MACRO_SOURCE_PAGE); assert.match(result.handbookUrl, /macro-handbook\.html$/);
  assert.equal(JSON.stringify(input), before);
});

test('empty, ambiguous and malformed reports cannot be persisted', () => {
  for (const body of [null, [], {}, { brief: {} }, { brief: fixture(), failure: {} }, { brief: { ...fixture(), summary: '' } },
    { brief: { ...fixture(), dimensions: [] } }, { brief: { ...fixture(), sources: [] } }, { brief: { ...fixture(), dataStatus: 'live' } }]) {
    assert.throws(() => normalizeMacroWrite(body, now));
  }
});

test('future and invalid days, future timestamps, and unspecified timezones are rejected', () => {
  for (const patch of [{ reportDate: '2026-10-24' }, { reportDate: '2026-02-30' }, { reportDate: '2026-1-1' },
    { generatedAt: '2026-10-23T08:41:00+08:00' }, { generatedAt: '2026-10-23T08:30:00' }, { generatedAt: '2026-10-22T08:30:00+08:00' }]) {
    assert.throws(() => normalizeMacroWrite({ brief: { ...fixture(), ...patch } }, now));
  }
});

test('unsupported source URLs and source-date relabeling are rejected', () => {
  for (const url of ['javascript:alert(1)', 'http://example.com', '//evil.example', 'https://user:password@example.com', 'file:///tmp/x']) {
    assert.throws(() => normalizeMacroWrite({ brief: { ...fixture(), handbookUrl: url } }, now));
  }
  assert.throws(() => normalizeMacroWrite({ brief: { ...fixture(), sourcePageUrl: 'https://example.com' } }, now));
  for (const asOf of ['today', '', '2026-13', '2026-11', '2026-10-24', '2026-Q5', '2027-Q1']) {
    assert.throws(() => normalizeMacroWrite({ brief: { ...fixture(), sources: [{ title: 'source', url: MACRO_SOURCE_PAGE, asOf }] } }, now));
  }
});

test('unknown date remains null and valid frequency-specific periods remain unchanged', () => {
  for (const asOf of [null, '2026', '2026-Q3', '2026-09', '2026-10-22', '2026-10-23T08:00:00+08:00']) {
    const result = normalizeMacroWrite({ brief: { ...fixture(), sources: [{ title: 'source', url: MACRO_SOURCE_PAGE, asOf }] } }, now);
    assert.equal(result.brief.sources[0].asOf, asOf);
  }
  const relative = normalizeMacroWrite({ brief: { ...fixture(), handbookUrl: '/macro-handbook.html' } }, now);
  assert.equal(relative.brief.handbookUrl, '/macro-handbook.html');
});

test('failed attempts have a separate write shape and leave no replacement brief', () => {
  const failure = { reportDate: '2026-10-23', attemptedAt: '2026-10-23T08:35:00+08:00', message: 'Synthetic timeout' };
  const result = normalizeMacroWrite({ failure }, now);
  assert.equal(result.failure.status, 'failed'); assert.equal(result.brief, undefined);
  assert.equal(result.failure.attemptedAt, '2026-10-23T00:35:00.000Z');
  assert.throws(() => normalizeMacroWrite({ failure: { ...failure, message: '' } }, now));
  assert.throws(() => normalizeMacroWrite({ failure: { ...failure, reportDate: '2026-10-24' } }, now));
});

test('Beijing midnight changes report freshness, never its source dates or contents', () => {
  const brief = normalizeMacroWrite({ brief: fixture() }, now).brief, before = JSON.stringify(brief);
  assert.equal(macroResponse(null, null, now).freshness, 'missing');
  assert.equal(macroResponse(brief, null, now).freshness, 'today');
  assert.equal(macroResponse(brief, null, Date.parse('2026-10-23T16:00:00Z')).freshness, 'stale');
  assert.equal(macroResponse(brief, null, Date.parse('2026-10-23T15:59:59Z')).freshness, 'today');
  assert.equal(JSON.stringify(brief), before);
  const failure = { reportDate: '2026-10-24', status: 'failed', message: 'Source unavailable' };
  const result = macroResponse(brief, failure, Date.parse('2026-10-24T01:00:00Z'));
  assert.equal(result.brief, brief); assert.equal(result.latestAttempt, failure);
});

test('body reader supports actual UTF-8 JSON and rejects malformed or oversized content without Content-Length', async () => {
  const value = { brief: { ...fixture(), title: '实际中文字符' } };
  assert.deepEqual(await readMacroBody(new Request('https://example.com/api/macro', { method: 'POST', body: JSON.stringify(value) })), value);
  await assert.rejects(readMacroBody(new Request('https://example.com', { method: 'POST', body: '{bad' })), /UTF-8 JSON/);
  await assert.rejects(readMacroBody(new Request('https://example.com', { method: 'POST', body: 'x'.repeat(MACRO_MAX_BYTES + 1) })), /128 KiB/);
  await assert.rejects(readMacroBody(new Request('https://example.com', { method: 'POST' })), /缺少/);
});

test('migration creates distinct daily-report and attempt storage, daily uniqueness is enforced', () => {
  const db = new DatabaseSync(':memory:');
  db.exec(readFileSync(new URL('../drizzle/0003_watery_stone_men.sql', import.meta.url), 'utf8'));
  const tables = db.prepare("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").all().map(row => row.name);
  assert.deepEqual(tables, ['macro_daily_briefs', 'macro_update_attempts']);
  const report = db.prepare('INSERT INTO macro_daily_briefs VALUES (?, ?, ?)');
  report.run('2026-10-23', '2026-10-23T00:30:00.000Z', JSON.stringify(fixture()));
  assert.throws(() => report.run('2026-10-23', '2026-10-23T00:35:00.000Z', '{}'), /UNIQUE/);
  db.prepare('INSERT INTO macro_update_attempts VALUES (?, ?, ?, ?)').run('2026-10-23', '2026-10-23T00:35:00.000Z', 'failed', 'Synthetic failure');
  assert.equal(db.prepare('SELECT count(*) AS n FROM macro_daily_briefs').get().n, 1);
  assert.equal(JSON.parse(db.prepare('SELECT payload FROM macro_daily_briefs').get().payload).title, fixture().title);
  db.close();
});
