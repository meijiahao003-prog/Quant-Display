/** Offline replay of an unmodified real Site response. Only request failures,
 * delayed completion, and an older-response branch are explicitly simulated.
 * Purpose/input/call/output/change for each step: docs/PRICE_REFRESH.md.
 */
import {readFile} from 'node:fs/promises';
import {createChartLoader} from '../public/chart-loader.mjs';

const real=JSON.parse(await readFile(new URL('./fixtures/price-refresh-510300-real.json',import.meta.url),'utf8'));
const item={symbol:real.symbol,kind:real.kind};
const key='real|native|510300|etf|intraday|2026-10-22';
// Fixed replay clock is test context, never used by the live dashboard.
const loader=createChartLoader({now:()=>Date.parse('2026-10-22T10:40:00+08:00')});
console.log(JSON.stringify({step:'real_input',rows:real.bars.length,as_of:real.as_of,fetched_at:real.fetched_at,first:real.bars.slice(0,2)},null,2));

// Step 1: prices finish before an explicitly held/simulated fund request.
let releaseFlow,last;
const heldFlow=new Promise(resolve=>{releaseFlow=resolve;});
const run=loader.load({key,item,requests:{price:async()=>real,flow:()=>heldFlow},onUpdate:value=>{last=value;}});
await new Promise(resolve=>setImmediate(resolve));
console.log(JSON.stringify({step:'price_before_flow',price_rows:last.price.bars.length,pending:last.pending,benchmark:last.benchmark,values_unchanged:JSON.stringify(last.price.bars)===JSON.stringify(real.bars)}));
releaseFlow({bars:[],status:'unavailable',error:'模拟：资金请求未取得记录'});await run;

// Step 2: a simulated failed refresh must keep the actual previous observations.
let initial;
await loader.load({key,item,requests:{price:async()=>{throw new Error('模拟：价格网络故障');}},onUpdate:value=>{initial??=value;last=value;}});
console.log(JSON.stringify({step:'retained_on_failure',initial_rows:initial.price.bars.length,initial_pending:initial.pending.price,rows:last.price.bars.length,status:last.price.status,source_status:last.price.source_status,stale:last.price.stale,as_of:last.price.as_of,fetched_at:last.price.fetched_at,error:last.price.error,values_unchanged:JSON.stringify(last.price.bars)===JSON.stringify(real.bars)}));

// Step 3: take only the first two REAL rows to simulate an older source response.
// This altered envelope is a test scenario, not another production capture.
const earlier={...real,bars:real.bars.slice(0,2),as_of:'2026-10-22T09:31:00+08:00'};
await loader.load({key,item,requests:{price:async()=>earlier},onUpdate:value=>{last=value;}});
console.log(JSON.stringify({step:'retained_on_older_response',simulated_input_rows:earlier.bars.length,rows:last.price.bars.length,as_of:last.price.as_of,error:last.price.error,values_unchanged:JSON.stringify(last.price.bars)===JSON.stringify(real.bars)}));

// Step 4: an empty response in another source context cannot reuse these prices.
await loader.load({key:key.replace('native','other-source'),item,requests:{price:async()=>({bars:[],error:'模拟：另一数据源为空'})},onUpdate:value=>{last=value;}});
console.log(JSON.stringify({step:'source_isolation',rows:last.price.bars.length,status:last.price.status,as_of:last.price.as_of}));

// Step 5: explicitly simulate a wrong identity while keeping original row values.
await loader.load({key:'identity-check',item,requests:{price:async()=>({...real,symbol:'159915'})},onUpdate:value=>{last=value;}});
console.log(JSON.stringify({step:'identity_rejection',simulated_symbol:'159915',expected_symbol:item.symbol,rows:last.price.bars.length,status:last.price.status,as_of:last.price.as_of}));
