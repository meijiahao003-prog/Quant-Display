$ErrorActionPreference = 'Stop'

# 作用：从脚本目录启动个人本地平台；首次缺少依赖时按锁文件安装。
# 输入：本目录 package.json / package-lock.json、本机 Node、npm。
# 使用：在 PowerShell 中运行 .\start.ps1。
# 输出：终端打印本地访问地址；本机环境及登录前置条件见 README。
# 变化：首次创建 node_modules，运行时写入 .local；不改写源码与材料原件。
Push-Location -LiteralPath $PSScriptRoot
try {
    $nodeCommand = Get-Command node -ErrorAction SilentlyContinue
    if (-not $nodeCommand) {
        throw '未找到 Node.js。请先安装 Node.js 22.16.0 或更高版本。'
    }
    $npmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $npmCommand) {
        $npmCommand = Get-Command npm -ErrorAction SilentlyContinue
    }
    if (-not $npmCommand) {
        throw '未找到 npm。请检查 Node.js 安装是否完整。'
    }

    $nodeVersionText = & $nodeCommand.Source --version
    if ($LASTEXITCODE -ne 0) {
        throw '无法读取 Node.js 版本。'
    }
    $nodeVersion = [version]($nodeVersionText.Trim().TrimStart('v'))
    if ($nodeVersion -lt [version]'22.16.0') {
        throw 'Node.js 版本过低；需要 22.16.0 或更高版本。'
    }

    if (-not (Test-Path -LiteralPath 'node_modules/@openai/codex/package.json')) {
        Write-Host '首次安装依赖，需要连接 npm 软件源。'
        & $npmCommand.Source ci
        if ($LASTEXITCODE -ne 0) {
            throw "npm ci 失败，退出码：$LASTEXITCODE。请检查网络和 npm 安装后重试。"
        }
    }
    $codexPackage = Get-Content -LiteralPath 'node_modules/@openai/codex/package.json' -Raw | ConvertFrom-Json
    if ($codexPackage.version -ne '0.163.0') {
        throw 'Codex 依赖版本不符合 0.163.0。请在本目录运行 npm ci 后重试。'
    }

    Write-Host '启动专家圆桌；默认访问 http://127.0.0.1:4317，按 Ctrl+C 停止。'
    & $npmCommand.Source start
    if ($LASTEXITCODE -ne 0) {
        throw "平台退出，退出码：$LASTEXITCODE。请查看上方具体错误。"
    }
}
catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
finally {
    Pop-Location
}
