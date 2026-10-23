/** Investment council handoff. Purpose, real input, runnable example, output and
 * before/after effects for these functions: docs/INVESTMENT_AGENT.md.
 * Only selected public market fields cross the window boundary; no credentials.
 */
import {shanghaiDate,withMarketFreshness} from './market-time.mjs';
export const COUNCIL_PROTOCOL = 'market-lens.council.v1';
/** Generate an unpredictable channel in HTTPS and loopback/preview browsers. */
export function newCouncilId() {
  if (typeof globalThis.crypto?.getRandomValues !== 'function') throw new Error('浏览器不支持安全随机数，请使用新版浏览器。');
  const bytes=globalThis.crypto.getRandomValues(new Uint8Array(16));
  return [...bytes].map(value=>value.toString(16).padStart(2,'0')).join('');
}
const pick = (object, keys) => Object.fromEntries(keys.filter(key => object?.[key] !== undefined).map(key => [key, structuredClone(object[key])]));
const FRESHNESS=['today','source_date','freshness','source_status'];
const META = ['symbol','kind','source','status','as_of','fetched_at','trade_date','unit','stale','error','note',...FRESHNESS];
const QUOTE = ['symbol','kind','name','price','change_pct','amount','volume','main_net','main_pct','as_of','fetched_at','source','status','stale','unit','flow_as_of','flow_status','flow_source','flow_stale',...FRESHNESS];

/** Accept a loopback HTTP origin only; never send the market bundle to a remote host. */
export function councilOrigin(value) {
  const url = new URL(value);
  if (url.protocol !== 'http:' || !['localhost','127.0.0.1','[::1]'].includes(url.hostname) || url.username || url.password || url.pathname !== '/' || url.search || url.hash) throw new Error('请填写本机地址，例如 http://127.0.0.1:4317');
  return url.origin;
}

/** Freeze a genuine response, with missing data kept missing and demo explicit. */
export function buildCouncilBundle({snapshot, selected, question='', price, flow, bundleId, now=new Date().toISOString()}) {
  if (!snapshot || !selected || !['index','etf'].includes(selected.kind) || !/^\d{6}$/.test(selected.symbol)) throw new Error('请先选择一个指数或 ETF。');
  if (!['real','demo'].includes(snapshot.mode) || !Number.isFinite(Date.parse(now))) throw new Error('数据模式或冻结时间无效。');
  const clock=Date.parse(now),today=shanghaiDate(clock),demo=snapshot.mode==='demo';
  const history = (value, keys) => {
    if (value?.symbol && value.symbol !== selected.symbol || value?.kind && value.kind !== selected.kind) throw new Error('历史数据与选定标的不一致，请重试。');
    const bars = Array.isArray(value?.bars) ? value.bars : [];
    if (bars.length > 260) throw new Error('当日序列超出 260 点，未截断数据；请检查来源。');
    return withMarketFreshness({...pick(value, META),symbol:selected.symbol,kind:selected.kind,status:value?.status??'unavailable',bars:bars.map(bar=>pick(bar,keys))},clock);
  };
  const priceHistory={...history(price,['time','open','high','low','close','volume','amount','cumulative_volume','cumulative_amount','source_time']),period:'intraday'};
  const flowHistory=history(flow,['time','main_net','super_net','large_net','medium_net','small_net','source_time']);
  const priceDay=priceHistory.bars.length?priceHistory.source_date:null,flowDay=flowHistory.bars.length?flowHistory.source_date:null;
  if(!demo&&priceDay&&flowDay&&priceDay!==flowDay)throw new Error(`价格分时为 ${priceDay}，资金分时为 ${flowDay}，尚未取得同日资料，请刷新后重试；未跨日拼接。`);
  const quote=withMarketFreshness(pick(selected,QUOTE),clock),quoteDay=shanghaiDate(quote.as_of??null),seriesDay=priceDay??flowDay;
  if(!demo&&!priceHistory.bars.length&&!flowHistory.bars.length&&!(typeof quote.price==='number'&&Number.isFinite(quote.price)&&quote.price>0))throw new Error('尚未取得可分析的真实报价或分时记录，请稍后重试。');
  const quoteMismatch=!demo&&quoteDay&&seriesDay&&quoteDay!==seriesDay;
  const frozenSnapshot={...pick(withMarketFreshness(snapshot,clock),['mode','status','source','as_of','fetched_at','stale',...FRESHNESS]),
    selected:quoteMismatch?{...pick(quote,['symbol','kind','name']),status:'unavailable',as_of:null,price:null,change_pct:null,amount:null,volume:null,main_net:null,main_pct:null,
      error:`最新报价仍为 ${quoteDay}，与本次 ${seriesDay} 分时不同日；原报价见 historical_selected`}:quote,
    instruments:(snapshot.instruments??[]).map(item=>withMarketFreshness(pick(item,QUOTE),clock)),
    ...(quoteMismatch?{historical_selected:quote}:{}),
  };
  const bundle = {
    schema_version:COUNCIL_PROTOCOL,bundle_id:bundleId,analysis_asof:now,timezone:'Asia/Shanghai',mode:snapshot.mode,
    selected:pick(selected,['symbol','kind','name']),
    question:question.trim() || `请结合 ${selected.name}（${selected.symbol}）的价格与资金分时，分别检查宏观环境、盘面与波段条件、基本面证据。列出事实、竞争解释、触发与失效条件、资料缺口；保留分歧。`,
    snapshot:frozenSnapshot,price_history:priceHistory,fund_flow_history:flowHistory,
    source_dates:{today,quote:quoteDay,price:priceDay,fund_flow:flowDay,quote_matches_series:!quoteMismatch,
      all_current_day:!demo&&[quoteDay,priceDay,flowDay].every(day=>day===today)},
    data_boundaries:['资金为供应商按成交单大小统计的当日累计净额，不代表机构身份或 ETF 申赎。','价格和资金保留各自源时间，抓取和冻结时间不作为市场时间。',
      `北京时间今天为 ${today}；报价源日期 ${quoteDay??'未知'}，价格分时 ${priceDay??'未取得'}，资金分时 ${flowDay??'未取得'}。非今日或缓存记录不得描述为今日实时行情。`,
      ...(quoteMismatch?['选定标的的异日报价仅保存在 historical_selected，当前报价保持空值；不得用该旧报价计算本次分时的涨跌幅、成交额或资金比例。']:[]),
      '没有提供当前宏观、财报、成分权重或持仓约束时，相应结论须标注证据不足。','两个宏观相关 Skill 共用部分方法与来源，不构成两份独立确认。'],
  };
  if (bundle.question.length > 6000) throw new Error('研究问题最多 6000 字。');
  if (new TextEncoder().encode(JSON.stringify(bundle)).length > 250000) throw new Error('读盘资料超过 250KB，未自动删减，请缩小研究范围。');
  return structuredClone(bundle);
}

/** A message is accepted only from this popup, origin and fresh channel. */
export function acceptsCouncilMessage(event, session) {
  return !!session && event.source === session.popup && event.origin === session.origin && event.data?.protocol === COUNCIL_PROTOCOL && event.data?.channel === session.channel && ['ready','state','run','error'].includes(event.data.type);
}
