import test from 'node:test';
import assert from 'node:assert/strict';
import {persistIndexFlowHistories,scheduleIndexFlowStorage,selectFlowFallback} from '../lib/flow-recovery.mjs';
import {getWatchingSnapshot} from '../lib/watching-data.mjs';

// Explicit synthetic transport/storage fixtures; these amounts are not observations.
const stamp=Date.parse('2026-10-22T10:15:00+08:00');
const item={symbol:'510300',kind:'etf',name:'Fixture ETF'};
const history=(symbol='510300',day='2026-10-22',changes={})=>({symbol,kind:symbol==='510300'?'etf':'index',scope:'instrument',unit:'CNY',status:'live',stale:false,trade_date:day,as_of:day+'T09:31:00+08:00',fetched_at:'2026-10-22T02:15:00.000Z',bars:[{time:day+'T09:31:00+08:00',main_net:100,large_net:40,super_net:60}],...changes});

test('today snapshot points beat yesterday upstream and stored minute histories without mixing',()=>{
  const yesterday=history('510300','2026-10-21',{status:'stale',stale:true});
  const today=history('510300','2026-10-22',{kind:undefined,status:'recorded',source:'本站真实采集',bars:[{time:'2026-10-22T10:15:00+08:00',main_net:73}],as_of:'2026-10-22T10:15:00+08:00'});
  const before=JSON.stringify({yesterday,today});
  const result=selectFlowFallback({upstream:yesterday,stored:{...yesterday,status:'cached'},sampled:today,item,now:stamp});
  assert.equal(result.source_type,'sampled_snapshots');assert.equal(result.trade_date,'2026-10-22');assert.equal(result.kind,'etf');
  assert.equal(result.bars.length,1);assert.deepEqual(result.bars,today.bars);assert.match(result.note,/不是完整/);
  assert.equal(JSON.stringify({yesterday,today}),before);
});

test('same-day provider history remains preferred to sparse snapshots and uses the newer provider response',()=>{
  const upstream=history('510300','2026-10-22',{status:'cached',stale:true});
  const stored=history('510300','2026-10-22',{status:'cached',stale:true,as_of:'2026-10-22T09:32:00+08:00',bars:[...upstream.bars,{time:'2026-10-22T09:32:00+08:00',main_net:140}]});
  const sampled=history('510300','2026-10-22',{status:'recorded',as_of:'2026-10-22T10:15:00+08:00'});
  const result=selectFlowFallback({upstream,stored,sampled,item,now:stamp});
  assert.equal(result.source_type,'provider_minutes');assert.equal(result.as_of,stored.as_of);assert.equal(result.bars.length,2);
});

test('an index never accepts ETF snapshots, and mismatched identity or date is rejected',()=>{
  const upstream=history('000001','2026-10-21',{status:'stale',stale:true});
  const result=selectFlowFallback({upstream,stored:null,sampled:history(),item:{symbol:'000001',kind:'index'},now:stamp});
  assert.equal(result.symbol,'000001');assert.equal(result.source_type,'provider_minutes');assert.equal(result.trade_date,'2026-10-21');
  const bad=selectFlowFallback({upstream:{symbol:'510300',kind:'etf',status:'unavailable',bars:[]},stored:history('159915'),sampled:history('510300','2026-10-22',{trade_date:'2026-10-21'}),item,now:stamp});
  assert.equal(bad.status,'unavailable');assert.deepEqual(bad.bars,[]);
});

test('index persistence bounds concurrency at three and isolates one failed writer',async()=>{
  const inputs=['000001','399001','399006','000300','000905','000688'].map(symbol=>history(symbol));
  const before=JSON.stringify(inputs);let active=0,maxActive=0,calls=0;
  const result=await persistIndexFlowHistories(inputs,async data=>{
    calls++;active++;maxActive=Math.max(active,maxActive);await new Promise(resolve=>setImmediate(resolve));active--;
    if(data.symbol==='399006')throw new Error('synthetic confidential transport text');
    return {status:'ok',saved:data.bars.length};
  },{concurrency:99});
  assert.equal(calls,6);assert.equal(maxActive,3);assert.equal(result.status,'partial');assert.equal(result.saved,5);
  assert.equal(result.results[2].status,'unavailable');assert.equal(JSON.stringify(result).includes('confidential'),false);
  assert.equal(JSON.stringify(inputs),before);
});

test('scheduled index writes do not delay the response or claim successful D1 commits',async()=>{
  let queued,calls=0;const errors=[];
  const result=scheduleIndexFlowStorage([history('000001'),history('000300')],{after:callback=>queued=callback,saveHistory:async()=>{calls++;return {status:'unavailable',saved:0,error:'D1 unavailable'};},logError:error=>errors.push(error)});
  assert.deepEqual(result,{status:'scheduled',candidates:2,error:null});assert.equal(calls,0);
  await queued();assert.equal(calls,2);assert.equal(errors.length,2);assert.equal(errors[0].symbol,'000001');
});

test('failed scheduling and unavailable histories do not prevent a valid snapshot',()=>{
  const errors=[];
  const failed=scheduleIndexFlowStorage([history('000001')],{after:()=>{throw new Error('No request scope');},saveHistory:async()=>({status:'ok'}),logError:value=>errors.push(value)});
  assert.equal(failed.status,'unavailable');assert.equal(errors.length,1);
  const skipped=scheduleIndexFlowStorage([history(),history('000001','2026-10-22',{status:'cached',stale:true}),history('000300','2026-10-22',{bars:[]})],{after:()=>{throw new Error('should not schedule');},saveHistory:async()=>({status:'ok'})});
  assert.deepEqual(skipped,{status:'skipped',candidates:0,error:null});
});

test('watch snapshot exposes the exact histories it already fetched for persistence',async()=>{
  const result=await getWatchingSnapshot({now:()=>stamp,getQuotes:async()=>({status:'live',as_of:'2026-10-22T10:15:00+08:00',instruments:[]}),getEtfFlow:async()=>({status:'unavailable',rows:[]}),getIndexFlow:async symbol=>history(symbol)});
  assert.equal(result.indexHistories.length,6);assert.equal(result.indexHistories[0].symbol,'000001');
  assert.equal(result.indexHistories[0].bars[0].main_net,100);assert.equal(result.indexHistories[0].trade_date,'2026-10-22');
});
