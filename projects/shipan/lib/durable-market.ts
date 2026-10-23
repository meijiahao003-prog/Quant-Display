import { eq, inArray, sql } from "drizzle-orm";
import { getDb } from "../db";
import { marketDataCache } from "../db/schema";
import { getSnapshot, getHistory, UNIVERSE } from "./market-data.mjs";
import { withMarketFreshness, shanghaiDate } from "../public/market-time.mjs";
import { asStoredMarket, selectDurableSnapshot, selectDurableHistory, validQuote, isPersistablePriceObservation } from "./durable-data.mjs";
import { getIntradayFlow, resolveIntradayInstrument } from "./intraday-flow.mjs";
import { getStoredFlowHistory } from "./flow-store";
import { getStoredIntradayFlowHistory, saveIntradayFlowHistory } from "./intraday-flow-store";
import { selectFlowFallback } from "./flow-recovery.mjs";

type Payload = Record<string, any>;
const quoteKey = (item: { kind: string; symbol: string }) => `quote:${item.kind}:${item.symbol}`;
const SOURCE = "腾讯财经公开行情 · 免费源，延迟未承诺";
const message = (error: unknown) => error instanceof Error ? error.message.slice(0, 180) : "市场数据存储暂不可用";
const isFresh = (payload: Payload) => ["live", "partial"].includes(payload.status) && !payload.stale;

/** Produce only allowlisted persistent history keys; no caller-supplied URL.
 * Purpose/input/call/output/before-after: docs/DURABLE_MARKET.md. */
export function marketHistoryKey(symbol: string, kind: string, period: string) {
  if (!UNIVERSE.some(item => item.symbol === symbol && item.kind === kind) || !["1m", "day"].includes(period)) {
    throw Object.assign(new Error("不支持的标的、品种或周期"), { status: 400 });
  }
  return `price:${kind}:${symbol}:${period}`;
}

/** Read persisted data only, without contacting any source. The snapshot is
 * assembled from independent, atomically ordered quote rows (16 + 32 keys).
 * Calls and real readback steps: docs/DURABLE_MARKET.md. */
export async function readStoredMarket(key: string): Promise<Payload | null> {
  if (key === "quote:snapshot") {
    const rows = await getDb().select().from(marketDataCache).where(inArray(marketDataCache.key, UNIVERSE.map(quoteKey)));
    if (!rows.length) return null;
    const instruments = rows.map(row => asStoredMarket(JSON.parse(row.payload)));
    return selectDurableSnapshot(null, { mode: "real", source: SOURCE, instruments });
  }
  const [, kind, symbol, period, extra] = key.split(":");
  if (!key.startsWith("price:") || extra !== undefined || marketHistoryKey(symbol, kind, period) !== key) {
    throw Object.assign(new Error("不支持的市场数据存储键"), { status: 400 });
  }
  const rows = await getDb().select().from(marketDataCache).where(eq(marketDataCache.key, key)).limit(1);
  return rows.length ? asStoredMarket(JSON.parse(rows[0].payload)) : null;
}

/** Read the small fixed-key inventory for unattended verification. Source date
 * and original fetched_at remain distinct from the time of this read. */
export async function readStoredMarketStatus() {
  const rows = await getDb().select().from(marketDataCache);
  return rows.map(row => {
    const data = JSON.parse(row.payload);
    return { key: row.key, symbol: data.symbol, kind: data.kind, period: data.period ?? "quote",
      as_of: data.as_of, source_date: shanghaiDate(data.as_of), fetched_at: row.fetchedAt,
      bars: data.bars?.length ?? null };
  });
}

/** Atomically reject older source observations, and older acquisitions of a
 * still-forming same-day candle. No cache response becomes a new observation. */
async function saveMarketRows(records: Array<{ key: string; payload: Payload }>) {
  const rows = records.filter(record => isPersistablePriceObservation(record.payload)).map(record => ({
    key: record.key, payload: JSON.stringify(record.payload), sourceEpoch: Date.parse(record.payload.as_of), fetchedAt: record.payload.fetched_at as string,
  }));
  if (!rows.length) return { status: "ok", saved: 0 };
  try {
    const db = getDb();
    const queries = rows.map(row => db.insert(marketDataCache).values(row).onConflictDoUpdate({
      target: marketDataCache.key, set: { payload: row.payload, sourceEpoch: row.sourceEpoch, fetchedAt: row.fetchedAt },
      setWhere: sql`${marketDataCache.sourceEpoch} < ${row.sourceEpoch} OR
        (${marketDataCache.sourceEpoch} = ${row.sourceEpoch} AND ${marketDataCache.fetchedAt} < ${row.fetchedAt})`,
    }));
    const result = await db.batch([queries[0], ...queries.slice(1)]);
    return { status: "ok", saved: result.reduce((sum, item) => sum + (item.meta?.changes ?? 0), 0) };
  } catch (error) { return { status: "unavailable", saved: 0, error: message(error) }; }
}

/** Refresh quotes and retain each last known real quote across cold starts.
 * The exact same operation serves page requests and cloud updater requests. */
export async function getDurableSnapshot(): Promise<Payload> {
  const [upstream, previous] = await Promise.all([
    getSnapshot(), readStoredMarket("quote:snapshot").then(data => ({ data, error: null as string | null }))
      .catch(error => ({ data: null, error: message(error) })),
  ]);
  const records = upstream.instruments.flatMap((row: Payload) => {
    const item = UNIVERSE.find(item => item.symbol === row.symbol && item.kind === row.kind);
    return item && validQuote(row, item) ? [{ key: quoteKey(item), payload: { ...row,
      cache_hit: row.cache_hit ?? upstream.cache_hit, fetched_at: row.fetched_at ?? upstream.fetched_at } }] : [];
  });
  const storage = await saveMarketRows(records);
  return { ...selectDurableSnapshot(upstream, previous.data), storage_status: storage.status,
    storage_saved: storage.saved, storage_error: storage.error ?? previous.error };
}

/** Persist a genuine series and return the newest same-asset series available;
 * failed/older data cannot erase today's stored points or rewrite their date. */
export async function getDurableHistory(symbol: string, kind: string, period: string): Promise<Payload> {
  const key = marketHistoryKey(symbol, kind, period);
  const [upstream, previous] = await Promise.all([
    getHistory(symbol, kind, period), readStoredMarket(key).then(data => ({ data, error: null as string | null }))
      .catch(error => ({ data: null, error: message(error) })),
  ]);
  const storage = await saveMarketRows(upstream.bars?.length ? [{ key, payload: upstream }] : []);
  return { ...selectDurableHistory(upstream, previous.data), storage_status: storage.status,
    storage_saved: storage.saved, storage_error: storage.error ?? previous.error };
}

/** Shared flow writer/reader for the chart, watchlist and unattended updater.
 * Prefer newest actual source day; preserve sampled/provider sequence identity. */
export async function getDurableFlowHistory(symbol: string, kind?: string): Promise<Payload> {
  const item = resolveIntradayInstrument(symbol, kind);
  const [upstream, stored, sampled] = await Promise.all([
    getIntradayFlow(item.symbol, item.kind), getStoredIntradayFlowHistory(item.symbol, item.kind),
    item.kind === "etf" ? getStoredFlowHistory(item.symbol) : Promise.resolve(null),
  ]);
  const storage = upstream.bars?.length && isFresh(upstream) ? await saveIntradayFlowHistory(upstream) :
    { status: stored.status === "unavailable" && stored.error ? "unavailable" : "ok", saved: 0, error: stored.error };
  const result = selectFlowFallback({ upstream, stored, sampled, item });
  return withMarketFreshness({ ...result, storage_status: storage.status, storage_saved: storage.saved,
    storage_error: storage.error ?? null });
}
