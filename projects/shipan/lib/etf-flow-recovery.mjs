import {FLOW_UNIVERSE,FLOW_NOTE,getFundFlow} from './fund-flow.mjs';
import {getIntradayFlow} from './intraday-flow.mjs';
import {shanghaiDate,withMarketFreshness} from '../public/market-time.mjs';

const SOURCE='东方财富 ETF 资金 · 快照 / 资金分时末点';
const NET_KEYS=['main_net','super_net','large_net','medium_net','small_net'];
const PCT_KEYS=['main_pct','super_pct','large_pct','medium_pct','small_pct'];
const OBSERVED=new Set(['live','partial','cached','stale','recorded']);
const finite=value=>typeof value==='number'&&Number.isFinite(value);
const nullable=value=>finite(value)?value:null;
const message=error=>String(error?.message??error??'资金源未返回有效数据').slice(0,180);
const stamp=value=>typeof value==='string'&&/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?(?:Z|[+-]\d{2}:\d{2})$/.test(value)&&shanghaiDate(value)?Date.parse(value):NaN;
const complete=row=>NET_KEYS.every(key=>finite(row?.[key]));
const cached=row=>row?.stale===true||['cached','stale','recorded'].includes(row?.status)||row?.source_status==='cached';

/** Keep one timestamped, correctly identified ETF row. Nulls stay null and zero
 * stays zero; no values are combined across timestamps. Executable cases and
 * before/after shapes: docs/ETF_FLOW_RECOVERY.md, tests/etf-flow-recovery.test.mjs. */
function normalizeRow(item,row,{parent={},stored=false,sourceType='snapshot'}={}){
  if(!row||row.symbol!==item.symbol||(row.kind&&row.kind!=='etf')||row.unit!=='CNY'||
    !OBSERVED.has(row.status)||!finite(row.main_net)||!Number.isFinite(stamp(row.as_of))||
    !row.trade_date||shanghaiDate(row.as_of)!==row.trade_date)return null;
  if(finite(row.super_net)&&finite(row.large_net)&&Math.abs(row.main_net-row.super_net-row.large_net)>1)return null;
  const isCached=stored||cached(row)||cached(parent);
  const fetchedAt=row.fetched_at??parent.fetched_at;
  const status=isCached?'cached':complete(row)?'live':'partial';
  return {
    symbol:item.symbol,kind:'etf',name:item.name,unit:'CNY',
    ...Object.fromEntries(NET_KEYS.map(key=>[key,nullable(row[key])])),
    ...Object.fromEntries(PCT_KEYS.map(key=>[key,sourceType==='provider_minutes'||stored?null:nullable(row[key])])),
    as_of:row.as_of,trade_date:row.trade_date,
    fetched_at:Number.isFinite(stamp(fetchedAt))?fetchedAt:null,
    source:row.source??parent.source??SOURCE,source_type:row.source_type??sourceType,
    status,source_status:status,stale:isCached,cache_hit:isCached,
    ...(row.error||isCached&&parent.error?{error:row.error??parent.error}:{}),
  };
}

/** Project the newest valid minute belonging to this ETF and provider day.
 * Amounts are copied verbatim; all percentages remain null. Executable examples
 * and validation results: docs/ETF_FLOW_RECOVERY.md. */
function minuteRow(item,history){
  if(!history||history.symbol!==item.symbol||history.kind!=='etf'||history.scope!=='instrument'||
    history.unit!=='CNY'||!OBSERVED.has(history.status)||!Array.isArray(history.bars)||
    history.bars.length>500||!history.trade_date||shanghaiDate(history.as_of)!==history.trade_date)return null;
  let latest=null;
  for(const bar of history.bars){
    const row=normalizeRow(item,{...bar,symbol:item.symbol,kind:'etf',unit:'CNY',
      as_of:bar?.time,trade_date:history.trade_date,status:history.status,
      source:history.source,source_type:'provider_minutes',fetched_at:bar?.fetched_at??history.fetched_at,
    },{parent:history,sourceType:'provider_minutes'});
    if(row&&(!latest||stamp(row.as_of)>=stamp(latest.as_of)))latest=row;
  }
  return latest;
}

/** Select an entire observation by source time; equal times favor fresh data,
 * then complete amount fields. Regressing responses cannot overwrite a newer
 * stored point. Executable boundary cases: tests/etf-flow-recovery.test.mjs. */
function newest(rows){
  return rows.filter(Boolean).sort((a,b)=>stamp(b.as_of)-stamp(a.as_of)||Number(cached(a))-Number(cached(b))||
    NET_KEYS.filter(key=>finite(b[key])).length-NET_KEYS.filter(key=>finite(a[key])).length)[0]??null;
}

/** Recover the ten-ETF ranking/structure payload from the snapshot, genuine
 * provider minute histories and durable rows. Readers are injectable for tests;
 * ordinary use supplies the database reader from the route. Five workers bound
 * upstream fan-out and a failed ETF never rejects its siblings.
 * Purpose, real-input call, observed/unexecuted output and before/after details:
 * docs/ETF_FLOW_RECOVERY.md.
 * @param {{getSnapshotFlow?: () => Promise<any>, getMinuteFlow?: (symbol: string, kind: string) => Promise<any>, getStoredRows?: () => Promise<{rows: Array<Record<string, any>>, status?: string, error?: string | null} | Array<Record<string, any>>>, now?: () => number}} options
 */
export async function getRecoveredEtfFlow({getSnapshotFlow=getFundFlow,getMinuteFlow=getIntradayFlow,getStoredRows=async()=>({rows:[]}),now=Date.now}={}){
  const [snapshotResult,storedResult]=await Promise.allSettled([
    Promise.resolve().then(()=>getSnapshotFlow()),Promise.resolve().then(()=>getStoredRows()),
  ]);
  const snapshot=snapshotResult.status==='fulfilled'?snapshotResult.value:{status:'unavailable',rows:[],error:message(snapshotResult.reason)};
  const stored=storedResult.status==='fulfilled'?storedResult.value:{status:'unavailable',rows:[],error:message(storedResult.reason)};
  const storedRows=Array.isArray(stored)?stored:Array.isArray(stored?.rows)?stored.rows:[];
  const rows=Array(FLOW_UNIVERSE.length),recoveryErrors=[],today=shanghaiDate(now());
  const work=FLOW_UNIVERSE.map((item,index)=>{
    const primary=newest((Array.isArray(snapshot?.rows)?snapshot.rows:[]).filter(row=>row?.symbol===item.symbol)
      .map(row=>normalizeRow(item,row,{parent:snapshot})));
    const saved=newest(storedRows.filter(row=>row?.symbol===item.symbol)
      .map(row=>normalizeRow(item,{...row,status:'cached'},{stored:true,sourceType:row.source_type??'stored_observation'})));
    const selected=newest([primary,saved]);
    rows[index]=selected;
    const needsRecovery=!primary||cached(primary)||primary.trade_date!==today||!complete(primary)||
      saved&&stamp(saved.as_of)>stamp(primary.as_of);
    return {item,index,primary,saved,needsRecovery};
  }).filter(item=>item.needsRecovery);
  let cursor=0;
  await Promise.all(Array.from({length:Math.min(5,work.length)},async()=>{
    while(cursor<work.length){
      const {item,index,primary,saved}=work[cursor++];
      try{
        const history=await getMinuteFlow(item.symbol,'etf'),point=minuteRow(item,history);
        rows[index]=newest([primary,point,saved]);
        if(!point||history.error)recoveryErrors.push(`${item.symbol} ${message(history?.error??'资金分时没有可用末点')}`);
      }catch(error){recoveryErrors.push(`${item.symbol} ${message(error)}`);}
    }
  }));
  const finished=now(),servedAt=new Date(finished).toISOString();
  const normalized=rows.map((row,index)=>withMarketFreshness(row??{
    symbol:FLOW_UNIVERSE[index].symbol,kind:'etf',name:FLOW_UNIVERSE[index].name,unit:'CNY',
    ...Object.fromEntries([...NET_KEYS,...PCT_KEYS].map(key=>[key,null])),
    as_of:null,trade_date:null,fetched_at:null,source:SOURCE,source_type:null,
    status:'unavailable',source_status:'unavailable',stale:false,
    error:recoveryErrors.find(error=>error.startsWith(FLOW_UNIVERSE[index].symbol+' '))??snapshot?.error??'资金源暂未提供有效记录',
  },finished));
  const valid=normalized.filter(row=>finite(row.main_net));
  const fresh=valid.filter(row=>!cached(row));
  const status=!valid.length?'unavailable':!fresh.length?'cached':
    valid.length===normalized.length&&fresh.length===valid.length&&valid.every(row=>row.status==='live')?'live':'partial';
  const times=valid.map(row=>row.as_of).sort((a,b)=>stamp(a)-stamp(b));
  const fetchTimes=valid.map(row=>row.fetched_at).filter(Boolean).sort((a,b)=>stamp(a)-stamp(b));
  const fallbackCount=valid.filter(row=>row.source_type!=='snapshot').length;
  const errors=[...(snapshot?.error?[`ETF 快照：${message(snapshot.error)}`]:[]),
    ...(stored?.error?[`历史读取：${message(stored.error)}`]:[]),...recoveryErrors];
  return withMarketFreshness({
    source:SOURCE,note:FLOW_NOTE+' 排行与结构优先使用 ETF 快照；缺失时读取该 ETF 最新资金分时末点或已存真实记录。每行保留自己的源时间与取得时间。',
    unit:'CNY',rows:normalized,status,source_status:status,
    as_of:times.at(-1)??null,fetched_at:fetchTimes.at(-1)??null,served_at:servedAt,last_attempt_at:servedAt,
    stale:status==='cached',cache_hit:valid.some(cached),fallback_count:fallbackCount,
    recovery_status:work.length?'attempted':'not_needed',storage_read_status:stored?.status??'ok',
    errors,error:status==='unavailable'?errors.join('；')||'ETF 资金暂无有效源记录':null,
  },finished);
}
