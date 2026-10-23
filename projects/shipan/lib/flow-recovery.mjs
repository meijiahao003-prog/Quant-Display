import {UNIVERSE} from './market-data.mjs';
import {shanghaiDate,withMarketFreshness} from '../public/market-time.mjs';

const INDEX_SYMBOLS=new Set(UNIVERSE.filter(item=>item.kind==='index').map(item=>item.symbol));
const validBars=value=>Array.isArray(value?.bars)&&value.bars.some(row=>typeof row?.main_net==='number'&&Number.isFinite(row.main_net));
const candidates=histories=>(Array.isArray(histories)?histories:[]).filter(history=>history?.kind==='index'&&INDEX_SYMBOLS.has(history.symbol)&&history.scope==='instrument'&&history.unit==='CNY'&&['live','partial'].includes(history.status)&&!history.stale&&validBars(history));

/** Persist only validated index minute histories, with at most three concurrent
 * writers. Failed writers remain explicit results and do not reject sibling work.
 * Inputs, executable test calls and observed output: docs/FLOW_RECOVERY.md. */
export async function persistIndexFlowHistories(histories,saveHistory,{concurrency=3}={}){
  if(typeof saveHistory!=='function')throw new TypeError('saveHistory 必须是存储函数');
  const eligible=candidates(histories),results=Array(eligible.length);
  const workers=Math.min(3,Math.max(1,Math.trunc(Number(concurrency))||1),eligible.length);
  let next=0;
  await Promise.all(Array.from({length:workers},async()=>{
    while(next<eligible.length){
      const index=next++,history=eligible[index];
      try{
        const result=await saveHistory(history);
        results[index]={symbol:history.symbol,trade_date:history.trade_date,as_of:history.as_of,...result};
      }catch{
        results[index]={symbol:history.symbol,trade_date:history.trade_date,as_of:history.as_of,status:'unavailable',saved:0,error:'指数资金分钟存储失败'};
      }
    }
  }));
  return {status:results.some(row=>row.status!=='ok')?'partial':'ok',candidates:eligible.length,saved:results.reduce((sum,row)=>sum+(Number.isFinite(row.saved)?row.saved:0),0),results};
}

/** Register a response-lifetime background write. Returning scheduled means only
 * the task was accepted, never that D1 has committed. Errors go to sanitized logs.
 * Actual Vinext waitUntil implementation and runnable tests: docs/FLOW_RECOVERY.md. */
export function scheduleIndexFlowStorage(histories,{after,saveHistory,logError=(_result)=>{}}){
  const eligible=candidates(histories);
  if(!eligible.length)return {status:'skipped',candidates:0,error:null};
  try{
    after(async()=>{
      const result=await persistIndexFlowHistories(eligible,saveHistory);
      for(const row of result.results)if(row.status!=='ok')logError({symbol:row.symbol,trade_date:row.trade_date,status:'unavailable',error:'指数资金分钟存储失败'});
    });
    return {status:'scheduled',candidates:eligible.length,error:null};
  }catch{
    const error='指数资金分钟存储任务未能调度';
    logError({status:'unavailable',error});
    return {status:'unavailable',candidates:eligible.length,error};
  }
}

/** Prefer the latest actual source DAY before preferring provider minute history.
 * Same-day provider records beat sampled snapshots; older-day provider records
 * cannot suppress today's genuine samples. Records are never merged or redated.
 * Inputs, real recorded replay and synthetic regression tests: docs/FLOW_RECOVERY.md. */
export function selectFlowFallback({upstream,stored,sampled,item,now=Date.now()}){
  const eligible=[{data:upstream,type:'provider_minutes',rank:2},{data:stored,type:'provider_minutes',rank:1},...(item.kind==='etf'?[{data:sampled,type:'sampled_snapshots',rank:0}]:[])].flatMap(candidate=>{
    const data=candidate.data,day=shanghaiDate(data?.as_of??null),tradeDate=shanghaiDate(data?.trade_date??null);
    if(!validBars(data)||data.unit!=='CNY'||data.symbol!==item.symbol||data.kind&&data.kind!==item.kind||!day||tradeDate&&tradeDate!==day)return [];
    return [{...candidate,day,epoch:Date.parse(data.as_of)}];
  }).sort((a,b)=>b.day.localeCompare(a.day)||(a.type===b.type?b.epoch-a.epoch||b.rank-a.rank:a.type==='provider_minutes'?-1:1));
  const selected=eligible[0];
  if(!selected)return withMarketFreshness({...upstream,source_type:'provider_minutes'},now);
  const data=selected.data;
  if(selected.type==='sampled_snapshots')return withMarketFreshness({
    ...data,kind:item.kind,name:item.name,scope:'instrument',source_type:selected.type,stale:true,
    upstream_source_date:shanghaiDate(upstream?.as_of??null),
    error:upstream?.error??null,
    note:'以下为该交易日已实际采集的资金净额点，不是完整的源分钟历史；未拼接旧日记录。'+(data.note??''),
  },now);
  return withMarketFreshness({
    ...data,source_type:selected.type,
    ...(data===stored?{error:upstream?.error??null,last_attempt_at:upstream?.last_attempt_at??upstream?.served_at,
      note:upstream?.error?data.note:`上游最新源日期为 ${shanghaiDate(upstream?.as_of??null)??'未知'}；显示最近保存的源日期 ${data.trade_date}，保留原始时间。`}:{}),
  },now);
}
