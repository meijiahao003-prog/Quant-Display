import { getDurableHistory } from "../../../lib/durable-market";

/** Only the module's allowlisted assets/periods can cause upstream requests. */
export async function GET(request: Request) {
  const params = new URL(request.url).searchParams;
  try {
    return Response.json(await getDurableHistory(params.get("symbol") ?? "", params.get("kind") ?? "", params.get("period") || "1m"), { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    return Response.json({ detail: error instanceof Error ? error.message : "行情参数无效" }, { status: 400 });
  }
}
