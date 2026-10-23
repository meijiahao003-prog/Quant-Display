import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createChartLoader} from '../public/chart-loader.mjs';

// Actual saved production price response; faults/deferred requests below are
// explicit simulations. No synthetic observations are returned to production.
const actual=JSON.parse(await readFile(new URL('../examples/fixtures/price-refresh-510300-real.json',import.meta.url),'utf8'));
const item={symbol:'510300',kind:'etf'},now=()=>Date.parse('2026-10-22T10:40:00+08:00');
const key='real|native|510300|etf|intraday|2026-10-22';
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
const tick=()=>new Promise(resolve=>setImmediate(resolve));

test('real price emits before a pending fund request; absent benchmark stays null',async()=>{
  const loader=createChartLoader({now}),flow=deferred(),updates=[];
  let completed=false;
  const run=loader.load({key,item,requests:{price:async()=>actual,flow:()=>flow.promise},onUpdate:value=>updates.push(value)}).then(()=>{completed=true;});
  assert.deepEqual(updates[0].pending,{price:true,flow:true,benchmark:false});
  await tick();
  assert.equal(updates.at(-1).price.bars.length,69);assert.equal(updates.at(-1).pending.price,false);
  assert.equal(updates.at(-1).pending.flow,true);assert.equal(updates.at(-1).benchmark,null);assert.equal(completed,false);
  flow.resolve({symbol:'510300',kind:'etf',bars:[],status:'unavailable',error:'模拟资金失败'});await run;
  assert.equal(updates.at(-1).price.bars.length,69);assert.equal(updates.at(-1).flow.status,'unavailable');
});

test('refresh preserves actual points while pending and after an empty or rejected response',async()=>{
  const loader=createChartLoader({now});
  await loader.load({key,item,requests:{price:async()=>actual},onUpdate:()=>{}});
  for(const reject of [false,true]){
    const request=deferred(),updates=[];
    const run=loader.load({key,item,requests:{price:()=>request.promise},onUpdate:value=>updates.push(value)});
    assert.deepEqual(updates[0].price.bars,actual.bars);assert.equal(updates[0].pending.price,true);
    if(reject)request.reject(new Error('模拟网络故障'));else request.resolve({bars:[],status:'unavailable',error:'模拟空响应'});
    await run;const price=updates.at(-1).price;
    assert.equal(price.status,'cached');assert.equal(price.source_status,'cached');assert.equal(price.stale,true);
    assert.deepEqual(price.bars,actual.bars);assert.equal(price.as_of,actual.as_of);assert.equal(price.fetched_at,actual.fetched_at);
    assert.match(price.error,/保留上次有效走势/);assert.equal(updates.at(-1).pending.price,false);
  }
});

test('obsolete same-key run cannot emit or overwrite a newer request',async()=>{
  const loader=createChartLoader({now}),old=deferred(),oldUpdates=[],newUpdates=[];
  const first=loader.load({key,item,requests:{price:()=>old.promise},onUpdate:value=>oldUpdates.push(value)});
  await loader.load({key,item,requests:{price:async()=>actual},onUpdate:value=>newUpdates.push(value)});
  old.resolve({...actual,as_of:'2026-10-22T09:31:00+08:00',bars:actual.bars.slice(0,2)});await first;
  assert.equal(oldUpdates.length,1);
  const read=[];await loader.load({key,item,requests:{price:async()=>({bars:[]})},onUpdate:value=>read.push(value)});
  assert.equal(read[0].price.bars.length,69);assert.equal(read[0].price.as_of,actual.as_of);
});

test('external selection invalidation prevents late emission and cache writes',async()=>{
  const loader=createChartLoader({now}),request=deferred(),updates=[];let selected=true;
  const run=loader.load({key,item,requests:{price:()=>request.promise},isCurrent:()=>selected,onUpdate:value=>updates.push(value)});
  selected=false;request.resolve(actual);await run;assert.equal(updates.length,1);
  const next=[];await loader.load({key,item,requests:{price:async()=>({bars:[]})},onUpdate:value=>next.push(value)});
  assert.equal(next[0].price.status,'loading');assert.equal(next.at(-1).price.bars.length,0);
});

test('distinct source and day keys do not reuse another context',async()=>{
  const loader=createChartLoader({now});await loader.load({key,item,requests:{price:async()=>actual},onUpdate:()=>{}});
  for(const separate of ['real|other-source|510300|etf|intraday|2026-10-22','real|native|510300|etf|intraday|2026-10-23']){
    const updates=[];await loader.load({key:separate,item,requests:{price:async()=>({bars:[]})},onUpdate:value=>updates.push(value)});
    assert.equal(updates[0].price.status,'loading');assert.equal(updates.at(-1).price.bars.length,0);
  }
});

test('older successful source response cannot replace newer genuine observations',async()=>{
  const loader=createChartLoader({now});await loader.load({key,item,requests:{price:async()=>actual},onUpdate:()=>{}});
  const updates=[];
  await loader.load({key,item,requests:{price:async()=>({...actual,as_of:'2026-10-22T09:31:00+08:00',bars:actual.bars.slice(0,2)})},onUpdate:value=>updates.push(value)});
  const price=updates.at(-1).price;assert.deepEqual(price.bars,actual.bars);assert.equal(price.as_of,actual.as_of);
  assert.equal(price.status,'cached');assert.match(price.error,/更早/);
});

test('identity mismatches are rejected, optional identities allowed, and all channels validate values',async()=>{
  const loader=createChartLoader({now}),updates=[];
  await loader.load({key,item,requests:{
    price:async()=>({...actual,symbol:'159915'}),
    flow:async()=>({symbol:'510300',kind:'index',bars:[{main_net:1}]}),
    benchmark:async()=>({...actual,symbol:'000300',kind:'etf'}),
  },onUpdate:value=>updates.push(value)});
  for(const channel of ['price','flow','benchmark'])assert.equal(updates.at(-1)[channel].bars.length,0);
  const {symbol,kind,...withoutIdentity}=actual;
  await loader.load({key,item,requests:{price:async()=>withoutIdentity,flow:async()=>({bars:[{main_net:0}]})},onUpdate:value=>updates.push(value)});
  assert.equal(updates.at(-1).price.bars.length,69);assert.equal(updates.at(-1).flow.bars[0].main_net,0);
  const bad=[];await loader.load({key:'invalid',item,requests:{price:async()=>({bars:[{close:0},{close:'4.2'},{close:NaN}]}),flow:async()=>({bars:[{main_net:null}]})},onUpdate:value=>bad.push(value)});
  assert.equal(bad.at(-1).price.status,'unavailable');assert.equal(bad.at(-1).flow.status,'unavailable');
});

test('real valid benchmark and cached flow settle independently',async()=>{
  // Benchmark identity and flow values here are explicit protocol fixtures.
  const loader=createChartLoader({now}),flow={symbol:'510300',kind:'etf',as_of:actual.as_of,unit:'CNY',bars:[{time:actual.as_of,main_net:0}]};
  let last;await loader.load({key,item,requests:{price:async()=>actual,flow:async()=>flow,benchmark:async()=>({symbol:'000300',kind:'index',bars:[{time:actual.as_of,close:4000}]})},onUpdate:value=>{last=value;}});
  assert.equal(last.benchmark.bars.length,1);
  await loader.load({key,item,requests:{price:async()=>actual,flow:async()=>{throw new Error('模拟资金故障');}},onUpdate:value=>{last=value;}});
  assert.deepEqual(last.flow.bars,flow.bars);assert.equal(last.flow.status,'cached');assert.equal(last.benchmark,null);
});

test('cache holds only 32 recent keys and emitted objects cannot mutate saved history',async()=>{
  const loader=createChartLoader({now});
  await loader.load({key,item,requests:{price:async()=>actual},onUpdate:value=>{if(value.price.bars.length)value.price.bars[0].close=999;}});
  let first;await loader.load({key,item,requests:{price:async()=>actual},onUpdate:value=>{first??=value;}});
  assert.equal(first.price.bars[0].close,4.185);
  for(let index=0;index<32;index++)await loader.load({key:`context-${index}`,item,requests:{price:async()=>actual},onUpdate:()=>{}});
  first=null;await loader.load({key,item,requests:{price:async()=>({bars:[]})},onUpdate:value=>{first??=value;}});
  assert.equal(first.price.status,'loading');assert.equal(first.price.bars.length,0);
});
