import {shanghaiDate,withMarketFreshness} from '../public/market-time.mjs';
/** Eastmoney's existing same-day order-size fund-flow minute series.
 * This is provider history, not a reconstruction from page snapshots.
 * Function purposes, real inputs, runnable examples and observed outputs:
 * docs/INTRADAY_FLOW.md. All money remains in CNY until the chart formats it.
 */
import { UNIVERSE } from './market-data.mjs';

export const INTRADAY_FLOW_SOURCE='东方财富 · 当日资金分时';
export const INTRADAY_FLOW_NOTE='直接补拉数据源已有的当日分钟资金序列。净额为当日累计值，主力为大单与超大单之和；指数采用数据源对应指数口径，ETF 为自身交易口径。缺失时段留空，订单分类不代表机构身份，公开源延迟未承诺。';
const TTL=60_000,MAX_BYTES=500_000,MAX_ROWS=500;
const KEYS=['main_net','small_net','medium_net','large_net','super_net'];
const REGULAR_MINUTES=[...Array.from({length:120},(_,i)=>571+i),...Array.from({length:120},(_,i)=>781+i)];
const clone=value=>JSON.parse(JSON.stringify(value));
const iso=epoch=>new Date(epoch).toISOString();
const number=value=>typeof value==='string'&&/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(value.trim())&&Number.isFinite(Number(value))?Number(value):null;
const networkError=error=>Object.assign(new Error(String(error?.message??error),{cause:error}),{name:error?.name??'Error',retryable:true});

/** Resolve only the platform's fixed universe; example: docs/INTRADAY_FLOW.md. */
export function resolveIntradayInstrument(symbol,kind){
  const item=UNIVERSE.find(row=>row.symbol===symbol&&(kind===undefined||row.kind===kind));
  if(!item){const error=new Error('资金分时仅支持观察池中的指数或 ETF，标的类型须匹配');error.status=400;throw error;}
  return item;
}

/** Build a fixed-origin request with an explicit exchange, never a user URL.
 * Actual-input execution: docs/INTRADAY_FLOW.md, intradayFlowUrl.
 */
export function intradayFlowUrl(symbol,kind){
  const item=resolveIntradayInstrument(symbol,kind);
  const params=new URLSearchParams({lmt:'0',klt:'1',secid:`${item.exchange==='sh'?1:0}.${item.symbol}`,fields1:'f1,f2,f3,f7',fields2:'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63'});
  return 'https://push2.eastmoney.com/api/qt/stock/fflow/kline/get?'+params;
}

/** Validate provider minute labels as Shanghai exchange-session timestamps.
 * Strict date rollover and session handling are exercised in the parser example.
 */
function parseMinute(value){
  const match=/^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})$/.exec(value);
  if(!match)return null;
  const [year,month,day,hour,minute]=match.slice(1).map(Number);
  if(year<2000||year>2100||month<1||month>12||day<1||hour>23||minute>59)return null;
  const local=new Date(Date.UTC(year,month-1,day,hour,minute));
  if(local.getUTCFullYear()!==year||local.getUTCMonth()!==month-1||local.getUTCDate()!==day)return null;
  const clock=hour*60+minute;
  if(!((clock>=570&&clock<=690)||(clock>=780&&clock<=900)))return null;
  return {date:value.slice(0,10),minute:clock,time:value.replace(' ','T')+':00+08:00',epoch:local.getTime()-8*3600_000};
}

/** Parse genuine provider observations only. See docs/INTRADAY_FLOW.md.
 * Bad identities/schema reject the response; bad rows are omitted and reported.
 * Optional missing amounts stay null. Duplicate minutes keep the last source row.
 * If a source spans dates, only its latest valid date is returned, never spliced.
 */
export function parseIntradayFlow(payload,symbol,kind){
  const item=resolveIntradayInstrument(symbol,kind),raw=payload?.data;
  if(payload?.rc!==0||!raw||!Array.isArray(raw.klines))throw new Error('东方财富资金分时返回格式或状态无效');
  if(String(raw.code)!==item.symbol||![0,1,'0','1'].includes(raw.market)||String(raw.market)!==(item.exchange==='sh'?'1':'0'))throw new Error('资金分时返回的代码或交易所与请求不一致');
  if(!raw.klines.length)throw new Error('数据源尚未提供该标的的当日资金分时');
  if(raw.klines.length>MAX_ROWS)throw new Error('资金分时记录超过长度限制');
  let invalidRows=0,missingFields=0,duplicateRows=0;
  const parsed=[];
  for(const line of raw.klines){
    const fields=typeof line==='string'?line.split(','):[];
    const stamp=fields.length===6?parseMinute(fields[0]):null;
    if(!stamp){invalidRows++;continue;}
    const values=fields.slice(1).map(number),[main,,,large,superNet]=values;
    if(main===null||(large!==null&&superNet!==null&&Math.abs(main-large-superNet)>1)){
      invalidRows++;continue;
    }
    if(values.some(value=>value===null))missingFields++;
    parsed.push({stamp,row:{time:stamp.time,...Object.fromEntries(KEYS.map((key,index)=>[key,values[index]]))}});
  }
  if(!parsed.length)throw new Error('资金分时没有通过时间和金额校验的有效记录');
  const tradeDate=parsed.map(point=>point.stamp.date).sort().at(-1);
  const points=new Map();let droppedOtherDayRows=0;
  for(const point of parsed){
    if(point.stamp.date!==tradeDate){droppedOtherDayRows++;continue;}
    if(points.has(point.stamp.minute))duplicateRows++;
    points.set(point.stamp.minute,point);
  }
  const ordered=[...points.values()].sort((a,b)=>a.stamp.epoch-b.stamp.epoch);
  const bars=ordered.map(point=>point.row),last=ordered.at(-1);
  const expected=REGULAR_MINUTES.filter(minute=>minute<=last.stamp.minute);
  const missing=expected.filter(minute=>!points.has(minute));
  const coverage={observed_minutes:bars.length,expected_minutes:expected.length,missing_minutes:missing.length,invalid_rows:invalidRows,duplicate_rows:duplicateRows,dropped_other_day_rows:droppedOtherDayRows,first_time:bars[0].time,last_time:last.row.time};
  const partial=invalidRows>0||missingFields>0||missing.length>0||droppedOtherDayRows>0;
  return {symbol:item.symbol,kind:item.kind,name:typeof raw.name==='string'?raw.name:item.name,source:INTRADAY_FLOW_SOURCE,scope:'instrument',unit:'CNY',status:partial?'partial':'live',bars,trade_date:tradeDate,as_of:last.row.time,coverage,note:INTRADAY_FLOW_NOTE};
}

/** Bounded response read, including servers that omit Content-Length.
 * Executed through the real getIntradayFlow example in docs/INTRADAY_FLOW.md.
 */
async function readPayload(response){
  if(Number(response.headers.get('content-length'))>MAX_BYTES)throw new Error('资金分时响应超过长度限制');
  const reader=response.body?.getReader();
  if(!reader)throw new Error('资金分时响应正文为空');
  const chunks=[];let length=0;
  try{
    while(true){
      let next;
      try{next=await reader.read();}catch(error){throw networkError(error);}
      const {done,value}=next;if(done)break;
      length+=value.byteLength;
      if(length>MAX_BYTES){await reader.cancel();throw new Error('资金分时响应超过长度限制');}
      chunks.push(value);
    }
  }finally{reader.releaseLock();}
  const bytes=new Uint8Array(length);let offset=0;
  for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.byteLength;}
  return JSON.parse(new TextDecoder().decode(bytes));
}

/** Per-instrument 60-second settled cache and request-local source reads with last-good
 * fallback. A failed refresh never relabels old observations as new source data.
 * timeoutMs bounds each attempt; at most two attempts. Worker request lifetime
 * and midnight cases: docs/SOURCE_REQUEST_LIFETIME.md; real calls: INTRADAY_FLOW.md.
 */
export function createIntradayFlowClient({fetchImpl=globalThis.fetch,now=Date.now,timeoutMs=12_000}={}){
  const cache=new Map(),generations=new Map();
  // One attempt includes its entire redirect/body sequence. The second, when
  // eligible, uses the already verified provider host rather than arbitrary URLs.
  async function attempt(original,symbol,kind,retry){
    const controller=new AbortController();let timer;
    const deadline=new Promise((_,reject)=>{timer=setTimeout(()=>{
      controller.abort();reject(networkError(new DOMException('东方财富资金分时请求超时','AbortError')));
    },timeoutMs);});
    const run=async()=>{
      let target=new URL(original);if(retry)target.hostname='push2delay.eastmoney.com';
      for(let redirects=0;redirects<=3;redirects++){
        let response;
        try{response=await fetchImpl(target.href,{signal:controller.signal,headers:{Accept:'application/json'},redirect:'manual'});}
        catch(error){throw networkError(error);}
        if([301,302,303,307,308].includes(response.status)){
          const location=response.headers.get('location');
          await response.body?.cancel();
          if(!location||redirects===3)throw new Error('资金分时重定向缺失或超过次数限制');
          const next=new URL(location,target);
          if(next.protocol!=='https:'||next.username||next.password||next.port||!['push2.eastmoney.com','push2delay.eastmoney.com'].includes(next.hostname)||next.pathname!==original.pathname){
            throw new Error('资金分时重定向目标不在已验证的数据源范围');
          }
          // The actual upstream redirects to push2delay with the same query.
          // Retain our allowlisted symbol and schema even if Location changes it.
          next.search=original.search;next.hash='';target=next;continue;
        }
        if(!response.ok){
          try{await response.body?.cancel();}catch{}
          throw Object.assign(new Error(`东方财富资金分时 HTTP ${response.status}`),{retryable:[502,503,504].includes(response.status)});
        }
        return {...parseIntradayFlow(await readPayload(response),symbol,kind),upstream_attempts:retry?2:1,upstream_host:target.hostname};
      }
      throw new Error('资金分时重定向超过次数限制');
    };
    try{return await Promise.race([run(),deadline]);}finally{clearTimeout(timer);}
  }
  async function load(symbol,kind){
    const original=new URL(intradayFlowUrl(symbol,kind));
    try{return await attempt(original,symbol,kind,false);}
    catch(error){if(!error?.retryable)throw error;return attempt(original,symbol,kind,true);}
  }
  return {async getIntradayFlow(symbol,kind){
    const item=resolveIntradayInstrument(symbol,kind),key=`${item.kind}:${item.symbol}`,stamp=now(),old=cache.get(key),requestDay=shanghaiDate(stamp);
    if(old&&stamp>=old.at&&stamp-old.at<TTL&&requestDay===old.requestDay)return withMarketFreshness({...clone(old.value),served_at:iso(stamp),cache_hit:true},stamp);
    // No cross-request Promise is retained: a canceled owner cannot strand a
    // later request or prevent the next Shanghai day's first source refresh.
    const requestId=(generations.get(key)??0)+1;generations.set(key,requestId);
    const request=(async()=>{
      try{
        const data=await load(item.symbol,item.kind),finished=now();
        const latest=cache.get(key),previous=latest?.value;
        if(previous?.bars?.length&&latest.requestId>requestId&&Date.parse(data.as_of)<=Date.parse(previous.as_of))return {...clone(previous),served_at:iso(finished),cache_hit:true};
        if(previous?.bars?.length&&Date.parse(data.as_of)<Date.parse(previous.as_of)){
          cache.set(key,{at:finished,requestDay,requestId,value:{...previous,status:'cached',source_status:'cached',stale:true,cache_hit:true,served_at:iso(finished),last_attempt_at:iso(finished),
            error:`资金源时间回退至 ${data.as_of}；保留已取得的较新记录 ${previous.as_of}`,
            upstream_attempts:data.upstream_attempts,upstream_host:data.upstream_host}});
        }else cache.set(key,{at:finished,requestDay,requestId,value:{...data,fetched_at:iso(finished),served_at:iso(finished),stale:false,cache_hit:false}});
      }catch(error){
        const finished=now(),time=iso(finished),message=error?.name==='AbortError'?'东方财富资金分时请求超时':String(error?.message??error).slice(0,180),latest=cache.get(key),previous=latest?.value??old?.value;
        if(latest?.requestId>requestId)return {...clone(latest.value),served_at:time,cache_hit:true};
        const value=previous?.bars?.length?{...previous,status:'cached',source_status:'cached',stale:true,cache_hit:true,served_at:time,last_attempt_at:time,error:message}:{symbol:item.symbol,kind:item.kind,name:item.name,source:INTRADAY_FLOW_SOURCE,scope:'instrument',unit:'CNY',status:'unavailable',bars:[],trade_date:null,as_of:null,fetched_at:null,served_at:time,last_attempt_at:time,stale:false,cache_hit:false,error:message,note:INTRADAY_FLOW_NOTE};
        cache.set(key,{at:finished,requestDay,requestId,value});
      }
      return clone(cache.get(key).value);
    })();
    return withMarketFreshness(await request,now());
  }};
}

const client=createIntradayFlowClient();
/** Shared Worker client; real calls and before/after data shape in docs/INTRADAY_FLOW.md. */
export const getIntradayFlow=(symbol,kind)=>client.getIntradayFlow(symbol,kind);
