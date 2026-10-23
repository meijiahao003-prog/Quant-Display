import { withMarketFreshness } from "../../../public/market-time.mjs";
import { getDurableFlowHistory } from "../../../lib/durable-market";

/** Prefer provider backfill; retain its genuine minute sequence across cold starts.
 * Real endpoint input/output and processing examples: docs/INTRADAY_FLOW.md.
 */
export async function GET(request: Request) {
  const params = new URL(request.url).searchParams;
  const symbol = params.get("symbol") ?? "510300";
  const kind = params.get("kind") ?? undefined;
  const headers = { "Cache-Control": "no-store" };
  try {
    return Response.json(await getDurableFlowHistory(symbol, kind), {headers});
  } catch (error) {
    const value = error as Error & { status?: number };
    return Response.json(withMarketFreshness({ status: "unavailable", error: value.message, bars: [] }), {
      status: value.status === 400 ? 400 : 500, headers,
    });
  }
}
