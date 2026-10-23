import { withMarketFreshness } from "../../../public/market-time.mjs";
import { getWatchingSnapshot } from "../../../lib/watching-data.mjs";
import { saveFlowSnapshot, getStoredEtfFlowRows } from "../../../lib/flow-store";
import { getRecoveredEtfFlow } from "../../../lib/etf-flow-recovery.mjs";
import { getDurableSnapshot, getDurableFlowHistory } from "../../../lib/durable-market";

/** Fixed observation pool; source and fetch timestamps stay separate. */
export async function GET() {
  const {snapshot, flow, indexHistories} = await getWatchingSnapshot({
    getQuotes: getDurableSnapshot,
    getIndexFlow: getDurableFlowHistory,
    getEtfFlow: () => getRecoveredEtfFlow({getStoredRows: getStoredEtfFlowRows}),
  });
  const storage = await saveFlowSnapshot(flow);
  return Response.json(withMarketFreshness({...snapshot,flow_storage_status:storage.status,flow_storage_error:storage.error??null,
    index_flow_storage_status:indexHistories.every((history: { storage_status?: string })=>history.storage_status==='ok')?'ok':'partial'}), { headers: { "Cache-Control": "no-store" } });
}
