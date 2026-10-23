CREATE TABLE `market_data_cache` (
	`key` text PRIMARY KEY NOT NULL,
	`payload` text NOT NULL,
	`source_epoch` integer NOT NULL,
	`fetched_at` text NOT NULL
);
