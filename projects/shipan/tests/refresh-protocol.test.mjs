import test from 'node:test';
import assert from 'node:assert/strict';
import {refreshTarget,refreshSummary,REFRESH_PROTOCOL} from '../lib/refresh-protocol.mjs';

test('updater accepts only the fixed universe and rejects mismatched identity',()=>{
  assert.equal(refreshTarget('https://example.com/api/refresh'),null);
  assert.equal(refreshTarget('https://example.com/api/refresh?symbol=510300&kind=etf').symbol,'510300');
  for(const query of ['symbol=510300&kind=index','kind=etf','symbol=999999&kind=etf']){
    assert.throws(()=>refreshTarget('https://example.com/api/refresh?'+query));
  }
  assert.equal(REFRESH_PROTOCOL.universe.length,16);
});

test('readback never treats newly fetched yesterday data as current-day success',()=>{
  const now=Date.parse('2026-10-23T10:00:00+08:00');
  const result=refreshSummary({status:'live',as_of:'2026-10-22T15:00:00+08:00',
    fetched_at:'2026-10-23T01:59:00Z',bars:[{time:'2026-10-22 15:00',close:1}]},now);
  assert.equal(result.status,'stale');assert.equal(result.freshness,'previous_day');
  assert.equal(result.source_date,'2026-10-22');assert.equal(result.fetched_at,'2026-10-23T01:59:00Z');
  assert.equal(result.points,1);assert.equal(refreshSummary(null,now).status,'unavailable');
});

test('quote summary counts each row source date instead of trusting global newest time',()=>{
  const now=Date.parse('2026-10-23T10:00:00+08:00');
  const result=refreshSummary({status:'live',as_of:'2026-10-23T10:00:00+08:00',instruments:[
    {price:1,status:'live',as_of:'2026-10-23T10:00:00+08:00'},
    {price:2,status:'live',as_of:'2026-10-22T15:00:00+08:00'},
    {price:null,status:'unavailable',as_of:null},
  ]},now);
  assert.equal(result.quotes,2);assert.equal(result.current_quotes,1);
});
