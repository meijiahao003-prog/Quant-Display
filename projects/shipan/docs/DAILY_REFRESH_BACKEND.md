# 每日切换与源日期识别

本次修复核验日为 2026-10-22（北京时间）。真实接口一直请求上游当前可用数据，没有按固定日期查询；修复的是“HTTP 成功即标 live”、跨午夜缓存、异日资金合并和历史回退的日期识别。不会把旧行情时间改成今天，也不推断交易所节假日。

## 数据字段

| 字段 | 含义 |
| --- | --- |
| `today` | 本次响应时北京时间日期 |
| `source_date` | `as_of` 的北京时间日期，缺少时使用有效的 `trade_date` |
| `freshness` | `current_day`、`previous_day`、`future_day` 或 `unknown` |
| `source_status` | 应用日期标记前的状态，例如 `live`、`partial`、`cached` |
| `status` / `stale` | 有效真实数据源日期非今天时为 `stale` / `true`；不可用与演示状态保持原值 |

`previous_day` 仅指源日期早于今天，既不保证它是上一交易日，也不表示接口故障。盘前、周末和节假日仍可能合理返回较早日期。`fetched_at` 是获取时间，不能用于替代行情时间。

## 函数与处理步骤

| 函数或步骤 | 目的与实际输入 | 调用 | 实际输出与前后变化 |
| --- | --- | --- | --- |
| `shanghaiDate(value)` | 将时间戳归属到北京时间日历日；实际测试输入 `2026-10-21T16:00:00Z` | `shanghaiDate('2026-10-21T16:00:00Z')` | 已执行，输出 `2026-10-22`；不传参用当前时间，显式 `null` / `undefined` / 无效值输出 `null` |
| `withMarketFreshness(payload, now)` | 为真实响应附加源日期状态；实际输入为下方仓库保存的 185 个资金分钟点 | 下方可运行命令 | 已执行，源日仍为 `2026-10-21`，`live` 转为 `stale`、`source_status=live`；185 个金额与源时间不变，输入对象不变 |
| 三个 `create*Client` 缓存出口 | 重新计算每次响应的日期状态，阻止跨北京时间日期继续命中未满 60 秒的缓存 | `node --test tests/market-time.test.mjs` | 已执行合成测试：23:59:50 获取后，23:59:55 命中缓存；次日 00:00:10 再请求上游；即使成功返回昨日数据也标旧日 |
| `mergeWatchingSnapshot(quotes, flow, indexes, now)` | 合并价格和资金，避免 ETF 不同源日期混用；合成输入为 10 月 22 日价格和 10 月 21 日资金 | 同上测试命令 | 已执行合成测试：ETF 价格保留，附加资金金额/比例为 `null`，保留旧资金时间与日期不一致说明；独立资金响应保留原金额并标旧日 |
| `/api/flow-history` 的所有 JSON 返回 | 在即时上游、内存回退、D1 回退和旧采样回退出口重新标记日期 | `GET /api/flow-history?symbol=510300&kind=etf` | 本次未执行部署后 D1 联调；已完成源码接入，未编造数据库输出。旧日新请求不会伪装成当日数据，D1 返回的日期和金额不改写 |

三种缓存客户端分别为 `createMarketDataClient`（报价及价格历史）、`createFundFlowClient`（ETF 资金快照）、`createIntradayFlowClient`（分钟资金）。网络失败仍保留原有成功数据和原获取时间，`source_status=cached`；仅日期归属重新计算。`rows`、`instruments`、`flows` 内每个条目也独立标记，合并字段 `flow_as_of` 还单独派生 `flow_today`、`flow_source_date`、`flow_freshness`、`flow_source_status` 并更新 `flow_status` / `flow_stale`；快照最终返回前再次计算日期，不能用整体最新时间掩盖某个标的的旧时间。

## 已保存真实输入回放

此处读取之前实际获取并保存的东方财富响应，**不是 10 月 22 日新抓取**，也不调用模型。固定 `now` 是跨日测试时钟。

在项目根目录运行：

```bash
node --input-type=module <<'JS'
import fs from 'node:fs';
import {withMarketFreshness} from './public/market-time.mjs';
const bundle=JSON.parse(fs.readFileSync('examples/fixtures/council-market-real.json','utf8'));
const payload=bundle.fund_flow_history;
const before=JSON.stringify(payload);
const result=withMarketFreshness(payload,Date.parse('2026-10-22T10:00:00+08:00'));
console.log(JSON.stringify({
  symbol:result.symbol,today:result.today,source_date:result.source_date,
  freshness:result.freshness,status:result.status,source_status:result.source_status,
  bars:result.bars.length,as_of:result.as_of,
  input_unchanged:JSON.stringify(payload)===before,
  bars_unchanged:JSON.stringify(result.bars)===JSON.stringify(payload.bars)
},null,2));
JS
```

实际输出：

```json
{
  "symbol": "510300",
  "today": "2026-10-22",
  "source_date": "2026-10-21",
  "freshness": "previous_day",
  "status": "stale",
  "source_status": "live",
  "bars": 185,
  "as_of": "2026-10-21T14:05:00+08:00",
  "input_unchanged": true,
  "bars_unchanged": true
}
```

## 验证范围

新增测试使用明确标注的合成协议数据，覆盖北京时间午夜、未满 TTL 跨日、正常返回旧数据、跨日断网、次日新数据恢复、周末旧数据、输入不变、演示不改写以及 ETF 价格/资金日期一致性。已有协议测试注入与 fixture 同一天的测试时钟，避免测试结果依赖运行日期。运行命令：

```bash
node --test tests/market-time.test.mjs tests/market-data.test.mjs tests/fund-flow.test.mjs tests/intraday-flow.test.mjs
```

本次未测试实际交易日历、未增加后台定时采集，也未伪造当日行情。页面触发刷新和页面不可见时的策略由前端控制；接口每次被调用都会按照上述规则判断日期。
