import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {retainQuoteSnapshot,failedSnapshot} from '../public/quote-snapshot.mjs';
import {retainFlowSnapshot} from '../public/flow-snapshot.mjs';
const {before_snapshot:actual}=JSON.parse(fs.readFileSync(new URL('../examples/fixtures/index-watchlist-real.json',import.meta.url),'utf8'));
const now=Date.parse('2026-10-21T13:45:00+08:00');

test('actual quote values/source times survive failed snapshot; cache labels reach every row',()=>{
  const before=JSON.stringify(actual),result=retainFlowSnapshot(actual,failedSnapshot(actual,'synthetic connection failure',now),now);
  assert.equal(result.instruments.length,actual.instruments.length);assert.equal(result.status,'cached');
  for(let i=0;i<actual.instruments.length;i++){
    const old=actual.instruments[i],row=result.instruments[i];
    for(const key of ['symbol','kind','price','change_pct','amount','volume','as_of'])assert.equal(row[key],old[key]);
    assert.equal(row.status,'cached');assert.equal(row.source_status,'cached');assert.equal(row.fetched_at,old.fetched_at??actual.fetched_at);
  }
  assert.equal(JSON.stringify(actual),before);
});

test('an older or missing price never overwrites newer real prices or independent incoming flow fields',()=>{
  const incoming={...actual,instruments:actual.instruments.map(row=>({...row,price:null,status:'unavailable',as_of:null,main_net:123,flow_as_of:'2026-10-21T13:44:00+08:00'}))};
  const result=retainQuoteSnapshot(actual,incoming,now);
  assert.equal(result.instruments[6].price,actual.instruments[6].price);assert.equal(result.instruments[6].main_net,123);
  assert.equal(result.instruments[6].flow_as_of,'2026-10-21T13:44:00+08:00');
  const old={...actual,instruments:actual.instruments.map(row=>({...row,as_of:'2026-10-21T09:31:00+08:00'}))};
  assert.equal(retainQuoteSnapshot(actual,old,now).instruments[6].as_of,actual.instruments[6].as_of);
});

test('rollover keeps genuine old-day quotes marked stale until a new source day arrives',()=>{
  const tomorrow=Date.parse('2026-10-22T09:35:00+08:00');
  const old=failedSnapshot(actual,'synthetic next-day outage',tomorrow);
  assert.equal(old.status,'stale');assert.equal(old.instruments[6].status,'stale');
  assert.equal(old.instruments[6].as_of,actual.instruments[6].as_of);assert.equal(old.instruments[6].source_date,'2026-10-21');
  // Explicit synthetic rollover envelope, not a real next-day quote.
  const fresh={...actual,instruments:[{...actual.instruments[6],as_of:'2026-10-22T09:34:00+08:00',fetched_at:'2026-10-22T01:34:01Z',price:4.2,status:'live'}]};
  const updated=retainQuoteSnapshot(old,fresh,tomorrow);
  assert.equal(updated.instruments[0].price,4.2);assert.equal(updated.instruments[0].status,'live');assert.equal(updated.source_date,'2026-10-22');
});

test('quote retention is identity-bound and never crosses demo mode',()=>{
  const unmatched={...actual,instruments:[{symbol:'999999',kind:'etf',price:null,status:'unavailable',as_of:null}]};
  assert.equal(retainQuoteSnapshot(actual,unmatched,now).instruments[0].price,null);
  const demo={...actual,mode:'demo'};
  assert.equal(retainQuoteSnapshot(actual,demo,now).mode,'demo');
  assert.equal(retainQuoteSnapshot(demo,unmatched,now).instruments[0].price,null);
});
