import test from 'node:test';
import assert from 'node:assert/strict';
import {getRecoveredEtfFlow} from '../lib/etf-flow-recovery.mjs';
import {FLOW_UNIVERSE} from '../lib/fund-flow.mjs';

// Synthetic protocol fixtures only. These are never presented as real market data.
const clock=Date.parse('2026-10-22T11:00:00+08:00');
const asOf='2026-10-22T10:55:00+08:00',fetchedAt='2026-10-22T02:55:04Z';
const row=(symbol='510300',changes={})=>({symbol,kind:'etf',unit:'CNY',status:'live',source:'synthetic ETF snapshot',
  trade_date:'2026-10-22',as_of:asOf,fetched_at:fetchedAt,main_net:100,super_net:70,large_net:30,medium_net:-40,small_net:-60,
  main_pct:10,...changes});
const history=(symbol='510300',changes={})=>({symbol,kind:'etf',scope:'instrument',unit:'CNY',status:'live',
  source:'synthetic ETF minutes',trade_date:'2026-10-22',as_of:asOf,fetched_at:fetchedAt,
  bars:[{time:asOf,main_net:100,super_net:70,large_net:30,medium_net:-40,small_net:-60}],...changes});
const unavailable=()=>({status:'unavailable',rows:[],error:'synthetic snapshot outage',fetched_at:null});
const run=changes=>getRecoveredEtfFlow({now:()=>clock,getSnapshotFlow:async()=>unavailable(),
  getStoredRows:async()=>({status:'ok',rows:[]}),getMinuteFlow:async symbol=>history(symbol),...changes});

test('cold snapshot outage recovers ranking and every structure field from minute data',async()=>{
  const result=await run();
  assert.equal(result.rows.length,10);assert.equal(result.status,'live');assert.equal(result.error,null);
  assert.equal(result.fallback_count,10);assert.match(result.errors[0],/snapshot outage/);
  const actual=result.rows[0];
  assert.equal(actual.main_net,100);assert.equal(actual.small_net,-60);assert.equal(actual.main_pct,null);
  assert.equal(actual.source_type,'provider_minutes');assert.equal(actual.as_of,asOf);assert.equal(actual.fetched_at,fetchedAt);
  assert.equal(actual.status,'live');assert.equal(actual.stale,false);
});

test('fresh complete snapshots avoid minute requests and keep percentages',async()=>{
  let calls=0;
  const result=await run({getSnapshotFlow:async()=>({status:'live',fetched_at:fetchedAt,rows:FLOW_UNIVERSE.map(x=>row(x.symbol))}),
    getMinuteFlow:async()=>{calls++;throw new Error('should not fetch');}});
  assert.equal(calls,0);assert.equal(result.status,'live');assert.equal(result.rows[0].main_pct,10);
  assert.equal(result.recovery_status,'not_needed');
});

test('recovery has five concurrent workers and a failing ETF leaves sibling rows intact',async()=>{
  let active=0,maximum=0;
  const calls=[];
  const result=await run({getMinuteFlow:async(symbol,kind)=>{
    assert.equal(kind,'etf');calls.push(symbol);active++;maximum=Math.max(maximum,active);
    try{await new Promise(resolve=>setTimeout(resolve,2));if(symbol==='510300')throw new Error('synthetic one ETF outage');return history(symbol);}
    finally{active--;}
  }});
  assert.equal(maximum,5);assert.equal(new Set(calls).size,10);assert.equal(result.status,'partial');
  assert.equal(result.rows[0].status,'unavailable');assert.equal(result.rows[0].main_net,null);
  assert.equal(result.rows.slice(1).every(x=>x.main_net===100),true);
});

test('wrong identity, scope, currency, source day and inconsistent amount cannot enter a row',async()=>{
  for(const changes of [{symbol:'000300'},{kind:'index'},{scope:'market'},{unit:'USD'},
    {trade_date:'2026-02-30'},{trade_date:'2026-10-21'},
    {bars:[{time:asOf,main_net:100,large_net:500,super_net:100}]}]){
    const result=await run({getMinuteFlow:async symbol=>history(symbol,changes)});
    assert.equal(result.rows[0].main_net,null);assert.equal(result.rows[0].status,'unavailable');
  }
});

test('zero is valid, missing optional values stay null and percentages are never reconstructed',async()=>{
  const result=await run({getMinuteFlow:async symbol=>history(symbol,{bars:[{time:asOf,main_net:0,large_net:0,super_net:0,medium_net:null,small_net:0}]})});
  assert.equal(result.status,'partial');assert.equal(result.rows[0].main_net,0);assert.equal(result.rows[0].medium_net,null);
  assert.equal(result.rows[0].small_net,0);assert.equal(result.rows[0].main_pct,null);
});

test('newer stored source timestamp beats older live responses and keeps acquisition time',async()=>{
  const savedTime='2026-10-22T10:58:00+08:00',savedFetch='2026-10-22T02:58:03Z';
  const saved=row('510300',{as_of:savedTime,fetched_at:savedFetch,main_net:150,super_net:100,large_net:50});
  const before=JSON.stringify(saved);
  const result=await run({getSnapshotFlow:async()=>({status:'live',rows:[row()]}),getStoredRows:async()=>({status:'ok',rows:[saved]})});
  assert.equal(result.rows[0].as_of,savedTime);assert.equal(result.rows[0].fetched_at,savedFetch);
  assert.equal(result.rows[0].main_net,150);assert.equal(result.rows[0].status,'cached');assert.equal(result.rows[0].source_status,'cached');
  assert.equal(result.status,'partial');assert.equal(JSON.stringify(saved),before);
});

test('today real minutes replace previous-day snapshot; all-old retained rows remain explicitly stale',async()=>{
  const old=row('510300',{trade_date:'2026-10-21',as_of:'2026-10-21T15:00:00+08:00',fetched_at:'2026-10-21T07:00:01Z'});
  const recovered=await run({getSnapshotFlow:async()=>({status:'live',rows:[old]})});
  assert.equal(recovered.rows[0].trade_date,'2026-10-22');assert.equal(recovered.rows[0].main_net,100);
  const retained=await run({getStoredRows:async()=>({rows:[old]}),getMinuteFlow:async()=>{throw new Error('outage');}});
  assert.equal(retained.status,'stale');assert.equal(retained.source_status,'cached');
  assert.equal(retained.rows[0].status,'stale');assert.equal(retained.rows[0].as_of,old.as_of);
  assert.equal(retained.rows[0].fetched_at,old.fetched_at);assert.equal(retained.rows[1].main_net,null);
});

test('equal source times prefer fresh upstream and do not mix old optional fields into newer rows',async()=>{
  const stored=row('510300',{main_net:200,super_net:100,large_net:100,medium_net:-200,small_net:0});
  const result=await run({getStoredRows:async()=>({rows:[stored]}),getMinuteFlow:async symbol=>history(symbol,{bars:[{time:asOf,main_net:100,super_net:70,large_net:30,medium_net:null,small_net:null}]})});
  assert.equal(result.rows[0].main_net,100);assert.equal(result.rows[0].medium_net,null);
  assert.equal(result.rows[0].status,'partial');assert.equal(result.rows[0].source_type,'provider_minutes');
});

test('snapshot and storage exceptions still allow minute recovery, and total failure returns unavailable',async()=>{
  const result=await run({getSnapshotFlow:async()=>{throw new Error('snapshot offline');},getStoredRows:async()=>{throw new Error('storage offline');}});
  assert.equal(result.status,'live');assert.equal(result.rows.length,10);assert.equal(result.storage_read_status,'unavailable');
  const missing=await run({getMinuteFlow:async()=>{throw new Error('minutes offline');}});
  assert.equal(missing.status,'unavailable');assert.equal(missing.fetched_at,null);assert.equal(missing.as_of,null);
  assert.equal(missing.rows.every(row=>row.main_net===null),true);
});
