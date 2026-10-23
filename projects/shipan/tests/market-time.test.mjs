import test from 'node:test';
import assert from 'node:assert/strict';
import {shanghaiDate,withMarketFreshness} from '../public/market-time.mjs';
import {createMarketDataClient,parseQuoteText} from '../lib/market-data.mjs';
import {createFundFlowClient,parseFundFlow} from '../lib/fund-flow.mjs';
import {createIntradayFlowClient} from '../lib/intraday-flow.mjs';
import {mergeWatchingSnapshot} from '../lib/watching-data.mjs';

// Synthetic protocol fixtures test rollover; these are not observed market data.
const oldSource='2026-10-21T09:31:00+08:00';
const flowFixture=(day='2026-10-21')=>({rc:0,data:{code:'510300',market:1,klines:[`${day} 09:31,10,-2,-8,4,6`]}});
const quoteFixture=(day='20261021')=>{
  const fields=Array(55).fill('');
  Object.assign(fields,{1:'SYNTHETIC ETF',2:'510300',3:'4.2',4:'4.1',5:'4.15',6:'100',30:day+'093100',32:'1',33:'4.2',34:'4.1',37:'100'});
  return `v_sh510300="${fields.join('~')}";`;
};
const etfFixture=(day='2026-10-21')=>({rc:0,data:{diff:[{f12:'510300',f13:1,f62:10,f184:1,f66:6,f69:.6,f72:4,f75:.4,f78:-2,f81:-.2,f84:-8,f87:-.8,f6:1000,f124:Date.parse(day+'T09:31:00+08:00')/1000,f297:day.replaceAll('-','')}]}});

test('Shanghai calendar date handles UTC midnight boundary, date-only values, and invalid input',()=>{
  assert.equal(shanghaiDate('2026-10-21T15:59:59Z'),'2026-10-21');
  assert.equal(shanghaiDate('2026-10-21T16:00:00Z'),'2026-10-22');
  assert.equal(shanghaiDate('2026-10-21 09:31'),'2026-10-21');
  assert.equal(shanghaiDate('2026-10-21'),'2026-10-21');
  for(const input of [null,undefined,'','broken','2026-02-30','2026-10-21T24:00:00+08:00',{},Symbol('invalid'),NaN,Infinity])assert.equal(shanghaiDate(input),null);
  assert.match(shanghaiDate(),/^\d{4}-\d{2}-\d{2}$/);
});

test('successful yesterday payload keeps values and source times, labels nested rows, and is idempotent',()=>{
  const input={status:'live',stale:false,as_of:oldSource,trade_date:'2026-10-21',fetched_at:'2026-10-22T02:00:00Z',bars:[{time:oldSource,main_net:10}],instruments:[{status:'live',as_of:oldSource,price:4.2}],rows:[{status:'live',as_of:oldSource,main_net:0}]};
  const before=JSON.stringify(input),now=Date.parse('2026-10-22T10:00:00+08:00');
  const result=withMarketFreshness(input,now);
  assert.equal(result.status,'stale');assert.equal(result.source_status,'live');assert.equal(result.stale,true);
  assert.equal(result.today,'2026-10-22');assert.equal(result.source_date,'2026-10-21');assert.equal(result.freshness,'previous_day');
  assert.deepEqual(result.bars,input.bars);assert.equal(result.as_of,oldSource);assert.equal(result.fetched_at,input.fetched_at);
  assert.equal(result.instruments[0].status,'stale');assert.equal(result.rows[0].main_net,0);
  assert.deepEqual(withMarketFreshness(result,now),result);assert.equal(JSON.stringify(input),before);
});

test('weekend/unknown/demo data does not invent today observations or assert a provider failure',()=>{
  const weekend=withMarketFreshness({status:'live',as_of:'2026-10-23T15:00:00+08:00',price:4.2},Date.parse('2026-10-24T10:00:00+08:00'));
  assert.equal(weekend.freshness,'previous_day');assert.equal(weekend.source_date,'2026-10-23');assert.equal(weekend.error,undefined);assert.equal(weekend.price,4.2);
  const empty=withMarketFreshness({status:'unavailable',as_of:null,bars:[]});assert.equal(empty.freshness,'unknown');assert.equal(empty.status,'unavailable');assert.equal(empty.source_date,null);
  const demo=withMarketFreshness({mode:'demo',status:'demo',as_of:oldSource},Date.parse('2026-10-22T10:00:00+08:00'));
  assert.equal(demo.status,'demo');assert.equal(demo.as_of,oldSource);assert.equal(demo.stale,undefined);
  assert.equal(withMarketFreshness(null),null);
});

test('all three caches fetch again after Shanghai midnight even within the 60-second TTL',async()=>{
  const start=Date.parse('2026-10-21T23:59:50+08:00');
  for(const kind of ['price','etf','intraday']){
    let clock=start,calls=0;
    const fetchImpl=async()=>{calls++;return kind==='price'?new Response(quoteFixture()):Response.json(kind==='etf'?etfFixture():flowFixture());};
    const client=kind==='price'?createMarketDataClient({now:()=>clock,fetchImpl}):kind==='etf'?createFundFlowClient({now:()=>clock,fetchImpl}):createIntradayFlowClient({now:()=>clock,fetchImpl});
    const read=()=>kind==='price'?client.getSnapshot():kind==='etf'?client.getFundFlow():client.getIntradayFlow('510300','etf');
    const first=await read();assert.equal(first.freshness,'current_day');
    clock+=5_000;assert.equal((await read()).cache_hit,true);assert.equal(calls,1);
    clock+=15_000;const rolled=await read();assert.equal(calls,2,kind);assert.equal(rolled.status,'stale');assert.equal(rolled.source_status,first.source_status);
    assert.equal(rolled.as_of,first.as_of);assert.equal(rolled.source_date,'2026-10-21');assert.equal(rolled.today,'2026-10-22');
    if(kind==='price')assert.equal(rolled.instruments.find(row=>row.symbol==='510300').status,'stale');
    if(kind==='etf')assert.equal(rolled.rows[0].status,'stale');
  }
});

test('new source day replaces stale history; failed rollover retains yesterday with original fetch time',async()=>{
  let clock=Date.parse('2026-10-21T23:59:50+08:00'),sourceDay='2026-10-21',outage=false;
  const client=createIntradayFlowClient({now:()=>clock,fetchImpl:async()=>{if(outage)throw new Error('Synthetic outage');return Response.json(flowFixture(sourceDay));}});
  const first=await client.getIntradayFlow('510300','etf');
  clock+=20_000;outage=true;const cached=await client.getIntradayFlow('510300','etf');
  assert.equal(cached.status,'stale');assert.equal(cached.source_status,'cached');assert.equal(cached.fetched_at,first.fetched_at);assert.equal(cached.as_of,first.as_of);
  clock=Date.parse('2026-10-22T09:32:00+08:00');outage=false;sourceDay='2026-10-22';
  const next=await client.getIntradayFlow('510300','etf');assert.equal(next.status,'live');assert.equal(next.freshness,'current_day');assert.equal(next.stale,false);assert.equal(next.source_date,sourceDay);
});

test('minute price history labels yesterday after a successful request without changing the provider date',async()=>{
  const clock=Date.parse('2026-10-22T10:00:00+08:00');
  const client=createMarketDataClient({now:()=>clock,fetchImpl:async()=>Response.json({code:0,data:{sh510300:{data:{date:'20261021',data:['0931 4.2 100 420']}}}})});
  const result=await client.getHistory('510300','etf','1m');
  assert.equal(result.status,'stale');assert.equal(result.source_status,'live');assert.equal(result.as_of,oldSource);assert.equal(result.bars[0].time,'2026-10-21 09:31');
});

test('ETF watchlist never attaches an older-day fund value to a current-day price',()=>{
  const now=Date.parse('2026-10-22T10:00:00+08:00'),quotes={status:'partial',as_of:'2026-10-22T09:31:00+08:00',instruments:parseQuoteText(quoteFixture('20261022'))};
  const result=mergeWatchingSnapshot(quotes,parseFundFlow(etfFixture()),[],now);
  const etf=result.instruments.find(row=>row.symbol==='510300');
  assert.equal(etf.price,4.2);assert.equal(etf.main_net,null);assert.equal(etf.main_pct,null);assert.equal(etf.flow_status,'unavailable');
  assert.equal(etf.flow_as_of,oldSource);assert.equal(etf.flow_freshness,'previous_day');assert.match(etf.flow_error,/资金日期 2026-10-21 与价格日期 2026-10-22/);
  assert.equal(result.flows[0].main_net,10);assert.equal(result.flows[0].status,'stale');
  assert.equal(withMarketFreshness(result,now).instruments.find(row=>row.symbol==='510300').flow_status,'unavailable');
});


test('joined flow metadata is recomputed on its own source day, including cache-shaped D1 results',()=>{
  const now=Date.parse('2026-10-22T10:00:00+08:00');
  const result=withMarketFreshness({status:'live',as_of:'2026-10-22T09:31:00+08:00',flow_status:'live',flow_stale:false,flow_as_of:oldSource},now);
  assert.equal(result.status,'live');assert.equal(result.freshness,'current_day');
  assert.equal(result.flow_status,'stale');assert.equal(result.flow_stale,true);assert.equal(result.flow_source_status,'live');
  assert.equal(result.flow_today,'2026-10-22');assert.equal(result.flow_source_date,'2026-10-21');assert.equal(result.flow_freshness,'previous_day');
  const stored=withMarketFreshness({status:'cached',stale:true,trade_date:'2026-10-21',as_of:oldSource,bars:[{time:oldSource,main_net:10}]},now);
  assert.equal(stored.status,'stale');assert.equal(stored.source_status,'cached');assert.equal(stored.bars[0].time,oldSource);
  const future=withMarketFreshness({status:'live',as_of:'2026-10-23T09:31:00+08:00',price:4.2},now);
  assert.equal(future.freshness,'future_day');assert.equal(future.status,'stale');assert.equal(future.price,4.2);
});
