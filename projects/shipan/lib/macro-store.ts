import { desc, sql } from 'drizzle-orm';
import { getDb } from '../db';
import { macroDailyBriefs, macroUpdateAttempts } from '../db/schema';
import { macroResponse } from './macro-brief.mjs';

type Brief = { reportDate: string; generatedAt: string; [key: string]: unknown };
type Attempt = { reportDate: string; attemptedAt: string; status: string; message: string };

/** Durable read only; requesting a report never triggers model/source fetching.
 * Real-input calls and before/after checks: docs/MACRO_API.md. */
export async function readMacroBrief() {
  const db = getDb();
  const [reports, attempts] = await Promise.all([
    db.select().from(macroDailyBriefs).orderBy(desc(macroDailyBriefs.reportDate), desc(macroDailyBriefs.generatedAt)).limit(1),
    db.select().from(macroUpdateAttempts).orderBy(desc(macroUpdateAttempts.attemptedAt), desc(macroUpdateAttempts.reportDate)).limit(1),
  ]);
  return macroResponse(reports.length ? JSON.parse(reports[0].payload) : null, attempts[0] ?? null);
}

/** Ordered daily upsert; separate failure status cannot delete a valid report.
 * Strictly newer generation wins even when requests finish out of order. */
export async function writeMacroBrief(input: { brief?: Brief; failure?: Attempt }) {
  const db = getDb();
  const brief = input.brief;
  const attempt: Attempt = brief ? { reportDate: brief.reportDate, attemptedAt: brief.generatedAt, status: 'success', message: '宏观先导已生成并保存；源日期详见报告来源。' } : input.failure!;
  const attemptQuery = db.insert(macroUpdateAttempts).values(attempt).onConflictDoUpdate({
    target: macroUpdateAttempts.reportDate,
    set: { attemptedAt: attempt.attemptedAt, status: attempt.status, message: attempt.message },
    setWhere: sql`${macroUpdateAttempts.attemptedAt} < ${attempt.attemptedAt}`,
  });
  if (!brief) {
    const result = await attemptQuery;
    return { saved: (result.meta?.changes ?? 0) > 0, reportSaved: false, preservedBrief: true };
  }
  const briefQuery = db.insert(macroDailyBriefs).values({ reportDate: brief.reportDate, generatedAt: brief.generatedAt, payload: JSON.stringify(brief) }).onConflictDoUpdate({
    target: macroDailyBriefs.reportDate,
    set: { generatedAt: brief.generatedAt, payload: JSON.stringify(brief) },
    setWhere: sql`${macroDailyBriefs.generatedAt} < ${brief.generatedAt}`,
  });
  const result = await db.batch([briefQuery, attemptQuery]);
  const reportSaved = (result[0].meta?.changes ?? 0) > 0;
  return { saved: reportSaved, reportSaved, preservedBrief: !reportSaved };
}
