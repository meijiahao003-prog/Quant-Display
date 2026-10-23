/** Runnable recorded-response replay, not a current market request.
 * Purpose, input, observed output and before/after: docs/INTRADAY_FLOW.md.
 * These are two real rows per instrument from 2026-10-21; omitted minutes
 * are intentionally absent. The replay clock and failure are test controls.
 */
import {resolveIntradayInstrument,intradayFlowUrl,parseIntradayFlow,createIntradayFlowClient,getIntradayFlow} from '../lib/intraday-flow.mjs';
import {buildPairedData,splitObservedSegments} from '../public/paired-chart.mjs';

if(process.argv[2]==='live'){
  const symbol=process.argv[3]??'510300',kind=process.argv[4]??undefined;
  console.log('当前网络调用；输出取决于实际连接与源状态。',await getIntradayFlow(symbol,kind));
}else{
  const excerpts=[
    {kind:'etf',payload:{rc:0,data:{code:'510300',market:1,name:'沪深300ETF',klines:[
      '2026-10-21 09:31,4918230,-5809187,848667,33773158,-28854928',
      '2026-10-21 13:08,-435354626,198497485,236857141,236046672,-671401298',
    ]}}},
    {kind:'index',payload:{rc:0,data:{code:'000300',market:1,name:'沪深300',klines:[
      '2026-10-21 09:31,786258944,-269395216,-516517120,-23740928,809999872',
      '2026-10-21 13:09,-4140492008,4895318328,-754347042,-3496042590,-644449418',
    ]}}},
  ];
  console.log('输入：2026-10-21 的真实响应字段摘录；每标的仅保留首、末行，非完整历史，也非当前行情。');
  for(const {kind,payload} of excerpts){
    const symbol=payload.data.code,before=JSON.stringify(payload);
    console.log('真实输入',JSON.stringify(payload));
    console.log('标的解析',JSON.stringify(resolveIntradayInstrument(symbol,kind)));
    console.log('上游 URL',intradayFlowUrl(symbol,kind));
    const parsed=parseIntradayFlow(payload,symbol,kind);
    console.log('解析输出',JSON.stringify(parsed));
    const priceBars=kind==='etf'?[{time:'2026-10-21 09:31',close:4.229}]:[];
    const paired=buildPairedData({priceBars,flowHistory:parsed,item:{symbol,kind}});
    const segments=splitObservedSegments(paired.flows.main_net,paired.flowObservations);
    console.log('对齐与分段',JSON.stringify({symbol,date:paired.date,axisSlots:paired.labels.length,prices:paired.priceCount,flows:paired.flowCount,segments:segments.length,missing0932:paired.flows.main_net[paired.labels.indexOf('09:32')]}));
    console.log('前后变化',JSON.stringify({sourceRows:payload.data.klines.length,outputRows:parsed.bars.length,inputUnchanged:before===JSON.stringify(payload),unit:parsed.unit}));
  }
  let clock=Date.parse('2026-10-21T05:08:30Z'),calls=0,fail=false;
  const client=createIntradayFlowClient({now:()=>clock,fetchImpl:async()=>{
    calls++;
    if(fail)throw new Error('离线例子明确注入的网络故障');
    return new Response(JSON.stringify(excerpts[0].payload),{headers:{'Content-Type':'application/json'}});
  }});
  const first=await client.getIntradayFlow('510300','etf');
  clock+=10_000;
  const hit=await client.getIntradayFlow('510300','etf');
  fail=true;clock+=60_000;
  const fallback=await client.getIntradayFlow('510300','etf');
  console.log('离线缓存回放（时钟和故障为测试控制）',JSON.stringify({fetchCalls:calls,firstStatus:first.status,hit:hit.cache_hit,fallbackStatus:fallback.status,stale:fallback.stale,sourceTimePreserved:fallback.as_of===first.as_of,fetchedTimePreserved:fallback.fetched_at===first.fetched_at,valuesPreserved:JSON.stringify(fallback.bars)===JSON.stringify(first.bars)}));
}
