# 资金曲线缺失的恢复逻辑

## 已观察到的真实输入

2026-10-22 的生产接口曾出现：同一批 `/api/snapshot` 六个指数的资金末点已经到当天 10:15，而独立 `/api/flow-history` 的 000001、000300 请求遇到 HTTP 502，回退到 10 月 21 日存储。原因之一是快照取到了完整指数资金序列，却只把末点合并进表格，没有把完整序列写入 D1，另一个 Worker 请求无法复用。

随后保存的两份未修改生产响应在 `examples/fixtures/flow-recovery-000001-real.json` 和 `flow-recovery-000300-real.json`：000001 为 10 月 22 日 10:20 的 50 点，000300 为 10 月 21 日 13:49 的 169 点且有 HTTP 502。它们是不同标的，示例分别处理，绝不互相替代。

## 处理步骤与可运行示例

### `getWatchingSnapshot` / 快照路由

**作用**：`getWatchingSnapshot` 现在额外返回本次已经抓取的六份 `indexHistories`，供路由复用，不额外请求源。路由仍只向浏览器返回行情快照，后台任务持久化指数分钟记录。

**真实输入**：上述曾经已在快照中出现当天末点、独立历史仍回退旧日的生产结果；已经保存的 000001 完整真实序列可用于检查持久化参数。内部六份历史的完整 payload 在本轮旧版请求中未导出，不捏造其内容。

**调用**：在站点登录会话的开发者控制台运行下列代码可读真实最新数据。此段发布后真实调用由部署验证执行；本文件写入时尚未执行新版生产 D1 联调。

```js
const snapshot = await fetch('/api/snapshot', {cache:'no-store'}).then(r=>r.json());
console.log({as_of:snapshot.as_of, status:snapshot.index_flow_storage_status,
  candidates:snapshot.index_flow_storage_candidates, error:snapshot.index_flow_storage_error});
const flow = await fetch('/api/flow-history?symbol=000001&kind=index', {cache:'no-store'}).then(r=>r.json());
console.log({as_of:flow.as_of, bars:flow.bars.length, source_type:flow.source_type, status:flow.status});
```

**输出语义及前后变化**：`scheduled` 仅说明任务调度被接受，不表示 D1 已成功写入。每条分钟点原时间、金额、字段保持不变；只增加对六份已有序列的持久化工作。任务失败不使价格请求失败。

### `persistIndexFlowHistories(histories, saveHistory, {concurrency})`

**作用**：过滤非指数、未验证、缓存或空序列，最多 3 个并发 writer，单标的失败不取消其他保存；每个结果保留标的、交易日、源时间及保存状态。生产传入已有 `saveIntradayFlowHistory`，沿用 D1 表和校验，不新增表。

**真实输入及调用**：

```sh
node examples/flow-recovery.mjs
```

已执行的离线示例读取真实 000001 的 50 点，打印首点：

```json
{"time":"2026-10-22T09:31:00+08:00","main_net":-27962352,"small_net":36936960,"medium_net":-12324096,"large_net":-47033856,"super_net":19071504}
```

**实际输出**：该示例显式使用只打印参数的适配器，`database_write_executed=false`、`saved=0`、`writer_status=not_executed`。它不是生产 D1 写入成功证明。已有 50 行、各列和金额原样传给适配器，没有补造或修改。

### `scheduleIndexFlowStorage(histories, {after, saveHistory, logError})`

**作用**：把上面的有界保存工作注册到请求生命周期后台；调度失败时返回 `unavailable`，真实写入失败使用只含代码、日期及固定错误文本的日志，不输出凭证或任意底层错误内容。

**真实输入、调用和实际输出**：同一 `node examples/flow-recovery.mjs` 以真实 000001 的 50 点注册本地回调，实际输出 `status=scheduled,candidates=1,callback_registered=true,callback_executed=false,database_write_executed=false`。只验证调度，不宣称数据库发生变化。

**生产保活依据**：检查当前安装的 Vinext `1.0.0-beta.5`，`node_modules/vinext/dist/shims/server.js` 中 `after()` 明确使用 request ExecutionContext 的 `waitUntil`，或经 unified request scope 的 after callback queue 注册；`build/sites-worker.ts` 把 Cloudflare `ctx` 传入 `handler.fetch`。因此生产路由使用 `after`（`next/server` 的现有 Vinext shim），不启动无保活的裸 Promise。

### `selectFlowFallback({upstream, stored, sampled, item, now})`

**作用**：同一标的候选先按真实源日期选最新日，同日优先源分钟历史；同日两个源分钟候选取较新时间。ETF 当天已采集点可以胜过昨天完整分钟序列，明确 `source_type=sampled_snapshots`。指数从不使用 ETF 快照。返回一份候选，不跨日合并、不改原日期。

**真实输入与调用**：运行上述离线示例，分别传入两份未修改生产记录。

**已执行输出及前后变化**：

| 标的 | 原日期 / 点数 | 输出日期 / 点数 | 新增字段 | 原分钟值 |
| --- | --- | --- | --- | --- |
| 000001 | 2026-10-22 / 50 | 2026-10-22 / 50 | source_type=provider_minutes | 全部保持 |
| 000300 | 2026-10-21 / 169 | 2026-10-21 / 169 | source_type=provider_minutes | 全部保持，仍为旧日 |

真实未取得的今日 000300 数据不会因选择逻辑而产生。今日采样胜过昨日分钟的边界另用明确合成 fixture 测试，未把合成数据写入生产。

### `createIntradayFlowClient` 的 attempt/load/cache 步骤

**作用**：原固定 `push2.eastmoney.com` 仍为主请求，全部重定向及正文读取共限时 12 秒；HTTP 502/503/504、网络中断、超时最多再尝试一次已验证的 `push2delay.eastmoney.com` 同路径及同参数。401/403/429、解析/字段校验错误和不安全重定向不重试。最多两次、总网络等待不超过约 24 秒。成功返回的更早 `as_of` 不再覆盖已有较新记录。

**真实输入**：本轮 000300 生产响应确有 `error=东方财富资金分时 HTTP 502`；实际重试是否成功需部署后的上游响应验证，本地不编造成功。

**可运行调用**：

```sh
node --test tests/intraday-flow-recovery.test.mjs tests/flow-recovery.test.mjs tests/fund-flow.test.mjs
```

**已执行输出**：22 项测试通过，0 失败。测试中的 HTTP 返回和故障时钟是明确的合成协议/存储适配器；包含 10/22→10/21 的响应倒退、502 后第二次成功、两次失败、禁止重试类别、6 个指数最多 3 并发保存及调度错误隔离。后续全量验证由发布流程统一执行。

**前后变化**：故障时不新增分钟，原 `bars/as_of/fetched_at` 保留；新增 `last_attempt_at` 与错误说明。成功结果附 `upstream_attempts`、`upstream_host`，方便区分本次是否使用备用尝试。源日期不一致保护继续生效。
