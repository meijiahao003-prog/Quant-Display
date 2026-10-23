import {UNIVERSE} from './market-data.mjs';
import {shanghaiDate,withMarketFreshness} from '../public/market-time.mjs';

/** Fixed-source updater protocol; no arbitrary URL, date override, or credentials.
 * Inputs, executable call and observed output: docs/BACKGROUND_UPDATES.md. */
export function refreshTarget(url){
  const params=new URL(url).searchParams,symbol=params.get('symbol'),kind=params.get('kind');
  if(!symbol&&!kind)return null;
  const item=UNIVERSE.find(row=>row.symbol===symbol&&row.kind===kind);
  if(!item)throw new Error('不支持的标的或品种');
  return item;
}

/** Summarize persisted/source evidence without treating HTTP 200 as fresh data.
 * Old data keeps its source/fetch times. Missing data remains unavailable. */
export function refreshSummary(payload,now=Date.now()){
  const value=withMarketFreshness(payload,now);
  return {status:value?.status??'unavailable',today:shanghaiDate(now),source_date:value?.source_date??null,
    freshness:value?.freshness??'unknown',as_of:value?.as_of??null,fetched_at:value?.fetched_at??null,
    source_type:value?.source_type??null,
    points:Array.isArray(value?.bars)?value.bars.length:undefined,
    quotes:Array.isArray(value?.instruments)?value.instruments.filter(row=>Number.isFinite(row.price)).length:undefined,
    current_quotes:Array.isArray(value?.instruments)?value.instruments.filter(row=>Number.isFinite(row.price)&&row.source_date===shanghaiDate(now)).length:undefined,
    storage_status:value?.storage_status??null,storage_saved:value?.storage_saved??null,error:value?.error??value?.storage_error??null};
}

export const REFRESH_PROTOCOL=Object.freeze({
  version:2,timezone:'Asia/Shanghai',universe:UNIVERSE.map(({symbol,kind,name})=>({symbol,kind,name})),
  macro:{endpoint:'/api/macro',workflow:'/macro-workflow.json',sourcePageUrl:'/macro-source.html',independentMarketRefresh:true},
  instructions:[
    '通过 Sites get_site 取得当前本站服务访问令牌，仅放入本站请求的 OAI-Sites-Authorization: Bearer 请求头；不写入文件、日志、任务提示或其他站点。',
    'GET /api/macro 核对当天宏观先导；晨间任务先按 /macro-workflow.json 更新宏观资料、研究结论并读回，再更新行情。盘中任务只核对宏观状态，不重复启动全量宏观采集；宏观失败不阻塞行情，也不把旧结论改成今天。',
    '先 POST /api/refresh 刷新16个报价；再对 universe 的每个标的 POST /api/refresh?symbol=代码&kind=品种。每个标的独立刷新价格分时、日线和资金分时并持久保存。最多并发4个标的，请求超时75秒。请求不需要正文。',
    'POST后对相同URL执行GET，读取持久保存结果；GET只读存储，不抓上游。以源时间、source_date和点数判断数据，以storage_status判断保存；HTTP成功不代表已取得今天数据。',
    '对请求失败、保存失败或今天缺失的数据，所有标的首轮结束后只重试1次；针对同一标的保留原有真实时间，不改写旧数据为今天。休市日照实保留最近源日，不补造数值。',
    '结束后GET /api/refresh汇总持久报价；记录各标的分时、日线及资金的源日期与缺失项。常规更新只写数据，不修改源码、不重新发布。',
  ],
});
