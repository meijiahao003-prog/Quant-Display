# ETF 排行与结构资金恢复

底部「资金排行与结构」消费 `/api/snapshot.flows`。此前该字段只来自 ETF `clist` 快照；上方已取得的同 ETF 资金分时没有进入此字段。新模块保留已有十只 ETF 的字段和单位，逐只恢复快照缺失的数据。

## `getRecoveredEtfFlow`：恢复 ETF 资金快照

**作用与参数**：读取 `getSnapshotFlow()` 的快照和 `getStoredRows()` 的已存真实记录；需要恢复的 ETF 通过 `getMinuteFlow(symbol, 'etf')` 取得当日分钟数据，最多五个并发请求。`now` 仅为日期判断及测试时钟，不改写源日期。默认使用项目现有东方财富客户端。数据库读取器由服务端路由传入。

**真实输入**：已登录平台 `/api/snapshot` 的真实 ETF 资金快照，以及 `/api/flow-history?symbol=510300&kind=etf` 的真实同标的资金分钟记录。必须包含 CNY 单位、标的身份、源时间和五档净额。此文不编造真实行情数值；下面调用在能访问上游的环境读取并打印真实输入与结果，当前文档调用尚未执行。

**调用示例（项目根目录）**：

```bash
node --input-type=module <<'JS'
import {getFundFlow} from './lib/fund-flow.mjs';
import {getIntradayFlow} from './lib/intraday-flow.mjs';
import {getRecoveredEtfFlow} from './lib/etf-flow-recovery.mjs';
const snapshot = await getFundFlow();
console.log('真实快照输入', snapshot.status, snapshot.rows.slice(0, 3));
const minutes = await getIntradayFlow('510300', 'etf');
console.log('真实分钟输入', minutes.status, minutes.bars?.slice(-3));
const result = await getRecoveredEtfFlow({getSnapshotFlow: async () => snapshot});
console.log('实际输出', result.status, result.rows.slice(0, 3));
console.log('前后变化', {
  beforeRows: snapshot.rows.length, afterRows: result.rows.length,
  beforeValid: snapshot.rows.filter(r => Number.isFinite(r.main_net)).length,
  afterValid: result.rows.filter(r => Number.isFinite(r.main_net)).length,
  fallbackCount: result.fallback_count
});
JS
```

**实际输出**：上述直接访问上游的示例尚未执行，不提供假定金额。自动化测试用显式合成的协议夹具验证恢复逻辑，执行结果见后文；线上真实结果由本次发布验证补充。

**前后变化**：始终保持十只 ETF、证券代码和金额单位 CNY。新增每行 `fetched_at`、`source_type` 和明确缓存状态；快照缺失时从同 ETF 的真实分钟末点恢复五档净额。百分比若分钟接口未提供则为 null。旧记录原有源日期与取得时间保持不变，跨日记录由界面分别显示并排除在异日排行外。

## 内部处理与可运行验证

所有内部处理均通过下述可运行测试覆盖；合成协议数据只用于回归检查，不作为行情、输出示例或持久化数据展示。

```bash
node --test tests/etf-flow-recovery.test.mjs
```

| 处理 | 作用 / 输入 | 实际输出与前后变化 |
|---|---|---|
| `normalizeRow` | 校验固定 ETF 身份、CNY、时间日期和主力恒等式；传入一行及源元数据 | 合成夹具中零保留为 0，缺字段保持 null，错标的/错日期/错误恒等式返回无效，不进入图表 |
| `minuteRow` | 从该 ETF 该交易日分钟序列选最新有效行 | 合成夹具分钟的五档净额逐项复制；百分比全部为 null，不用不同时间的成交额推算 |
| `newest` | 比较快照、分钟末点、已存记录的真实源时间 | 合成夹具中 10:58 已存点优于 10:55 新请求；保留 10:58 金额及原取得时间并标缓存 |
| 并发恢复 | 最多五个 ETF 同时请求，单只失败保留其他结果 | 十只合成 ETF 中一只失败时返回九只有效、一只空值；整体 partial |
| 汇总状态 | 以各行实际状态汇总来源、有效数量及时间 | 全部真实上游恢复可为 live；部分缓存为 partial；全缓存为 cached；旧交易日另外标 stale |

执行状态：本次已执行，9 项通过、0 项失败。用户可复制以上命令获得逐项输出；测试不要求网络、Token 或数据库。

## 本次真实生产响应回放（已执行）

**作用**：把修复前已保存的生产快照和沪深 300 ETF 资金分钟响应传给恢复函数。其余九只未提供分钟输入，不伪造其金额；此回放只验证一个实际标的。

**真实输入**：2026-10-22 生产 `/api/snapshot` 的十只 ETF 全部无有效资金，错误为「东方财富资金源 HTTP 502」；同日 `510300` 的 `/api/flow-history` 已取得 87 个真实分钟点，源时间截至 `2026-10-22T10:57:00+08:00`。

**调用示例（本次工作目录中的真实响应文件）**：

```bash
node --input-type=module <<'JS'
import fs from 'node:fs';
import {getRecoveredEtfFlow} from './lib/etf-flow-recovery.mjs';
const snapshot = JSON.parse(fs.readFileSync('examples/fixtures/etf-panel-unavailable-20261022.json', 'utf8'));
const minutes = JSON.parse(fs.readFileSync('examples/fixtures/etf-minutes-510300-20261022.json', 'utf8'));
const flow = {
  rows: snapshot.flows, status: snapshot.flow_status,
  source: snapshot.flow_source, error: snapshot.flow_error,
  as_of: snapshot.flow_as_of, fetched_at: snapshot.flow_fetched_at
};
console.log('真实输入', flow.status, flow.error, minutes.symbol, minutes.bars.length, minutes.bars.at(-1));
const result = await getRecoveredEtfFlow({
  getSnapshotFlow: async () => flow,
  getMinuteFlow: async symbol => symbol === '510300' ? minutes : {status: 'unavailable', bars: []},
  getStoredRows: async () => ({rows: []}),
  now: () => Date.parse('2026-10-22T11:00:00+08:00')
});
console.log('实际输出', result.status, result.rows[0]);
console.log('前后变化', {
  beforeRows: flow.rows.length, afterRows: result.rows.length,
  beforeValid: flow.rows.filter(r => Number.isFinite(r.main_net)).length,
  afterValid: result.rows.filter(r => Number.isFinite(r.main_net)).length
});
JS
```

**实际输出**：恢复结果 `partial`、仍为 10 行、有效资金从 0 行变为 1 行。`510300` 的金额如下，均为人民币元，逐项等于原始分钟末点。

| 字段 | 实际值 |
|---|---:|
| 主力 `main_net` | -617192375 |
| 超大单 `super_net` | -776342342 |
| 大单 `large_net` | 159149967 |
| 中单 `medium_net` | 296730506 |
| 小单 `small_net` | 320461041 |
| 主力比例 `main_pct` | null |

源时间保持 `2026-10-22T10:57:00+08:00`；实际取得时间保持 `2026-10-22T02:56:48.186Z`，不替换为本次回放时间。分钟标签是上游提供的标签，本项目不自行调整。类型仍为数值或 null，单位仍为 CNY，代码仍为字符串 `510300`。`source_type` 为 `provider_minutes`。这一步是实际生产响应回放，尚不等于新版部署后的全十只 ETF 在线验证。

## `getStoredEtfFlowRows`：同时读取两类已存真实记录

**作用与参数**：无参数。使用服务端既有 D1 连接，分别读取 `fund_flow_samples` 和 `intraday_fund_flow_bars` 中每只 ETF 源时间最新的记录。每个来源至多十行，共至多二十行；由恢复模块在两个来源之间继续选择源时间较新者。查询不写入数据库。

**真实输入**：本次从生产接口取得的八只 ETF 分钟序列与另外两只 ETF 的已存采样历史，共 728 条实际记录。使用与新读取函数相同的 SQL 选择最新源记录，实际得到 10 行；结果已保存于 `examples/fixtures/stored-etf-query-20261022.json`。

**调用示例（在已登录的平台页面控制台执行，新版路由间接调用该函数）**：

```js
const response = await fetch('/api/snapshot', {cache: 'no-store'});
const snapshot = await response.json();
console.log('实际输出', snapshot.flow_status, snapshot.flow_as_of);
console.table(snapshot.flows.map(row => ({
  symbol: row.symbol, main_net: row.main_net,
  source_type: row.source_type, source_time: row.as_of,
  fetched_at: row.fetched_at, status: row.status
})));
```

**实际输出**：上述新版页面调用在本节编写时尚未执行。已完成的真实 SQL 回放输出中，`159915` 和 `512660` 的源时间为 `2026-10-22T10:47:57+08:00`，来源为 `stored_snapshot`；其余八只的源时间为 `2026-10-22T10:59:00+08:00`，来源为 `stored_provider_minutes`。前五行如下：

| 代码 | 主力净额（CNY） | 源时间 | 来源类型 |
|---|---:|---|---|
| 159915 | -1677734864 | 10:47:57 | stored_snapshot |
| 512660 | -5910286 | 10:47:57 | stored_snapshot |
| 510050 | -124196906 | 10:59:00 | stored_provider_minutes |
| 510300 | -619645945 | 10:59:00 | stored_provider_minutes |
| 510500 | -151588987 | 10:59:00 | stored_provider_minutes |

**前后变化**：728 条真实历史记录筛选为 10 条最新源记录，没有求和、插值或改写金额。代码、五档净额、源日期、源时间、原取得时间都保留。返回层补充 `unit='CNY'`、`kind='etf'`、`status='cached'`、`stale=true`；因此“取到了旧记录”不会被误报为“刚从上游更新”。

## `saveFlowSnapshot`：保留逐行取得时间与来源

**作用与参数**：接收恢复后的 `flow` 对象，仅保存 `live` 或 `partial` 且未标 stale 的有效行。每行优先使用自己的 `fetched_at` 和 `source`；旧格式缺少逐行字段时兼容使用整体元数据。缓存、错日期或非数值主力净额不写入。

**真实输入**：本次真实回放 `510300` 行的 `as_of` 为 `2026-10-22T10:57:00+08:00`，`fetched_at` 为 `2026-10-22T02:56:48.186Z`，`source` 为「东方财富 · 当日资金分时」，五档金额见上表。

**调用示例**：上面的已登录同源 `fetch('/api/snapshot')` 会在服务端恢复 ETF 数据后执行 `saveFlowSnapshot(flow)`；可打印存储返回状态：

```js
const snapshot = await fetch('/api/snapshot', {cache: 'no-store'}).then(r => r.json());
console.log({
  storage_status: snapshot.flow_storage_status,
  storage_error: snapshot.flow_storage_error,
  example_row: snapshot.flows.find(row => row.symbol === '510300')
});
```

**实际输出**：该新版线上写入示例在本节编写时尚未执行，存储成功状态必须以部署后实际响应为准，不由本地回放推定。

**前后变化**：从统一使用整个快照的取得时间与来源，改为优先保存每行的真实取得时间与来源。表结构、唯一键、金额字段类型和保留期限不变；同一分钟已有较新源时间不会被较旧记录覆盖。合并结果为 partial 时仍允许保存其中有效的新鲜行，缓存行不被当作新采样再次写入。
