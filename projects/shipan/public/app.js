/* Market Lens — UI controller. Demo fixtures are intentionally synthetic.
 * Data contracts and real-data runnable examples: backend/EXAMPLES.md.
 * No credentials or model keys are embedded in this frontend. */
'use strict';
import {buildPairedData,splitObservedSegments} from './paired-chart.mjs';
import {buildCouncilBundle,newCouncilId} from './council-client.mjs';
import {initInvestmentPanel} from './investment-panel.mjs';
import {initMacroPanel} from './macro-panel.mjs';
import {workspaceView} from './workspace-views.mjs';
import {shanghaiDate,withMarketFreshness} from './market-time.mjs';
import {createRefreshController} from './refresh-controller.mjs';
import {createChartLoader} from './chart-loader.mjs';
import {retainFlowSnapshot,flowPanelState} from './flow-snapshot.mjs';
import {retainQuoteSnapshot,failedSnapshot} from './quote-snapshot.mjs';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const readLocal = (key, fallback) => {try{return JSON.parse(localStorage.getItem(key)) ?? fallback;}catch{return fallback;}};
const persist = (key, value) => {try{localStorage.setItem(key,JSON.stringify(value));}catch{toast('浏览器存储不可用，请导出保留数据。');}};
const finite = n => n !== null && n !== undefined && n !== '' && Number.isFinite(Number(n));
const number = (n,d=2) => finite(n) ? Number(n).toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d}) : '—';
const signed = (n,d=2) => finite(n) ? `${Number(n)>0?'+':''}${number(n,d)}` : '—';
const cls = n => !finite(n)||Number(n)===0?'neutral':Number(n)>0?'up':'down';
const state = {epoch:0,mode:'real',snapshot:null,selected:'510300',flowSymbol:'510300',flowHistory:null,pairedFlowHistory:null,flowRequestId:0,flowLoading:false,kind:'etf',period:'intraday',view:'overview',sort:'',direction:-1,base:'',token:'',capabilities:{samples:false,training:false,ollama:false,server_analysis:false},connecting:false,favorites:readLocal('ml_favorites',['510300','159915','518880']),rules:readLocal('ml_alerts',[]),events:readLocal('ml_alert_events',[]),bars:[],requestId:0,refreshing:false};
let priceChart,flowChart,toastTimer,macroReader,macroCalendarDay=shanghaiDate();
const chartLoader=createChartLoader();
const COLORS={up:'#f28078',down:'#72cdb1',lime:'#c4ed81',grid:'#2a352c',muted:'#788d79'};

// Explicit synthetic data, fixed scenario time (never represented as current quotes).
const FIXTURE_TIME='2026-10-20T14:30:00+08:00';
const fixtureRows=[['000001','上证指数','index',3846.72,.83,588000000000,null],['399001','深证成指','index',12859.31,1.42,792000000000,null],['399006','创业板指','index',2856.48,1.76,385000000000,null],['000300','沪深300','index',4502.68,.65,325000000000,null],['000905','中证500','index',6819.52,1.12,213000000000,null],['000688','科创50','index',1276.35,-.46,98000000000,null],['510300','沪深300ETF','etf',4.583,1.28,4890000000,386000000],['510500','中证500ETF','etf',6.728,.86,3180000000,158000000],['159915','创业板ETF','etf',2.816,2.16,6820000000,512000000],['588000','科创50ETF','etf',1.273,-.63,2790000000,-184000000],['512480','半导体ETF','etf',1.192,2.84,3310000000,289000000],['512660','军工ETF','etf',1.085,-1.12,1160000000,-73000000],['518880','黄金ETF','etf',7.327,.72,3960000000,178000000],['513100','纳指ETF','etf',1.752,-.35,1780000000,-92000000],['510050','上证50ETF','etf',2.969,.42,2690000000,126000000],['512010','医药ETF','etf',.486,-1.42,960000000,-156000000]];

const NATIVE_CAPABILITIES={samples:false,training:false,ollama:false,server_analysis:false};
function capabilitiesFrom(payload,fallback={samples:true,training:true,ollama:true,server_analysis:true}){return {...fallback,...(payload?.capabilities??{})};}
function supports(feature){return state.mode==='real'&&state.capabilities[feature]!==false;}
function emptySnapshot(status='loading',error=null){return {mode:'real',status,source:'腾讯公开行情 · 内置连接',as_of:null,fetched_at:null,capabilities:{...NATIVE_CAPABILITIES},instruments:fixtureRows.map(([symbol,name,kind])=>({symbol,name,kind,price:null,change_pct:null,amount:null,volume:null,main_net:null,main_pct:null,as_of:null,status,unit:{amount:'CNY',main_net:null}})),market_flow:[],flows:[],flow_status:status==='loading'?'loading':'unavailable',errors:error?[{source:'内置行情连接',error}]:[]};}
function sourceTime(value,short=false){if(!value)return '源时间未知';const d=new Date(value);if(!Number.isFinite(d.getTime()))return String(value).replace('T',' ').slice(0,19);return d.toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false,...(short?{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}:{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'})});}
function validateSnapshot(data){if(!data||!Array.isArray(data.instruments)||data.instruments.some(x=>!x||typeof x.symbol!=='string'||typeof x.name!=='string'||!['index','etf'].includes(x.kind)))throw new Error('行情服务返回结构不正确');return withMarketFreshness(data);}

function demoSnapshot(){const flows=fixtureRows.filter(x=>x[2]==='etf').map(([symbol,name,kind,price,change_pct,amount,main_net])=>({symbol,name,main_net,main_pct:main_net/amount*100,super_net:main_net*.65,large_net:main_net*.35,medium_net:-main_net*.4,small_net:-main_net*.6,as_of:FIXTURE_TIME,status:'demo',unit:'CNY'}));return {flows,flow_status:'demo',flow_as_of:FIXTURE_TIME,mode:'demo',status:'demo',source:'合成演示数据 / 非真实行情',as_of:FIXTURE_TIME,fetched_at:null,instruments:fixtureRows.map(([symbol,name,kind,price,change_pct,amount,main_net])=>({symbol,name,kind,price,change_pct,amount,main_net,as_of:FIXTURE_TIME,status:'demo',unit:{amount:'CNY',main_net:'CNY'}})),market_flow:Array.from({length:15},(_,i)=>({date:`示例 ${i+1}`,main_net:(Math.sin(i*1.1)*65+(i-6)*6)*1e8})),errors:[]};}
function demoBars(item,period){let price=item.price/(1+item.change_pct/100),bars=[];const count=period==='intraday'?211:65;for(let i=0;i<count;i++){const fraction=i/(count-1),seed=Number(item.symbol)%17;let trend=item.price/(1+item.change_pct/100)+(item.price-item.price/(1+item.change_pct/100))*fraction;let wave=(Math.sin(i*.47+seed)*.22+Math.cos(i*1.37)*.12+Math.sin(i*.1)*.45)*(period==='intraday'?.004:.025)*item.price;const close=i===count-1?item.price:trend+wave,open=price,high=Math.max(open,close)+Math.abs(Math.sin(i))*item.price*.0015,low=Math.min(open,close)-Math.abs(Math.cos(i))*item.price*.0015;let time;if(period==='intraday'){let m=570+i;if(m>690)m+=90;time=`${String(Math.floor(m/60)).padStart(2,'0')}:${String(m%60).padStart(2,'0')}`;}else{const d=new Date(Date.UTC(2026,9,20)-(count-1-i)*(period==='week'?7:1)*86400000);time=d.toISOString().slice(0,10);}bars.push({time,open,close,high,low,volume:Math.round(6000+Math.abs(Math.sin(i*.71))*23000+(count-i)*60),amount:null});price=close;}return bars;}
function money(n,unit){if(!finite(n))return '—';return state.mode==='demo'||unit==='CNY'?`${signed(Number(n)/1e8,2)} 亿元`:`${signed(n,0)} 原值`;}
function amount(n,unit){if(!finite(n))return '—';return state.mode==='demo'||unit==='CNY'?`${number(Number(n)/1e8,2)} 亿元`:`${number(n,0)} 原值`;}
function toast(message){$('toast').textContent=message;$('toast').classList.add('show');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').classList.remove('show'),4500);}
function download(name,content,type='application/json'){const url=URL.createObjectURL(new Blob([content],{type}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
async function api(path,options={}){const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),95000);try{const response=await fetch(`${state.base}${path}`,{cache:'no-store',...options,headers:{'Content-Type':'application/json',...(state.token?{'Authorization':`Bearer ${state.token}`}:{ }),...options.headers},signal:controller.signal});if(!response.ok){let detail=await response.text();try{detail=JSON.parse(detail).detail??detail;}catch{}throw new Error(typeof detail==='string'?detail:JSON.stringify(detail));}return response.headers.get('content-type')?.includes('application/json')?response.json():response.text();}finally{clearTimeout(timer);}}
function current(){return state.snapshot?.instruments?.find(x=>x.symbol===state.selected&&x.kind===state.kind);}
function sparkSvg(item,large=false){if(state.mode!=='demo')return '<span class="muted tiny">查看详情 ↗</span>';const pts=demoBars(item,'intraday').filter((_,i)=>i%4===0);const lo=Math.min(...pts.map(x=>x.close)),hi=Math.max(...pts.map(x=>x.close));const p=pts.map((x,i)=>`${i/(pts.length-1)*100},${32-(x.close-lo)/(hi-lo||1)*27}`).join(' ');return `<svg class="${large?'spark':'table-spark'}" viewBox="0 0 100 36" aria-hidden="true"><polyline points="${p}" stroke="${item.change_pct>=0?COLORS.up:COLORS.down}" stroke-width="1.6" fill="none"/></svg>`;}
function renderTickers(){const ids=['000001','399001','399006','000300'];$('ticker-grid').innerHTML=ids.map(id=>{const item=state.snapshot.instruments.find(x=>x.symbol===id&&x.kind==='index');return item?`<button class="ticker" data-symbol="${id}" data-kind="index"><div class="ticker-name">${esc(item.name)}<small>${id}</small></div><div class="ticker-price">${number(item.price)}</div><div class="ticker-change ${cls(item.change_pct)}">${signed(item.change_pct)}% <span style="opacity:.5;margin-left:8px">${state.mode==='demo'?'示例':esc(sourceTime(item.as_of,true))}</span></div>${sparkSvg(item,true)}</button>`:'';}).join('');$('ticker-grid').querySelectorAll('button').forEach(b=>b.onclick=()=>selectInstrument(b.dataset.symbol,b.dataset.kind));}
function renderTable(){let rows=state.snapshot.instruments??[];const query=$('search').value.trim().toLowerCase(),kind=$('kind-filter').value;rows=rows.filter(x=>(x.name.toLowerCase().includes(query)||x.symbol.includes(query))&&(kind==='all'||kind===x.kind||kind==='favorite'&&state.favorites.includes(`${x.kind}:${x.symbol}`)||kind==='favorite'&&x.kind==='etf'&&state.favorites.includes(x.symbol)));if(state.sort)rows=[...rows].sort((a,b)=>{const va=a[state.sort],vb=b[state.sort];return !finite(va)?1:!finite(vb)?-1:(Number(va)-Number(vb))*state.direction;});$('watch-count').textContent=rows.length;$('watch-rows').innerHTML=rows.length?rows.map(x=>`<tr data-symbol="${esc(x.symbol)}" data-kind="${x.kind}" class="${x.symbol===state.selected&&x.kind===state.kind?'selected':''}" tabindex="0" aria-label="查看${esc(x.name)}"><td><span class="asset-name">${esc(x.name)}</span><span class="asset-code">${x.symbol} · ${x.kind==='etf'?'ETF':'指数'}</span></td><td>${number(x.price,x.kind==='etf'?3:2)}</td><td><span class="row-change ${cls(x.change_pct)}">${signed(x.change_pct)}%</span></td><td>${sparkSvg(x)}</td><td class="${cls(x.main_net)}" title="${esc([x.flow_source,x.flow_error??flowStatusText(x.flow_status)].filter(Boolean).join(' · '))}">${money(x.main_net,x.unit?.main_net)}<small class="flow-cell-note">${esc(state.mode==='demo'?'合成示例':!finite(x.main_net)?'暂无有效资金数据':`${['previous_day','future_day'].includes(x.flow_freshness)?'非今日 · ':x.flow_source_status==='cached'||x.flow_status==='cached'?'缓存 · ':''}${sourceTime(x.flow_as_of,true)}`)}</small></td><td><button class="star ${isFavorite(x)?'saved':''}" aria-label="关注${esc(x.name)}" data-fav="${x.symbol}" data-kind="${x.kind}">${isFavorite(x)?'★':'☆'}</button></td></tr>`).join(''):'<tr><td colspan="6" style="text-align:center;padding:30px;color:#8c9c80">没有匹配的标的，请调整搜索或筛选。</td></tr>';$('watch-rows').querySelectorAll('tr[data-symbol]').forEach(row=>{row.onclick=()=>selectInstrument(row.dataset.symbol,row.dataset.kind);row.onkeydown=e=>{if(e.key==='Enter')selectInstrument(row.dataset.symbol,row.dataset.kind);};});$('watch-rows').querySelectorAll('[data-fav]').forEach(b=>b.onclick=e=>{e.stopPropagation();toggleFavorite(b.dataset.fav,b.dataset.kind);});$('watch-caption').textContent=state.mode==='demo'?'合成资金示例 · 红涨绿跌':`走势：${state.snapshot.source} · 资金：东方财富 ETF 快照 / 指数资金分时末点 · 各行标注资金源时间 · 缺失显示 —`;}
function isFavorite(item){return state.favorites.includes(`${item.kind}:${item.symbol}`)||item.kind==='etf'&&state.favorites.includes(item.symbol);}
function toggleFavorite(symbol,kind){const item={symbol,kind};if(isFavorite(item))state.favorites=state.favorites.filter(x=>x!==`${kind}:${symbol}`&&!(kind==='etf'&&x===symbol));else state.favorites.push(`${kind}:${symbol}`);persist('ml_favorites',state.favorites);renderTable();renderInstrument();}
/** Latest same-instrument, same-day flow; no values are copied between ETF and index.
 * Example inputs and results: docs/INTRADAY_FLOW.md (selected instrument summary).
 */
function currentInstrumentFlow(){
 const item=current();if(!item)return null;
 const history=state.pairedFlowHistory;
 if(history){
  const aligned=buildPairedData({item,flowHistory:history,priceAsOf:state.priceHistory?.as_of??item.as_of,demo:state.mode==='demo'});
  const latest=aligned.flowObservations.filter(Boolean).at(-1);
  if(latest)return {...latest,unit:'CNY',as_of:latest.source_time,status:history.status,source:history.source};
 }
 // The watchlist snapshot now carries each index's own latest fund-flow minute.
 // Real input, invocation, output and changes: docs/INTRADAY_FLOW.md, 观察池指数资金修复.
 const row=item.kind==='etf'?flowRows().find(x=>x.symbol===item.symbol):finite(item.main_net)?{symbol:item.symbol,kind:item.kind,main_net:item.main_net,main_pct:item.main_pct,as_of:item.flow_as_of,source:item.flow_source,status:item.flow_status,stale:item.flow_stale,unit:item.unit?.main_net}:null;
 if(row&&item.as_of&&row.as_of&&String(item.as_of).slice(0,10)!==String(row.as_of).slice(0,10))return null;
 return row?{...row,status:item.kind==='etf'&&state.snapshot.flow_status==='cached'?'cached':row.status}:null;
}
function renderInstrument(){const item=current();if(!item)return;const flow=currentInstrumentFlow();$('paired-symbol').innerHTML=state.snapshot.instruments.map(x=>`<option value="${x.kind}:${esc(x.symbol)}">${esc(x.name)} · ${esc(x.symbol)}</option>`).join('');$('paired-symbol').value=`${item.kind}:${item.symbol}`;$('advice-symbol').innerHTML=$('paired-symbol').innerHTML;$('advice-symbol').value=`${item.kind}:${item.symbol}`;$('selected-name').textContent=item.name;$('selected-code').textContent=item.symbol;$('selected-kind').textContent=item.kind==='etf'?'ETF':'指数';$('selected-price').textContent=number(item.price,item.kind==='etf'?3:2);$('selected-change').textContent=`${signed(item.change_pct)}%`;$('selected-change').className=cls(item.change_pct);$('selected-amount').textContent=amount(item.amount,item.unit?.amount);$('selected-flow').textContent=money(flow?.main_net,flowUnit(flow));$('selected-flow').className=cls(flow?.main_net);$('quote-asof').textContent=state.mode==='demo'?'合成示例':sourceTime(item.as_of);$('chart-label').textContent=item.name;$('agent-context-name').textContent=`${item.name} · ${item.symbol}`;$('favorite').textContent=isFavorite(item)?'★ 已加入自选':'☆ 加入自选';renderWatchSummary();}
async function selectInstrument(symbol,kind){state.selected=symbol;state.kind=kind;state.priceHistory=null;state.pairedFlowHistory=null;if(kind==='etf'){state.flowSymbol=symbol;state.flowHistory=null;state.flowRequestId++;}renderInstrument();renderTable();renderFlow();renderDateStatus();await Promise.all([loadChart(),loadFlowHistory()]);}
/** Independent price/flow refresh; real replay and before/after: docs/PRICE_REFRESH.md. */
async function loadChart({allowPendingSnapshot=false}={}){
 const item=current();if(!item)return;
 const requestId=++state.requestId,epoch=state.epoch,snapshot=state.snapshot,period=state.period,mode=state.mode,compare=$('compare').checked;
 const key=JSON.stringify([epoch,mode,state.base,item.kind,item.symbol,period,shanghaiDate()]);
 const stillCurrent=()=>requestId===state.requestId&&epoch===state.epoch&&item.symbol===state.selected&&item.kind===state.kind&&period===state.period;
 if(state.chartKey!==key){
  state.chartKey=key;state.bars=[];state.priceHistory=null;state.pairedFlowHistory=null;
  priceChart?.dispose();priceChart=null;
  $('price-chart').innerHTML='<div class="chart-empty">正在读取价格走势…</div>';
  $('paired-meta').textContent='';
 }
 $('price-chart').classList.toggle('paired',period==='intraday');
 if(snapshot.status==='loading'&&!allowPendingSnapshot){$('chart-source').textContent='正在连接内置行情源';$('chart-note').textContent='等待真实行情';return;}
 const benchItem=snapshot.instruments.find(x=>x.symbol==='000300'&&x.kind==='index');
 await chartLoader.load({key,item,isCurrent:stillCurrent,requests:{
  price:()=>mode==='demo'?Promise.resolve({bars:demoBars(item,period),status:'demo',source:'合成演示',as_of:FIXTURE_TIME}):api(`/api/history?symbol=${item.symbol}&kind=${item.kind}&period=${period==='intraday'?'1m':'day'}`),
  flow:period==='intraday'?()=>mode==='demo'?Promise.resolve(demoFlowHistory(item)):api(`/api/flow-history?symbol=${encodeURIComponent(item.symbol)}&kind=${encodeURIComponent(item.kind)}`):undefined,
  benchmark:compare?()=>mode==='demo'?Promise.resolve({bars:demoBars(benchItem,period),status:'demo',as_of:FIXTURE_TIME}):api(`/api/history?symbol=000300&kind=index&period=${period==='intraday'?'1m':'day'}`):undefined
 },onUpdate:view=>{
  const price=withMarketFreshness(view.price),flow=withMarketFreshness(view.flow);
  let benchmark=view.benchmark?.bars;
  state.priceHistory=price;state.bars=Array.isArray(price?.bars)?price.bars:[];
  state.pairedFlowHistory=period==='intraday'?flow:null;
  renderInstrument();renderDateStatus();
  if(period==='week'&&mode!=='demo'){state.bars=weeklyBars(state.bars);benchmark=weeklyBars(benchmark??[]);}
  $('chart-source').textContent=`价格：${price?.source??snapshot.source} · ${mode==='demo'?'合成示例':flowStatusText(price?.status)}${view.pending.price?' · 正在刷新，保留已有走势':''}`;
  $('chart-note').textContent=mode==='demo'?'合成情景 · 非实际交易记录':period==='intraday'?'源时间按北京时间显示':'历史 OHLC · 未复权';
  if(price?.error)$('chart-note').textContent+=' · '+price.error;
  if(period!=='intraday'&&!state.bars.length){
   priceChart?.dispose();priceChart=null;
   $('price-chart').innerHTML=`<div class="chart-empty">${view.pending.price?'正在读取价格走势…':'价格走势暂不可用'}<br><small>${esc(price?.error??'')}</small></div>`;
   return;
  }
  if(!priceChart){$('price-chart').innerHTML='';priceChart=echarts.init($('price-chart'));}
  if(period==='intraday'){drawPairedTrend(price,flow,benchmark,view.pending);return;}
  priceChart.clear();drawPrice(benchmark);
  $('paired-status').textContent=`${period==='week'?'周 K':'日 K'} · ${state.bars.length} 个价格点${view.pending.price?' · 正在刷新':''}`;
  $('paired-meta').textContent=`价格源时间 ${sourceTime(price?.as_of)}${price?.error?' · '+price.error:''}。日 K / 周 K 暂未接入对应的长期资金历史。`;
 }});
}

/** Explicit demo only; these generated values are never persisted as market observations. */
function demoFlowHistory(item){
 const row=flowRows().find(x=>x.symbol===item.symbol),base=Number(row?.main_net??0);
 return {symbol:item.symbol,kind:item.kind,status:'demo',unit:'CNY',as_of:FIXTURE_TIME,trade_date:'2026-10-20',source:'合成资金演示',bars:Array.from({length:18},(_,i)=>{const net=base*(i+1)/18+Math.sin(i*.7)*Math.abs(base)*.08;return {time:`2026-10-20T14:${String(13+i).padStart(2,'0')}:00+08:00`,main_net:net,super_net:net*.65,large_net:net*.35};})};
}

/** Two linked grids use the exact same minute buckets, with independent units and gaps. */
function drawPairedTrend(price,flow,benchmark,pending={}){
 const item=current(),demo=state.mode==='demo';
 const data=buildPairedData({priceBars:state.bars,flowHistory:flow,item,priceAsOf:price.as_of??item.as_of,demo});
 const bench=benchmark?.length?buildPairedData({priceBars:benchmark,flowHistory:null,item:{symbol:'000300',kind:'index'},demo}):null;
 const compare=$('compare').checked&&bench?.date===data.date&&bench?.priceCount>0&&data.priceCount>0;
 const priceBase=data.prices.find(finite),benchBase=bench?.prices.find(finite);
 const displayPrices=compare?data.prices.map(x=>finite(x)?(x/priceBase-1)*100:null):data.prices;
 const flowLatest=data.flowObservations.filter(Boolean).at(-1);
 const flowLabel=demo?'合成演示':flowStatusText(flow?.status??'unavailable');
 const sampled=flow?.source_type==='sampled_snapshots';
 const rowStatus=sampled?flowLabel+' · 本站采集点，非完整分钟历史':flowLabel;
 $('paired-status').textContent=`${demo?'固定合成演示日期':'实际行情日期'} ${data.date??'待取得'}${!demo&&data.date&&data.date!==shanghaiDate()?' · 非今日数据，等待源更新':''} · 上图${compare?'涨跌对比':'价格'}，下图累计净额 · ${data.priceCount} 个价格点 · ${data.flowCount} 个${demo?'合成资金点':sampled?'本站采集点（非完整分时）':'资金分钟点'}${data.dateMismatch?' · 资金源日期 '+data.flowDate+' 与价格日不同':''}${pending.price?' · 价格更新中':''}${pending.flow?' · 资金独立更新中':''}`;
 $('paired-meta').innerHTML=`<div><b>价格来源</b> ${esc(price.source??state.snapshot.source)} · ${esc(flowStatusText(price.status))} · 源时间 ${esc(sourceTime(price.as_of??item.as_of))}${price.error?' · '+esc(price.error):''}</div><div><b>资金来源</b> ${esc(demo?'合成演示':flow?.source??'尚未取得')} · ${esc(rowStatus)} · 源时间 ${esc(sourceTime(flowLatest?.source_time??flowLatest?.time??flow?.as_of))}</div><div>${esc(data.flowMessage||'同一交易日按分钟归位；提示保留源秒时间。资金缺失分钟留空，不补零、不延用前值。')} 主力 = 超大单 + 大单净额；正值净流入，负值净流出。</div><div>${demo?'合成情景仅演示交互。':'打开页面补拉当日已有资金分时；页面可见时每 60 秒刷新。只连接相邻分钟的真实数据，缺失时段与午休断开。'}${flow?.note?' '+esc(flow.note):''}${flow?.error?' '+esc(flow.error):''}</div>`;
 const series=[{name:item.name,type:'line',xAxisIndex:0,yAxisIndex:0,data:displayPrices,connectNulls:false,showSymbol:data.priceCount<=10,symbolSize:3,lineStyle:{width:1.9,color:COLORS.lime},itemStyle:{color:COLORS.lime},areaStyle:{color:'#a5d36c0b'}}];
 if(compare)series.push({name:'沪深300（起点=0%）',type:'line',xAxisIndex:0,yAxisIndex:0,data:bench.prices.map(x=>finite(x)?(x/benchBase-1)*100:null),connectNulls:false,showSymbol:false,lineStyle:{color:'#8ca6d4',width:1.3,type:'dashed'}});
 for(const [name,key,color,width] of [['主力净额','main_net',COLORS.up,2.1],['超大单净额','super_net','#8cabd1',1.3],['大单净额','large_net','#c9aa7e',1.3]]){
  const segments=splitObservedSegments(data.flows[key],data.flowObservations,demo?180000:90000);
  if(!segments.length)segments.push(data.flows[key]);
  segments.forEach((segment,index)=>series.push({name,type:'line',xAxisIndex:1,yAxisIndex:1,data:segment.map(x=>finite(x)?Number(x)/1e8:null),connectNulls:true,showSymbol:segment.filter(finite).length<=10,symbolSize:4,lineStyle:{color,width},itemStyle:{color},markLine:key==='main_net'&&index===0?{symbol:'none',silent:true,label:{show:false},lineStyle:{color:'#66735d',type:'dashed',width:1},data:[{yAxis:0}]}:undefined}));
 }
 const graphic=[];
 if(!data.priceCount)graphic.push({type:'text',left:'center',top:'20%',style:{text:pending.price?'正在读取价格走势…':'价格走势暂不可用',fill:COLORS.muted,fontSize:12}});
 if(!data.flowCount){
  const reason=flow?.error?String(flow.error).slice(0,32):'资金源尚未返回该价格日的有效分钟数据';
  const message=pending.flow?'正在读取资金走势\n价格与资金独立读取，互不等待':data.dateMismatch?`价格日期 ${data.date}\n资金日期 ${data.flowDate}\n${reason}\n等待同日数据 · 每 60 秒重试`:`暂无同日资金分钟数据\n${reason}\n页面可见时每 60 秒重试`;
  graphic.push({type:'text',left:'center',top:'65%',style:{text:message,fill:COLORS.muted,fontSize:11,textAlign:'center',lineHeight:21}});
 }
 priceChart.setOption({animation:false,backgroundColor:'transparent',textStyle:{fontFamily:'sans-serif',fontSize:10},title:[{text:compare?'价格走势 · 起点归一化（%）':`价格走势 · ${item.kind==='index'?'点':'元'}`,left:56,top:7,textStyle:{fontSize:12,color:'#c2d0b9',fontWeight:'normal'}},{text:'主力资金 · 当日累计净额（亿元）',left:56,top:'43%',textStyle:{fontSize:12,color:'#c2d0b9',fontWeight:'normal'}}],legend:{top:'48%',textStyle:{color:'#9cae92',fontSize:10},data:['主力净额','超大单净额','大单净额']},tooltip:{trigger:'axis',confine:true,backgroundColor:'#16251c',borderColor:'#4b6340',textStyle:{color:'#d5e4c8',fontSize:11},axisPointer:{type:'cross'},formatter:params=>{const i=params[0]?.dataIndex;if(i===undefined)return '';const p=data.priceObservations[i],f=data.flowObservations[i];return `<b>${esc(item.name)} · ${esc(data.date)} ${esc(data.labels[i])}</b><br>价格：${number(data.prices[i],item.kind==='etf'?3:2)} ${item.kind==='etf'?'元':'点'}<br>价格源：${esc(sourceTime(p?.source_time??p?.time))}<br><br>主力净额：${money(f?.main_net,'CNY')}<br>超大单净额：${money(f?.super_net,'CNY')}<br>大单净额：${money(f?.large_net,'CNY')}<br>资金源：${esc(sourceTime(f?.source_time??f?.time))}`;}},axisPointer:{link:[{xAxisIndex:'all'}],label:{backgroundColor:'#52644b'}},grid:[{left:62,right:25,top:38,height:'28%'},{left:62,right:25,top:'55%',height:'31%'}],xAxis:[0,1].map((gridIndex)=>({type:'category',gridIndex,data:data.labels,boundaryGap:false,axisTick:{show:false},axisLine:{lineStyle:{color:'#3b4835'}},axisLabel:{show:gridIndex===1,color:COLORS.muted,fontSize:10,interval:(i,label)=>['09:30','10:30','11:30','14:00','15:00'].includes(label)},axisPointer:{show:true},splitLine:{show:false}})),yAxis:[{scale:true,splitNumber:3,axisLabel:{color:COLORS.muted,fontSize:10,formatter:v=>compare?`${v.toFixed(2)}%`:v.toFixed(item.kind==='etf'?3:0)},splitLine:{lineStyle:{color:COLORS.grid,type:'dashed'}}},{gridIndex:1,scale:false,splitNumber:3,axisLabel:{color:COLORS.muted,fontSize:10,formatter:v=>Number(v).toFixed(2)},splitLine:{lineStyle:{color:COLORS.grid,type:'dashed'}}}],dataZoom:[{type:'inside',xAxisIndex:[0,1],filterMode:'none',start:0,end:100},{type:'slider',xAxisIndex:[0,1],filterMode:'none',bottom:8,height:18,start:0,end:100,borderColor:'#34432f',fillerColor:'#9ebd6b18',textStyle:{color:COLORS.muted,fontSize:9},showDataShadow:false}],series,graphic},true);
}

function weeklyBars(bars){const groups=new Map();for(const bar of bars){const d=new Date(String(bar.time).slice(0,10)+'T00:00:00Z');if(!Number.isFinite(d.getTime()))continue;d.setUTCDate(d.getUTCDate()-((d.getUTCDay()+6)%7));const key=d.toISOString().slice(0,10),old=groups.get(key);if(old){old.high=Math.max(old.high,bar.high);old.low=Math.min(old.low,bar.low);old.close=bar.close;old.volume=finite(old.volume)&&finite(bar.volume)?Number(old.volume)+Number(bar.volume):null;}else groups.set(key,{...bar,time:key,volume:finite(bar.volume)?Number(bar.volume):null});}return [...groups.values()];}
function drawPrice(benchmark){const bars=state.bars,hasVolume=bars.some(b=>finite(b.volume)),compare=$('compare').checked&&benchmark?.length,base=bars[0].close;const times=bars.map(x=>String(x.time)),baseBench=benchmark?.[0]?.close,lookup=new Map((benchmark??[]).map(b=>[String(b.time),b.close]));const series=[];if(state.period==='intraday'||compare)series.push({name:current().name,type:'line',data:bars.map(b=>compare?(b.close/base-1)*100:b.close),symbol:'none',lineStyle:{width:1.8,color:COLORS.lime},areaStyle:compare?undefined:{color:new echarts.graphic.LinearGradient(0,0,0,1,[{offset:0,color:'#97cc4e30'},{offset:1,color:'#97cc4e00'}])}});else series.push({name:current().name,type:'candlestick',data:bars.map(b=>[b.open,b.close,b.low,b.high]),itemStyle:{color:COLORS.up,color0:COLORS.down,borderColor:COLORS.up,borderColor0:COLORS.down}});if(compare)series.push({name:'沪深300（起点=0%）',type:'line',symbol:'none',connectNulls:false,data:times.map(t=>lookup.has(t)?(lookup.get(t)/baseBench-1)*100:null),lineStyle:{color:'#8ca6d4',width:1.5,type:'dashed'}});if(hasVolume)series.push({name:'成交量',type:'bar',xAxisIndex:1,yAxisIndex:1,data:bars.map(b=>({value:b.volume,itemStyle:{color:b.close>=b.open?'#b9655866':'#65b99d66'}}))});priceChart.setOption({animation:false,backgroundColor:'transparent',textStyle:{fontFamily:'ui-monospace, monospace',fontSize:9},tooltip:{trigger:'axis',backgroundColor:'#16251c',borderColor:'#4b6340',textStyle:{color:'#d5e4c8',fontSize:11},axisPointer:{type:'cross',lineStyle:{color:'#52644b'}}},axisPointer:{link:[{xAxisIndex:'all'}]},grid:[{left:61,right:22,top:17,height:'61%'},{left:61,right:22,top:'76%',height:'15%'}],xAxis:[{type:'category',data:times,axisLine:{show:false},axisTick:{show:false},axisLabel:{show:false},boundaryGap:state.period!=='intraday',splitLine:{show:true,interval:20,lineStyle:{color:'#263129',type:'dashed'}}},{type:'category',data:times,gridIndex:1,axisLine:{lineStyle:{color:'#34432f'}},axisTick:{show:false},axisLabel:{color:COLORS.muted,fontSize:9,interval:Math.floor(bars.length/5)},boundaryGap:state.period!=='intraday'}],yAxis:[{scale:true,splitNumber:4,axisLabel:{color:COLORS.muted,fontSize:9,formatter:v=>compare?`${v.toFixed(2)}%`:v.toFixed(current().kind==='etf'?3:0)},splitLine:{lineStyle:{color:COLORS.grid,type:'dashed'}}},{gridIndex:1,splitNumber:1,axisLabel:{show:false},splitLine:{show:false}}],dataZoom:[{type:'inside',xAxisIndex:[0,1],start:0,end:100}],series});if(compare)$('chart-note').textContent+=' · 双序列起点=0%';if(!hasVolume)$('chart-note').textContent+=' · 本序列成交量不可用';}
// Flow figures are cumulative net amounts, never inferred gross buy/sell totals.
function flowRows(){
 const explicit=state.snapshot.flows;
 if(Array.isArray(explicit))return explicit;
 return (state.snapshot.instruments??[]).filter(x=>x.kind==='etf').map(x=>({...x,unit:x.unit?.main_net}));
}
function flowUnit(row){return row?.unit==='CNY'||row?.unit?.main_net==='CNY'?'CNY':null;}
function selectedFlow(){return flowRows().find(x=>x.symbol===state.flowSymbol);}
function flowStatusText(status){return {loading:'连接中',live:'源数据已取得',partial:'部分字段缺失',cached:'缓存 · 未更新',unavailable:'暂不可用',demo:'合成演示',stale:'源数据较旧',recorded:'已存资金记录',empty:'暂无资金分钟数据'}[status]??'延迟未承诺';}
function renderWatchSummary(){
 const item=current();if(!item)return;
 const row=currentInstrumentFlow();
 $('watch-summary-content').innerHTML=`<div class="watch-summary-heading"><b>${esc(item.name)}</b><span>${esc(item.symbol)} · ${item.kind==='etf'?'ETF':'指数'}</span><strong>${number(item.price,item.kind==='etf'?3:2)}</strong><em class="${cls(item.change_pct)}">${signed(item.change_pct)}%</em></div><dl class="watch-facts"><div><dt>成交额</dt><dd>${amount(item.amount,item.unit?.amount)}</dd></div><div><dt>主力净额</dt><dd class="${cls(row?.main_net)}">${money(row?.main_net,flowUnit(row))}</dd></div><div><dt>主力净占比</dt><dd>${finite(row?.main_pct)?signed(row.main_pct)+'%':'—'}</dd></div></dl><div class="watch-source"><b>价格 · ${state.base?'已连接服务':'腾讯'}</b><span>${esc(state.mode==='demo'?'合成演示':sourceTime(item.as_of))}</span><b>资金 · ${state.mode==='demo'?'合成演示':state.base?'已连接服务':'东方财富'}</b><span>${esc(sourceTime(row?.as_of??item.flow_as_of))}</span><small>${esc(flowStatusText(row?.status??'unavailable'))} · ${item.kind==='index'?'指数资金采用东方财富该指数统计口径':'净额按行情商订单分类统计'}</small></div>`;
}
function flowBaseOption(){return {animationDuration:250,textStyle:{fontFamily:'sans-serif',fontSize:10},tooltip:{trigger:'axis',backgroundColor:'#17251c',borderColor:'#415437',textStyle:{color:'#d2e0c5',fontSize:11}},grid:{left:80,right:32,top:35,bottom:55}};}
function renderFlow(){
 flowChart?.dispose();flowChart=null;
 const mode=$('flow-mode').value,demo=state.mode==='demo',all=flowRows(),validRows=all.filter(x=>finite(x.main_net)),history=state.flowHistory;
 const etfs=state.snapshot.instruments.filter(x=>x.kind==='etf');
 if(!etfs.some(x=>x.symbol===state.flowSymbol)&&etfs.length)state.flowSymbol=etfs[0].symbol;
 const selected=selectedFlow();
 const panel=flowPanelState({...state.snapshot,flows:all},state.flowSymbol,mode),rankDate=panel.date;
 const rows=mode==='ranking'&&!demo?panel.rows:validRows,excluded=mode==='ranking'?panel.excluded:0;
 $('flow-symbol').innerHTML=etfs.map(x=>`<option value="${esc(x.symbol)}"${x.symbol===state.flowSymbol?' selected':''}>${esc(x.name)} · ${esc(x.symbol)}</option>`).join('');
 $('flow-symbol').disabled=mode==='ranking';
 $('flow-context').textContent=mode==='ranking'?'仅统计当前 ETF 观察池':'ETF 资金结构 · 指数资金走势请查看上方联合图';
 const status=demo?'demo':(mode==='intraday'&&history?history.status:panel.status);
 $('flow-source-label').textContent=demo?'合成资金情景 · 非真实市场':panel.source??'东方财富 ETF 资金';
 $('flow-source-status').textContent=status==='partial'?`部分数据 · ${panel.cached_count} 只缓存`:flowStatusText(status);
 $('flow-source-status').className='flow-status '+(status==='live'?'ok':status==='cached'||status==='stale'||status==='unavailable'?'warn':'');
 const sourceAt=mode==='intraday'&&history?.as_of?history.as_of:panel.as_of;
 const fetchedAt=mode==='intraday'&&history?history.fetched_at??history.bars?.at(-1)?.fetched_at:panel.fetched_at;
 $('flow-source-time').textContent=demo?'固定演示时间：'+sourceTime(FIXTURE_TIME):`资金源时间：${sourceTime(sourceAt)} · 实际取得：${fetchedAt?sourceTime(fetchedAt):'尚未取得'} · 免费源延迟未承诺`;
 const storageWarning=!demo&&state.snapshot.flow_storage_status==='unavailable'?'采集存储暂不可用：'+(state.snapshot.flow_storage_error??'本次快照尚未保存，盘中记录可能有缺口。'):'';
 $('flow-storage-warning').hidden=!storageWarning;$('flow-storage-warning').textContent=storageWarning;
 const note=demo?'合成示例，不代表实际资金。':state.snapshot.flow_note??'主力净额 = 超大单净额 + 大单净额；订单分类不代表机构身份，也不等于 ETF 申赎。';
 $('flow-note').textContent=note;
 $('flow-chart').innerHTML='';
 const isCny=demo||(mode==='ranking'?rows.length>0&&rows.every(x=>flowUnit(x)==='CNY'):mode==='intraday'&&history?history.unit==='CNY':flowUnit(selected)==='CNY');
 const divisor=isCny?1e8:1,unit=isCny?'亿元':'原值（单位待核实）';
 const empty=message=>{$('flow-chart').innerHTML=`<div class="chart-empty"><b>${esc(message)}</b><span>缺失不代表零流入，价格与成交额不能替代主力净额。</span></div>`;};
 if(mode==='ranking'){
  const positive=rows.filter(x=>Number(x.main_net)>0).length,negative=rows.filter(x=>Number(x.main_net)<0).length;
  $('flow-summary').innerHTML=`<div>净流入 ETF<strong class="up">${rows.length?positive:'—'}<small> 只</small></strong></div><div>净流出 ETF<strong class="down">${rows.length?negative:'—'}<small> 只</small></strong></div><div class="flow-caption">有效 ${rows.length} / ${etfs.length} 只 · ${esc(unit)}<br>${demo?'合成示例':`排行日期 ${esc(rankDate??'待取得')} · ${excluded} 只异日 / 日期未知记录未纳入`}<br>源交易日累计净额，非全市场统计</div>`;
  if(!rows.length){empty(status==='loading'?'正在连接资金数据源…':'ETF 主力资金暂不可用');return;}
  const data=[...rows].sort((a,b)=>Number(a.main_net)-Number(b.main_net));
  flowChart=echarts.init($('flow-chart'));
  flowChart.setOption({...flowBaseOption(),grid:{left:112,right:80,top:22,bottom:40},tooltip:{...flowBaseOption().tooltip,formatter:params=>{const entry=data[params[0]?.dataIndex];return entry?`<b>${esc(entry.name??entry.symbol)}</b><br>主力净额：${money(entry.main_net,flowUnit(entry))}<br>资金源：${esc(sourceTime(entry.as_of))}<br>实际取得：${esc(sourceTime(entry.fetched_at??state.snapshot.flow_fetched_at))}<br>${esc(flowStatusText(entry.status))}`:'';}},xAxis:{type:'value',name:unit,nameLocation:'middle',nameGap:25,axisLabel:{color:COLORS.muted},splitLine:{lineStyle:{color:COLORS.grid,type:'dashed'}}},yAxis:{type:'category',data:data.map(x=>x.name??x.symbol),axisLabel:{color:'#bdcdb2',fontSize:12},axisTick:{show:false},axisLine:{show:false}},series:[{name:'主力净额',type:'bar',barMaxWidth:24,data:data.map(x=>({value:Number(x.main_net)/divisor,itemStyle:{color:x.main_net>=0?COLORS.up:COLORS.down,borderRadius:2}})),label:{show:true,position:'right',fontSize:11,color:'#c4d3b8',formatter:p=>signed(p.value,2)}}]});
  return;
 }
 if(mode==='structure'){
  $('flow-summary').innerHTML=`<div>当日主力净额<strong class="${cls(selected?.main_net)}">${money(selected?.main_net,flowUnit(selected))}</strong></div><div>主力净占比<strong class="${cls(selected?.main_pct)}">${finite(selected?.main_pct)?signed(selected.main_pct)+'%':'—'}</strong></div><div class="flow-caption">超大单 + 大单 = 主力净额<br>${esc(unit)} · 正值净流入 / 负值净流出</div>`;
  const keys=[['超大单','super_net'],['大单','large_net'],['中单','medium_net'],['小单','small_net']];
  if(!selected||!keys.some(([,key])=>finite(selected[key]))){empty('所选 ETF 大小单净额暂不可用');return;}
  flowChart=echarts.init($('flow-chart'));
  flowChart.setOption({...flowBaseOption(),tooltip:{...flowBaseOption().tooltip,valueFormatter:v=>finite(v)?`${signed(v,2)} ${unit}`:'未提供'},xAxis:{type:'category',data:keys.map(x=>x[0]),axisLabel:{color:'#bdcdb2',fontSize:12},axisTick:{show:false},axisLine:{lineStyle:{color:COLORS.grid}}},yAxis:{type:'value',name:unit,axisLabel:{color:COLORS.muted},splitLine:{lineStyle:{color:COLORS.grid,type:'dashed'}}},series:[{name:'当日累计净额',type:'bar',barMaxWidth:60,data:keys.map(([,key])=>({value:finite(selected[key])?Number(selected[key])/divisor:null,itemStyle:{color:selected[key]>=0?COLORS.up:COLORS.down,borderRadius:3}})),label:{show:true,position:'top',color:'#c4d3b8',formatter:p=>finite(p.value)?signed(p.value,2):'—'}}]});
  return;
 }
 const bars=(history?.bars??[]).filter(x=>Number.isFinite(new Date(x.time).getTime())&&finite(x.main_net));
 const last=bars.at(-1),prev=bars.at(-2),delta=last&&prev&&new Date(last.time)-new Date(prev.time)<=90000?Number(last.main_net)-Number(prev.main_net):null;
 $('flow-summary').innerHTML=`<div>最新分钟主力净额<strong class="${cls(last?.main_net)}">${money(last?.main_net,isCny?'CNY':null)}</strong></div><div>较前分钟变化<strong class="${cls(delta)}">${money(delta,isCny?'CNY':null)}</strong></div><div class="flow-caption">${bars.length} 个${demo?'合成示例':'资金分钟'}点 · ${esc(unit)}<br>变化值为累计净额之差</div>`;
 $('flow-note').textContent=`${demo?'合成采集演示。':'东方财富当日资金分时，打开页面补拉已有分钟数据。'}${history?.note??''} 缺失分钟与午休断开曲线。${note}`;
 if(!bars.length){$('flow-chart').innerHTML=`<div class="chart-empty"><b>${state.flowLoading?'正在读取当日资金分时…':'暂无可用资金分钟点'}</b><span>${esc(history?.error??'资金源暂未返回有效分钟序列；缺失时段保持为空。')}</span></div>`;return;}
 const points=[];
 for(let i=0;i<bars.length;i++){
  if(i>0&&new Date(bars[i].time)-new Date(bars[i-1].time)>90000)points.push({time:new Date(new Date(bars[i-1].time).getTime()+1000).toISOString(),gap:true});
  points.push(bars[i]);
 }
 flowChart=echarts.init($('flow-chart'));
 flowChart.setOption({...flowBaseOption(),animation:false,legend:{top:6,textStyle:{color:'#9cae92',fontSize:10},data:['主力净额','超大单净额','大单净额']},tooltip:{...flowBaseOption().tooltip,valueFormatter:v=>finite(v)?`${signed(v,3)} ${unit}`:'数据间断'},xAxis:{type:'category',data:points.map(x=>sourceTime(x.time).split(' ').at(-1)),boundaryGap:false,axisLabel:{color:COLORS.muted,fontSize:9},axisTick:{show:false},axisLine:{lineStyle:{color:COLORS.grid}}},yAxis:{type:'value',name:unit,scale:true,axisLabel:{color:COLORS.muted},splitLine:{lineStyle:{color:COLORS.grid,type:'dashed'}}},dataZoom:[{type:'inside',start:0,end:100}],series:[['主力净额','main_net',COLORS.lime],['超大单净额','super_net','#8cabd1'],['大单净额','large_net','#c9aa7e']].map(([name,key,color],i)=>({name,type:'line',connectNulls:false,showSymbol:bars.length<3,symbolSize:6,lineStyle:{width:i?1.3:2,color},itemStyle:{color},data:points.map(x=>!x.gap&&finite(x[key])?Number(x[key])/divisor:null)}))});
}
async function loadFlowHistory(){
 if($('flow-mode').value!=='intraday')return;
 const requestId=++state.flowRequestId,epoch=state.epoch,symbol=state.flowSymbol,snapshot=state.snapshot;
 if(snapshot.status==='loading')return;
 state.flowLoading=true;renderFlow();
 try{
  const payload=state.mode==='demo'?{symbol,status:'demo',unit:'CNY',as_of:FIXTURE_TIME,note:'',bars:Array.from({length:18},(_,i)=>{const row=selectedFlow(),base=Number(row?.main_net??0),net=base*(i+1)/18+Math.sin(i*.7)*Math.abs(base)*.08;return {time:`2026-10-20T14:${String(13+i).padStart(2,'0')}:00+08:00`,main_net:net,super_net:net*.65,large_net:net*.35};})}:await api(`/api/flow-history?symbol=${encodeURIComponent(symbol)}&kind=etf`);
  if(requestId!==state.flowRequestId||epoch!==state.epoch||symbol!==state.flowSymbol)return;
  if(!payload||!Array.isArray(payload.bars))throw new Error('资金历史返回格式无效');
  state.flowHistory=withMarketFreshness(payload);
 }catch(error){if(requestId!==state.flowRequestId||epoch!==state.epoch||symbol!==state.flowSymbol)return;state.flowHistory={bars:[],status:'unavailable',error:String(error.message).slice(0,160)};}
 finally{if(requestId===state.flowRequestId&&epoch===state.epoch&&symbol===state.flowSymbol){state.flowLoading=false;renderFlow();renderDateStatus();}}
}

/** Separate the current Shanghai day from genuine source dates. Historical values
 * keep their original day; daily refresh examples are in docs/DAILY_REFRESH.md. */
function renderDateStatus(){
 const today=shanghaiDate(),demo=state.mode==='demo',item=current();
 const priceDate=shanghaiDate(state.priceHistory?.as_of??item?.as_of??state.snapshot.as_of);
 const flowDate=shanghaiDate(state.pairedFlowHistory?.as_of??item?.flow_as_of??state.snapshot.flow_as_of);
 const dates=[priceDate,flowDate].filter(Boolean),old=!demo&&dates.some(day=>day!==today);
 const hint=demo?'固定合成示例，不随日期伪装成真实行情':old?'尚未取得今日完整数据；盘前或休市时保留源最近记录':dates.length<2?'正在等待价格与资金源日期':'日期随行情源自动更新';
 $('market-date').classList.toggle('historical',old||demo);
 $('market-date').innerHTML=`<span>今天 <b>${esc(today)}</b> · 北京时间</span><span>${demo?'演示':'价格'}日期 <b>${esc(priceDate??'待取得')}</b></span><span>资金日期 <b>${esc(flowDate??'待取得')}</b></span><span>${state.refreshing||state.connecting?'正在刷新…':state.lastChecked?'本次检查 '+esc(sourceTime(state.lastChecked,true)):'等待首次更新'}</span><small>${esc(hint)}</small>`;
}
function renderStatus(){
 const demo=state.mode==='demo',status=state.snapshot.status,label=demo?'演示模式':({loading:'正在连接',cached:'缓存行情',unavailable:'数据不可用',stale:'源数据较旧'}[status]??'公开行情');
 $('source-status').innerHTML=`<i></i>${label}`;$('session-tag').textContent=demo?'固定合成情景 · 非今日实盘':'自动更新 · 跨日切换 · 60 秒刷新';
 const error=state.snapshot.errors?.[0],errorText=error?(typeof error==='string'?error:error.error??error.message??'数据源异常'):'';
 $('data-banner').innerHTML=demo?'<span>◌</span><div><b>当前为演示数据</b>　所有价格与资金流均为合成示例，不代表真实市场。可切回内置免费行情。</div><button id="banner-connect">连接设置 →</button>':`<span>◌</span><div><b>${esc(status==='loading'?'正在连接内置行情':status==='unavailable'?'行情暂不可用':status==='cached'?'当前为缓存数据':status==='stale'?'源日期尚未更新到今天':'已连接行情服务')}</b>　${esc(state.snapshot.source)} · 行情时间：${esc(sourceTime(state.snapshot.as_of))} · 抓取：${esc(state.snapshot.fetched_at?sourceTime(state.snapshot.fetched_at):'尚未取得')} · 上游延迟未承诺${errorText?' · '+esc(errorText.slice(0,180)):''}</div><button id="banner-connect">连接详情 →</button>`;
 $('banner-connect').onclick=openSettings;
 renderDateStatus();
}
function renderAll({reloadCharts=true}={}){if(reloadCharts){state.flowHistory=null;state.flowRequestId++;state.flowLoading=false;}renderStatus();renderTickers();renderInstrument();renderTable();renderFlow();renderAlerts();return reloadCharts?Promise.allSettled([loadChart(),loadFlowHistory()]):Promise.resolve();}
async function refresh(){
 if(state.refreshing||state.connecting)return;const epoch=state.epoch;state.refreshing=true;$('refresh').disabled=true;$('refresh').textContent='↻ 读取中';renderDateStatus();
 // Current-symbol charts start immediately; the broader observation pool may be slower.
 const charts=state.mode==='real'?Promise.allSettled([loadChart(),loadFlowHistory()]):Promise.resolve();
 try{if(state.mode==='demo'){toast('演示情景保持固定；切回内置行情后自动读取最新真实数据。');}else{const fresh=validateSnapshot(await api('/api/snapshot'));if(epoch!==state.epoch)return;state.snapshot=retainFlowSnapshot(state.snapshot,retainQuoteSnapshot(state.snapshot,fresh));state.capabilities=capabilitiesFrom(fresh,state.capabilities);let selectionChanged=false;if(!current()&&fresh.instruments.length){state.selected=fresh.instruments[0].symbol;state.kind=fresh.instruments[0].kind;selectionChanged=true;}await renderAll({reloadCharts:selectionChanged});checkAlerts();}}
 catch(e){if(epoch!==state.epoch)return;state.snapshot=retainFlowSnapshot(state.snapshot,failedSnapshot(state.snapshot,e.message));await renderAll({reloadCharts:false});toast(`报价刷新失败，价格与资金曲线仍独立重试：${e.message.slice(0,100)}`);}
 finally{await charts;if(epoch===state.epoch){state.refreshing=false;state.lastChecked=new Date().toISOString();$('refresh').disabled=false;$('refresh').textContent='↻ 刷新';renderDateStatus();}}
}
function openSettings(){$('gateway').value=state.base;$('gateway-token').value=state.token;$('connection-result').textContent=state.snapshot.status==='unavailable'?'内置行情暂不可用，可重试连接或填写自己的完整 Python 服务地址。':'';$('settings-dialog').showModal();}
async function connect(options={}){
 const automatic=options.automatic===true,value=options.native===true?'':$('gateway').value.trim().replace(/\/$/,'');
 if(value){try{const url=new URL(value);if(!['http:','https:'].includes(url.protocol)||url.username||url.password)throw new Error();if(location.protocol==='https:'&&url.protocol==='http:')throw new Error('HTTPS 页面无法稳定访问 HTTP 服务，请在本地同源打开或配置 HTTPS。');}catch(e){$('connection-result').textContent=e.message.startsWith('HTTPS')?e.message:'请输入完整的 http(s) 服务地址，不在地址中包含凭据。';return;}}
 const epoch=++state.epoch,old={base:state.base,token:state.token,mode:state.mode};state.base=value;state.token=options.native===true?'':$('gateway-token').value;state.connecting=true;state.refreshing=false;$('refresh').disabled=false;$('refresh').textContent='↻ 刷新';$('connect-real').disabled=true;$('connect-native').disabled=true;$('connection-result').textContent='正在检查服务并读取真实行情…';
 const sameSource=old.mode==='real'&&old.base===state.base&&old.token===state.token;
 if(automatic)void loadChart({allowPendingSnapshot:true});
 try{const health=await api('/api/health');if(epoch!==state.epoch)return;if(!health||typeof health!=='object'||health.status!=='ok')throw new Error('行情服务健康检查未通过');const data=validateSnapshot(await api('/api/snapshot'));if(epoch!==state.epoch)return;state.capabilities=capabilitiesFrom(data,capabilitiesFrom(health));state.snapshot=sameSource?retainFlowSnapshot(state.snapshot,retainQuoteSnapshot(state.snapshot,data)):data;state.mode='real';if(value)persist('ml_gateway',value);let selectionChanged=false;if(!current()&&data.instruments.length){state.selected=data.instruments[0].symbol;state.kind=data.instruments[0].kind;selectionChanged=true;}$('settings-dialog').close();await renderAll({reloadCharts:!automatic||selectionChanged});checkAlerts();if(!automatic)toast(data.instruments.some(x=>finite(x.price))?'行情接口已连接；请核对来源与行情时间。':'服务已连接，但上游行情暂不可用。');}
 catch(e){if(epoch!==state.epoch)return;state.base=old.base;state.token=old.token;if(automatic){state.base='';state.token='';state.mode='real';state.capabilities={...NATIVE_CAPABILITIES};state.snapshot=sameSource?retainFlowSnapshot(state.snapshot,failedSnapshot(state.snapshot,e.message)):emptySnapshot('unavailable',e.message);await renderAll({reloadCharts:false});}$('connection-result').textContent=`连接失败：${e.message.slice(0,220)}。${automatic?'保留已取得的真实记录和源时间；价格、资金分时独立更新。':'当前数据模式保持不变。'}`;}
 finally{if(epoch===state.epoch){state.connecting=false;state.lastChecked=new Date().toISOString();$('connect-real').disabled=false;$('connect-native').disabled=false;renderDateStatus();}}
}
/** Freeze selected market input before asynchronous reads; preserve each source date.
 * Real-response runnable example and before/after: docs/INVESTMENT_AGENT.md. */
async function freezeCouncilBundle(question){
 const selected=structuredClone(current()),mode=state.mode,epoch=state.epoch,bundleId=newCouncilId();
 if(!selected)throw new Error('请先选择一个指数或 ETF。');
 const tasks=mode==='demo'?[Promise.resolve({symbol:selected.symbol,kind:selected.kind,bars:demoBars(selected,'intraday').map(row=>({...row,time:'2026-10-20T'+row.time+':00+08:00'})),status:'demo',source:'合成价格演示',as_of:FIXTURE_TIME}),Promise.resolve(demoFlowHistory(selected))]:[api(`/api/history?symbol=${selected.symbol}&kind=${selected.kind}&period=1m`),api(`/api/flow-history?symbol=${selected.symbol}&kind=${selected.kind}`)];
 const result=await Promise.allSettled(tasks);
 if(epoch!==state.epoch||mode!==state.mode)throw new Error('读取期间切换了数据来源，请按当前来源重新准备资料。');
 const snapshot=structuredClone(state.snapshot),latestSelected=snapshot.instruments?.find(row=>row.symbol===selected.symbol&&row.kind===selected.kind)??selected;
 const payload=index=>result[index].status==='fulfilled'?result[index].value:{symbol:selected.symbol,kind:selected.kind,bars:[],status:'unavailable',error:String(result[index].reason?.message??'数据读取失败')};
 return buildCouncilBundle({snapshot,selected:latestSelected,question,price:payload(0),flow:payload(1),bundleId,now:new Date().toISOString()});
}
function changeView(view){
 const config=workspaceView(view);state.view=config.key;
 document.querySelectorAll('.nav[data-view]').forEach(x=>{const active=x.dataset.view===config.key;x.classList.toggle('active',active);if(active)x.setAttribute('aria-current','page');else x.removeAttribute('aria-current');});
 $('page-title').innerHTML=`${esc(config.title)}<span class="small-tag">CN</span>`;$('page-subtitle').textContent=config.subtitle;
 for(const [id,visible] of Object.entries(config.visible))$(id).hidden=!visible;
 document.body.dataset.view=config.key;
 setTimeout(()=>{priceChart?.resize();flowChart?.resize();},0);
}

function openAlerts(){$('alert-symbol').innerHTML=state.snapshot.instruments.map(x=>`<option value="${x.kind}:${x.symbol}">${esc(x.name)} ${x.symbol}</option>`).join('');$('alert-symbol').value=`${state.kind}:${state.selected}`;$('alert-dialog').showModal();}
function saveAlert(){const threshold=Number($('alert-threshold').value);if(!Number.isFinite(threshold)||threshold<=0){toast('阈值必须为正数。');return;}const [kind,symbol]=$('alert-symbol').value.split(':');if(!symbol){toast('请先获取可用标的。');return;}state.rules.push({id:Date.now(),kind,symbol,threshold});persist('ml_alerts',state.rules);$('alert-dialog').close();renderAlerts();checkAlerts();toast('监控规则已保存，页面打开时生效。');}
function checkAlerts(){if(state.mode==='real'&&['loading','unavailable','cached','stale'].includes(state.snapshot.status))return;for(const rule of state.rules){const item=state.snapshot.instruments.find(x=>x.symbol===rule.symbol&&x.kind===rule.kind),stamp=item?.as_of??state.snapshot.as_of;if(!item||!finite(item.change_pct)||Math.abs(item.change_pct)<rule.threshold||state.mode==='real'&&(['loading','unavailable','cached','stale'].includes(item.status)||!stamp))continue;const key=`${rule.id}:${state.mode}:${stamp}`;if(state.events.some(x=>x.key===key))continue;state.events.push({key,name:item.name,change:item.change_pct,time:stamp??'时间未知',mode:state.mode});}state.events=state.events.slice(-100);persist('ml_alert_events',state.events);renderAlerts();}
function renderAlerts(){$('alert-dot').style.display=state.events.length?'block':'none';$('alert-rules').innerHTML=state.rules.length?state.rules.map(r=>`<div class="alert-rule"><b>${esc(r.symbol)}</b><span>绝对涨跌幅 ≥ ${number(r.threshold)}%</span><button data-remove="${r.id}">移除规则</button></div>`).join(''):'<p class="footnote">暂无监控规则。点击「新建规则」添加关注标的。</p>';$('alert-rules').querySelectorAll('[data-remove]').forEach(b=>b.onclick=()=>{state.rules=state.rules.filter(r=>String(r.id)!==b.dataset.remove);persist('ml_alerts',state.rules);renderAlerts();});$('alert-log').innerHTML=state.events.length?state.events.slice(-20).reverse().map(e=>`<div class="alert-event">${esc(e.name)} 涨跌幅达到 <span class="${cls(e.change)}">${signed(e.change)}%</span><small>${esc(e.time)} · ${e.mode==='demo'?'合成演示事件':'真实数据快照事件'}</small></div>`).join(''):'<p class="footnote">尚无触发事件。</p>';}
function renderResearch(){const repos=[['Apache ECharts','67.6k','Apache-2.0','已采用 · 本地图表库 6.1.0','apache/echarts','分时 / K线、量价对照、资金流排行与交互缩放。'],['AKShare','23.0k','MIT','已实现后端适配','akfamily/akshare','中国指数、ETF、历史行情和东方财富资金字段；需本地服务联网。'],['LangGraph','43.6k','MIT','后端可选编排','langchain-ai/langgraph','组织趋势、资金与风险视角；工作流本身不等于训练过的金融模型。'],['TradingAgents','112k','Apache-2.0','研究参考 · 未整体集成','TauricResearch/TradingAgents','借鉴角色拆分与保留分歧的研究流程，并非已安装完整 TradingAgents 终端。'],['Microsoft Qlib','49.6k','MIT','训练扩展参考 · 未集成','microsoft/qlib','后续特征、时间切分、预测、回测与评估；当前提供独立分类基线。'],['OpenBB','74.5k','Apache-2.0（V5）','多市场数据扩展参考','openbq-org/OpenBB','统一数据服务与适配器设计。商业 Workspace 界面不等于开源仓库。']];$('repo-cards').innerHTML=repos.map(r=>`<div class="repo-card"><div><a href="https://github.com/${r[4]}" target="_blank" rel="noopener">${r[0]} ↗</a><span>☆ ${r[1]} · ${r[2]}</span></div><p><b style="color:#bfd0b0;font-weight:400">${r[3]}</b><br>${r[5]}</p></div>`).join('');}
function bindUI(){$('advice-symbol').onchange=()=>{const [kind,symbol]=$('advice-symbol').value.split(':');selectInstrument(symbol,kind);};$('advice-market').onclick=()=>changeView('overview');$('paired-symbol').onchange=()=>{const [kind,symbol]=$('paired-symbol').value.split(':');selectInstrument(symbol,kind);};['open-settings','settings-side','source-status'].forEach(id=>$(id).onclick=openSettings);$('banner-connect').onclick=openSettings;$('connect-real').onclick=()=>connect();$('connect-native').onclick=()=>connect({native:true});$('use-demo').onclick=()=>{state.epoch++;state.connecting=false;$('connect-real').disabled=false;$('connect-native').disabled=false;state.mode='demo';state.snapshot=demoSnapshot();$('settings-dialog').close();renderAll();toast('已切换到明确标记的合成演示数据。');};$('refresh').onclick=()=>{void macroReader?.refresh();return refresh();};$('search').oninput=renderTable;$('kind-filter').onchange=renderTable;$('flow-mode').onchange=()=>{renderFlow();loadFlowHistory();};$('flow-symbol').onchange=()=>{state.flowSymbol=$('flow-symbol').value;state.flowHistory=null;state.flowRequestId++;renderFlow();loadFlowHistory();};$('favorite').onclick=()=>toggleFavorite(state.selected,state.kind);$('compare').onchange=loadChart;$('flow-expand').onclick=()=>{$('flow-panel').classList.toggle('expanded');flowChart?.resize();};$('chart-expand').onclick=()=>{$('chart-panel').classList.toggle('expanded');priceChart?.resize();};document.addEventListener('keydown',e=>{if(e.key==='Escape'){$('chart-panel').classList.remove('expanded');$('flow-panel').classList.remove('expanded');priceChart?.resize();flowChart?.resize();}});document.querySelectorAll('[data-period]').forEach(b=>b.onclick=()=>{state.period=b.dataset.period;document.querySelectorAll('[data-period]').forEach(x=>x.classList.toggle('active',x===b));loadChart();});document.querySelectorAll('[data-sort]').forEach(b=>b.onclick=()=>{state.direction=state.sort===b.dataset.sort?-state.direction:-1;state.sort=b.dataset.sort;renderTable();});document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>changeView(b.dataset.view));$('export-snapshot').onclick=()=>download('market-lens-snapshot.json',JSON.stringify(state.snapshot,null,2));$('add-alert').onclick=openAlerts;$('open-alerts').onclick=openAlerts;$('save-alert').onclick=saveAlert;window.addEventListener('resize',()=>{priceChart?.resize();flowChart?.resize();});}
state.snapshot=emptySnapshot();bindUI();initInvestmentPanel({getBundle:freezeCouncilBundle,download,toast});macroReader=initMacroPanel();void macroReader.refresh();renderResearch();renderAll();changeView('overview');
// Calendar changes invalidate freshness even before today's first source tick.
const refreshController=createRefreshController({refresh,isActive:()=>state.mode==='real',isVisible:()=>!document.hidden,isBusy:()=>state.refreshing||state.connecting,onDateChange:()=>{
 if(state.mode==='demo'){renderDateStatus();return;}
 state.snapshot=withMarketFreshness(state.snapshot);state.priceHistory=withMarketFreshness(state.priceHistory);state.pairedFlowHistory=withMarketFreshness(state.pairedFlowHistory);
 renderStatus();renderTickers();renderInstrument();renderTable();renderFlow();
}});
function tickClock(){
 $('clock').textContent=new Date().toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'})+' CST';
 void refreshController.tick();
 if(macroCalendarDay!==shanghaiDate()){macroCalendarDay=shanghaiDate();macroReader?.redraw();void macroReader?.refresh();}
}
tickClock();setInterval(tickClock,1000);
setInterval(()=>{if(!document.hidden)void macroReader.refresh();},60000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden){void refreshController.resume();void macroReader.refresh();}});
for(const event of ['focus','pageshow','online'])window.addEventListener(event,()=>{void refreshController.resume();void macroReader.refresh();});
// Only this Site's same-origin service is discovered automatically. Custom gateways require an explicit connection.
connect({automatic:true,native:true});
