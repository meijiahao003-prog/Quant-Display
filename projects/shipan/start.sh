#!/usr/bin/env bash
set -euo pipefail
# Purpose/input: install this project's lockfile and start its local dashboard.
# Run: bash projects/shipan/start.sh (from repository root), or bash start.sh here.
# Output: package install, local migration results and the loopback URL.
# Changes: node_modules and .wrangler/state only; no cloud writes or model calls.
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
command -v node >/dev/null || { echo '请先安装 Node.js 22.16+。' >&2; exit 1; }
command -v pnpm >/dev/null || { echo '请先执行：npm install -g pnpm@11.25.0' >&2; exit 1; }
pnpm install --frozen-lockfile
exec node scripts/shipan.mjs start
