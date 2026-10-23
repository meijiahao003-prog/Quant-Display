# 识盘 · ShiPan

Quant-Display 下的独立市场监控与投资研究子项目，由「观市 · Market Lens」线上第 17 版移植。保留看盘、自选与资金筛选、价格与资金联动图、宏观先导、投资建议和本地三专家圆桌。

## 本地启动

需要 Node.js 22.16+、pnpm 11.25.0。前端与接口使用同一套 Vite / Vinext 服务，本地 D1 保存缓存和宏观报告。

作用：按锁文件安装依赖、应用本地数据库迁移、启动识盘。
真实输入：本仓库 `projects/shipan` 源码、`pnpm-lock.yaml`、`drizzle/*.sql`；不读取你的 QMT 或 ClickHouse。
调用示例（从仓库根目录）：

```bash
npm install -g pnpm@11.25.0
bash projects/shipan/start.sh
```

Windows PowerShell：

```powershell
npm install -g pnpm@11.25.0
powershell -File .\projects\shipan\start.ps1
```

实际输出：验证环境已启动 Vite，打印 `Local: http://127.0.0.1:5173/`；真实行情以你本机接口响应为准，不保证免费数据源始终可用。
前后变化：创建依赖与 `.wrangler/state` 本地数据库；不写原观市线上数据库、不改变原始行情数值。按 Ctrl+C 停止。

## 启动投资 Agent

作用：启动原有 Codex / ChatGPT 登录桥和三专家研究工作站。
真实输入：`investment-agent` 源码、锁定依赖及三个专家 Skill；登录使用你本机账号。
调用示例（另开终端，从仓库根目录）：

```bash
bash projects/shipan/investment-agent/start.sh
```

实际输出：脚本默认地址为 `http://127.0.0.1:4317`；本次未登录用户 ChatGPT、未运行真实模型会审。
前后变化：首次安装依赖，登录和研究记录留在本机；不会上传到 GitHub。随后在识盘的「投资建议」选择标的并发送资料。

## 功能与迁移边界

| 模块 | GitHub 版状态 |
| --- | --- |
| 看盘、筛选、自选、价格与资金图 | 保留第 17 版实现；页面品牌改为识盘 |
| 腾讯价格、东方财富资金 | 保留真实接口、源日期、缓存和失败标记；未将测试数据冒充当前行情 |
| 宏观先导 | 保留报告校验、读写与过期提示；新本地库初始无个人报告 |
| 三专家 Agent | 保留 Skill、知识摘要、Codex 登录桥与资料冻结协议；需本机登录 |
| 每日宏观及盘中自动更新 | 保留接口协议；ChatGPT 账户中的定时任务不随 GitHub 源码迁移 |
| 旧 Python 后端 | 保留于 `backend/`；其能力与当前 Worker 接口不同，不作为最新平台默认启动方式 |

宏观资料入口为 `/macro-source.html`，手册为 `/macro-handbook.html`。接入自己的资料后，按 `docs/MACRO_API.md` 向 `/api/macro` 提交带真实来源日期的结论。默认 `sourcePageUrl` 为 `/macro-source.html`。上传源码不会自动生成今天的宏观结论。

原有线上账号、私有资料引用、运行数据库、会审记录、依赖和历史下载压缩包不纳入提交。本地模拟登录仅用于回环地址开发；若部署公网，需要另行配置身份认证和数据写入权限。GitHub 仓库地址不是运行中的网站。

## 验证与结构

- 平台 Node 测试：141 / 141 通过。
- Agent Node 测试：65 / 65 通过。
- Vinext 构建通过；本地数据库迁移命令通过。
- 未验证用户本机模型登录、实盘数据可用性、无人值守宏观任务和浏览器完整交互。

`public/` 为工作台界面；`app/api/` 与 `lib/` 为接口；`drizzle/` 为迁移；`investment-agent/` 为专家工作站；`backend/` 为旧 Python 服务；`tests/` 为回归测试。

更多信息见 [迁移说明](docs/GITHUB_MIGRATION.md)、[第三方许可](THIRD_PARTY_NOTICES.md)。`README-UPSTREAM.md` 及其他既有说明保留原版本验证记录，不代表本次重新验证了真实数据源。
