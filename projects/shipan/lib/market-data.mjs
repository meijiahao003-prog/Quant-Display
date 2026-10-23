import {shanghaiDate,withMarketFreshness} from '../public/market-time.mjs';
/** Tencent public market data. No credentials, evaluation of remote JS, or invented prices.
 * Runnable real-input examples and observed output: docs/TENCENT_DATA.md.
 */
export const UNIVERSE = Object.freeze([
  ['000001','上证指数','index','sh'],['399001','深证成指','index','sz'],
  ['399006','创业板指','index','sz'],['000300','沪深300','index','sh'],
  ['000905','中证500','index','sh'],['000688','科创50','index','sh'],
  ['510300','沪深300ETF','etf','sh'],['510500','中证500ETF','etf','sh'],
  ['159915','创业板ETF','etf','sz'],['588000','科创50ETF','etf','sh'],
  ['512480','半导体ETF','etf','sh'],['512660','军工ETF','etf','sh'],
  ['518880','黄金ETF','etf','sh'],['513100','纳指ETF','etf','sh'],
  ['510050','上证50ETF','etf','sh'],['512010','医药ETF','etf','sh'],
].map(([symbol,name,kind,exchange])=>Object.freeze({symbol,name,kind,exchange,code:exchange+symbol})));

const SOURCE='腾讯财经公开行情 · 免费源，延迟未承诺';
const FLOW_NOTE='腾讯报价不包含主力资金净流入；不使用成交额、委差或买卖盘量替代。';
const TTL=60_000;
const MAX_BYTES=2_000_000;
const n=value=>value===null||value===undefined||String(value).trim()===''||!Number.isFinite(Number(value))?null:Number(value);
const positive=value=>{const parsed=n(value);return parsed!==null&&parsed>0?parsed:null;};
const nonnegative=value=>{const parsed=n(value);return parsed!==null&&parsed>=0?parsed:null;};
const clone=value=>JSON.parse(JSON.stringify(value));
const iso=value=>new Date(value).toISOString();
const dateOf=value=>{
  const s=String(value??'').replaceAll('-','');
  if(!/^\d{8}$/.test(s))return null;
  const date=`${s.slice(0,4)}-${s.slice(4,6)}-${s.slice(6,8)}`;
  const parsed=new Date(date+'T00:00:00Z');
  return Number.isFinite(parsed.getTime())&&parsed.toISOString().slice(0,10)===date?date:null;
};

/** Validate the provider's timestamp without inventing a date. See docs: parseQuoteText example. */
export function parseSourceTime(value){
  const s=String(value??'');const date=dateOf(s.slice(0,8));
  if(!date||!/^\d{14}$/.test(s)||Number(s.slice(8,10))>23||Number(s.slice(10,12))>59||Number(s.slice(12,14))>59)return null;
  return `${date}T${s.slice(8,10)}:${s.slice(10,12)}:${s.slice(12,14)}+08:00`;
}

function emptyRow(item){return {...item,price:null,previous_close:null,open:null,high:null,low:null,change_pct:null,amount:null,volume:null,main_net:null,main_pct:null,as_of:null,status:'unavailable',source:SOURCE,unit:{price:item.kind==='index'?'index_points':'CNY',amount:'CNY',volume:'source_raw_unverified',main_net:'unavailable'}};}

/** Parse bounded Tencent assignment text; only expected identifiers are accepted.
 * Purpose/input/call/output/change example: docs/TENCENT_DATA.md §1.
 */
export function parseQuoteText(text){
  if(typeof text!=='string'||text.length>MAX_BYTES)throw new Error('报价响应过大或格式无效');
  const records=new Map();
  for(const match of text.matchAll(/(?:^|[;\r\n])\s*v_((?:sh|sz)\d{6})="([^"\r\n]*)"/g)){
    const fields=match[2].split('~');
    if(fields.length>=38)records.set(match[1],fields);
  }
  return UNIVERSE.map(item=>{
    const row=emptyRow(item),f=records.get(item.code);
    if(!f||f[2]!==item.symbol)return row;
    row.name=f[1]?.trim()||item.name;
    row.price=positive(f[3]);row.previous_close=positive(f[4]);row.open=positive(f[5]);
    row.high=positive(f[33]);row.low=positive(f[34]);row.change_pct=n(f[32]);
    row.amount=nonnegative(f[37])===null?null:Number(f[37])*10_000;
    row.volume=nonnegative(f[6]);row.as_of=parseSourceTime(f[30]);
    row.status=row.price===null?'unavailable':row.as_of?'live':'partial';
    return row;
  });
}

/** Read Tencent JSON (no JSONP/eval); keep intraday point fields distinct from OHLC.
 * Real input/call/output/change examples: docs/TENCENT_DATA.md §2 and §3.
 */
export function parseHistory(payload,item,period){
  if(!payload||payload.code!==0)throw new Error('腾讯走势返回错误状态');
  const entry=payload.data?.[item.code];
  if(!entry)throw new Error('腾讯走势没有此标的数据');
  let bars=[];
  if(period==='1m'){
    const date=dateOf(entry.data?.date);
    if(!date||!Array.isArray(entry.data?.data))throw new Error('分时日期或记录格式无效');
    for(const raw of entry.data.data.slice(0,1000)){
      if(typeof raw!=='string')continue;
      const fields=raw.trim().split(/\s+/),clock=fields[0],close=positive(fields[1]);
      if(!/^\d{4}$/.test(clock)||Number(clock.slice(0,2))>23||Number(clock.slice(2))>59||close===null)continue;
      bars.push({time:`${date} ${clock.slice(0,2)}:${clock.slice(2)}`,open:null,close,high:null,low:null,volume:null,amount:null,cumulative_volume:nonnegative(fields[2]),cumulative_amount:nonnegative(fields[3])});
    }
  }else{
    // Empty adjustment parameter requests unadjusted bars; never silently consume qfqday.
    if(!Array.isArray(entry.day))throw new Error('未返回未复权日线');
    for(const raw of entry.day.slice(-320)){
      if(!Array.isArray(raw))continue;
      const time=dateOf(raw[0]),open=positive(raw[1]),close=positive(raw[2]),high=positive(raw[3]),low=positive(raw[4]);
      if(!time||[open,close,high,low].some(v=>v===null)||high<Math.max(open,close)||low>Math.min(open,close)||low>high)continue;
      bars.push({time,open,close,high,low,volume:nonnegative(raw[5]),amount:nonnegative(raw[8])===null?null:Number(raw[8])*10_000});
    }
  }
  bars=[...new Map(bars.map(bar=>[bar.time,bar])).values()].sort((a,b)=>a.time.localeCompare(b.time));
  if(!bars.length)throw new Error('暂无有效走势记录');
  return {bars,source:SOURCE,status:'live',symbol:item.symbol,kind:item.kind,period,as_of:period==='1m'?bars.at(-1).time.replace(' ','T')+':00+08:00':bars.at(-1).time,adjustment:'none',unit:{price:item.kind==='index'?'index_points':'CNY',volume:'source_raw_unverified',amount:'CNY',cumulative_volume:'source_raw_unverified',cumulative_amount:'source_raw_unverified'},note:period==='1m'?'分时价格采样点，不是分钟 OHLC；累计量额保留原始字段及未核实单位，分钟成交量不推算。':'未复权日线；最后一根可能尚未收盘。'};
}

/** Health reports capability, not provider reachability. Run §4 live example in docs. */
export function getHealth(){return {status:'ok',service:'market-lens-tencent',provider:'tencent',refresh_seconds:60,capabilities:{quotes:true,history:true,training:false,samples:false,ollama:false,server_analysis:false,main_flow:false},limitations:[FLOW_NOTE,'免费公开源无延迟或可用性保证；内存缓存可随服务实例回收。']};}

/** Client factory allows deterministic failure tests; ordinary callers use exported functions.
 * Parameters: fetchImpl HTTP transport, now millisecond clock, timeoutMs total
 * fetch+body deadline (default 12s). Real-input examples: docs/TENCENT_DATA.md;
 * executable failure cases: node --test tests/market-data.test.mjs.
 */
export function createMarketDataClient({fetchImpl=globalThis.fetch,now=Date.now,timeoutMs=12_000}={}){
  // Only settled values survive requests. A Worker may cancel the request owning
  // an in-flight Promise; another request must never await that orphaned work.
  // Reproduction and rollover examples: docs/SOURCE_REQUEST_LIFETIME.md.
  const cache=new Map(),generations=new Map();
  async function read(url,encoding='utf-8'){
    const controller=new AbortController();let timer;
    const deadline=new Promise((_,reject)=>{
      timer=setTimeout(()=>{
        controller.abort();
        reject(new DOMException('腾讯源请求超时','AbortError'));
      },timeoutMs);
    });
    const receive=async()=>{
      const response=await fetchImpl(url,{signal:controller.signal,headers:{Accept:'*/*'},redirect:'manual'});
      if(!response.ok)throw new Error(`腾讯源 HTTP ${response.status}`);
      const length=Number(response.headers.get('content-length'));
      if(length>MAX_BYTES)throw new Error('腾讯响应超过长度限制');
      const bytes=await response.arrayBuffer();
      if(bytes.byteLength>MAX_BYTES)throw new Error('腾讯响应超过长度限制');
      return new TextDecoder(encoding).decode(bytes);
    };
    try{return await Promise.race([receive(),deadline]);}finally{clearTimeout(timer);}
  }
  async function cached(key,load,unavailable){
    const stamp=now(),existing=cache.get(key),requestDay=shanghaiDate(stamp);
    if(existing&&stamp>=existing.at&&stamp-existing.at<TTL&&requestDay===existing.requestDay)return withMarketFreshness({...clone(existing.value),served_at:iso(stamp),cache_hit:true},stamp);
    const requestId=(generations.get(key)??0)+1;generations.set(key,requestId);
    const task=(async()=>{
      try{
        const result=await load();const finished=now(),latest=cache.get(key),previous=latest?.value;
        if(previous&&latest.requestId>requestId&&Date.parse(result.as_of)<=Date.parse(previous.as_of))return {...clone(previous),served_at:iso(finished),cache_hit:true};
        if(previous&&Date.parse(result.as_of)<Date.parse(previous.as_of))throw new Error(`腾讯源时间回退至 ${result.as_of}；保留较新记录 ${previous.as_of}`);
        if(result.instruments&&previous?.instruments){
          const previousRows=new Map(previous.instruments.map(row=>[row.code,row]));let retained=0;
          result.instruments=result.instruments.map(row=>{
            const old=previousRows.get(row.code);
            if(old?.price!==null&&Number.isFinite(Date.parse(old?.as_of))&&(row.price===null||!row.as_of||Date.parse(row.as_of)<Date.parse(old.as_of))){
              retained++;return {...old,status:'cached',source_status:'cached',stale:true,fetched_at:old.fetched_at??previous.fetched_at};
            }
            return row;
          });
          if(retained){result.status='partial';result.as_of=result.instruments.map(row=>row.as_of).filter(Boolean).sort().at(-1)??result.as_of;}
        }
        const value={...result,fetched_at:iso(finished),served_at:iso(finished),cache_hit:false,stale:false};
        cache.set(key,{at:finished,requestDay,requestId,value});return clone(value);
      }catch(error){
        const failed=now(),message=error.name==='AbortError'?'腾讯源请求超时':String(error.message).slice(0,180),latest=cache.get(key)??existing;
        if(latest?.requestId>requestId)return {...clone(latest.value),served_at:iso(failed),cache_hit:true};
        if(latest&&latest.value.status!=='unavailable'){
          const value={...clone(latest.value),status:'cached',source_status:'cached',stale:true,served_at:iso(failed),last_attempt_at:iso(failed),error:message,errors:[message],cache_hit:true};
          if(value.instruments)value.instruments=value.instruments.map(row=>row.price===null?row:{...row,status:'cached',source_status:'cached',stale:true});
          // Back off failed upstream calls for the same TTL, preserving original fetched_at.
          cache.set(key,{at:failed,requestDay,requestId,value});return clone(value);
        }
        const value={...unavailable(),status:'unavailable',as_of:null,fetched_at:null,served_at:iso(failed),last_attempt_at:iso(failed),error:message,errors:[message],stale:false,cache_hit:false};
        cache.set(key,{at:failed,requestDay,requestId,value});return clone(value);
      }
    })();return withMarketFreshness(await task,now());
  }
  return {
    async getSnapshot(){
      return cached('snapshot',async()=>{
        const instruments=parseQuoteText(await read('https://qt.gtimg.cn/q='+UNIVERSE.map(x=>x.code).join(','),'gb18030'));
        const valid=instruments.filter(x=>x.price!==null);
        if(!valid.length)throw new Error('腾讯未返回有效报价');
        const missing=instruments.filter(x=>x.price===null||!x.as_of);
        return {mode:'real',status:missing.length?'partial':'live',source:SOURCE,as_of:valid.map(x=>x.as_of).filter(Boolean).sort().at(-1)??null,instruments,market_flow:[],flow_status:'unavailable',flow_note:FLOW_NOTE,errors:missing.map(x=>`${x.code} 报价或源时间缺失`)};
      },()=>({mode:'real',source:SOURCE,instruments:UNIVERSE.map(emptyRow),market_flow:[],flow_status:'unavailable',flow_note:FLOW_NOTE}));
    },
    async getHistory(symbol,kind,period){
      const item=UNIVERSE.find(x=>x.symbol===symbol&&x.kind===kind);
      if(!item||!['1m','day'].includes(period)){const error=new Error('不支持的标的、品种或周期');error.status=400;throw error;}
      const url=period==='1m'?`https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=${item.code}`:`https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?param=${item.code},day,,,240,`;
      return cached(`history:${item.code}:${period}`,async()=>parseHistory(JSON.parse(await read(url)),item,period),()=>({bars:[],source:SOURCE,symbol,kind,period}));
    },
  };
}
const client=createMarketDataClient();
/** Fetch the fixed 16-asset live snapshot. Runnable example and observed output: docs §4. */
export const getSnapshot=()=>client.getSnapshot();
/** Fetch an allowed asset's actual minute points or unadjusted daily bars. Runnable example: docs §4. */
export const getHistory=(symbol,kind,period)=>client.getHistory(symbol,kind,period);
