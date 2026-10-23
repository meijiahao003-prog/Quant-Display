CREATE TABLE `intraday_fund_flow_bars` (
	`kind` text NOT NULL,
	`symbol` text NOT NULL,
	`minute_bucket` integer NOT NULL,
	`trade_date` text NOT NULL,
	`source_time` text NOT NULL,
	`source_epoch` integer NOT NULL,
	`fetched_at` text NOT NULL,
	`source` text NOT NULL,
	`main_net` real NOT NULL,
	`super_net` real,
	`large_net` real,
	`medium_net` real,
	`small_net` real,
	PRIMARY KEY(`kind`, `symbol`, `minute_bucket`)
);
--> statement-breakpoint
CREATE INDEX `intraday_flow_asset_date_idx` ON `intraday_fund_flow_bars` (`kind`,`symbol`,`trade_date`,`source_epoch`);--> statement-breakpoint
CREATE INDEX `intraday_flow_retention_idx` ON `intraday_fund_flow_bars` (`trade_date`);