# 行情与资金联合分时图

上图是所选标的价格，下图是同一指数或ETF的主力、超大单和大单累计净额；共用上海时间轴、十字光标和缩放。资金直接补拉东方财富已有的当日分钟历史，不必从打开页面开始采满。来源和回退机制见 [INTRADAY_FLOW.md](INTRADAY_FLOW.md)。下方ETF排行和结构仍用独立快照，时间可能与分时末点不同。

## `buildPairedData({priceBars,flowHistory,item,priceAsOf,demo})`

**作用与参数**：将 `{time,close,...}` 价格数组与 `{symbol,kind,unit,bars,...}` 资金响应对齐。`item` 指定当前代码/类型；`priceAsOf` 仅在无价格记录时提供源交易日；`demo=false`，只有显式演示才接受固定演示日的纯时钟。指数必须匹配kind，旧ETF记录允许没有kind。

**真实输入**：`examples/intraday-flow.mjs` 中2026-10-21响应摘录，ETF510300在09:31/13:08的主力为4918230/-435354626元，指数000300在09:31/13:09为786258944/-4140492008元；ETF另有当日09:31价格4.229。指数例子未提供价格。每标的只摘录两点，不代表源端只有两点，也不是当前行情。

**可复制调用**（项目根目录，Node22+，离线）：

```bash
node examples/intraday-flow.mjs
```

**实际输出**（已执行）：

```text
510300: date=2026-10-21, axisSlots=242, prices=1, flows=2, missing0932=null
000300: date=2026-10-21, axisSlots=242, prices=0, flows=2, missing0932=null
```

**前后变化**：数据转为242个交易时刻位置，标签数不等于实测点数；09:30—11:30和13:00—15:00各含121个端点，午休压缩。输入和原金额不变；观测新增 `source_time/source_epoch`；缺口为null，不补值、不变0。界面才把元换算亿元。

## 每一步处理

各步骤通过上述已执行脚本触发，真实输入使用同一日期明确的摘录。

| 步骤与作用 | 真实输入 / 调用 | 实际输出 | 前后变化 |
| --- | --- | --- | --- |
| `finite` 数值检查 | 脚本中的价格4.229、主力4918230 | 原值保留 | 价格须大于0；资金允许正负及0，未知不转0 |
| `parseTime` 按上海时间定位 | `2026-10-21 09:31` 与带+08:00的资金时间 | 都在09:31，资金源时间规范为 `2026-10-21T09:31:00.000+08:00` | 明确时区，不改变源时刻；显式时区先转换上海时间 |
| 标的/类型/单位检查 | 000300、index、CNY | 指数2点通过 | 错代码、错类型、未知单位不绘制；ETF资金不能代替指数 |
| 交易日选择 | ETF有同日价格；指数只有同日资金 | 均为2026-10-21 | 优先最新价格日，再价格源时间，最后资金日；不同日不拼接 |
| 分钟去重和空缺 | 两个不同分钟 | 保留2点，09:32为null | 同分钟取源时刻较新的有效点；不创造中间值 |
| 格式化和提示框 | ETF末点-435354626元 | 数组保留原值，显示时为-4.35354626亿元 | 只在显示时缩放，源时间和取得时间分列 |

## `splitObservedSegments(values,observations,maxGapMs=180000)`

**作用与参数**：将数值/null数组及对应源观测分组，默认允许相邻真实点最多间隔3分钟。

**真实输入**：上面ETF09:31/13:08两个真实摘录点及各自 `source_epoch`。

**调用**：运行 `node examples/intraday-flow.mjs`，脚本执行 `splitObservedSegments(paired.flows.main_net,paired.flowObservations)`。

**实际输出**：ETF、指数都是 `segments=2`；每段保留242个位置、仅1个实际点，09:32仍null。

**前后变化**：2点仍为2点，只新增线段分组。相隔不超过3分钟的点可连线，较大空档和午休断开；短缺口直线只连接两端观测，不代表中间有实测。单点有标记。

## 当前站点真实输入调用

**作用**：取得同标的价格和资金，再进行相同对齐。**真实输入**为运行时响应，不能预写当前数值。

**可复制调用**（已登录站点的开发者工具）：

```javascript
const {buildPairedData}=await import('/paired-chart.mjs');
const symbol='000300',kind='index'; // 也可改为510300、etf
const [price,flow]=await Promise.all([
  fetch(`/api/history?symbol=${symbol}&kind=${kind}&period=1m`).then(r=>r.json()),
  fetch(`/api/flow-history?symbol=${symbol}&kind=${kind}`).then(r=>r.json()),
]);
console.log('真实输入',{price,flow});
const paired=buildPairedData({priceBars:price.bars,priceAsOf:price.as_of,flowHistory:flow,item:{symbol,kind}});
console.log('实际输出',paired);
console.log('前后变化',{priceInput:price.bars?.length,flowInput:flow.bars?.length,priceOutput:paired.priceCount,flowOutput:paired.flowCount,axisSlots:paired.labels.length,message:paired.flowMessage});
```

**执行状态**：该浏览器组合命令本页编写时尚未原样执行；前述离线真实摘录已执行。**前后变化**：资金请求会保存真实分钟历史到D1；前端转换本身不修改输入，旧日缓存不会和新日价格拼接。

## 页面处理函数

共同可操作入口：打开平台，选择510300分时，再切换000300分时。真实输入为当次API响应，生产UI数值本页尚未记录；离线回放不等于生产UI已验证。

| 函数 / 作用及参数 | 可操作调用 | 输出与前后变化 |
| --- | --- | --- |
| `loadChart()` 读取当前价格/资金，独立处理失败 | 选择当前标的，保持分时 | 更新上下图；请求编号、模式、快照和标的防止旧响应覆盖新图；运行时状态待页面验证 |
| `drawPairedTrend(price,flow,benchmark)` 绘图 | 加载流程自动调用 | 共享光标和缩放；源值不变，单点显示，资金缺失时价格独立保留 |
| `currentInstrumentFlow()` 选当前同日分时末点用于摘要 | 分时加载后自动触发 | 摘要净额/源时间跟随当前标的；无同日分时按已有可用数据处理，不用ETF值代替指数 |
| `demoFlowHistory(item)` 服务显式演示模式 | 手动切换演示后选标的 | 输入输出均明确合成，不是本页真实案例，不请求真实源、不写真实存储 |

日K、周K仍展示价格，说明本次资金源是当日分钟历史。离线针对性检查入口：`node --test tests/paired-chart.test.mjs`；合成协议测试数据不进入生产行情或存储。
