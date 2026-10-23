import test from 'node:test';
import assert from 'node:assert/strict';
import {
  resolveIntradayInstrument,
  intradayFlowUrl,
  parseIntradayFlow,
  createIntradayFlowClient,
} from '../lib/intraday-flow.mjs';

// Synthetic protocol fixtures only; no fixture is published as a market observation.
const fixture = (klines = ['2026-10-21 09:31,10,-2,-8,4,6'], changes = {}) => ({
  rc: 0,
  data: {code: '510300', market: 1, name: 'Synthetic ETF fixture', klines, ...changes},
});
const sourceEpoch = time => Date.parse(time);

test('allowlist resolves ETF and index separately and pins the upstream URL', () => {
  assert.equal(resolveIntradayInstrument('510300', 'etf').exchange, 'sh');
  assert.equal(resolveIntradayInstrument('000300', 'index').kind, 'index');
  const url = new URL(intradayFlowUrl('000300', 'index'));
  assert.equal(url.origin, 'https://push2.eastmoney.com');
  assert.equal(url.pathname, '/api/qt/stock/fflow/kline/get');
  assert.equal(url.searchParams.get('secid'), '1.000300');
  assert.equal(url.searchParams.get('klt'), '1');
  assert.equal(url.searchParams.get('lmt'), '0');
  for (const [symbol, kind] of [['000300', 'etf'], ['510300', 'index'], ['https://example.com', 'etf'], ['600519', 'stock']]) {
    assert.throws(() => resolveIntradayInstrument(symbol, kind), error => error.status === 400);
  }
});

test('parsed minute amounts keep CNY units, real zero, and missing optional fields', () => {
  const result = parseIntradayFlow(fixture([
    '2026-10-21 09:31,0,0,0,0,0',
    '2026-10-21 09:32,10,-,-8,4,6',
  ]), '510300', 'etf');
  assert.equal(result.symbol, '510300');
  assert.equal(result.kind, 'etf');
  assert.equal(result.scope, 'instrument');
  assert.equal(result.unit, 'CNY');
  assert.equal(result.trade_date, '2026-10-21');
  assert.equal(result.status, 'partial');
  assert.equal(result.bars.length, 2);
  assert.equal(result.bars[0].main_net, 0);
  assert.equal(result.bars[1].main_net, 10);
  assert.equal(result.bars[1].small_net, null);
  assert.equal(result.bars[1].medium_net, -8);
  assert.equal(result.bars[1].large_net, 4);
  assert.equal(result.bars[1].super_net, 6);
  assert.equal(sourceEpoch(result.as_of), Date.parse('2026-10-21T09:32:00+08:00'));
});

test('wrong instrument identity, absent data and oversized responses never become chart history', () => {
  for (const changes of [{market: 0}, {code: '000300'}, {code: undefined}, {market: undefined}]) {
    assert.throws(() => parseIntradayFlow(fixture(undefined, changes), '510300', 'etf'));
  }
  for (const market of [null, '', false, [], [0], {}, '00']) {
    assert.throws(() => parseIntradayFlow(fixture(undefined, {code: '159915', market}), '159915', 'etf'));
  }
  for (const market of [0, '0']) {
    assert.equal(parseIntradayFlow(fixture(undefined, {code: '159915', market}), '159915', 'etf').symbol, '159915');
  }
  for (const market of [1, '1']) {
    assert.equal(parseIntradayFlow(fixture(undefined, {market}), '510300', 'etf').symbol, '510300');
  }
  assert.throws(() => parseIntradayFlow({rc: 0, data: null}, '510300', 'etf'));
  assert.throws(() => parseIntradayFlow({rc: 1, data: fixture().data}, '510300', 'etf'));
  assert.throws(() => parseIntradayFlow(fixture([]), '510300', 'etf'));
  assert.throws(() => parseIntradayFlow(fixture(Array(501).fill('2026-10-21 09:31,10,-2,-8,4,6')), '510300', 'etf'));
});

test('bad dates, missing main amount, lunch points and inconsistent money leave gaps', () => {
  const result = parseIntradayFlow(fixture([
    '2026-10-21 09:31,10,-2,-8,4,6',
    '2026-02-30 09:32,10,-2,-8,4,6',
    '2026-10-21 24:00,10,-2,-8,4,6',
    '2026-10-21 12:00,10,-2,-8,4,6',
    '2026-10-21 09:32,-,-2,-8,4,6',
    '2026-10-21 09:33,1000,-2,-8,4,6',
    '2026-10-21 09:34,NaN,-2,-8,4,6',
    '2026-10-21 09:35,Infinity,-2,-8,4,6',
    '2026-10-21 09:36,10,-2,-8,4,6,unexpected',
    '2026-10-21 09:37,20,-4,-16,8,12',
  ]), '510300', 'etf');
  assert.equal(result.status, 'partial');
  assert.equal(result.bars.length, 2);
  assert.deepEqual(result.bars.map(row => row.main_net), [10, 20]);
  assert.equal(result.coverage.invalid_rows, 8);
  assert.ok(result.coverage.missing_minutes > 0);
});

test('only the latest source day is kept, minute duplicates resolve deterministically, and times sort', () => {
  const result = parseIntradayFlow(fixture([
    '2026-10-20 15:00,100,-20,-80,40,60',
    '2026-10-21 09:32,10,-2,-8,4,6',
    '2026-10-21 09:31,0,0,0,0,0',
    '2026-10-21 09:32,20,-4,-16,8,12',
  ]), '510300', 'etf');
  assert.equal(result.trade_date, '2026-10-21');
  assert.equal(result.bars.length, 2);
  assert.deepEqual(result.bars.map(row => row.main_net), [0, 20]);
  assert.ok(sourceEpoch(result.bars[0].time) < sourceEpoch(result.bars[1].time));
  assert.equal(result.coverage.duplicate_rows, 1);
  assert.equal(result.coverage.dropped_other_day_rows, 1);
  const previousDay = parseIntradayFlow(fixture(['2026-10-20 09:31,10,-2,-8,4,6']), '510300', 'etf');
  assert.equal(previousDay.trade_date, '2026-10-20');
});

test('concurrent misses have independent requests, settled cache copies are isolated, and identity is part of the key', async () => {
  let clock = Date.parse('2026-10-21T09:32:00+08:00'), calls = 0;
  const client = createIntradayFlowClient({now: () => clock, fetchImpl: async url => {
    calls++;
    const [market, code] = new URL(url).searchParams.get('secid').split('.');
    return Response.json(fixture(undefined, {code, market: Number(market)}));
  }});
  const [first, second] = await Promise.all([client.getIntradayFlow('510300', 'etf'), client.getIntradayFlow('510300', 'etf')]);
  assert.equal(calls, 2);
  assert.deepEqual(first, second);
  first.bars[0].main_net = 9999;
  assert.equal(second.bars[0].main_net, 10);
  assert.equal((await client.getIntradayFlow('510300', 'etf')).bars[0].main_net, 10);
  const index = await client.getIntradayFlow('000300', 'index');
  assert.equal(index.symbol, '000300');
  assert.equal(calls, 3);
  clock += 60_001;
  await client.getIntradayFlow('510300', 'etf');
  assert.equal(calls, 4);
});

test('failed or mismatched refresh preserves original source and acquisition times as cached', async () => {
  let clock = Date.parse('2026-10-21T09:32:00+08:00'), outage = false;
  const client = createIntradayFlowClient({now: () => clock, fetchImpl: async () => {
    if (outage) return Response.json(fixture(undefined, {code: '000300'}));
    return Response.json(fixture());
  }});
  const first = await client.getIntradayFlow('510300', 'etf');
  assert.equal(first.status, 'live');
  clock += 60_001; outage = true;
  const cached = await client.getIntradayFlow('510300', 'etf');
  assert.equal(cached.status, 'cached');
  assert.equal(cached.stale, true);
  assert.equal(cached.as_of, first.as_of);
  assert.equal(cached.fetched_at, first.fetched_at);
  assert.equal(cached.trade_date, first.trade_date);
  assert.equal(cached.bars[0].main_net, 10);
  assert.notEqual(cached.served_at, first.served_at);
  assert.ok(cached.error);
});

test('a first-load failure has no invented points or data timestamp', async () => {
  const client = createIntradayFlowClient({fetchImpl: async () => {throw new Error('Synthetic network outage');}});
  const result = await client.getIntradayFlow('000300', 'index');
  assert.equal(result.status, 'unavailable');
  assert.deepEqual(result.bars, []);
  assert.equal(result.as_of, null);
  assert.equal(result.fetched_at, null);
  assert.equal(result.symbol, '000300');
  assert.equal(result.kind, 'index');
});

test('actual Eastmoney 302 redirect keeps the requested symbol and shared timeout signal', async () => {
  const calls=[];
  const client=createIntradayFlowClient({now:()=>Date.parse('2026-10-21T09:32:00+08:00'),fetchImpl:async(url,options)=>{
    calls.push({url,options});
    if(calls.length===1)return new Response(null,{status:302,headers:{location:'//push2delay.eastmoney.com/api/qt/stock/fflow/kline/get?secid=1.000300'}});
    return Response.json(fixture());
  }});
  const result=await client.getIntradayFlow('510300','etf');
  assert.equal(result.status,'live');
  assert.equal(calls.length,2);
  assert.equal(new URL(calls[1].url).hostname,'push2delay.eastmoney.com');
  assert.equal(new URL(calls[1].url).search,new URL(calls[0].url).search);
  assert.equal(calls[1].options.signal,calls[0].options.signal);
  assert.deepEqual(calls[1].options.headers,{Accept:'application/json'});
  assert.equal(calls[1].options.redirect,'manual');
});

test('unsafe redirects and loops are rejected without following untrusted locations', async () => {
  for(const location of ['https://example.com/api/qt/stock/fflow/kline/get','http://push2delay.eastmoney.com/api/qt/stock/fflow/kline/get','https://push2delay.eastmoney.com/other','https://user:pass@push2delay.eastmoney.com/api/qt/stock/fflow/kline/get','https://push2delay.eastmoney.com:8443/api/qt/stock/fflow/kline/get']){
    let calls=0;
    const client=createIntradayFlowClient({fetchImpl:async()=>{calls++;return new Response(null,{status:302,headers:{location}});}});
    assert.equal((await client.getIntradayFlow('510300','etf')).status,'unavailable');
    assert.equal(calls,1);
  }
  let calls=0;
  const loop=createIntradayFlowClient({fetchImpl:async url=>{calls++;return new Response(null,{status:302,headers:{location:url}});}});
  assert.equal((await loop.getIntradayFlow('510300','etf')).status,'unavailable');
  assert.equal(calls,4);
});
