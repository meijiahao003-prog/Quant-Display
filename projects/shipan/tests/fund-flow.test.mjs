import test from 'node:test';
import assert from 'node:assert/strict';
import {createFundFlowClient,parseFundFlow,fundFlowUrl} from '../lib/fund-flow.mjs';
import {mergeWatchingSnapshot,getWatchingSnapshot,getWatchingHealth} from '../lib/watching-data.mjs';
import {parseQuoteText} from '../lib/market-data.mjs';

// Explicit synthetic protocol fixtures, never displayed as observed market data.
const fixture=(changes={})=>({rc:0,data:{diff:[{f12:'510300',f13:1,f62:-120,f184:-12,f66:-200,f69:-20,f72:80,f75:8,f78:50,f81:5,f84:70,f87:7,f6:1000,f124:1792550274,f297:20261021,...changes}]}});
const response=()=>Response.json(fixture());
const fixtureNow=Date.parse('2026-10-21T14:00:00+08:00');

test('fields map with original CNY, source date preserved and absent values are not zero',()=>{
  const parsed=parseFundFlow(fixture()),r=parsed.rows[0];
  assert.equal(parsed.rows.length,10);assert.equal(parsed.status,'partial');
  assert.equal(r.main_net,-120);assert.equal(r.super_net,-200);assert.equal(r.large_net,80);
  assert.equal(r.unit,'CNY');assert.equal(r.as_of,'2026-10-21T10:37:54+08:00');assert.equal(r.trade_date,'2026-10-21');
  assert.equal(parsed.rows[1].main_net,null);
  const zero=parseFundFlow(fixture({f62:0,f66:0,f72:0,f184:0,f78:'-'})).rows[0];
  assert.equal(zero.main_net,0);assert.equal(zero.medium_net,null);assert.equal(zero.status,'partial');
  assert.equal(parseFundFlow(fixture({f62:'-'})).rows[0].main_net,null);
});

test('invalid exchange, shifted units and cross-day timestamps cannot become chart points',()=>{
  for(const change of [{f13:0},{f124:0},{f297:20260230},{f297:20261020},{f62:-120000},{f184:88}]){
    const row=parseFundFlow(fixture(change)).rows[0];assert.equal(row.main_net,null);assert.equal(row.status,'unavailable');
  }
  assert.throws(()=>parseFundFlow({rc:1,data:{diff:[]}}));
  assert.throws(()=>parseFundFlow({rc:0,data:null}));
  const url=new URL(fundFlowUrl());assert.equal(url.hostname,'push2delay.eastmoney.com');
  assert.equal(url.searchParams.get('fs').split(',').length,10);assert.equal(url.searchParams.get('fltt'),'2');
});

test('failed refresh retains actual data times and is clearly cached, without caller mutation',async()=>{
  let clock=fixtureNow,calls=0,failed=false;
  const client=createFundFlowClient({now:()=>clock,fetchImpl:async()=>{calls++;if(failed)throw new Error('fixture outage');return response();}});
  const [a,b]=await Promise.all([client.getFundFlow(),client.getFundFlow()]);
  assert.deepEqual(a,b);assert.equal(calls,2);const fetched=a.fetched_at;
  clock+=61000;failed=true;const stale=await client.getFundFlow();
  assert.equal(stale.status,'cached');assert.equal(stale.stale,true);assert.equal(stale.fetched_at,fetched);
  assert.equal(stale.rows[0].status,'cached');assert.equal(stale.rows[0].as_of,'2026-10-21T10:37:54+08:00');
  stale.rows[0].main_net=999;assert.equal((await client.getFundFlow()).rows[0].main_net,-120);assert.equal(calls,3);
});

test('cold outage remains unavailable and price+flow merge keeps independent status and dates',async()=>{
  const failed=createFundFlowClient({fetchImpl:async()=>{throw new Error('fixture outage');}});
  const noFlow=await failed.getFundFlow();assert.equal(noFlow.fetched_at,null);assert.equal(noFlow.status,'unavailable');
  const quotes={instruments:parseQuoteText(''),status:'unavailable',source:'price fixture',as_of:null};
  const merged=mergeWatchingSnapshot(quotes,parseFundFlow(fixture()),[],fixtureNow);
  assert.equal(merged.status,'unavailable');assert.equal(merged.flow_status,'partial');
  assert.equal(merged.instruments[0].main_net,null);assert.equal(merged.instruments[0].flow_status,'unavailable');
  assert.equal(merged.instruments[6].main_net,-120);assert.equal(merged.instruments[6].price,null);
  assert.equal(merged.instruments[6].as_of,null);assert.equal(merged.instruments[6].flow_as_of,'2026-10-21T10:37:54+08:00');
  assert.equal(getWatchingHealth().capabilities.main_flow,true);assert.equal(getWatchingHealth().capabilities.training,false);
});

test('total deadline resolves a cold fetch that ignores abort instead of hanging', {timeout:1000},async()=>{
  let signal,calls=0;
  const client=createFundFlowClient({timeoutMs:10,now:()=>fixtureNow,fetchImpl:async(_url,options)=>{
    calls++;signal=options.signal;return new Promise(()=>{});
  }});
  const [first,second]=await Promise.all([client.getFundFlow(),client.getFundFlow()]);
  assert.equal(signal.aborted,true);assert.equal(calls,2);assert.deepEqual(first,second);
  assert.equal(first.status,'unavailable');assert.equal(first.as_of,null);assert.equal(first.fetched_at,null);
  assert.match(first.error,/请求超时/);assert.equal(first.rows.every(row=>row.main_net===null),true);
});

test('total deadline covers an uncooperative body and preserves prior real source times', {timeout:1000},async()=>{
  let clock=fixtureNow,hang=false,signal;
  const client=createFundFlowClient({timeoutMs:10,now:()=>clock,fetchImpl:async(_url,options)=>{
    signal=options.signal;
    return hang?{ok:true,headers:new Headers(),arrayBuffer:()=>new Promise(()=>{})}:response();
  }});
  const first=await client.getFundFlow();
  hang=true;clock+=61000;
  const retained=await client.getFundFlow();
  assert.equal(signal.aborted,true);assert.equal(retained.status,'cached');assert.equal(retained.stale,true);
  assert.equal(retained.as_of,first.as_of);assert.equal(retained.fetched_at,first.fetched_at);
  assert.equal(retained.rows[0].main_net,first.rows[0].main_net);assert.equal(retained.rows[0].status,'cached');
  assert.match(retained.error,/请求超时/);
  // A later call starts a new local read after the settled error cache expires.
  hang=false;clock+=61000;
  const recovered=await client.getFundFlow();
  assert.equal(recovered.status,'partial');assert.equal(recovered.stale,false);
  assert.notEqual(recovered.fetched_at,first.fetched_at);
});


// Synthetic protocol fixtures exercise the join only; real observations are in the documented live example.
const indexFixture=(changes={})=>({symbol:'000300',kind:'index',scope:'instrument',unit:'CNY',status:'live',trade_date:'2026-10-21',as_of:'2026-10-21T13:40:00+08:00',fetched_at:'2026-10-21T05:40:06Z',source:'index fixture',bars:[{time:'2026-10-21T13:39:00+08:00',main_net:20},{time:'2026-10-21T13:40:00+08:00',main_net:0}],...changes});
const indexQuotes=()=>({instruments:parseQuoteText('').map(row=>({...row,as_of:'2026-10-21T13:40:02+08:00'})),status:'live',source:'price fixture'});

test('index last minute is joined independently, preserving zero, cached source time and ETF ranking',()=>{
  const flow=parseFundFlow(fixture()),quotes=indexQuotes(),history=indexFixture({status:'cached',stale:true,error:'fixture timeout'});
  const before=JSON.stringify({quotes,flow,history}),merged=mergeWatchingSnapshot(quotes,flow,[history],fixtureNow);
  const row=merged.instruments.find(item=>item.symbol==='000300');
  assert.equal(row.main_net,0);assert.equal(row.main_pct,null);assert.equal(row.unit.main_net,'CNY');
  assert.equal(row.flow_status,'cached');assert.equal(row.flow_stale,true);assert.equal(row.flow_error,'fixture timeout');
  assert.equal(row.flow_as_of,'2026-10-21T13:40:00+08:00');assert.equal(row.flow_fetched_at,'2026-10-21T05:40:06Z');
  assert.equal(row.as_of,'2026-10-21T13:40:02+08:00');assert.equal(row.flow_source,'index fixture');
  assert.equal(merged.flows[0].main_net,flow.rows[0].main_net);assert.equal(merged.flows[0].source_status,flow.rows[0].status);assert.equal(merged.flows.length,10);
  for(const key of ['status','source','as_of','fetched_at','stale','note'])assert.equal(merged['flow_'+key],flow[key]);
  assert.equal(merged.instruments.find(item=>item.symbol==='510300').main_net,-120);
  assert.equal(JSON.stringify({quotes,flow,history}),before);
});

test('index identity, currency, invalid date and cross-day data do not become current table amounts',()=>{
  const flow=parseFundFlow(fixture()),quotes=indexQuotes();
  for(const changes of [{symbol:'510300'},{kind:'etf'},{scope:'market'},{unit:'USD'},{trade_date:'2026-02-30'},{bars:[{time:'2026-10-21T13:40:00+08:00',main_net:null}]},{bars:[{time:'2026-02-30T13:40:00+08:00',main_net:10}]}]){
    const row=mergeWatchingSnapshot(quotes,flow,[indexFixture(changes)],fixtureNow).instruments.find(item=>item.symbol==='000300');
    assert.equal(row.main_net,null);assert.equal(row.flow_status,'unavailable');
  }
  const old=indexFixture({trade_date:'2026-10-20',bars:[{time:'2026-10-20T15:00:00+08:00',main_net:123}]});
  const row=mergeWatchingSnapshot(quotes,flow,[old],fixtureNow).instruments.find(item=>item.symbol==='000300');
  assert.equal(row.main_net,null);assert.equal(row.flow_stale,true);assert.match(row.flow_error,/资金日期.*价格日期/);
  assert.equal(row.flow_as_of,'2026-10-20T15:00:00+08:00');
  quotes.instruments.find(item=>item.symbol==='000300').as_of='2026-10-21T05:40:02Z';
  assert.equal(mergeWatchingSnapshot(quotes,flow,[indexFixture()],fixtureNow).instruments.find(item=>item.symbol==='000300').main_net,0);
});

test('snapshot fetch limits index concurrency to three and isolates a failed index',async()=>{
  const flow=parseFundFlow(fixture());let active=0,maxActive=0;const calls=[];
  const result=await getWatchingSnapshot({now:()=>fixtureNow,getQuotes:async()=>indexQuotes(),getEtfFlow:async()=>flow,getIndexFlow:async(symbol,kind)=>{
    calls.push([symbol,kind]);active++;maxActive=Math.max(maxActive,active);
    try{await Promise.resolve();if(symbol==='399001')throw new Error('one index unavailable');return indexFixture({symbol});}
    finally{active--;}
  }});
  assert.equal(calls.length,6);assert.equal(new Set(calls.map(([symbol])=>symbol)).size,6);
  assert.equal(calls.every(([,kind])=>kind==='index'),true);assert.equal(maxActive,3);assert.deepEqual(result.flow.rows.map(row=>row.main_net),flow.rows.map(row=>row.main_net));
  const failed=result.snapshot.instruments.find(item=>item.symbol==='399001');
  assert.equal(failed.main_net,null);assert.equal(failed.flow_status,'unavailable');assert.equal(failed.flow_error,'one index unavailable');
  assert.equal(result.snapshot.instruments.filter(item=>item.kind==='index'&&item.main_net===0).length,5);
  assert.equal(result.snapshot.instruments.find(item=>item.symbol==='510300').main_net,-120);
});
