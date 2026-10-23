import { and, desc, eq, lt, sql } from "drizzle-orm";
import { getDb } from "../db";
import { fundFlowSamples } from "../db/schema";
import { UNIVERSE } from "./market-data.mjs";

const ETF_SYMBOLS = new Set(UNIVERSE.filter(item => item.kind === "etf").map(item => item.symbol));
const SOURCE = "东方财富 ETF 资金 · 本站实际采集";
const NOTE = "本站按页面刷新采集源时间对应的资金净额，同一分钟保留最后一次源观测；并非完整全日历史，空档不补值。正负值为净额，不是独立的买入或卖出总额。";

type FlowRow = {
  symbol?: string;
  status?: string;
  stale?: boolean;
  source?: string;
  fetched_at?: string | null;
  unit?: string;
  as_of?: string | null;
  trade_date?: string | null;
  main_net?: number | null;
  super_net?: number | null;
  large_net?: number | null;
  medium_net?: number | null;
  small_net?: number | null;
};
type FlowSnapshot = {
  status?: string;
  stale?: boolean;
  source?: string;
  fetched_at?: string | null;
  rows?: FlowRow[];
};

const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const nullableNumber = (value: unknown) => finite(value) ? value : null;
const chinaDate = (epoch: number) => new Date(epoch + 8 * 60 * 60 * 1000).toISOString().slice(0, 10);

/** Persist only genuine, timestamped live observations; safe to call after every snapshot.
 * Source time, not request time, determines identity. See docs/EASTMONEY_FLOW.md.
 */
export async function saveFlowSnapshot(payload: FlowSnapshot) {
  if (!["live", "partial"].includes(payload.status ?? "") || payload.stale) {
    return { status: "ok", saved: 0, skipped: "非新鲜源数据，不追加采样点" };
  }
  const fetchedEpoch = Date.parse(payload.fetched_at ?? "");
  if (!Number.isFinite(fetchedEpoch)) return { status: "unavailable", saved: 0, error: "资金采集时间缺失，未写入历史" };
  const rows = (payload.rows ?? []).flatMap(row => {
    const sourceEpoch = Date.parse(row.as_of ?? "");
    if (!row.symbol || !ETF_SYMBOLS.has(row.symbol) || !["live", "partial"].includes(row.status ?? "") || row.stale || row.unit !== "CNY" ||
      !finite(row.main_net) || !Number.isFinite(sourceEpoch) || chinaDate(sourceEpoch) !== row.trade_date) return [];
    return [{
      symbol: row.symbol, minuteBucket: Math.floor(sourceEpoch / 60_000), tradeDate: row.trade_date!,
      sourceTime: row.as_of!, sourceEpoch, fetchedAt: Number.isFinite(Date.parse(row.fetched_at ?? "")) ? row.fetched_at! : payload.fetched_at!, source: row.source || payload.source || SOURCE,
      mainNet: row.main_net, superNet: nullableNumber(row.super_net), largeNet: nullableNumber(row.large_net),
      mediumNet: nullableNumber(row.medium_net), smallNet: nullableNumber(row.small_net),
    }];
  });
  if (!rows.length) return { status: "ok", saved: 0, skipped: "没有可保存的实时 ETF 资金记录" };
  try {
    const db = getDb();
    // Ten known ETFs keep this batch bounded. Do not replace a source sample with an older response.
    const mutations = rows.slice(0, ETF_SYMBOLS.size).map(row => db.insert(fundFlowSamples).values(row).onConflictDoUpdate({
      target: [fundFlowSamples.symbol, fundFlowSamples.minuteBucket],
      set: {
        tradeDate: row.tradeDate, sourceTime: row.sourceTime, sourceEpoch: row.sourceEpoch,
        fetchedAt: row.fetchedAt, source: row.source, mainNet: row.mainNet, superNet: row.superNet,
        largeNet: row.largeNet, mediumNet: row.mediumNet, smallNet: row.smallNet,
      },
      setWhere: lt(fundFlowSamples.sourceEpoch, row.sourceEpoch),
    }));
    // All writes and retention cleanup are one D1 batch; schema is migration-owned.
    const cutoff = chinaDate(fetchedEpoch - 30 * 24 * 60 * 60 * 1000);
    const cleanup = db.delete(fundFlowSamples).where(lt(fundFlowSamples.tradeDate, cutoff));
    const results = await db.batch([mutations[0], ...mutations.slice(1), cleanup]);
    return { status: "ok", saved: results.slice(0, -1).reduce((count, result) => count + (result.meta?.changes ?? 0), 0) };
  } catch {
    return { status: "unavailable", saved: 0, error: "资金历史存储暂不可用；即时净额不受影响" };
  }
}

/** Recover each ETF's last observed amounts from both durable sources. Reads at
 * most two rows per ETF using indexed source-time lookups, never previous prices.
 * Executable examples and observed output: docs/ETF_FLOW_RECOVERY.md. */
export async function getStoredEtfFlowRows() {
  try {
    const symbols = sql.join([...ETF_SYMBOLS].map(symbol => sql`${symbol}`), sql`, `);
    const rows = await getDb().all<Record<string, unknown>>(sql`
      SELECT s.symbol, s.source_time AS as_of, s.trade_date, s.fetched_at, s.source,
        s.main_net, s.super_net, s.large_net, s.medium_net, s.small_net,
        'stored_snapshot' AS source_type
      FROM fund_flow_samples s
      WHERE s.symbol IN (${symbols}) AND s.source_epoch = (
        SELECT MAX(p.source_epoch) FROM fund_flow_samples p WHERE p.symbol = s.symbol)
      UNION ALL
      SELECT s.symbol, s.source_time AS as_of, s.trade_date, s.fetched_at, s.source,
        s.main_net, s.super_net, s.large_net, s.medium_net, s.small_net,
        'stored_provider_minutes' AS source_type
      FROM intraday_fund_flow_bars s
      WHERE s.kind = 'etf' AND s.symbol IN (${symbols}) AND s.source_epoch = (
        SELECT MAX(p.source_epoch) FROM intraday_fund_flow_bars p WHERE p.kind = 'etf' AND p.symbol = s.symbol)
    `);
    return {status: "ok", rows: rows.map(row => ({...row, kind: "etf", unit: "CNY", status: "cached", source_status: "cached", stale: true, main_pct: null}))};
  } catch {
    return {status: "unavailable", rows: [], error: "已保存的 ETF 资金暂时无法读取"};
  }
}

/** Read the latest recorded trading day, preserving missing periods and source timestamps. */
export async function getStoredFlowHistory(symbol: string) {
  if (!ETF_SYMBOLS.has(symbol)) {
    const error = new Error("仅支持观察池中的 ETF 资金历史") as Error & { status: number };
    error.status = 400;
    throw error;
  }
  const base = { symbol, source: SOURCE, unit: "CNY", bars: [], as_of: null, trade_date: null, note: NOTE };
  try {
    const db = getDb();
    const latest = await db.select({ tradeDate: fundFlowSamples.tradeDate }).from(fundFlowSamples)
      .where(eq(fundFlowSamples.symbol, symbol)).orderBy(desc(fundFlowSamples.tradeDate)).limit(1);
    if (!latest.length) return { ...base, status: "empty", note: `${NOTE} 暂无采样，请先刷新资金行情。` };
    const day = latest[0].tradeDate;
    const rows = await db.select().from(fundFlowSamples)
      .where(and(eq(fundFlowSamples.symbol, symbol), eq(fundFlowSamples.tradeDate, day)))
      .orderBy(desc(fundFlowSamples.sourceEpoch)).limit(500);
    rows.reverse();
    return {
      ...base, status: "recorded", trade_date: day, as_of: rows.at(-1)?.sourceTime ?? null,
      source: rows.at(-1)?.source ?? SOURCE,
      fetched_at: rows.map(row => row.fetchedAt).sort().at(-1) ?? null,
      bars: rows.map(row => ({ time: row.sourceTime, main_net: row.mainNet, super_net: row.superNet,
        large_net: row.largeNet, medium_net: row.mediumNet, small_net: row.smallNet, fetched_at: row.fetchedAt })),
    };
  } catch {
    return { ...base, status: "unavailable", error: "资金历史存储暂不可用", note: `${NOTE} 目前无法读取历史存储。` };
  }
}
