# ETF 资金快照接入与验证

线上价格与走势继续使用腾讯。本文描述下方ETF排行和大小单结构使用的当日累计净额快照，字段对应 AKShare `fund_etf_spot_em()`。主图已改用独立的指数/ETF当日分钟历史，见 [INTRADAY_FLOW.md](INTRADAY_FLOW.md)。Cloudflare Worker 直接读取该公开上游，线上不运行 Python AKShare、不需要 Tushare Token。

## 源、字段和单位

- 上游：`https://push2delay.eastmoney.com/api/qt/clist/get`
- 官方映射：https://github.com/akfamily/akshare/blob/main/akshare/fund/fund_etf_em.py
- 观察池筛选语法参考：https://github.com/akfamily/akshare/blob/main/akshare/index/index_global_em.py
- 官方金额渲染：https://quote.eastmoney.com/center/static/build/index.js
- 官方资金表单位：https://quote.eastmoney.com/zs000107.html?jump_to_web=true

保留 `fltt=2`、`invt=2`、`np=1`、`pz=100`；将全市场 `fs=b:...` 收窄为固定十只 ETF 的 `i:1.510300,...,i:0.159915`。已实测单次 HTTP200 返回 total10、10条数据。`pz=2000` 实测仍只有100条，不依赖它绕过分页。

| 源字段 | 输出 | 单位 |
| --- | --- | --- |
| f62 / f184 | main_net / main_pct | 元 / % |
| f66 / f69 | super_net / super_pct | 元 / % |
| f72 / f75 | large_net / large_pct | 元 / % |
| f78 / f81 | medium_net / medium_pct | 元 / % |
| f84 / f87 | small_net / small_pct | 元 / % |
| f124 | as_of | Unix秒转上海时区 |
| f297 | trade_date | YYYYMMDD转YYYY-MM-DD |

AKShare文档对部分金额单位写“-”，因此另查东方财富官方前端：主力净流入直接映射 `f62`，格式化函数直接除以1e8追加“亿”、除以1e4追加“万”，没有预先缩放；官方资金表标注元。实际10只ETF中 `f62=f66+f72`，`f62/f6*100` 与 `f184` 的舍入值相符。服务保留原始元，界面再换算亿元；这些一致性检查也用于发现上游字段变更。

主力净额是供应商按单大小分类的统计值，不是已识别机构的真实账户资金。此源只有净额，不能反推总流入和总流出，不能替代 ETF 份额申赎。这个快照只覆盖ETF；指数分时另从当日资金历史接口读取。

## 解析与读取函数

作用：`fundFlowUrl()` 生成白名单请求；`parseFundFlow(payload)` 转换实际JSON字段；`getFundFlow()` 增加12秒超时、60秒缓存、并发合并和失败时带标记的最后成功值。`createFundFlowClient({fetchImpl,now})` 用于离线故障测试，不用于生成展示数据。

真实输入：2026-10-21 10:37:54上海时间，510300返回 `f62=-173944896,f66=-358195344,f72=184250448,f124=1792550274,f297=20261021`。

在项目根目录运行（Node22+，需要上游网络）：

```bash
node --input-type=module - <<'JS'
import {fundFlowUrl,parseFundFlow,getFundFlow} from './lib/fund-flow.mjs';
const response=await fetch(fundFlowUrl(),{signal:AbortSignal.timeout(12000)});
if(!response.ok) throw new Error(`HTTP ${response.status}`);
const input=await response.json();
const parsed=parseFundFlow(input);
console.log('真实输入:',input.data.diff.find(row=>row.f12==='510300'));
console.log('解析输出:',parsed.rows.find(row=>row.symbol==='510300'));
const live=await getFundFlow();
console.log('客户端输出:',live);
console.log('前后变化:',{sourceRows:input.data.diff.length,outputRows:parsed.rows.length,status:parsed.status,unit:parsed.unit});
JS
```

执行状态：上述组合命令未原样重复执行；已独立执行真实源请求、真实返回值的 `parseFundFlow`，以及完整 `getFundFlow()`。完整客户端实际输出为 `status=live,rows=10,as_of=2026-10-21T10:39:37+08:00,fetched_at=2026-10-21T02:39:37.884Z`。510300主力净额 `-186438976`元，159915为`-911336432`元。这是验证时记录，运行时数据会变化。

前后变化：字段由f编号转可读名称；金额数值保持元；时间保持源时间，抓取时间单列；缺失值为null，合法0保留0；错市场、日期不一致、异常字段比例不进入绘图。失败不生成演示数据，缓存保留原源时间与原抓取时间并标注cached/stale。

## 合并、保存与读取

`mergeWatchingSnapshot(quotes,flow)` 合并独立来源，只给ETF附加资金字段，不覆盖腾讯价格和时间。`getWatchingSnapshot()` 并行请求两个来源；一个源故障不隐藏另一个源。`getWatchingHealth()` 报告能力，不宣称上游一定在线。

旧版 `saveFlowSnapshot(flow)` 接收已解析的真实快照并写入D1 `fund_flow_samples`；`getStoredFlowHistory(symbol)` 读取最新已存交易日。按ETF与源分钟去重，单分钟只保留较新的实际源时间；缓存、失败、无效值不增加新点。保留30天，单日返回最多500点。旧采样只作为ETF的最后回退，不与新表 `intraday_fund_flow_bars` 的源端分钟历史混合。

可在已登录站点的开发者工具控制台运行真实同源例子：

```javascript
const snapshot=await fetch('/api/snapshot').then(r=>r.json());
console.log({price_status:snapshot.status,flow_status:snapshot.flow_status,flow_time:snapshot.flow_as_of,storage:snapshot.flow_storage_status});
const history=await fetch('/api/flow-history?symbol=510300&kind=etf').then(r=>r.json());
console.log(history);
```

执行状态：上述浏览器示例未原样执行，旧快照验证见前文；新分时验证见 `INTRADAY_FLOW.md`。输入为已登录同源请求，不需要把私有站点访问令牌写进前端。前后变化：刷新仍保存实际快照；`/api/flow-history` 现在优先请求当日分钟历史并保存到独立新表，失败再回退。保存失败会单列状态，当前资金快照仍可看。

旧采样表只包含本站实际采集点；新主图直接补拉上游已有的当日分钟序列，不必等待页面采满。页面可见时每60秒刷新；关闭页面不会后台持续采集，重开后可再取得源端仍提供的当日记录。源端缺口不补值，不能把相邻累计净额差称为总流入或总流出，也不能跨交易日作差。

## 检查

`node --test tests/market-data.test.mjs tests/fund-flow.test.mjs` 已通过10项测试：字段、空值、单位变化检测、错日期、故障缓存、价格资金独立合并与原腾讯接口回归。测试夹具明确为合成协议数据，不进入线上行情或持久化记录。
