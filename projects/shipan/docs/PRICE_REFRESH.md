# 价格走势图刷新修复

旧逻辑在每次刷新开始就销毁价格图，再等待价格、资金、对比请求全部完成。资金请求慢时，已经取得的价格也不能显示；某次价格返回空数组还会清除之前成功的数据。现在同一上下文保留已有图，三个请求各自返回就各自绘制，失败保留最近一次真实观察值并显示缓存状态。

## 真实输入与统一可运行示例

本轮直接读取生产接口 `/api/history?symbol=510300&kind=etf&period=1m`，原响应完整保存在 `examples/fixtures/price-refresh-510300-real.json`。它是腾讯公开源实际返回的数据，不是 QMT 或虚构数据库查询结果。

| 字段 | 实际值 |
| --- | --- |
| 标的 | 510300 / etf |
| 行数 | 69 |
| `as_of` | 2026-10-22T10:38:00+08:00 |
| `fetched_at` | 2026-10-22T02:37:32.601Z |
| 第一行 | time=2026-10-22 09:30，close=4.185 |
| 第二行 | time=2026-10-22 09:31，close=4.178 |
| 最后一行 | time=2026-10-22 10:38，close=4.129 |

每行字段为 `time,open,close,high,low,volume,amount,cumulative_volume,cumulative_amount`。真实分时源没有提供的 OHLC 分量及分钟量额保持 `null`，不按当前报价补造分钟价格。

在项目根目录直接运行：

```sh
node examples/price-refresh.mjs
node --test tests/chart-loader.test.mjs
```

以上命令已执行。例子读取上述完整真实响应；资金延迟、网络失败、旧响应和错标的仅为明确标记的离线模拟，不代表本次对实时接口或数据库执行了这些操作。示例中的固定回放时钟只用于离线验证，线上仍使用当前北京时间。

## `createChartLoader({now})` 与 `load(...)`

**作用**：`now` 为可注入的毫秒时钟，生产默认 `Date.now`。`load` 的 `key` 隔离数据上下文；`item` 指明标的；`requests` 是价格、资金及可选基准请求函数；`onUpdate` 接收独立进度；`isCurrent` 阻止已切换选择的旧请求更新界面或缓存。最多保留 32 个最近上下文。

**真实输入**：上表原始 69 行生产响应。

**调用示例**：下面代码可在项目根目录保存为 `.mjs` 后运行，也可使用上面的完整例子。

```js
import {readFile} from 'node:fs/promises';
import {createChartLoader} from './public/chart-loader.mjs';
const price=JSON.parse(await readFile('./examples/fixtures/price-refresh-510300-real.json','utf8'));
const loader=createChartLoader({now:()=>Date.parse('2026-10-22T10:40:00+08:00')});
await loader.load({
  key:'real|native|510300|etf|intraday|2026-10-22',
  item:{symbol:'510300',kind:'etf'},
  requests:{price:async()=>price},
  onUpdate:state=>console.log({rows:state.price.bars.length,pending:state.pending}),
});
```

**实际输出**：首次通知为加载占位；价格完成后为 69 行。完整示例的延迟资金场景实际输出：

```json
{"step":"price_before_flow","price_rows":69,"pending":{"price":false,"flow":true,"benchmark":false},"benchmark":null,"values_unchanged":true}
```

**前后变化**：69 行真实价格原样进入价格图，不等待资金。未请求的基准始终为 `null` 且 `pending=false`。每次通知复制状态，外部渲染代码不能改写缓存。`load()` 在全部已请求通道结束后才完成。

## `validSeries`：校验标的和可用数值

**作用**：价格及基准至少有一个有限、正数的数值型 `close`；资金至少有一个有限数值型 `main_net`，零净额有效。响应若提供 `symbol/kind`，必须匹配当前标的；基准必须匹配 `000300/index`。明确演示适配器允许省略身份字段。原数组不被过滤或改写，由现有绘图层处理缺失点。

**真实输入**：原 69 行与完全相同的价格值。只为测试错误分支，在离线例子中把响应外层 `symbol` 改为 `159915`；这是故障模拟，不是另一份实际行情。

**调用示例**：运行 `node examples/price-refresh.mjs`，查看 `identity_rejection` 步骤；它经 `load` 调用内部校验函数。

**实际输出**：

```json
{"step":"identity_rejection","simulated_symbol":"159915","expected_symbol":"510300","rows":0,"status":"unavailable","as_of":null}
```

**前后变化**：错标的输入不能成为当前标的的走势；本次显示 0 行且源时间为空。原 fixture 的 69 行、字段、类型和数值不变。

## `retained`：刷新失败后保留有效走势

**作用**：若同一上下文已有成功记录，空响应、拒绝或非法数据不能抹掉它；设置 `status/source_status=cached`、`stale=true`，记录尝试时间和失败原因。源日期跨日时现有 `withMarketFreshness` 会额外标记 `stale`，不会改写源日期。

**真实输入**：首次请求成功得到原 69 行。第二次请求由例子显式抛出“模拟：价格网络故障”。

**调用示例**：运行 `node examples/price-refresh.mjs`，查看 `retained_on_failure`；测试文件另覆盖空数组分支。

**实际输出**：

```json
{"step":"retained_on_failure","initial_rows":69,"initial_pending":true,"rows":69,"status":"cached","source_status":"cached","stale":true,"as_of":"2026-10-22T10:38:00+08:00","fetched_at":"2026-10-22T02:37:32.601Z","error":"模拟：价格网络故障；保留上次有效走势及原始源时间","values_unchanged":true}
```

**前后变化**：刷新开始、请求中、失败后三个阶段均保留 69 行；只增加/改变恢复状态、错误及尝试时间，`bars/as_of/fetched_at`、每行字段类型及金额完全不变。没有用新抓取时间伪装源时间。

## `sourceEpoch`：阻止数据源时间倒退

**作用**：比较原始 `as_of`，缺少时才取记录时间；裸日期时间按北京时间解释，不比较 `fetched_at/served_at`。更早的成功响应仍走 `retained`。

**真实输入**：已保存原 69 行；模拟旧响应仅截取原数据前 2 行并明确把 `as_of` 设为 09:31。两条价格仍来自原记录，修改后的响应外壳仅用于回退测试。

**调用示例**：运行 `node examples/price-refresh.mjs`，查看 `retained_on_older_response`。

**实际输出**：

```json
{"step":"retained_on_older_response","simulated_input_rows":2,"rows":69,"as_of":"2026-10-22T10:38:00+08:00","error":"数据源返回了更早的走势；保留上次有效走势及原始源时间","values_unchanged":true}
```

**前后变化**：较旧的 2 行不会替代已有 69 行；已有时间、字段、类型及数值全部保持。

## `unavailable`、上下文隔离与过时请求

**作用**：没有当前上下文的有效历史时明确返回空状态。生产 key 包含连接 epoch、数据模式、源地址、品种、代码、周期、北京时间日期，切换数据源或日期不能复用另一上下文的记录。每个 key 的请求代数和 `isCurrent` 双重检查阻止晚到旧请求覆盖新选择；32 项之外的旧缓存被淘汰。

**真实输入**：原 69 行缓存在 native 上下文；另一源上下文的输入是明确模拟的空响应。

**调用示例**：运行 `node examples/price-refresh.mjs` 查看 `source_isolation`；运行测试命令检查过时请求、32 项淘汰及快照复制隔离。

**实际输出**：

```json
{"step":"source_isolation","rows":0,"status":"unavailable","as_of":null}
```

相关测试实际为 **9 项通过、0 项失败**，包括价格先显示、请求中及失败保留、旧请求不能覆盖、不同源/日期隔离、旧时间响应回退、身份/类型校验、独立资金/基准和有界缓存。

**前后变化**：不同源的新上下文保持 0 行；原上下文缓存保留原 69 行，不互相合并。过时请求不产生新通知，也不写缓存。

## `public/app.js` 集成步骤

**作用**：`loadChart()` 使用上面的加载器，每个 `onUpdate` 就绘制当前已有通道；同一上下文保留 ECharts 实例。`renderAll()` 不再先清除价格与配对资金历史。首次内置连接时，价格请求与行情快照并行启动，避免先等待包含资金查询的快照才出现价格。

**真实输入**：上述已取得的 510300 真实价格，配合本轮真实资金响应进行浏览器核验；独立等待、失败分支由明确的 QA 模拟触发。

**调用示例**：打开站点选择 510300，在“分时 + 资金”下刷新；正常价格返回后应立即显示上图，资金返回后补下图。离线直接运行统一例子可复现数据调度；浏览器验证由发布前 QA 单独记录。

**实际输出**：上述离线数据调度及 9 项测试已经执行。主任务还在本地浏览器中使用实际 `app.js` 和已捕获的真实生产响应，观察并核对了以下结果。请求等待/失败是显式 QA 模拟，记录值来自真实响应；它们不是发布后线上接口调用结果。

| 浏览器 QA 场景 | 实际观察 |
| --- | --- |
| 资金请求暂不完成 | 69 个价格点的绿色曲线可见；资金点为 0 |
| 放行资金响应 | 69 个价格点与 59 个资金点同时可见 |
| 再刷新时价格返回空/不可用，资金仍等待 | 原 69 个价格点与 59 个资金点仍可见；价格来源显示“缓存 · 未更新” |

临时 QA 页面在打包前移除。本次部署后的实际数据联调由主任务另行验证。

**前后变化**：价格不再因资金慢请求被清空或延后；同一标的刷新不闪退成空图，失败保留真实历史且明确标记。切换标的/数据源仍清除不匹配的画面，资金日期与价格日期保护继续有效。
