/** Independent chart requests with a bounded, context-keyed last-good cache.
 * Real input, runnable calls, observed output and changes for each processing
 * step are documented in docs/PRICE_REFRESH.md and examples/price-refresh.mjs.
 */
import {withMarketFreshness} from './market-time.mjs';

const CHANNELS=['price','flow','benchmark'];
const finite=value=>typeof value==='number'&&Number.isFinite(value);
const copy=value=>structuredClone(value);

/** Accept usable observations only for the requested identity, when supplied.
 * Missing identity is allowed for the explicit demo adapter. Values stay intact.
 * Runnable identity/empty-response example: docs/PRICE_REFRESH.md, validation.
 */
function validSeries(payload,item,channel){
  if(!payload||typeof payload!=='object'||!Array.isArray(payload.bars))return false;
  const expected=channel==='benchmark'?{symbol:'000300',kind:'index'}:item;
  if(['symbol','kind'].some(key=>payload[key]!==undefined&&payload[key]!==expected[key]))return false;
  return payload.bars.some(row=>row&&(
    channel==='flow'?finite(row.main_net):finite(row.close)&&row.close>0
  ));
}

/** Compare original source timestamps, never fetch/serve times.
 * A bare provider timestamp is Shanghai time. Source rollback example: docs.
 */
function sourceEpoch(payload){
  const epoch=value=>{
    if(typeof value!=='string'||!/^\d{4}-\d{2}-\d{2}(?:[ T]|$)/.test(value))return null;
    let text=value.trim();
    if(/^\d{4}-\d{2}-\d{2}$/.test(text))text+='T00:00:00+08:00';
    else if(/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,3})?)?$/.test(text))text=text.replace(' ','T')+'+08:00';
    const result=Date.parse(text);return Number.isFinite(result)?result:null;
  };
  const explicit=epoch(payload?.as_of);
  if(explicit!==null)return explicit;
  const stamps=(payload?.bars??[]).map(row=>epoch(row?.time)).filter(value=>value!==null);
  return stamps.length?Math.max(...stamps):null;
}

/** Preserve genuine cached rows while making a failed or regressed refresh clear.
 * Original bars/as_of/fetched_at remain unchanged; only recovery metadata changes.
 * Runnable real-response failure example: examples/price-refresh.mjs.
 */
function retained(previous,reason,now){
  return withMarketFreshness({...copy(previous),status:'cached',source_status:'cached',
    stale:true,error:`${reason}；保留上次有效走势及原始源时间`,
    last_attempt_at:new Date(now).toISOString(),cache_hit:true},now);
}

/** Without valid prior observations, return an explicit empty state.
 * A mismatched response cannot leak another symbol's bars or source date.
 * Runnable validation example: docs/PRICE_REFRESH.md.
 */
function unavailable(item,channel,reason,now){
  const identity=channel==='benchmark'?{symbol:'000300',kind:'index'}:item;
  return {symbol:identity.symbol,kind:identity.kind,bars:[],status:'unavailable',
    source_status:'unavailable',stale:false,as_of:null,fetched_at:null,error:reason,
    last_attempt_at:new Date(now).toISOString()};
}

/** now: injectable millisecond clock. Caller key must include source, mode,
 * symbol, kind, period and current Shanghai date. At most 32 contexts are kept.
 * load() emits immediately and whenever one channel settles, then resolves.
 * Each real-input processing step is runnable in examples/price-refresh.mjs.
 */
export function createChartLoader({now=Date.now}={}){
  const cache=new Map();
  let generation=0;
  return {
    async load({key,item,requests,onUpdate,isCurrent=()=>true}){
      if(typeof key!=='string'||!key||!item||typeof requests?.price!=='function'||typeof onUpdate!=='function')throw new TypeError('价格加载需要上下文 key、item、price 请求和 onUpdate');
      if(!isCurrent())return;
      const entry=cache.get(key)??{},run=++generation;
      entry.generation=run;cache.delete(key);cache.set(key,entry);
      while(cache.size>32)cache.delete(cache.keys().next().value);
      const active=()=>cache.get(key)===entry&&entry.generation===run&&isCurrent();
      const values={},pending={};
      for(const channel of CHANNELS){
        const requested=typeof requests[channel]==='function';
        pending[channel]=requested;
        const identity=channel==='benchmark'?{symbol:'000300',kind:'index'}:item;
        values[channel]=!requested?null:validSeries(entry[channel],item,channel)
          ?withMarketFreshness(copy(entry[channel]),now())
          :{symbol:identity.symbol,kind:identity.kind,bars:[],status:'loading',source_status:'loading'};
      }
      const emit=()=>{if(active())onUpdate(copy({...values,pending}));};
      emit();
      await Promise.all(CHANNELS.filter(channel=>pending[channel]).map(async channel=>{
        let payload,failure;
        try{payload=await requests[channel]();}
        catch(error){failure=String(error?.message??error??'请求失败').slice(0,180);}
        if(!active())return;
        const previous=entry[channel],hasPrevious=validSeries(previous,item,channel);
        const valid=!failure&&validSeries(payload,item,channel);
        const oldTime=hasPrevious?sourceEpoch(previous):null,newTime=valid?sourceEpoch(payload):null;
        let next;
        if(valid&&oldTime!==null&&newTime!==null&&newTime<oldTime){
          next=retained(previous,'数据源返回了更早的走势',now());
        }else if(valid){
          next=withMarketFreshness(copy(payload),now());
        }else{
          const reason=failure||String(payload?.error??'本次未取得匹配标的的有效走势').slice(0,180);
          next=hasPrevious?retained(previous,reason,now()):unavailable(item,channel,reason,now());
        }
        if(!active())return;
        entry[channel]=copy(next);values[channel]=next;pending[channel]=false;emit();
      }));
    },
  };
}
