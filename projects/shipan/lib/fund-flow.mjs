import {shanghaiDate,withMarketFreshness} from '../public/market-time.mjs';
/** Eastmoney ETF order-size net flow, matching AKShare fund_etf_spot_em fields.
 * This Worker calls the public upstream; it does not run Python AKShare.
 * Real inputs, units, execution and limitations: docs/EASTMONEY_FLOW.md.
 */
import { UNIVERSE } from './market-data.mjs';

export const FLOW_SOURCE='东方财富 ETF 资金 · AKShare 同源字段 · 免费延迟源';
export const FLOW_NOTE='主力净额为超大单与大单净额之和，是供应商按成交单大小统计的净值；不代表机构身份，也不是 ETF 申赎。免费源无延迟保证。';
export const FLOW_UNIVERSE=Object.freeze(UNIVERSE.filter(row=>row.kind==='etf'));
const FIELDS='f2,f3,f5,f6,f12,f13,f14,f15,f16,f62,f66,f69,f72,f75,f78,f81,f84,f87,f124,f184,f297';
const TTL=60_000,MAX_BYTES=500_000;
const clone=x=>JSON.parse(JSON.stringify(x));
const num=x=>x===null||x===undefined||typeof x==='boolean'||String(x).trim()===''||!Number.isFinite(Number(x))?null:Number(x);
const moneyFields={main_net:'f62',main_pct:'f184',super_net:'f66',super_pct:'f69',large_net:'f72',large_pct:'f75',medium_net:'f78',medium_pct:'f81',small_net:'f84',small_pct:'f87'};
const empty=item=>({symbol:item.symbol,name:item.name,...Object.fromEntries(Object.keys(moneyFields).map(key=>[key,null])),as_of:null,trade_date:null,status:'unavailable',source:FLOW_SOURCE,unit:'CNY'});
const dateOf=x=>{
  const s=String(x??'').replaceAll('-','');
  if(!/^\d{8}$/.test(s))return null;
  const d=`${s.slice(0,4)}-${s.slice(4,6)}-${s.slice(6,8)}`;
  const parsed=new Date(d+'T00:00:00Z');
  return Number.isFinite(parsed.getTime())&&parsed.toISOString().slice(0,10)===d?d:null;
};
const sourceTime=x=>{
  const n=num(x);
  if(n===null||n<946684800||n>4102444800)return null;
  return new Date(n*1000+8*3600000).toISOString().slice(0,19)+'+08:00';
};

/** One bounded request for the ten allowed ETFs; no user-controlled upstream URL. */
export function fundFlowUrl(){
  const params=new URLSearchParams({pn:'1',pz:'100',po:'1',np:'1',ut:'bd1d9ddb04089700cf9c27f6f7426281',fltt:'2',invt:'2',wbp2u:'|0|0|0|web',fid:'f12',fs:FLOW_UNIVERSE.map(x=>`i:${x.exchange==='sh'?'1':'0'}.${x.symbol}`).join(','),fields:FIELDS});
  return 'https://push2delay.eastmoney.com/api/qt/clist/get?'+params;
}

/** Strict fixed-schema parse. '-' and missing data stay null; zero remains zero. */
export function parseFundFlow(payload){
  if(!payload||payload.rc!==0||!Array.isArray(payload.data?.diff))throw new Error('东方财富资金响应格式或状态无效');
  if(payload.data.diff.length>100)throw new Error('东方财富资金记录超过限制');
  const records=new Map(payload.data.diff.map(x=>[`${x.f13}.${x.f12}`,x]));
  const rows=FLOW_UNIVERSE.map(item=>{
    const row=empty(item),raw=records.get(`${item.exchange==='sh'?1:0}.${item.symbol}`);
    if(!raw)return row;
    row.as_of=sourceTime(raw.f124);row.trade_date=dateOf(raw.f297);
    if(!row.as_of||!row.trade_date||row.as_of.slice(0,10)!==row.trade_date){row.error='资金源时间与交易日期缺失或不一致';return row;}
    for(const [name,field] of Object.entries(moneyFields))row[name]=num(raw[field]);
    // Detect a changed schema/scaling before rendering money. No replacement inference.
    const amount=num(raw.f6);
    const inconsistent=(row.main_net!==null&&row.super_net!==null&&row.large_net!==null&&Math.abs(row.main_net-row.super_net-row.large_net)>1)
      ||(row.main_net!==null&&row.main_pct!==null&&amount>0&&Math.abs(row.main_net/amount*100-row.main_pct)>0.15);
    if(inconsistent){for(const key of Object.keys(moneyFields))row[key]=null;row.error='资金字段一致性检查未通过';return row;}
    row.status=row.main_net===null?'unavailable':['super_net','large_net','medium_net','small_net'].some(key=>row[key]===null)?'partial':'live';
    return row;
  });
  const valid=rows.filter(x=>x.main_net!==null);
  return {source:FLOW_SOURCE,note:FLOW_NOTE,unit:'CNY',rows,status:valid.length===rows.length&&rows.every(x=>x.status==='live')?'live':valid.length?'partial':'unavailable',as_of:valid.map(x=>x.as_of).sort().at(-1)??null,errors:rows.filter(x=>x.status!=='live').map(x=>`${x.symbol} ${x.error||'部分资金字段缺失'}`)};
}

/** TTL cache and last-good data preserve source time and fetch time on failures.
 * timeoutMs is a total fetch+body deadline (default 12s), independently enforced
 * even when upstream ignores AbortSignal. Runnable regression examples:
 * node --test tests/fund-flow.test.mjs; real-input calls: docs/EASTMONEY_FLOW.md.
 */
export function createFundFlowClient({fetchImpl=globalThis.fetch,now=Date.now,timeoutMs=12_000}={}){
  // Share completed data only, not promises owned by a possibly canceled Worker
  // request. Each cache miss has its own bounded source read. See lifetime docs.
  let cache=null,generation=0;
  async function load(){
    const controller=new AbortController();let timer;
    const deadline=new Promise((_,reject)=>{
      timer=setTimeout(()=>{
        controller.abort();
        reject(new DOMException('东方财富资金源请求超时','AbortError'));
      },timeoutMs);
    });
    const read=async()=>{
      const response=await fetchImpl(fundFlowUrl(),{signal:controller.signal,headers:{Accept:'application/json'},redirect:'manual'});
      if(!response.ok)throw new Error(`东方财富资金源 HTTP ${response.status}`);
      if(Number(response.headers.get('content-length'))>MAX_BYTES)throw new Error('资金响应超过长度限制');
      const body=await response.arrayBuffer();
      if(body.byteLength>MAX_BYTES)throw new Error('资金响应超过长度限制');
      const data=parseFundFlow(JSON.parse(new TextDecoder().decode(body)));
      if(data.status==='unavailable')throw new Error(data.errors.join('；')||'未返回有效 ETF 资金数据');
      return data;
    };
    try{return await Promise.race([read(),deadline]);}finally{clearTimeout(timer);}
  }
  return {async getFundFlow(){
    const stamp=now(),requestDay=shanghaiDate(stamp);
    if(cache&&stamp>=cache.at&&stamp-cache.at<TTL&&requestDay===cache.requestDay)return withMarketFreshness({...clone(cache.value),served_at:new Date(stamp).toISOString(),cache_hit:true},stamp);
    const requestId=++generation;
    const task=(async()=>{
      try{
        const data=await load(),finished=now(),time=new Date(finished).toISOString();
        const previous=cache?.value;
        if(previous&&cache.requestId>requestId&&Date.parse(data.as_of)<=Date.parse(previous.as_of))return {...clone(previous),served_at:time,cache_hit:true};
        if(previous&&Date.parse(data.as_of)<Date.parse(previous.as_of))throw new Error(`东方财富资金源时间回退至 ${data.as_of}；保留较新记录 ${previous.as_of}`);
        if(previous?.rows){
          const previousRows=new Map(previous.rows.map(row=>[row.symbol,row]));let retained=0;
          data.rows=data.rows.map(row=>{
            const old=previousRows.get(row.symbol);
            if(old?.main_net!==null&&Number.isFinite(Date.parse(old?.as_of))&&(row.main_net===null||Date.parse(row.as_of)<Date.parse(old.as_of))){
              retained++;return {...old,status:'cached',source_status:'cached',stale:true,fetched_at:old.fetched_at??previous.fetched_at};
            }
            return row;
          });
          if(retained){data.status='partial';data.as_of=data.rows.map(row=>row.as_of).filter(Boolean).sort().at(-1)??data.as_of;}
        }
        cache={at:finished,requestDay,requestId,value:{...data,fetched_at:time,served_at:time,stale:false,cache_hit:false}};
      }catch(error){
        const finished=now(),time=new Date(finished).toISOString();
        const message=error.name==='AbortError'?'东方财富资金源请求超时':String(error.message).slice(0,180);
        if(cache?.requestId>requestId)return {...clone(cache.value),served_at:time,cache_hit:true};
        const old=cache?.value;
        cache={at:finished,requestDay,requestId,value:old&&old.status!=='unavailable'?{...old,status:'cached',source_status:'cached',rows:old.rows.map(row=>row.main_net===null?row:{...row,status:'cached',source_status:'cached',stale:true}),stale:true,cache_hit:true,served_at:time,last_attempt_at:time,error:message}:{source:FLOW_SOURCE,note:FLOW_NOTE,unit:'CNY',rows:FLOW_UNIVERSE.map(empty),status:'unavailable',as_of:null,fetched_at:null,served_at:time,last_attempt_at:time,stale:false,cache_hit:false,error:message}};
      }
      return clone(cache.value);
    })();
    return withMarketFreshness(await task,now());
  }};
}
const client=createFundFlowClient();
export const getFundFlow=()=>client.getFundFlow();
