# ETF 资金排行与结构：前端恢复

修复范围是底部排行与大小单结构。该区域此前只使用 `/api/snapshot` 的 `flows`，没有自动采用上方资金分时；HTTP 200 中的 `unavailable` 仍会覆盖已有资金。现在后端提供同一 schema 的逐 ETF 回退，前端再防止空响应或更早响应清除已经显示的真实值。

下述示例使用仓库原有 `examples/fixtures/index-watchlist-real.json` 中真实保存的生产响应，记录时间为 **2026-10-21**，不是今天新拉取的行情。故障响应与跨日测试是明确模拟；金额没有编造。没有接入 QMT 或用户数据库。示例已在当前开发环境执行。

## 1. `retainFlowSnapshot(previous, incoming, now)`

**作用**：在同一数据模式和接口来源内逐 ETF 比较已有数据 `previous` 与新响应 `incoming`。新数据缺失或源时间更早时，保留真实旧值并标为缓存。`now` 是状态计算时钟，生产调用默认当前时间；回放传入对应历史时间。演示模式、来源切换时不复用前一来源记录。行情价格字段保持新响应原值。

**真实输入**（第一只 ETF，人民币元）：

| 字段 | 真实保存值 |
|---|---:|
| symbol | 510300 |
| main_net | -534887248 |
| super_net | -783764624 |
| large_net | 248877376 |
| medium_net | 288512800 |
| small_net | 246374447 |
| as_of | 2026-10-21T13:41:24+08:00 |
| flow_fetched_at | 2026-10-21T05:41:26.157Z |

真实输入总计 10 行 ETF。故障 envelope 仅将本次 `flows` 设为空、状态设为 `unavailable`，模拟响应成功但资金内容缺失。

**调用示例**：在项目根目录复制执行，使用既有 Node.js 内置库。

```bash
node examples/etf-flow-panel.mjs
```

该可运行脚本先读取真实快照并打印第一行，再调用：

```js
const failed = {...actual, flows: [], flow_status: 'unavailable',
  flow_fetched_at: null, flow_error: '模拟请求失败'};
const retained = retainFlowSnapshot(actual, failed,
  Date.parse('2026-10-21T13:45:00+08:00'));
```

**实际输出**（完整脚本会打印全部首行字段）：

```json
{"rows":10,"status":"cached","first":{"symbol":"510300","main_net":-534887248,"as_of":"2026-10-21T13:41:24+08:00","fetched_at":"2026-10-21T05:41:26.157Z","status":"cached"}}
```

**前后变化**：本次故障响应 0 个有效资金行 → 回退 10 个已有真实行。资金金额仍为 number；时间仍为 string；金额、源时间、原取得时间不变。新增或更新的是 `status/source_status=cached`、`stale=true` 和失败原因。不会把真实旧资金改写成“当前时刻新资金”。内部有效性判断只接受有限数值、人民币、有效源时间和已观测状态；回退和刷新标签由上述同一个脚本实际覆盖。

## 2. `flowPanelState(snapshot, symbol, mode, now)`

**作用**：`snapshot` 为已有逐 ETF 资金记录；`symbol` 是结构模式选择的 ETF；`mode` 为 `ranking` 或 `structure`；`now` 用于跨日状态。排行只比较最新一个实际源日期，结构使用选中 ETF 自己的状态、来源、源时间及实际取得时间。

**真实输入**：沿用步骤 1 的 10 个真实资金行，全部为因模拟故障保留的缓存。没有新增观察值。

**调用示例**：同一个 `node examples/etf-flow-panel.mjs` 命令已包含以下调用，直接执行即可。

```js
const ranking = flowPanelState(retained, null, 'ranking', now);
const structure = flowPanelState(retained, '510300', 'structure', now);
```

**实际输出**：

```json
{"rows":10,"date":"2026-10-21","excluded":0,"status":"cached","cached_count":10}
{"symbol":"510300","status":"cached","as_of":"2026-10-21T13:41:24+08:00","fetched_at":"2026-10-21T05:41:26.157Z","main_net":-534887248}
```

**前后变化**：排行输入/输出均为同日 10 行；结构选择 1 行。数值、类型、源时间未变。选择和状态信息单独返回；界面换算成亿元仅用于显示。跨日时，旧记录仍可在相应 ETF 结构中查看并标旧；不会参与新日排行，也不会作为新日价格旁的当前净额。

## 3. 页面处理与验证

`refresh()` 在同一连接内把响应交给 `retainFlowSnapshot`，再由 `renderFlow()` 取 `flowPanelState` 结果。排行悬停逐只显示源时间、实际取得时间、是否缓存；结构标题使用该 ETF 的真实来源。`connect()` 不复用此前来源，价格与上方联合图继续由原有独立加载器负责。

**调用示例**：

```bash
node --check public/app.js
node --test tests/flow-snapshot.test.mjs
```

**实际输出**：6 项通过、0 项失败。检查覆盖 HTTP 200 空资金、时间回退、零净额、跨日只排同日记录、HTTP 故障时结构缓存标识、逐行取得时间、ETF/指数身份隔离。真实金额来自保存响应；更改时间或将值置零的案例仅为明确的回归夹具，不是市场数据。

**前后变化**：原来有效 → 空资金响应会清空底部图；现在保留原始记录并显示缓存。价格数值和指数资金不由此模块替换。上线和真实浏览器核对由主工作流程另行执行，此文不声称已经发布。
