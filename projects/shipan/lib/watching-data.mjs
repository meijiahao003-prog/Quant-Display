import {shanghaiDate,withMarketFreshness} from '../public/market-time.mjs';
/** Join independently timestamped prices and flows; never attach ETF flow to an index. */
import {getSnapshot,getHealth,UNIVERSE} from './market-data.mjs';
import {getFundFlow} from './fund-flow.mjs';
import {getIntradayFlow} from './intraday-flow.mjs';

const INDEXES=UNIVERSE.filter(item=>item.kind==='index');
const finite=value=>typeof value==='number'&&Number.isFinite(value);

/** Parse explicit source timestamps, preserving Shanghai dates.
 * Purpose, real input, runnable call, output and changes: docs/INTRADAY_FLOW.md, 观察池指数资金修复.
 */
function sourceStamp(value){
  if(typeof value!=='string')return null;
  const match=/^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,3})?(Z|[+-]\d{2}:\d{2})$/.exec(value);
  if(!match||Number(match[2])>23||Number(match[3])>59||Number(match[4])>59)return null;
  const day=new Date(match[1]+'T00:00:00Z'),epoch=Date.parse(value);
  if(!Number.isFinite(day.getTime())||day.toISOString().slice(0,10)!==match[1]||!Number.isFinite(epoch))return null;
  const zone=match[5];
  if(zone!=='Z'&&(Number(zone.slice(1,3))>14||Number(zone.slice(4))>59||(Number(zone.slice(1,3))===14&&Number(zone.slice(4))!==0)))return null;
  return {epoch,date:new Date(epoch+8*3600_000).toISOString().slice(0,10)};
}

/** Project only this index's latest valid source minute; never derive gross flows or percentages.
 * Complete real-input example: docs/INTRADAY_FLOW.md, 观察池指数资金修复.
 */
function projectIndexFlow(item,history){
  const base={main_net:null,main_pct:null,flow_status:'unavailable',flow_as_of:null,flow_fetched_at:null,flow_source:null,flow_stale:false,flow_error:null,flow_unit:'unavailable'};
  if(!history)return base;
  if(history.symbol!==item.symbol||history.kind!=='index'||history.scope!=='instrument')return {...base,flow_error:'指数资金标的或统计范围不匹配'};
  const metadata={...base,flow_source:history.source??null,flow_fetched_at:sourceStamp(history.fetched_at)?history.fetched_at:null,flow_stale:history.stale===true||history.status==='cached',flow_freshness:history.freshness,flow_source_date:history.source_date,flow_source_status:history.source_status??history.status,flow_error:history.error??null};
  if(history.unit!=='CNY')return {...metadata,flow_error:'指数资金金额单位未核实'};
  if(!['live','partial','cached','stale'].includes(history.status)||!Array.isArray(history.bars))return metadata;
  const day=sourceStamp(String(history.trade_date??'')+'T00:00:00+08:00');
  if(!day||day.date!==history.trade_date)return {...metadata,flow_error:'指数资金交易日期无效'};
  let latest=null;
  for(const bar of history.bars){
    const stamp=sourceStamp(bar?.time);
    if(!stamp||stamp.date!==history.trade_date||!finite(bar.main_net))continue;
    if(!latest||stamp.epoch>latest.stamp.epoch)latest={bar,stamp};
  }
  if(!latest)return {...metadata,flow_error:metadata.flow_error??'尚无有效指数资金分钟记录'};
  const priceStamp=sourceStamp(item.as_of),sourceTime=latest.bar.time;
  if(priceStamp&&priceStamp.date!==latest.stamp.date)return {...metadata,flow_as_of:sourceTime,flow_stale:true,flow_error:`资金日期 ${latest.stamp.date} 与价格日期 ${priceStamp.date} 不一致`};
  return {...metadata,main_net:latest.bar.main_net,flow_as_of:sourceTime,flow_status:history.status==='stale'?'stale':metadata.flow_stale?'cached':history.status,flow_unit:'CNY'};
}

/** Join ETF snapshots and index minute histories without combining their ranking universes.
 * Third parameter is an array of actual per-index histories; examples: docs/INTRADAY_FLOW.md, 观察池指数资金修复.
 */
export function mergeWatchingSnapshot(quotes,flow,indexHistories=[],now=Date.now()){
  quotes=withMarketFreshness(quotes,now);flow=withMarketFreshness(flow,now);
  indexHistories=indexHistories.map(history=>withMarketFreshness(history,now));
  const rows=new Map(flow.rows.map(row=>[row.symbol,row]));
  const indexes=new Map(indexHistories.filter(Boolean).map(history=>[history.symbol,history]));
  return {...quotes,
    instruments:quotes.instruments.map(row=>{
      if(row.kind==='index'){
        const {flow_unit,...projected}=projectIndexFlow(row,indexes.get(row.symbol));
        return {...row,...projected,unit:{...row.unit,main_net:flow_unit}};
      }
      const f=rows.get(row.symbol),priceDate=shanghaiDate(row.as_of??null),flowDate=shanghaiDate(f?.as_of??null);
      const mismatch=priceDate&&flowDate&&priceDate!==flowDate;
      return {...row,main_net:mismatch?null:f?.main_net??null,main_pct:mismatch?null:f?.main_pct??null,flow_status:mismatch?'unavailable':f?.status??'unavailable',flow_as_of:f?.as_of??null,flow_source:f?.source??null,flow_fetched_at:f?.fetched_at??flow.fetched_at??null,flow_stale:Boolean(mismatch)||f?.stale===true||flow.stale===true||f?.status==='cached',flow_freshness:f?.freshness,flow_source_date:f?.source_date,flow_source_status:f?.source_status??f?.status,flow_error:mismatch?`资金日期 ${flowDate} 与价格日期 ${priceDate} 不一致`:f?.error??flow.error??null,unit:{...row.unit,main_net:mismatch?'unavailable':f?.unit??'unavailable'}};
    }),
    flows:flow.rows,flow_status:flow.status,flow_source:flow.source,flow_as_of:flow.as_of,flow_fetched_at:flow.fetched_at,
    flow_stale:flow.stale,flow_freshness:flow.freshness,flow_source_date:flow.source_date,flow_source_status:flow.source_status,flow_note:flow.note,flow_error:flow.error??null,flow_errors:flow.errors??[],
  };
}

/** Read six index histories with three workers while prices and ETF snapshots load.
 * Optional loaders are test controls; ordinary calls use the existing cached clients.
 * Real-input calls and outputs: docs/INTRADAY_FLOW.md, 观察池指数资金修复.
 */
export async function getWatchingSnapshot({getQuotes=getSnapshot,getEtfFlow=getFundFlow,getIndexFlow=getIntradayFlow,now=Date.now}={}){
  const indexHistories=Array(INDEXES.length);let next=0;
  const indexWorkers=Array.from({length:3},async()=>{
    while(next<INDEXES.length){
      const index=next++,item=INDEXES[index];
      try{indexHistories[index]=await getIndexFlow(item.symbol,item.kind);}
      catch(error){indexHistories[index]={symbol:item.symbol,kind:item.kind,scope:'instrument',unit:'CNY',status:'unavailable',bars:[],error:String(error?.message??error).slice(0,180)};}
    }
  });
  const [quotes,flow]=await Promise.all([getQuotes(),getEtfFlow(),Promise.all(indexWorkers)]);
  const stamp=now();
  return {snapshot:mergeWatchingSnapshot(quotes,flow,indexHistories,stamp),flow:withMarketFreshness(flow,stamp),indexHistories:indexHistories.map(history=>withMarketFreshness(history,stamp))};
}
export function getWatchingHealth(){
  const old=getHealth();
  return {...old,service:'market-lens-market-watch',provider:'tencent+eastmoney',capabilities:{...old.capabilities,main_flow:true,flow_history:true,background_refresh:true,persistent_prices:true},limitations:['腾讯提供走势，东方财富提供 ETF 资金快照与指数 / ETF 当日资金分时；各自源时间独立。','免费源不承诺更新延迟；60 秒是页面可见时的刷新与源缓存间隔，后台频率由已关联定时任务决定。','后台和页面共用持久存储；上游缺口不补造，失败时标明缓存与原始日期。指数采用上游统计口径，ETF 申赎未接入。']};
}
