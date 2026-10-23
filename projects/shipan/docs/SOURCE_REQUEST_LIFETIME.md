# 源请求生命周期与按北京时间跨日刷新

## 已修复的问题

三个模块此前把未完成 Promise 放在模块级客户端的 `pending` 中。Worker 请求被取消后，属于该请求的网络或计时工作可能不再继续；后来的请求却会再次等待同一个未完成 Promise。给最早请求再加一个普通超时，无法保证另一个请求不继承该等待。

现在跨请求只共享**已经完成的缓存数据**：每次缓存未命中独立发起有界请求，不共享未完成工作。并发冷请求可能重复读取同一源；后续请求仍可使用 60 秒已完成缓存。

缓存属于**发起请求时的北京时间日期**。前一晚发出的请求即使午夜后才完成，也不能阻止新一天第一次刷新。时间戳只用于缓存资格与状态，不会改写行情的实际交易日期。更晚完成的旧响应不能覆盖已取得的新源时间；旧请求失败不能把更新请求的成功缓存改成不可用。

## 真实保存输入核对（已执行）

以下是仓库已有真实生产响应，不是 10 月 23 日新行情，也不是人工报价。未连接 QMT 或用户数据库。

| 数据 | 文件 | 行数 | 真实源时间 | 510300 对应值 |
|---|---|---:|---|---:|
| 价格快照 | `examples/fixtures/index-watchlist-real.json` | 16 | 2026-10-21 13:41:24 +08:00 | 4.188 元 |
| ETF 资金快照 | 同上，`before_snapshot.flows` | 10 | 2026-10-21 13:41:25 +08:00（整批最新） | -534887248 元 |
| 价格分时 | `examples/fixtures/price-refresh-510300-real.json` | 69 | 2026-10-22 10:38:00 +08:00 | 末点 4.129 元 |
| 资金分时 | `examples/fixtures/etf-minutes-510300-20261022.json` | 87 | 2026-10-22 10:57:00 +08:00 | 末点 -617192375 元 |

**作用**：读取原有真实响应，确认改动不需要更改金额、价格或源时间；参数是项目内已保存文件路径。

**调用示例**：从项目根目录执行，只有 Node.js 内置库。

```bash
node --input-type=module - <<'JS'
import {readFile} from 'node:fs/promises';
const quotes=JSON.parse(await readFile('examples/fixtures/index-watchlist-real.json','utf8')).before_snapshot;
const prices=JSON.parse(await readFile('examples/fixtures/price-refresh-510300-real.json','utf8'));
const minutes=JSON.parse(await readFile('examples/fixtures/etf-minutes-510300-20261022.json','utf8'));
console.log({quoteRows:quotes.instruments.length,flowRows:quotes.flows.length,
  pricePoints:prices.bars.length,flowPoints:minutes.bars.length,
  quote:quotes.instruments.find(x=>x.symbol==='510300').price,
  net:quotes.flows[0].main_net,lastPrice:prices.bars.at(-1).close,
  lastFlow:minutes.bars.at(-1).main_net});
JS
```

**实际输出**：`quoteRows=16, flowRows=10, pricePoints=69, flowPoints=87, quote=4.188, net=-534887248, lastPrice=4.129, lastFlow=-617192375`。

**前后变化**：只是读取和打印，源文件、行数、价格、金额、数值类型与时间不变。

## `createMarketDataClient`：报价和价格走势

**作用**：`fetchImpl` 为 HTTP 实现（默认现有 `fetch`）；`now` 为毫秒时钟（默认当前时间）；`timeoutMs` 为一次获取与正文读取的总期限（默认 12000ms）。返回 `getSnapshot()` 与 `getHistory(symbol,kind,period)`，后者沿用证券代码、`etf/index` 与 `1m/day` 参数。

内部 `read()` 仍负责源请求和正文期限；`cached()` 现在只保存最终值和请求起始日，不保存 Promise。它在请求完成时重新检查当前缓存，保护较新源数据；对价格快照逐证券保留缺失或时间回退前的真实报价。保留行标为缓存，未把一个证券的价格用于另一个证券。

**真实输入**：原腾讯接口固定 16 个观察池标的；分时示例输入 `510300 / etf / 1m`。上表的历史价格记录可核验输出字段。

**调用示例**（直接读取真实上游；本轮未执行此联网命令，不编造结果）：

```bash
node --input-type=module - <<'JS'
import {createMarketDataClient} from './lib/market-data.mjs';
const client=createMarketDataClient();
const snapshot=await client.getSnapshot();
console.log({status:snapshot.status,as_of:snapshot.as_of,fetched_at:snapshot.fetched_at,rows:snapshot.instruments.length});
console.table(snapshot.instruments.slice(0,5));
const history=await client.getHistory('510300','etf','1m');
console.log({status:history.status,as_of:history.as_of,count:history.bars.length});
console.table(history.bars.slice(0,5));
JS
```

**实际输出**：上述联网调用尚未执行；命令会打印真正返回的状态、源时间、行数和前五行。请求失败输出明确不可用或原时刻缓存，不补造价格。

**前后变化**：保留原 schema；通常报价仍为 16 行，分时行数由源决定。取消跨请求等待未完成 Promise；缓存记录新增内部 `requestDay/requestId` 元数据，不向用户冒充源字段。数值单位不变；每个被保留的报价行有缓存状态及原取得时间。

## `createFundFlowClient`：ETF 资金快照

**作用**：参数同上。`getFundFlow()` 获取固定 10 个 ETF 的真实资金快照；内部 `load()` 保留原请求与校验，客户端现在只共享已完成数据，并逐 ETF 防止缺失或旧日期覆盖新记录。

**真实输入**：原 `fundFlowUrl()` 的固定 ETF 列表；真实保存样例见上表的 10 行资金快照。

**调用示例**（真实上游命令，本轮未执行）：

```bash
node --input-type=module - <<'JS'
import {createFundFlowClient} from './lib/fund-flow.mjs';
const data=await createFundFlowClient().getFundFlow();
console.log({status:data.status,as_of:data.as_of,fetched_at:data.fetched_at,count:data.rows.length,error:data.error});
console.table(data.rows.slice(0,5));
JS
```

**实际输出**：尚未执行此联网调用。运行后如实打印，失败仍保留 null 或已有真实缓存。

**前后变化**：继续 10 个允许的 ETF，金额为人民币 number、缺失为 null；不填零。TTL 计算新增请求起始日，跨日不继承昨日未完成工作。保留旧行会标记 `cached/stale`，且保留源时间与原取得时间。

## `createIntradayFlowClient`：当日资金分钟序列

**作用**：`fetchImpl/now` 同上；新增可选 `timeoutMs`，默认仍为每次尝试 12000ms，最多两次。`getIntradayFlow(symbol,kind)` 沿用固定标的校验；内部 `attempt()` 的完整重定向、正文链路受同一尝试期限限制，`load()` 只对原有允许重试的错误执行第二次尝试。

**真实输入**：`510300 / etf`，历史真实记录为 87 个分钟点（上表）。

**调用示例**（真实上游命令，本轮未执行）：

```bash
node --input-type=module - <<'JS'
import {createIntradayFlowClient} from './lib/intraday-flow.mjs';
const data=await createIntradayFlowClient().getIntradayFlow('510300','etf');
console.log({status:data.status,trade_date:data.trade_date,as_of:data.as_of,fetched_at:data.fetched_at,count:data.bars.length,error:data.error});
console.table(data.bars.slice(0,5));
JS
```

**实际输出**：尚未执行此联网调用；真正联网结果应以主流程的生产接口验证为准。

**前后变化**：原分钟序列、金额单位和字段不变。每个缓存未命中请求独立执行；回退比较使用完成时最新缓存，消除“两个请求开始时都读到旧缓存、晚返回旧数据又覆盖新数据”的竞态。没有把昨日期限或昨日分钟改成今天。

## 生命周期与跨日验证（已执行）

**作用与输入**：用明确合成的协议测试夹具模拟未完成源请求、取消后的悬挂状态、北京时间跨午夜、晚到旧响应和不理会 AbortSignal 的源。合成金额仅用于测试，不能用于页面真实数据。

**调用示例**：

```bash
node --test tests/source-request-lifetime.test.mjs tests/market-data.test.mjs tests/fund-flow.test.mjs tests/intraday-flow.test.mjs tests/intraday-flow-recovery.test.mjs tests/market-time.test.mjs
```

**实际输出**：59 tests、59 pass、0 fail。其中 17 项新验证分别覆盖报价、价格分时、ETF 资金快照、资金分时的旧请求挂起不阻塞新请求、午夜后重取、晚到昨日数据不能覆盖今日、顺序源回退，以及资金分时完整尝试期限。

**前后变化**：原“并发调用只产生一个共享 Promise”的断言改成“并发冷请求各自执行、已完成缓存仍复用”。测试保留了源时间、取得时间、金额不变、零值和身份校验等原有要求。此测试验证请求生命周期逻辑，不声称生产源今日一定有数据。
