import {UNIVERSE} from './market-data.mjs';
import {withMarketFreshness} from '../public/market-time.mjs';

const observed=new Set(['live','partial','cached','stale']);
const finite=value=>typeof value==='number'&&Number.isFinite(value);
const stamp=value=>typeof value==='string'&&Number.isFinite(Date.parse(value))?Date.parse(value):NaN;
const clone=value=>JSON.parse(JSON.stringify(value));

/** A newly acquired genuine source response may legitimately describe an older
 * trading day. Persist that observation, never a cache read or future-day value.
 * Old source time still cannot beat newer stored time in the atomic DB upsert.
 * Regression input/output: docs/DURABLE_MARKET.md. */
export function isPersistablePriceObservation(payload){
  return Boolean(payload&&['live','partial'].includes(payload.source_status??payload.status)&&
    ['live','partial','stale'].includes(payload.status)&&payload.cache_hit!==true&&
    (!payload.stale||payload.status==='stale'&&payload.freshness==='previous_day')&&
    Number.isFinite(stamp(payload.as_of))&&Number.isFinite(stamp(payload.fetched_at)));
}

/** A stored fallback keeps the observation and acquisition times intact.
 * Real-input replay and output: docs/DURABLE_MARKET.md. */
export function asStoredMarket(payload,now=Date.now(),error=null){
  if(!payload)return null;
  return withMarketFreshness({...clone(payload),status:'cached',source_status:'cached',stale:true,
    cache_hit:true,served_at:new Date(now).toISOString(),...(error?{error}:{})},now);
}

/** Validate a timestamped price quote before comparing complete observations.
 * Called by the persisted snapshot merger; examples: docs/DURABLE_MARKET.md. */
export function validQuote(row,item){
  return Boolean(row&&row.symbol===item.symbol&&row.kind===item.kind&&finite(row.price)&&row.price>0&&
    observed.has(row.status)&&Number.isFinite(stamp(row.as_of)));
}

/** Retain each instrument's newest whole quote, including when a partial
 * upstream response omits another instrument. No fields cross source times.
 * Inputs, executable cases and before/after: docs/DURABLE_MARKET.md. */
export function selectDurableSnapshot(upstream,stored,now=Date.now()){
  const instruments=UNIVERSE.map(item=>{
    const current=upstream?.instruments?.find(row=>row.symbol===item.symbol&&row.kind===item.kind);
    const previous=stored?.instruments?.find(row=>row.symbol===item.symbol&&row.kind===item.kind);
    const currentValid=validQuote(current,item),previousValid=validQuote(previous,item);
    if(previousValid&&(!currentValid||stamp(previous.as_of)>stamp(current.as_of)))return asStoredMarket(previous,now,upstream?.error??'本次报价缺失或源时间早于已保存记录');
    if(currentValid)return withMarketFreshness({...clone(current),fetched_at:current.fetched_at??upstream.fetched_at},now);
    return {...item,price:null,previous_close:null,open:null,high:null,low:null,change_pct:null,amount:null,volume:null,
      main_net:null,main_pct:null,as_of:null,fetched_at:null,status:'unavailable',source_status:'unavailable',
      unit:{price:item.kind==='index'?'index_points':'CNY',amount:'CNY',volume:'source_raw_unverified',main_net:'unavailable'}};
  });
  const valid=instruments.filter((row,index)=>validQuote(row,UNIVERSE[index]));
  const fresh=valid.filter(row=>!row.stale&&!['cached','stale'].includes(row.status));
  const status=!valid.length?'unavailable':!fresh.length?'cached':valid.length===UNIVERSE.length&&fresh.length===valid.length?'live':'partial';
  const sourceTimes=valid.map(row=>row.as_of).sort((a,b)=>stamp(a)-stamp(b));
  const fetchTimes=valid.map(row=>row.fetched_at).filter(value=>Number.isFinite(stamp(value))).sort((a,b)=>stamp(a)-stamp(b));
  return withMarketFreshness({...stored,...upstream,mode:'real',instruments,status,source_status:status,
    as_of:sourceTimes.at(-1)??null,fetched_at:fetchTimes.at(-1)??null,served_at:new Date(now).toISOString(),
    stale:valid.length>0&&!fresh.length,cache_hit:valid.some(row=>row.cache_hit||row.stale),
    market_flow:[],flow_status:'unavailable',flow_note:upstream?.flow_note??stored?.flow_note??'价格源不提供主力资金。'},now);
}

/** Choose the newer entire price series for the exact symbol/kind/period.
 * Equal source dates use fetched_at, needed for the still-forming daily candle.
 * Real inputs and regression cases: docs/DURABLE_MARKET.md. */
export function selectDurableHistory(upstream,stored,now=Date.now()){
  const usable=payload=>payload&&Array.isArray(payload.bars)&&payload.bars.length>0&&
    Number.isFinite(stamp(payload.as_of))&&observed.has(payload.status)&&payload.bars.some(bar=>finite(bar.close)&&bar.close>0);
  const matching=usable(stored)&&stored.symbol===upstream?.symbol&&stored.kind===upstream?.kind&&stored.period===upstream?.period;
  if(matching&&(!usable(upstream)||stamp(stored.as_of)>stamp(upstream.as_of)||
    stamp(stored.as_of)===stamp(upstream.as_of)&&stamp(stored.fetched_at)>stamp(upstream.fetched_at))){
    return asStoredMarket(stored,now,upstream?.error??'本次走势源时间早于已保存记录');
  }
  return withMarketFreshness(upstream,now);
}
