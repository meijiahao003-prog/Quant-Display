# 每日刷新与会审资料日期

页面报价、选定标的价格、选定标的资金是独立返回的真实数据。观察池较慢时，当前价格与资金请求立即并行启动；观察池对象更新不会废弃仍属于同一来源、标的和周期的分时响应。连接失败不会清空此前取得的同来源报价，旧值保留原始源时间并标为缓存或旧日。

## 报价保留处理

`retainQuoteSnapshot(previous, incoming, now)` 作用：在同一来源和真实模式内，按 `kind:symbol` 与源时间选择报价字段。`now` 是判断北京时间日期的毫秒时钟，不用于改写行情时间。资金字段保留传入数据的独立内容。`failedSnapshot(previous, error, now)` 作用：把失败请求表示为无新报价，然后执行上述保留处理；不生成新价格。

真实输入使用仓库内 `index-watchlist-real.json` 的 16 条已记录生产报价。以下示例已执行；故障文字与 10 月 23 日时钟是回放条件，不代表对当天行情的重新查询。

```bash
node --input-type=module <<'JS'
import fs from 'node:fs';
import {retainQuoteSnapshot,failedSnapshot} from './public/quote-snapshot.mjs';
const before=JSON.parse(fs.readFileSync('examples/fixtures/index-watchlist-real.json','utf8')).before_snapshot;
console.log('真实输入前两行',before.instruments.slice(0,2));
const clock=Date.parse('2026-10-23T10:00:00+08:00');
const failure=failedSnapshot(before,'回放连接失败',clock);
const result=retainQuoteSnapshot(before,failure,clock);
console.log('实际输出前两行',result.instruments.slice(0,2));
console.log('前后变化',{before:before.instruments.length,after:result.instruments.length,status:result.status,
  examplePrice:result.instruments[6].price,sourceTime:result.instruments[6].as_of});
JS
```

实际输出：`before=16`、`after=16`、`status='stale'`；沪深 300 ETF 的价格仍为 `4.188`，源时间仍为 `2026-10-21T13:41:24+08:00`。行数、代码、金额类型、价格与原时间不变；增加逐行缓存/日期标识，不把回放时钟称作源时间。不同代码、演示模式及不同连接来源不复用旧报价。

## 会审资料冻结与日期检查

`freezeCouncilBundle` 在用户点击后立即读取选定标的分时，读取完成才记录冻结时间，并使用此时已有的同来源报价。`buildCouncilBundle` 仍对允许字段做拷贝，新增 `source_dates` 和源日期标识：冻结时间只是材料准备时间。价格与资金分时如果来自不同日期，返回包含两个实际日期的错误，保留原序列，不拼接。选定报价日期与同日分时不同，则原报价放入 `snapshot.historical_selected`，当前报价字段为空；旧报价仍作为明确标记的历史证据存在。

真实输入：已有 2026-10-21 会审报价、2026-10-22 的 69 个价格点和 87 个资金点。以下示例已执行，并通过现有本地 Agent 导入校验。

```bash
node --input-type=module <<'JS'
import fs from 'node:fs';
import {buildCouncilBundle} from './public/council-client.mjs';
import {validateMarketLensBundle} from './investment-agent/src/market-lens.mjs';
const recorded=JSON.parse(fs.readFileSync('examples/fixtures/council-market-real.json','utf8'));
const price=JSON.parse(fs.readFileSync('examples/fixtures/price-refresh-510300-real.json','utf8'));
const flow=JSON.parse(fs.readFileSync('examples/fixtures/etf-minutes-510300-20261022.json','utf8'));
console.log('真实输入',{quote:recorded.snapshot.selected,priceLast:price.bars.at(-1),flowLast:flow.bars.at(-1)});
const bundle=buildCouncilBundle({snapshot:recorded.snapshot,selected:recorded.snapshot.selected,
  price,flow,bundleId:'date-audit-real-replay',now:'2026-10-23T02:00:00Z'});
console.log('实际输出',{dates:bundle.source_dates,currentQuote:bundle.snapshot.selected.price,
  historicalQuote:bundle.snapshot.historical_selected.price,
  pricePoints:bundle.price_history.bars.length,flowPoints:bundle.fund_flow_history.bars.length,
  importedDates:validateMarketLensBundle(bundle).dates});
JS
```

实际输出：今天标签 `2026-10-23`；报价源日期 `2026-10-21`；价格及资金日期均 `2026-10-22`；`all_current_day=false`。当前报价为 null，历史报价保留 `4.193`。69 个价格点与 87 个资金点全部保留，源时间分别仍为 `10:38` 和 `10:57`；导入成功。价格原有累计量额也保留原字段和单位，未转换成分钟量额。

前后变化：增加来源日期说明和历史报价容器，数据金额、分钟数和原始时间保持不变；当前报价不再混用旧日金额，界面同时显示价格源时间、资金源时间与旧日/缺失提示。`public/refresh-controller.mjs` 的跨日、恢复可见、联网恢复及繁忙排队逻辑保持原有行为；无需让观察池请求阻塞选定曲线的启动。

## 已执行验证

```bash
node --test tests/quote-snapshot.test.mjs tests/council-client.test.mjs tests/chart-loader.test.mjs tests/refresh-controller.test.mjs tests/flow-snapshot.test.mjs
node --check public/app.js
```

31 项测试通过，0 失败；脚本语法检查通过。覆盖真实报价回放、空响应、源时间倒退、跨午夜、同来源重连、异日会审资料以及原有 Agent 导入。真实 10 月 23 日在线返回和发布状态需要以部署验证结果为准，上述回放未冒充今天数据。
