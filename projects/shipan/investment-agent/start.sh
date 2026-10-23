#!/usr/bin/env bash
set -euo pipefail

# 作用：从脚本所在目录启动个人本地平台；首次缺少依赖时按锁文件安装。
# 输入：本目录的 package.json / package-lock.json 与本机 Node、npm。
# 使用：bash start.sh
# 输出：终端打印本地访问地址；实际启动结果取决于本机环境。
# 变化：首次创建 node_modules，运行时写入 .local；源码和输入材料不被改写。
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$project_dir"

if ! command -v node >/dev/null 2>&1; then
  echo "未找到 Node.js。请先安装 Node.js 22.16.0 或更高版本，再运行本脚本。" >&2
  exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
  echo "未找到 npm。请检查 Node.js 安装是否完整。" >&2
  exit 1
fi
if ! node -e 'const [major, minor] = process.versions.node.split(".").map(Number); process.exit(major > 22 || (major === 22 && minor >= 16) ? 0 : 1)'; then
  echo "Node.js 版本过低；需要 22.16.0 或更高版本。当前版本：$(node --version)" >&2
  exit 1
fi

if [[ ! -f node_modules/@openai/codex/package.json ]]; then
  echo "首次安装依赖，需要连接 npm 软件源。"
  npm ci
fi
if ! node -e 'process.exit(require("@openai/codex/package.json").version === "0.163.0" ? 0 : 1)'; then
  echo "Codex 依赖版本不符合锁定版本 0.163.0。请在本目录运行 npm ci 后重试。" >&2
  exit 1
fi

echo "启动专家圆桌；默认访问 http://127.0.0.1:4317，按 Ctrl+C 停止。"
exec npm start
