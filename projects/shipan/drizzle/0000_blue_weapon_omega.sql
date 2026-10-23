CREATE TABLE `fund_flow_samples` (
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
	PRIMARY KEY(`symbol`, `minute_bucket`)
);
--> statement-breakpoint
CREATE INDEX `fund_flow_symbol_date_idx` ON `fund_flow_samples` (`symbol`,`trade_date`,`source_epoch`);--> statement-breakpoint
CREATE INDEX `fund_flow_retention_idx` ON `fund_flow_samples` (`trade_date`);