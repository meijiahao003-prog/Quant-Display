import { readMacroBrief, writeMacroBrief } from '../../../lib/macro-store';
import { macroResponse, normalizeMacroWrite, readMacroBody } from '../../../lib/macro-brief.mjs';

const headers = { 'Cache-Control': 'no-store' };

/** Persistent macro conclusion and executable updater contract. The owner-private
 * Sites gateway authenticates unattended callers, as for /api/refresh. */
export async function GET() {
  try { return Response.json(await readMacroBrief(), { headers }); }
  catch (error) { return Response.json({ ...macroResponse(null), ok: false, error: error instanceof Error ? error.message : '读取宏观先导失败' }, { status: 503, headers }); }
}

/** Validated sourced report writer; same-day retries and stale responses cannot
 * erase a newer report. See docs/MACRO_API.md for actual-input examples. */
export async function POST(request: Request) {
  const origin = request.headers.get('origin');
  if (origin && origin !== new URL(request.url).origin) return Response.json({ ok: false, error: '来源不匹配' }, { status: 403, headers });
  let input;
  try { input = normalizeMacroWrite(await readMacroBody(request)); }
  catch (error) { return Response.json({ ok: false, error: error instanceof Error ? error.message : '宏观先导正文无效' }, { status: 400, headers }); }
  try {
    const write = await writeMacroBrief(input);
    return Response.json({ ...await readMacroBrief(), write }, { headers });
  } catch (error) { return Response.json({ ok: false, error: error instanceof Error ? error.message : '保存宏观先导失败' }, { status: 503, headers }); }
}
