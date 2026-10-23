import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {buildCouncilBundle,councilOrigin,acceptsCouncilMessage,COUNCIL_PROTOCOL} from '../public/council-client.mjs';
import {validateMarketLensBundle} from '../investment-agent/src/market-lens.mjs';
const actual=JSON.parse(fs.readFileSync(new URL('../examples/fixtures/council-market-real.json',import.meta.url),'utf8'));
const input=()=>({snapshot:structuredClone(actual.snapshot),selected:structuredClone(actual.snapshot.selected),price:structuredClone(actual.price_history),flow:structuredClone(actual.fund_flow_history),bundleId:actual.bundle_id,now:actual.analysis_asof});
test('actual recorded series retain every point/date/net amount in detached evidence',()=>{
 const raw=input(),before=JSON.stringify(raw),b=buildCouncilBundle(raw);
 assert.deepEqual(b.price_history.bars,actual.price_history.bars);assert.deepEqual(b.fund_flow_history.bars,actual.fund_flow_history.bars);
 assert.equal(JSON.stringify(raw),before);raw.flow.bars[0].main_net=0;assert.notEqual(b.fund_flow_history.bars[0].main_net,0);
});
test('credentials and service configuration are never part of exported market fields',()=>{
 const raw=input();raw.snapshot.token='test-secret';raw.snapshot.authorization='test-secret';raw.selected.password='test-secret';raw.flow.headers={authorization:'test-secret'};
 assert.equal(JSON.stringify(buildCouncilBundle(raw)).includes('test-secret'),false);
});
test('wrong instrument and oversized history are rejected rather than sent or truncated',()=>{
 const raw=input();raw.flow.symbol='000300';assert.throws(()=>buildCouncilBundle(raw),/不一致/);
 raw.flow.symbol=raw.selected.symbol;raw.flow.bars=Array(261).fill(raw.flow.bars[0]);assert.throws(()=>buildCouncilBundle(raw),/260/);
});
test('only exact loopback popup and fresh channel may return council output',()=>{
 assert.equal(councilOrigin('http://127.0.0.1:4317'),'http://127.0.0.1:4317');
 for(const x of ['https://remote.test','http://127.0.0.1.evil.test','http://user:pass@localhost:4317','http://localhost:4317/path'])assert.throws(()=>councilOrigin(x));
 const session={popup:{},origin:'http://127.0.0.1:4317',channel:'fresh'},event={source:null,origin:session.origin,data:{protocol:COUNCIL_PROTOCOL,channel:'fresh',type:'run'}};
 event.source=session.popup;assert.equal(acceptsCouncilMessage(event,session),true);
 for(const patch of [{source:{}},{origin:'http://localhost:4317'},{data:{...event.data,channel:'stale'}}])assert.equal(acceptsCouncilMessage({...event,...patch},session),false);
});

test('recorded older-day evidence is marked stale relative to the actual freeze date without redating',()=>{
 const raw=input();raw.now='2026-10-23T03:00:00Z';
 const b=buildCouncilBundle(raw);
 assert.equal(b.source_dates.today,'2026-10-23');assert.equal(b.source_dates.price,'2026-10-21');
 assert.equal(b.source_dates.all_current_day,false);assert.equal(b.price_history.status,'stale');assert.equal(b.snapshot.selected.status,'stale');
 assert.deepEqual(b.price_history.bars,raw.price.bars);assert.equal(b.price_history.as_of,raw.price.as_of);
 assert.match(b.data_boundaries.join(' '),/非今日或缓存记录不得描述为今日实时行情/);
 assert.doesNotThrow(()=>validateMarketLensBundle(b));
});

test('actual Oct21 quote remains historical while actual Oct22 series pass the existing Agent importer',()=>{
 const raw=input();
 raw.price=JSON.parse(fs.readFileSync(new URL('../examples/fixtures/price-refresh-510300-real.json',import.meta.url),'utf8'));
 raw.flow=JSON.parse(fs.readFileSync(new URL('../examples/fixtures/etf-minutes-510300-20261022.json',import.meta.url),'utf8'));
 raw.now='2026-10-22T03:00:00Z';
 const b=buildCouncilBundle(raw);
 assert.equal(b.source_dates.quote_matches_series,false);assert.equal(b.snapshot.selected.price,null);assert.equal(b.snapshot.selected.as_of,null);
 assert.equal(b.snapshot.historical_selected.price,raw.selected.price);assert.equal(b.snapshot.historical_selected.as_of,raw.selected.as_of);
 assert.equal(b.snapshot.historical_selected.status,'stale');
 assert.deepEqual(b.price_history.bars,raw.price.bars);assert.deepEqual(b.fund_flow_history.bars,raw.flow.bars);
 assert.doesNotThrow(()=>validateMarketLensBundle(b));
});

test('different real source-day histories are rejected before opening a mixed-day council',()=>{
 const raw=input();raw.flow=JSON.parse(fs.readFileSync(new URL('../examples/fixtures/etf-minutes-510300-20261022.json',import.meta.url),'utf8'));
 assert.throws(()=>buildCouncilBundle(raw),/价格分时为 2026-10-21，资金分时为 2026-10-22/);
});
