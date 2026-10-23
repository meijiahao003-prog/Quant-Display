$ErrorActionPreference = 'Stop'
# Input: this project's source and lockfile. Run: powershell -File .\start.ps1
# Output: install/migration logs and local URL. Changes only dependencies/local DB.
Set-Location $PSScriptRoot
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw '需要 Node.js 22.16+。' }
if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) { throw '请先执行 npm install -g pnpm@11.25.0' }
pnpm install --frozen-lockfile
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
node scripts/shipan.mjs start
exit $LASTEXITCODE
