import test from 'node:test';
import assert from 'node:assert/strict';
import {createMarketDataClient,parseQuoteText,parseHistory,parseSourceTime,UNIVERSE,getHealth} from '../lib/market-data.mjs';

// Explicit synthetic protocol fixtures. Never market observations or training samples.
const quoteFixture=(changes={})=>{
  const fields=Array(55).fill('');
  Object.assign(fields,{1:'FIXTURE ETF',2:'510300',3:'4.218',4:'4.220',5:'4.234',6:'2710415',30:'20261021101130',32:'-0.05',33:'4.237',34:'4.215',37:'114521'},changes);
  return `v_sh510300="${fields.join('~')}";`;
};
const fixtureFetch=async()=>new Response(quoteFixture(),{status:200});

test('source nulls stay null, verified amount converts exactly, unsupported capital flow stays absent',()=>{
  const rows=parseQuoteText(quoteFixture({32:'0',37:'0'}));
  assert.equal(rows.length,16);
  const etf=rows.find(x=>x.symbol==='510300');
  assert.equal(etf.price,4.218);assert.equal(etf.change_pct,0);assert.equal(etf.amount,0);
  assert.equal(etf.main_net,null);assert.equal(etf.main_pct,null);
  assert.equal(etf.as_of,'2026-10-21T10:11:30+08:00');
  assert.equal(rows[0].price,null);assert.equal(rows[0].status,'unavailable');
  assert.equal(parseQuoteText(quoteFixture())[6].amount,1_145_210_000);
  assert.equal(parseQuoteText(quoteFixture({37:''}))[6].amount,null);
});

test('malformed timestamps and assignments cannot masquerade as live valid data',()=>{
  for(const value of ['20260230101130','20261021241130','20261021106030','',null])assert.equal(parseSourceTime(value),null);
  const row=parseQuoteText(quoteFixture({30:'',3:'0'}))[6];
  assert.equal(row.as_of,null);assert.equal(row.price,null);assert.equal(row.status,'unavailable');
  assert.ok(parseQuoteText('alert("bad");v_sh510300="truncated";').every(x=>x.price===null));
  assert.ok(parseQuoteText(quoteFixture({2:'000001'})).every(x=>x.price===null));
});

test('actual minute protocol keeps points and cumulative counters separate from candle volume',()=>{
  const item=UNIVERSE[6];
  const p={code:0,data:{sh510300:{data:{date:'20261021',data:['0930 4.234 53836 22794162.00','0931 4.229 388694 164481136.00','2500 9 1 2']}}}};
  const result=parseHistory(p,item,'1m');
  assert.equal(result.bars.length,2);assert.equal(result.bars[0].close,4.234);
  assert.equal(result.bars[0].open,null);assert.equal(result.bars[0].high,null);assert.equal(result.bars[0].volume,null);
  assert.equal(result.bars[1].cumulative_volume,388694);assert.equal(result.as_of,'2026-10-21T09:31:00+08:00');
  assert.throws(()=>parseHistory({code:0,data:{sh510300:{data:{date:'',data:['0930 4.2 10']}}}},item,'1m'));
});

test('daily bars require valid unadjusted OHLC and preserve source volume units',()=>{
  const parsed=parseHistory({code:0,data:{sh510300:{day:[['2026-04-22','4.760','4.811','4.813','4.756','10125926.00',{},'2.42','485159.31'],['2026-04-23','4.8','4.9','4.7','4.6','9']]}}},UNIVERSE[6],'day');
  assert.equal(parsed.bars.length,1);assert.equal(parsed.bars[0].amount,4_851_593_100);assert.equal(parsed.bars[0].volume,10125926);
  assert.equal(parsed.unit.volume,'source_raw_unverified');
  assert.throws(()=>parseHistory({code:0,data:{sh510300:{qfqday:[]}}},UNIVERSE[6],'day'));
});

test('settled cache reuses data without sharing in-flight requests; refresh failures preserve original source/fetch time and mark stale',async()=>{
  let clock=Date.parse('2026-10-21T10:12:00+08:00'),calls=0,fail=false;
  const client=createMarketDataClient({now:()=>clock,fetchImpl:async()=>{calls++;if(fail)throw new Error('fixture outage');return fixtureFetch();}});
  const [a,b]=await Promise.all([client.getSnapshot(),client.getSnapshot()]);assert.equal(calls,2);assert.deepEqual(a,b);
  const original=a.fetched_at;clock+=5000;
  assert.equal((await client.getSnapshot()).cache_hit,true);assert.equal(calls,2);
  clock+=60_000;fail=true;
  const stale=await client.getSnapshot();assert.equal(stale.status,'cached');assert.equal(stale.stale,true);assert.equal(stale.fetched_at,original);assert.equal(stale.instruments[6].price,4.218);assert.equal(stale.instruments[6].status,'cached');
  stale.instruments[6].price=99;
  assert.equal((await client.getSnapshot()).instruments[6].price,4.218);
});

test('first failure never becomes a fake cache, and parameter validation prevents arbitrary requests',async()=>{
  let clock=Date.parse('2026-10-21T10:12:00+08:00'),calls=0;
  const client=createMarketDataClient({now:()=>clock,fetchImpl:async()=>{calls++;throw new Error('fixture outage');}});
  assert.equal((await client.getSnapshot()).status,'unavailable');clock+=61000;
  const result=await client.getSnapshot();assert.equal(result.status,'unavailable');assert.equal(result.fetched_at,null);assert.ok(result.instruments.every(x=>x.price===null));
  const before=calls;
  for(const args of [['https://example.com','etf','day'],['510300','index','day'],['510300','etf','5m']])await assert.rejects(client.getHistory(...args),{status:400});
  assert.equal(calls,before);assert.equal(getHealth().capabilities.main_flow,false);
});

test('total read deadline bounds ignored aborts during fetch or body and retains last good prices', {timeout:1000},async()=>{
  let clock=Date.parse('2026-10-21T10:12:00+08:00'),signal;
  const cold=createMarketDataClient({timeoutMs:10,now:()=>clock,fetchImpl:async(_url,options)=>{
    signal=options.signal;return new Promise(()=>{});
  }});
  const unavailable=await cold.getSnapshot();
  assert.equal(signal.aborted,true);assert.equal(unavailable.status,'unavailable');
  assert.equal(unavailable.as_of,null);assert.equal(unavailable.fetched_at,null);assert.match(unavailable.error,/请求超时/);

  let hang=false;
  const warm=createMarketDataClient({timeoutMs:10,now:()=>clock,fetchImpl:async(_url,options)=>{
    signal=options.signal;
    return hang?{ok:true,headers:new Headers(),arrayBuffer:()=>new Promise(()=>{})}:fixtureFetch();
  }});
  const first=await warm.getSnapshot();hang=true;clock+=61000;
  const retained=await warm.getSnapshot();
  assert.equal(signal.aborted,true);assert.equal(retained.status,'cached');assert.equal(retained.stale,true);
  assert.equal(retained.as_of,first.as_of);assert.equal(retained.fetched_at,first.fetched_at);
  assert.equal(retained.instruments[6].price,first.instruments[6].price);assert.equal(retained.instruments[6].status,'cached');
  assert.match(retained.error,/请求超时/);
  hang=false;clock+=61000;
  assert.equal((await warm.getSnapshot()).stale,false);
});
