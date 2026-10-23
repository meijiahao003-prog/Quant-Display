import test from 'node:test';
import assert from 'node:assert/strict';
import {createIntradayFlowClient,intradayFlowUrl} from '../lib/intraday-flow.mjs';

// Explicit synthetic provider-protocol responses. No test values are market data.
const fixture=(day='2026-10-22')=>({rc:0,data:{code:'510300',market:1,name:'Synthetic ETF',klines:[`${day} 09:31,100,-20,-80,40,60`]}});
const clock=()=>Date.parse('2026-10-22T10:15:00+08:00');

test('a successful older source response cannot overwrite already observed newer-day funds',async()=>{
 let stamp=clock(),day='2026-10-22',calls=0;
 const client=createIntradayFlowClient({now:()=>stamp,fetchImpl:async()=>{calls++;return Response.json(fixture(day));}});
 const first=await client.getIntradayFlow('510300','etf');
 stamp+=60_001;day='2026-10-21';
 const retained=await client.getIntradayFlow('510300','etf');
 assert.equal(calls,2);assert.equal(retained.trade_date,'2026-10-22');
 assert.equal(retained.status,'cached');assert.equal(retained.stale,true);
 assert.equal(retained.as_of,first.as_of);assert.equal(retained.fetched_at,first.fetched_at);
 assert.deepEqual(retained.bars,first.bars);assert.notEqual(retained.last_attempt_at,first.fetched_at);
 assert.match(retained.error,/回退至 2026-10-21/);
});

test('502 503 and 504 retry once at the verified delay host with the same query',async()=>{
 for(const status of [502,503,504]){
  const calls=[];
  const client=createIntradayFlowClient({now:clock,fetchImpl:async(url,options)=>{
   calls.push({url,options});return calls.length===1?new Response(null,{status}):Response.json(fixture());
  }});
  const result=await client.getIntradayFlow('510300','etf');
  assert.equal(result.status,'live');assert.equal(result.upstream_attempts,2);assert.equal(calls.length,2);
  assert.equal(calls[0].url,intradayFlowUrl('510300','etf'));
  assert.equal(new URL(calls[1].url).origin,'https://push2delay.eastmoney.com');
  assert.equal(new URL(calls[1].url).pathname,new URL(calls[0].url).pathname);
  assert.equal(new URL(calls[1].url).search,new URL(calls[0].url).search);
  assert.notEqual(calls[0].options.signal,calls[1].options.signal);
 }
});

test('fetch network errors and an injected timeout retry without waiting for a real timeout',async()=>{
 for(const failure of [new Error('Synthetic network outage'),new TypeError('fetch failed'),new DOMException('Synthetic timeout','AbortError')]){
  let calls=0;
  const client=createIntradayFlowClient({now:clock,fetchImpl:async()=>{calls++;if(calls===1)throw failure;return Response.json(fixture());}});
  const result=await client.getIntradayFlow('510300','etf');
  assert.equal(calls,2);assert.equal(result.status,'live');assert.equal(result.bars[0].main_net,100);
 }
});

test('permission and rate-limit errors do not trigger another request',async()=>{
 for(const status of [400,401,403,404,429,500]){
  let calls=0;
  const client=createIntradayFlowClient({now:clock,fetchImpl:async()=>{calls++;return new Response(null,{status});}});
  const result=await client.getIntradayFlow('510300','etf');
  assert.equal(calls,1);assert.equal(result.status,'unavailable');assert.match(result.error,new RegExp(`HTTP ${status}`));
 }
});

test('invalid JSON schema and unsafe redirects cannot trigger the retry host',async()=>{
 const cases=[()=>new Response('{invalid-json'),()=>Response.json({...fixture(),rc:1}),
  ()=>new Response(null,{status:302,headers:{location:'https://example.com/api/qt/stock/fflow/kline/get'}})];
 for(const response of cases){
  let calls=0;
  const client=createIntradayFlowClient({now:clock,fetchImpl:async()=>{calls++;return response();}});
  const result=await client.getIntradayFlow('510300','etf');
  assert.equal(calls,1);assert.equal(result.status,'unavailable');assert.deepEqual(result.bars,[]);
 }
});

test('two failed attempts retain the last newer data and never start a third attempt',async()=>{
 let stamp=clock(),calls=0,outage=false;
 const client=createIntradayFlowClient({now:()=>stamp,fetchImpl:async()=>{calls++;return outage?new Response(null,{status:502}):Response.json(fixture());}});
 const first=await client.getIntradayFlow('510300','etf');
 stamp+=60_001;outage=true;
 const retained=await client.getIntradayFlow('510300','etf');
 assert.equal(calls,3);assert.equal(retained.status,'cached');assert.equal(retained.as_of,first.as_of);
 assert.equal(retained.fetched_at,first.fetched_at);assert.deepEqual(retained.bars,first.bars);assert.match(retained.error,/HTTP 502/);
});

test('a failed primary redirect sequence gets only one new bounded attempt',async()=>{
 const calls=[];
 const client=createIntradayFlowClient({now:clock,fetchImpl:async(url,options)=>{
  calls.push({url,options});
  if(calls.length===1)return new Response(null,{status:302,headers:{location:'//push2delay.eastmoney.com/api/qt/stock/fflow/kline/get?secid=1.000300'}});
  if(calls.length===2)return new Response(null,{status:504});
  return Response.json(fixture());
 }});
 const result=await client.getIntradayFlow('510300','etf');
 assert.equal(calls.length,3);assert.equal(result.upstream_attempts,2);
 assert.equal(calls[0].options.signal,calls[1].options.signal);assert.notEqual(calls[1].options.signal,calls[2].options.signal);
 assert.equal(new URL(calls[2].url).searchParams.get('secid'),'1.510300');
});

test('a response-body transport failure may retry but no invented records are added',async()=>{
 let calls=0;
 const client=createIntradayFlowClient({now:clock,fetchImpl:async()=>{
  calls++;if(calls===1)return new Response(new ReadableStream({start(controller){controller.error(new TypeError('Synthetic body network failure'));}}));
  return Response.json(fixture());
 }});
 const result=await client.getIntradayFlow('510300','etf');
 assert.equal(calls,2);assert.equal(result.status,'live');assert.equal(result.bars.length,1);
 assert.deepEqual(result.bars[0],{time:'2026-10-22T09:31:00+08:00',main_net:100,small_net:-20,medium_net:-80,large_net:40,super_net:60});
});
