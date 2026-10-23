import { and, desc, eq, lt, sql } from "drizzle-orm";
import { getDb } from "../db";
import { intradayFundFlowBars } from "../db/schema";
import { resolveIntradayInstrument } from "./intraday-flow.mjs";

type FlowBar = {
  time?: string;
  main_net?: number | null;
  super_net?: number | null;
  large_net?: number | null;
  medium_net?: number | null;
  small_net?: number | null;
};
type FlowHistory = {
  symbol?: string;
  kind?: string;
  scope?: string;
  unit?: string;
  status?: string;
  stale?: boolean;
  source?: string;
  trade_date?: string | null;
  fetched_at?: string | null;
  bars?: FlowBar[];
};

const SOURCE = "东方财富 · 当日资金分时";
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const nullable = (value: unknown) => finite(value) ? value : null;
const chinaDate = (epoch: number) => new Date(epoch + 8 * 60 * 60 * 1000).toISOString().slice(0, 10);

/** Store only genuine upstream minute-history observations; never mix snapshot rows.
 * Parameters, real input, runnable call, output and changes: docs/INTRADAY_FLOW.md.
 */
export async function saveIntradayFlowHistory(payload: FlowHistory) {
  if (!["live", "partial"].includes(payload.status ?? "") || payload.stale) {
    return { status: "ok", saved: 0, skipped: "非新鲜资金分时，不重复保存缓存" };
  }
  const item = resolveIntradayInstrument(payload.symbol, payload.kind);
  const fetchedEpoch = Date.parse(payload.fetched_at ?? "");
  if (payload.unit !== "CNY" || payload.scope !== "instrument" || !Number.isFinite(fetchedEpoch) ||
    !/^\d{4}-\d{2}-\d{2}$/.test(payload.trade_date ?? "") || !Array.isArray(payload.bars) || payload.bars.length > 500) {
    return { status: "unavailable", saved: 0, error: "资金分时存储元数据无效" };
  }
  const latestByMinute = new Map<number, typeof intradayFundFlowBars.$inferInsert>();
  for (const bar of payload.bars) {
    const epoch = Date.parse(bar.time ?? "");
    if (!finite(bar.main_net) || !Number.isFinite(epoch) || chinaDate(epoch) !== payload.trade_date) continue;
    const minuteBucket = Math.floor(epoch / 60_000);
    const old = latestByMinute.get(minuteBucket);
    if (old && old.sourceEpoch > epoch) continue;
    latestByMinute.set(minuteBucket, {
      kind: item.kind, symbol: item.symbol, minuteBucket, tradeDate: payload.trade_date!,
      sourceTime: bar.time!, sourceEpoch: epoch, fetchedAt: payload.fetched_at!,
      source: payload.source || SOURCE, mainNet: bar.main_net,
      superNet: nullable(bar.super_net), largeNet: nullable(bar.large_net),
      mediumNet: nullable(bar.medium_net), smallNet: nullable(bar.small_net),
    });
  }
  const rows = [...latestByMinute.values()];
  if (!rows.length) return { status: "ok", saved: 0, skipped: "没有有效资金分时记录" };
  try {
    const db = getDb();
    let saved = 0;
    // A retry with unchanged amounts is read-only. Same-minute source corrections
    // are allowed, but an earlier in-flight request cannot overwrite a later one.
    for (let start = 0; start < rows.length; start += 80) {
      const writes = rows.slice(start, start + 80).map(row => db.insert(intradayFundFlowBars).values(row).onConflictDoUpdate({
        target: [intradayFundFlowBars.kind, intradayFundFlowBars.symbol, intradayFundFlowBars.minuteBucket],
        set: {
          tradeDate: row.tradeDate, sourceTime: row.sourceTime, sourceEpoch: row.sourceEpoch,
          fetchedAt: row.fetchedAt, source: row.source, mainNet: row.mainNet,
          superNet: row.superNet, largeNet: row.largeNet, mediumNet: row.mediumNet, smallNet: row.smallNet,
        },
        setWhere: sql`${intradayFundFlowBars.fetchedAt} <= ${row.fetchedAt} AND (
          ${intradayFundFlowBars.mainNet} IS NOT ${row.mainNet} OR
          ${intradayFundFlowBars.superNet} IS NOT ${row.superNet} OR
          ${intradayFundFlowBars.largeNet} IS NOT ${row.largeNet} OR
          ${intradayFundFlowBars.mediumNet} IS NOT ${row.mediumNet} OR
          ${intradayFundFlowBars.smallNet} IS NOT ${row.smallNet} OR
          ${intradayFundFlowBars.sourceEpoch} IS NOT ${row.sourceEpoch})`,
      }));
      const results = await db.batch([writes[0], ...writes.slice(1)]);
      saved += results.reduce((count, result) => count + (result.meta?.changes ?? 0), 0);
    }
    const cutoff = chinaDate(fetchedEpoch - 30 * 24 * 60 * 60 * 1000);
    await db.delete(intradayFundFlowBars).where(lt(intradayFundFlowBars.tradeDate, cutoff));
    return { status: "ok", saved };
  } catch {
    return { status: "unavailable", saved: 0, error: "资金分时历史存储暂不可用；仍展示本次上游数据" };
  }
}

/** Read one asset's latest stored provider day, accurately marked as cached.
 * Runnable endpoint/read-back example and actual output: docs/INTRADAY_FLOW.md.
 */
export async function getStoredIntradayFlowHistory(symbol: string, kind?: string) {
  const item = resolveIntradayInstrument(symbol, kind);
  const base = {
    symbol: item.symbol, kind: item.kind, name: item.name, scope: "instrument", unit: "CNY",
    source: SOURCE, bars: [] as Array<Record<string, unknown>>, as_of: null as string | null,
    trade_date: null as string | null, fetched_at: null as string | null,
    error: null as string | null,
    served_at: new Date().toISOString(), stale: true, cache_hit: true,
  };
  try {
    const db = getDb();
    const asset = and(eq(intradayFundFlowBars.kind, item.kind), eq(intradayFundFlowBars.symbol, item.symbol));
    const latest = await db.select({ tradeDate: intradayFundFlowBars.tradeDate }).from(intradayFundFlowBars)
      .where(asset).orderBy(desc(intradayFundFlowBars.tradeDate)).limit(1);
    if (!latest.length) return { ...base, status: "unavailable", note: "尚无已保存的当日资金分时" };
    const day = latest[0].tradeDate;
    const rows = await db.select().from(intradayFundFlowBars)
      .where(and(asset, eq(intradayFundFlowBars.tradeDate, day)))
      .orderBy(desc(intradayFundFlowBars.sourceEpoch)).limit(500);
    rows.reverse();
    const last = rows.at(-1)!;
    const fetchedAt = rows.map(row => row.fetchedAt).sort().at(-1)!;
    const bars = rows.map(row => ({
      time: row.sourceTime, main_net: row.mainNet, super_net: row.superNet,
      large_net: row.largeNet, medium_net: row.mediumNet, small_net: row.smallNet,
      fetched_at: row.fetchedAt,
    }));
    const minuteOf = (epoch: number) => {
      const date = new Date(epoch + 8 * 60 * 60 * 1000);
      return date.getUTCHours() * 60 + date.getUTCMinutes();
    };
    const lastMinute = minuteOf(last.sourceEpoch);
    const expected = [...Array.from({ length: 120 }, (_, i) => 571 + i), ...Array.from({ length: 120 }, (_, i) => 781 + i)]
      .filter(minute => minute <= lastMinute);
    const observed = new Set(rows.map(row => minuteOf(row.sourceEpoch)));
    return {
      ...base, status: "cached", source: last.source, trade_date: day, as_of: last.sourceTime,
      fetched_at: fetchedAt, bars,
      note: "上游暂不可用，显示此前保存的东方财富分钟资金序列；源时间保持原值，缺失分钟未补造。",
      coverage: { observed_minutes: bars.length, expected_minutes: expected.length,
        missing_minutes: expected.filter(minute => !observed.has(minute)).length },
    };
  } catch {
    return { ...base, status: "unavailable", error: "资金分时历史存储暂不可用", note: "无法读取此前保存的资金分时" };
  }
}
