/** Shanghai source-day metadata; never rewrite provider observations.
 * Purpose/input/call/output examples: docs/DAILY_REFRESH_BACKEND.md.
 */
const SHANGHAI_OFFSET=8*3_600_000;
const OBSERVED_STATUSES=new Set(['live','partial','cached','recorded','delayed','stale']);

/** Return YYYY-MM-DD in Asia/Shanghai, or null for an invalid value.
 * Date-only source values already express a trading date and keep that date.
 */
export function shanghaiDate(value){
  if(arguments.length===0)value=Date.now();
  if(value===null||value===undefined||value==='')return null;
  if(!['number','string'].includes(typeof value)&&!(value instanceof Date))return null;
  if(typeof value==='string'){
    const text=value.trim(),match=/^(\d{4}-\d{2}-\d{2})(?:[ T]|$)/.exec(text);
    if(match){
      const date=new Date(match[1]+'T00:00:00Z');
      if(!Number.isFinite(date.getTime())||date.toISOString().slice(0,10)!==match[1])return null;
      if(text===match[1])return match[1];
    }
    const clock=/[ T](\d{2}):(\d{2})(?::(\d{2}))?/.exec(text);
    if(clock&&(Number(clock[1])>23||Number(clock[2])>59||Number(clock[3]??0)>59))return null;
    // Provider bare datetimes are exchange-local, never the server's timezone.
    if(/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,3})?)?$/.test(text))value=text.replace(' ','T')+'+08:00';
  }
  const epoch=value instanceof Date?value.getTime():typeof value==='number'?value:Date.parse(value);
  if(!Number.isFinite(epoch))return null;
  const shifted=new Date(epoch+SHANGHAI_OFFSET);
  return Number.isFinite(shifted.getTime())?shifted.toISOString().slice(0,10):null;
}

/** Add current/source-day labels without changing prices, bars, or source times.
 * Successfully obtained earlier-day observations stay visible with status=stale;
 * this says nothing about exchange holidays or whether the source has failed.
 * Reapply at every response, including cache hits, to reflect midnight rollover.
 */
export function withMarketFreshness(payload,now=Date.now()){
  if(!payload||typeof payload!=='object'||Array.isArray(payload))return payload;
  const today=shanghaiDate(now),sourceDate=shanghaiDate(payload.as_of??null)??shanghaiDate(payload.trade_date??null);
  const freshness=!today||!sourceDate?'unknown':sourceDate===today?'current_day':sourceDate<today?'previous_day':'future_day';
  const sourceStatus=payload.source_status??payload.status;
  const observed=payload.mode!=='demo'&&sourceStatus!=='demo'&&OBSERVED_STATUSES.has(sourceStatus)&&OBSERVED_STATUSES.has(payload.status);
  const staleDay=observed&&['previous_day','future_day'].includes(freshness);
  const result={...payload,today,source_date:sourceDate,freshness,source_status:sourceStatus,
    ...(staleDay?{status:'stale',stale:true}:{})};
  for(const key of ['instruments','rows','flows'])if(Array.isArray(payload[key]))result[key]=payload[key].map(row=>withMarketFreshness(row,now));
  // Joined quote payloads carry fund freshness independently from price freshness.
  if(Object.hasOwn(payload,'flow_as_of')||Object.hasOwn(payload,'flow_status')){
    const flow=withMarketFreshness({mode:payload.mode,status:payload.flow_status,source_status:payload.flow_source_status,as_of:payload.flow_as_of,stale:payload.flow_stale},now);
    Object.assign(result,{flow_today:flow.today,flow_source_date:flow.source_date,flow_freshness:flow.freshness,
      flow_source_status:flow.source_status,flow_status:flow.status,flow_stale:flow.stale});
  }
  return result;
}
