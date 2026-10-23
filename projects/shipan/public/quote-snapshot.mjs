import {shanghaiDate,withMarketFreshness} from './market-time.mjs';

const PRICE_KEYS=['price','previous_close','open','high','low','change_pct','amount','volume','as_of','fetched_at','source'];
const OBSERVED=new Set(['live','partial','cached','stale','delayed']);
const valid=row=>row&&typeof row.price==='number'&&Number.isFinite(row.price)&&row.price>0&&
  shanghaiDate(row.as_of)&&Number.isFinite(Date.parse(row.as_of))&&OBSERVED.has(row.status);
const identity=row=>`${row.kind}:${row.symbol}`;

/** Keep real quote fields by identity/source time within one provider and mode.
 * Incoming independently timed flow fields are retained. Old quotes receive
 * cached/stale labels, never a new source timestamp. Executable real-response
 * replay and before/after values: docs/DAILY_EVIDENCE.md. */
export function retainQuoteSnapshot(previous,incoming,now=Date.now()){
  if(incoming?.mode!=='real'||previous?.mode!=='real'||!Array.isArray(incoming?.instruments))return withMarketFreshness(incoming,now);
  const oldById=new Map((previous.instruments??[]).map(row=>[identity(row),row]));
  const instruments=incoming.instruments.map(item=>{
    const old=oldById.get(identity(item));
    if(valid(old)&&(!valid(item)||Date.parse(item.as_of)<Date.parse(old.as_of))){
      const kept=Object.fromEntries(PRICE_KEYS.map(key=>[key,old[key]??(['source','fetched_at'].includes(key)?previous[key]:null)]));
      return withMarketFreshness({...item,...kept,
        unit:{...item.unit,...Object.fromEntries(['price','amount','volume'].filter(key=>old.unit?.[key]!==undefined).map(key=>[key,old.unit[key]]))},
        status:'cached',source_status:'cached',stale:true,
        error:'本次未取得更新报价，保留原始价格与源时间'},now);
    }
    return withMarketFreshness({...item,source:item.source??incoming.source,fetched_at:item.fetched_at??incoming.fetched_at},now);
  });
  const usable=instruments.filter(valid);
  const allOld=usable.length>0&&usable.every(row=>row.stale===true||row.status==='cached'||row.status==='stale');
  const status=!usable.length?'unavailable':allOld?'cached':usable.length<instruments.length||usable.some(row=>row.status!=='live')?'partial':'live';
  const latest=key=>usable.map(row=>row[key]).filter(value=>Number.isFinite(Date.parse(value))).sort((a,b)=>Date.parse(a)-Date.parse(b)).at(-1)??null;
  return withMarketFreshness({...incoming,instruments,status,source_status:status,stale:allOld,
    as_of:latest('as_of'),fetched_at:latest('fetched_at'),
  },now);
}

/** Represent a failed snapshot request without erasing the last real quote or
 * relabeling its source date. Flow retention is applied separately by the caller.
 * Call/example/output: docs/DAILY_EVIDENCE.md and tests/quote-snapshot.test.mjs. */
export function failedSnapshot(previous,error,now=Date.now()){
  return retainQuoteSnapshot(previous,{...previous,status:'unavailable',source_status:'unavailable',
    as_of:null,fetched_at:null,flows:Array.isArray(previous?.flows)?[]:undefined,
    flow_status:'unavailable',flow_source_status:'unavailable',flow_error:String(error),flow_stale:true,
    instruments:(previous?.instruments??[]).map(row=>({...row,price:null,as_of:null,status:'unavailable',source_status:'unavailable',
      flow_status:typeof row.main_net==='number'&&Number.isFinite(row.main_net)?'cached':'unavailable',
      flow_source_status:typeof row.main_net==='number'&&Number.isFinite(row.main_net)?'cached':'unavailable',flow_stale:true})),
    errors:[{source:'刷新连接',error:String(error)}],
  },now);
}
