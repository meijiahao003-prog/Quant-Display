import { getDurableSnapshot, getDurableHistory, getDurableFlowHistory, readStoredMarket, marketHistoryKey } from '../../../lib/durable-market';
import { getStoredIntradayFlowHistory } from '../../../lib/intraday-flow-store';
import { getStoredFlowHistory } from '../../../lib/flow-store';
import { selectFlowFallback } from '../../../lib/flow-recovery.mjs';
import { refreshTarget, refreshSummary, REFRESH_PROTOCOL } from '../../../lib/refresh-protocol.mjs';

const headers = { 'Cache-Control': 'no-store' };

/** Read durable observations only. The owner-private Sites gateway authenticates
 * unattended service calls; fixed allowlisted market data needs no visitor identity.
 * Inputs/calls/outputs/before-after: docs/BACKGROUND_UPDATES.md. */
export async function GET(request: Request) {
  try {
    const item = refreshTarget(request.url);
    if (!item) return Response.json({ ...REFRESH_PROTOCOL, checked_at: new Date().toISOString(),
      quotes: refreshSummary(await readStoredMarket('quote:snapshot')), readback: 'persistent_storage' }, {headers});
    const [minute, daily, storedFlow, sampledFlow] = await Promise.all([
      readStoredMarket(marketHistoryKey(item.symbol,item.kind,'1m')),
      readStoredMarket(marketHistoryKey(item.symbol,item.kind,'day')),
      getStoredIntradayFlowHistory(item.symbol,item.kind),
      item.kind==='etf'?getStoredFlowHistory(item.symbol):Promise.resolve(null),
    ]);
    const flow=selectFlowFallback({upstream:null,stored:storedFlow,sampled:sampledFlow,item});
    return Response.json({symbol:item.symbol,kind:item.kind,readback:'persistent_storage',
      minute:refreshSummary(minute),daily:refreshSummary(daily),flow:refreshSummary(flow)}, {headers});
  } catch (error) {
    return Response.json({status:'unavailable',error:error instanceof Error?error.message:'读取失败'}, {status:400,headers});
  }
}

/** Idempotent fixed-source update, callable while the dashboard is closed.
 * Every operation awaits storage; callers verify using GET on the same URL. */
export async function POST(request: Request) {
  const origin=request.headers.get('origin');
  if(origin&&origin!==new URL(request.url).origin)return Response.json({error:'来源不匹配'}, {status:403,headers});
  try {
    const item=refreshTarget(request.url);
    if(!item)return Response.json({quotes:refreshSummary(await getDurableSnapshot()),updated_at:new Date().toISOString()}, {headers});
    const results=await Promise.allSettled([
      getDurableHistory(item.symbol,item.kind,'1m'),getDurableHistory(item.symbol,item.kind,'day'),
      getDurableFlowHistory(item.symbol,item.kind),
    ]);
    const values=results.map(result=>refreshSummary(result.status==='fulfilled'?result.value:
      {status:'unavailable',error:result.reason instanceof Error?result.reason.message:'数据刷新失败'}));
    return Response.json({symbol:item.symbol,kind:item.kind,updated_at:new Date().toISOString(),
      minute:values[0],daily:values[1],flow:values[2]}, {headers});
  } catch(error) {
    return Response.json({status:'unavailable',error:error instanceof Error?error.message:'更新失败'}, {status:400,headers});
  }
}
