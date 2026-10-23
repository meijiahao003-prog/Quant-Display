import { getWatchingHealth } from "../../../lib/watching-data.mjs";

/** Same-origin capability discovery; no external data request or credential. */
export function GET() {
  return Response.json(getWatchingHealth(), { headers: { "Cache-Control": "no-store" } });
}
