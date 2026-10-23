import { index, integer, primaryKey, real, sqliteTable, text } from "drizzle-orm/sqlite-core";

/** Actual provider observations, at most one (latest) source observation per minute. */
export const fundFlowSamples = sqliteTable("fund_flow_samples", {
  symbol: text("symbol").notNull(),
  minuteBucket: integer("minute_bucket").notNull(),
  tradeDate: text("trade_date").notNull(),
  sourceTime: text("source_time").notNull(),
  sourceEpoch: integer("source_epoch").notNull(),
  fetchedAt: text("fetched_at").notNull(),
  source: text("source").notNull(),
  mainNet: real("main_net").notNull(),
  superNet: real("super_net"),
  largeNet: real("large_net"),
  mediumNet: real("medium_net"),
  smallNet: real("small_net"),
}, table => [
  primaryKey({ columns: [table.symbol, table.minuteBucket] }),
  index("fund_flow_symbol_date_idx").on(table.symbol, table.tradeDate, table.sourceEpoch),
  index("fund_flow_retention_idx").on(table.tradeDate),
]);

/** Provider minute-history bars are separate from page-triggered ETF snapshots. */
export const intradayFundFlowBars = sqliteTable("intraday_fund_flow_bars", {
  kind: text("kind").notNull(),
  symbol: text("symbol").notNull(),
  minuteBucket: integer("minute_bucket").notNull(),
  tradeDate: text("trade_date").notNull(),
  sourceTime: text("source_time").notNull(),
  sourceEpoch: integer("source_epoch").notNull(),
  fetchedAt: text("fetched_at").notNull(),
  source: text("source").notNull(),
  mainNet: real("main_net").notNull(),
  superNet: real("super_net"),
  largeNet: real("large_net"),
  mediumNet: real("medium_net"),
  smallNet: real("small_net"),
}, table => [
  primaryKey({ columns: [table.kind, table.symbol, table.minuteBucket] }),
  index("intraday_flow_asset_date_idx").on(table.kind, table.symbol, table.tradeDate, table.sourceEpoch),
  index("intraday_flow_retention_idx").on(table.tradeDate),
]);

/** Latest genuine quote per instrument and price history per instrument/period. */
export const marketDataCache = sqliteTable("market_data_cache", {
  key: text("key").primaryKey(),
  payload: text("payload").notNull(),
  sourceEpoch: integer("source_epoch").notNull(),
  fetchedAt: text("fetched_at").notNull(),
});

/** One sourced macro conclusion per report day; source dates stay in the payload. */
export const macroDailyBriefs = sqliteTable("macro_daily_briefs", {
  reportDate: text("report_date").primaryKey(),
  generatedAt: text("generated_at").notNull(),
  payload: text("payload").notNull(),
});

/** Update outcomes are separate so a failure cannot erase a valid daily report. */
export const macroUpdateAttempts = sqliteTable("macro_update_attempts", {
  reportDate: text("report_date").primaryKey(),
  attemptedAt: text("attempted_at").notNull(),
  status: text("status").notNull(),
  message: text("message").notNull(),
});
