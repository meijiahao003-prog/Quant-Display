import {shanghaiDate,withMarketFreshness} from './market-time.mjs';

/** Keep each ETF's last genuine observation when a successful HTTP response has
 * missing/older flow data. Call only within the same source/mode context.
 * Purpose, actual recorded input, runnable calls/output and changes:
 * docs/ETF_FLOW_PANEL.md. No amounts or source timestamps are interpolated.
 */
export function retainFlowSnapshot(previous,incoming,now=Date.now()){
  if(incoming?.mode==='demo'||previous?.mode==='demo'||!Array.isArray(incoming?.flows))return incoming;
  const observed=new Set(['live','partial','cached','stale','recorded','delayed']);
  const valid=row=>row?.unit==='CNY'&&typeof row.main_net==='number'&&Number.isFinite(row.main_net)
    &&Number.isFinite(Date.parse(row.as_of))&&shanghaiDate(row.as_of)
    &&(!row.trade_date||row.trade_date===shanghaiDate(row.as_of))&&observed.has(row.status);
  const incomingRows=Array.isArray(incoming.flows)?incoming.flows:[];
  const oldRows=new Map((previous?.flows??[]).map(row=>[row.symbol,row]));
  const nextRows=new Map(incomingRows.map(row=>[row.symbol,row]));
  const instruments=incoming.instruments??[];
  const flows=instruments.filter(item=>item.kind==='etf').map(item=>{
    const value=nextRows.get(item.symbol),oldValue=oldRows.get(item.symbol);
    const next=value?{...value,source:value.source??incoming.flow_source,fetched_at:value.fetched_at??incoming.flow_fetched_at}:null;
    const old=oldValue?{...oldValue,source:oldValue.source??previous.flow_source,fetched_at:oldValue.fetched_at??previous.flow_fetched_at}:null;
    if(valid(old)&&(!valid(next)||Date.parse(next.as_of)<Date.parse(old.as_of))){
      return withMarketFreshness({...old,status:'cached',source_status:'cached',stale:true,
        error:next?.error??incoming.flow_error??'本次未取得更新的资金数据，保留上次实际记录'},now);
    }
    return withMarketFreshness(next??{symbol:item.symbol,name:item.name,unit:'CNY',status:'unavailable',main_net:null,as_of:null,fetched_at:null},now);
  });
  const metadata=flowPanelState({...incoming,flows},null,'ranking',now);
  const bySymbol=new Map(flows.map(row=>[row.symbol,row]));
  return withMarketFreshness({...incoming,flows,
    flow_status:metadata.status,flow_source_status:metadata.status,flow_stale:metadata.status==='cached'||metadata.status==='stale',
    flow_as_of:metadata.as_of,flow_fetched_at:metadata.fetched_at,flow_source:metadata.source??incoming.flow_source,
    instruments:instruments.map(item=>{
      const row=bySymbol.get(item.symbol);if(item.kind!=='etf'||!valid(row))return item;
      const mismatch=shanghaiDate(item.as_of)&&shanghaiDate(item.as_of)!==shanghaiDate(row.as_of);
      return {...item,main_net:mismatch?null:row.main_net,main_pct:mismatch?null:row.main_pct??null,
        flow_status:mismatch?'unavailable':row.status,flow_source_status:mismatch?'unavailable':row.source_status??row.status,
        flow_as_of:row.as_of,flow_fetched_at:row.fetched_at,flow_source:row.source,flow_stale:Boolean(mismatch)||row.stale===true,
        flow_error:mismatch?`资金日期 ${shanghaiDate(row.as_of)} 与价格日期 ${shanghaiDate(item.as_of)} 不一致`:row.error??null,
        unit:{...item.unit,main_net:mismatch?'unavailable':'CNY'}};
    }),
  },now);
}

/** Select one source day for ranking; structure keeps the chosen ETF's own time
 * and provenance. Mixed cached/live rows are labeled partial, never all live.
 * Executed actual-record examples and before/after shapes: docs/ETF_FLOW_PANEL.md.
 */
export function flowPanelState(snapshot,symbol,mode='ranking',now=Date.now()){
  const all=(snapshot?.flows??[]).map(row=>withMarketFreshness(row,now));
  const valid=all.filter(row=>typeof row.main_net==='number'&&Number.isFinite(row.main_net)&&row.unit==='CNY'&&shanghaiDate(row.as_of));
  const date=valid.map(row=>shanghaiDate(row.as_of)).sort().at(-1)??null;
  const rows=valid.filter(row=>shanghaiDate(row.as_of)===date),selected=all.find(row=>row.symbol===symbol);
  const displayed=mode==='structure'?(selected?[selected]:[]):rows;
  const status=mode==='structure'?selected?.status??'unavailable':!rows.length?(snapshot?.flow_status==='loading'?'loading':'unavailable'):
    rows.every(row=>row.status==='stale')?'stale':rows.every(row=>row.status==='cached'||row.stale===true)?'cached':
    rows.length<all.length||rows.some(row=>row.status!=='live'||row.stale===true)?'partial':'live';
  const sources=[...new Set(displayed.map(row=>row.source??snapshot?.flow_source).filter(Boolean))];
  return {rows,selected,date,excluded:valid.length-rows.length,status,
    as_of:displayed.map(row=>row.as_of).filter(Boolean).sort().at(-1)??null,
    fetched_at:displayed.map(row=>row.fetched_at??snapshot?.flow_fetched_at).filter(Boolean).sort().at(-1)??null,
    source:sources.length===1?sources[0]:sources.length?'东方财富 ETF 资金 · 快照 / 分时记录':snapshot?.flow_source??null,
    cached_count:displayed.filter(row=>row.status==='cached'||row.stale===true).length,
  };
}
