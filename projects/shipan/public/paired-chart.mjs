/** Align observed prices and instrument net flow to Shanghai trading minutes.
 * Runnable real-response examples, actual output, and each processing step:
 * docs/PAIRED_CHART.md. This module does not fetch, invent, or fill market data.
 */
const MINUTES=[...Array.from({length:121},(_,i)=>570+i),...Array.from({length:121},(_,i)=>780+i)];
const LABELS=MINUTES.map(minute=>`${String(Math.floor(minute/60)).padStart(2,'0')}:${String(minute%60).padStart(2,'0')}`);
const MINUTE_INDEX=new Map(MINUTES.map((minute,index)=>[minute,index]));
const FLOW_KEYS=['main_net','super_net','large_net'];
const finite=value=>typeof value==='number'&&Number.isFinite(value);

/** Strict timestamps: bare date/time is Shanghai; explicit offsets are respected.
 * Demo clock-only inputs are accepted only with demo=true and the fixed demo date.
 */
function parseTime(value,demo=false){
  if(typeof value!=='string')return null;
  let text=value.trim();
  if(demo&&/^\d{2}:\d{2}(?::\d{2})?$/.test(text))text='2026-10-20T'+text;
  const match=/^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,3}))?)?(Z|[+-]\d{2}:\d{2})?)?$/.exec(text);
  if(!match)return null;
  const year=Number(match[1]),month=Number(match[2]),day=Number(match[3]),hour=Number(match[4]??0),minute=Number(match[5]??0),second=Number(match[6]??0),millisecond=Number((match[7]??'').padEnd(3,'0'));
  if(year<1970||year>2100||month<1||month>12||day<1||hour>23||minute>59||second>59)return null;
  const local=Date.UTC(year,month-1,day,hour,minute,second,millisecond),check=new Date(local);
  if(check.getUTCFullYear()!==year||check.getUTCMonth()!==month-1||check.getUTCDate()!==day)return null;
  let offset=480;
  const zone=match[8];
  if(zone==='Z')offset=0;
  else if(zone){
    const zoneHour=Number(zone.slice(1,3)),zoneMinute=Number(zone.slice(4,6));
    if(zoneHour>14||zoneMinute>59||(zoneHour===14&&zoneMinute!==0))return null;
    offset=(zone[0]==='-'?-1:1)*(zoneHour*60+zoneMinute);
  }
  const epoch=local-offset*60_000,shanghai=new Date(epoch+480*60_000),iso=shanghai.toISOString();
  return {epoch,date:iso.slice(0,10),minute:shanghai.getUTCHours()*60+shanghai.getUTCMinutes(),source_time:iso.slice(0,23)+'+08:00'};
}

/** Build equal-length chart arrays without conflating observation and fetch times.
 * priceBars: observed {time,close,...}; flowHistory: {symbol,unit,bars,...};
 * item: current {symbol,kind}; priceAsOf: source date fallback, never fetch time;
 * demo: explicitly enable the fixed clock-only demo input format.
 */
export function buildPairedData({priceBars=[],flowHistory=null,item={},priceAsOf=null,demo=false}={}){
  const pricePoints=(Array.isArray(priceBars)?priceBars:[]).flatMap(row=>{
    if(!row||!finite(row.close)||row.close<=0)return [];
    const stamp=parseTime(row.time,demo);
    return stamp&&MINUTE_INDEX.has(stamp.minute)?[{row,stamp}]:[];
  });
  const supported=['etf','index'].includes(item.kind),matching=supported&&flowHistory?.symbol===item.symbol&&(flowHistory?.kind===item.kind||(item.kind==='etf'&&flowHistory?.kind===undefined)),unitValid=matching&&flowHistory?.unit==='CNY';
  const flowPoints=(unitValid&&Array.isArray(flowHistory?.bars)?flowHistory.bars:[]).flatMap(row=>{
    if(!row||!finite(row.main_net))return [];
    const stamp=parseTime(row.time,demo);
    return stamp&&MINUTE_INDEX.has(stamp.minute)?[{row,stamp}]:[];
  });
  const latestPrice=pricePoints.reduce((latest,point)=>!latest||point.stamp.epoch>latest.stamp.epoch?point:latest,null);
  const latestFlow=flowPoints.reduce((latest,point)=>!latest||point.stamp.epoch>latest.stamp.epoch?point:latest,null);
  const date=latestPrice?.stamp.date??parseTime(priceAsOf,demo)?.date??latestFlow?.stamp.date??null;
  const priceObservations=Array(LABELS.length).fill(null),flowObservations=Array(LABELS.length).fill(null);
  for(const point of pricePoints){
    if(point.stamp.date!==date)continue;
    const index=MINUTE_INDEX.get(point.stamp.minute),old=priceObservations[index];
    if(!old||point.stamp.epoch>old.source_epoch)priceObservations[index]={...point.row,source_time:point.stamp.source_time,source_epoch:point.stamp.epoch};
  }
  for(const point of flowPoints){
    if(point.stamp.date!==date)continue;
    const index=MINUTE_INDEX.get(point.stamp.minute),old=flowObservations[index];
    if(!old||point.stamp.epoch>old.source_epoch)flowObservations[index]={...point.row,source_time:point.stamp.source_time,source_epoch:point.stamp.epoch};
  }
  const prices=priceObservations.map(row=>row?.close??null);
  const flows=Object.fromEntries(FLOW_KEYS.map(key=>[key,flowObservations.map(row=>row&&finite(row[key])?row[key]:null)]));
  const priceCount=priceObservations.filter(Boolean).length,flowCount=flowObservations.filter(Boolean).length;
  let flowMessage;
  if(!supported)flowMessage='当前标的类型不支持资金分时。';
  else if(!flowHistory)flowMessage='资金分时尚未取得；等待数据源返回当日分钟序列。';
  else if(!matching)flowMessage='资金历史标的与当前标的不一致，未绘制。';
  else if(!unitValid)flowMessage=flowHistory.status==='unavailable'?'资金历史暂不可用；价格走势可独立查看。':'资金金额单位未核实，未绘制。';
  else if(flowCount)flowMessage=`按北京时间的分钟对齐，保留各自源时间；${demo?'合成示例已明确标记':'资金源缺失分钟留空，未补值'}。`;
  else if(latestFlow&&latestFlow.stamp.date!==date)flowMessage=`尚无 ${date} 的资金分钟记录；最新资金日期为 ${latestFlow.stamp.date}，未跨日拼接。`;
  else flowMessage=flowHistory.status==='unavailable'?'资金历史暂不可用；价格走势可独立查看。':'暂无同日资金分钟数据；不会补造缺失走势。';
  const flowDate=latestFlow?.stamp.date??parseTime(flowHistory?.as_of,demo)?.date??null;
  return {date,flowDate,dateMismatch:Boolean(date&&flowDate&&date!==flowDate),labels:[...LABELS],prices,flows,priceObservations,flowObservations,priceCount,flowCount,flowMessage,flowUnit:unitValid?'CNY':null};
}

/** Group nearby actual observations without inserting values in missing minutes.
 * Each result keeps the original axis length. A chart may connect null slots within
 * one segment; gaps longer than maxGapMs (including lunch) start another segment.
 * Actual-input executable example: docs/PAIRED_CHART.md, splitObservedSegments.
 */
export function splitObservedSegments(values,observations,maxGapMs=180_000){
  if(!Array.isArray(values)||!Array.isArray(observations))return [];
  if(!finite(maxGapMs)||maxGapMs<0)throw new TypeError('maxGapMs 必须为非负有限毫秒数');
  const segments=[];
  let segment=null,lastEpoch=null;
  for(let index=0;index<values.length;index++){
    const value=values[index],epoch=observations[index]?.source_epoch;
    if(!finite(value)||!finite(epoch))continue;
    if(!segment||epoch-lastEpoch>maxGapMs||epoch<lastEpoch){
      segment=Array(values.length).fill(null);segments.push(segment);
    }
    segment[index]=value;lastEpoch=epoch;
  }
  return segments;
}
