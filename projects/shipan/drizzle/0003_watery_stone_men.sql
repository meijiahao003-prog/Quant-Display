CREATE TABLE `macro_daily_briefs` (
	`report_date` text PRIMARY KEY NOT NULL,
	`generated_at` text NOT NULL,
	`payload` text NOT NULL
);
--> statement-breakpoint
CREATE TABLE `macro_update_attempts` (
	`report_date` text PRIMARY KEY NOT NULL,
	`attempted_at` text NOT NULL,
	`status` text NOT NULL,
	`message` text NOT NULL
);
