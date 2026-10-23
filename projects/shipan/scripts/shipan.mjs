/**
 * Standalone launcher. Actual input: this checkout, locked Node dependencies,
 * drizzle/*.sql and local D1 state; never reads QMT, accounts or cloud databases.
 * Copy/run from projects/shipan: node scripts/shipan.mjs migrate
 * Output: Wrangler prints applied/pending migrations. Before/after: only local
 * .wrangler/state changes; existing records and source files are preserved.
 * Copy/run: node scripts/shipan.mjs start
 * Output: migrations followed by the Vite local URL. Starts a foreground process.
 * Exact observed results and limits are recorded in docs/GITHUB_MIGRATION.md.
 */
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import './sites-env.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));
const command = process.argv[2] ?? 'start';
if (!['start', 'migrate'].includes(command)) throw new Error('用法：node scripts/shipan.mjs start|migrate');
const [major, minor] = process.versions.node.split('.').map(Number);
if (major < 22 || (major === 22 && minor < 16)) throw new Error('需要 Node.js 22.16 或更高版本。');
const wrangler = fileURLToPath(new URL('../node_modules/wrangler/bin/wrangler.js', import.meta.url));
const vite = fileURLToPath(new URL('../node_modules/vite/bin/vite.js', import.meta.url));
if (!existsSync(wrangler) || !existsSync(vite)) throw new Error('请先执行 pnpm install --frozen-lockfile。');

const migration = spawnSync(process.execPath, [wrangler, 'd1', 'migrations', 'apply', 'DB',
  '--local', '--config', 'wrangler.local.jsonc', '--persist-to', '.wrangler/state'],
  { cwd: root, stdio: 'inherit', env: { ...process.env, CI: 'true' } });
if (migration.error) throw migration.error;
if (migration.status !== 0) process.exit(migration.status ?? 1);

if (command === 'start') {
  console.log('识盘工作台：http://127.0.0.1:5173（Ctrl+C 停止）');
  // The same DB id and persistence directory are used by Vite and migrations.
  process.argv = [process.execPath, vite, '--host', '127.0.0.1', '--port', '5173', '--strictPort'];
  await import(new URL('../node_modules/vite/bin/vite.js', import.meta.url));
}
