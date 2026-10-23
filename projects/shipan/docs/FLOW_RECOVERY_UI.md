# 资金图空白：日期与错误说明

用户截图的文字为“资金日期与价格不一致 / 当前交易日保持空白”。它代表保护逻辑拒绝将异日资金画到价格当天，并不是“主力净额为零”。后端恢复策略见 `FLOW_RECOVERY.md`。

本轮生产观测（2026-10-22，修复前）：

| 接口实际输出 | 源时间 | 状态与点数 |
| --- | --- | --- |
| /api/snapshot 中上证指数 000001 | 2026-10-22 10:15 +08:00 | flow_status=live，main_net=-11891750773 CNY |
| /api/flow-history?symbol=000001&kind=index | 2026-10-21 14:49 +08:00 | stale，229 点，error=东方财富资金分时 HTTP 502 |
| /api/snapshot 中沪深300 000300 | 2026-10-22 10:15 +08:00 | flow_status=live，main_net=-10948910711 CNY |
| /api/flow-history?symbol=000300&kind=index | 2026-10-21 13:49 +08:00 | stale，169 点，同样 HTTP 502 |
| /api/flow-history?symbol=159915&kind=etf | 2026-10-22 10:13:03 +08:00 | recorded，仅 5 个已有采集点 |
| /api/flow-history?symbol=518880&kind=etf | 2026-10-22 10:15 +08:00 | live，45 个资金分钟点，storage_status=ok |

这些数值来自此次实际生产读取；接口来源不同、请求时间独立，不推断所有标的都能持续成功。

## buildPairedData

作用：按同一标的、同一上海日期把真实价格与资金记录放到共用分钟轴；新增 flowDate/dateMismatch 返回两种源日期的差异。参数 priceBars、flowHistory、item 保持原合同。

真实输入：仓库已有 `examples/fixtures/council-market-real.json`，为 510300 的真实历史记录，价格187点、资金185点，日期为2026-10-21。

复制运行：

```sh
node --input-type=module - <<'JS'
import fs from 'node:fs';
import {buildPairedData} from './public/paired-chart.mjs';
const x=JSON.parse(fs.readFileSync('examples/fixtures/council-market-real.json'));
const y=buildPairedData({priceBars:x.price_history.bars,flowHistory:x.fund_flow_history,item:x.selected,priceAsOf:x.price_history.as_of});
console.log({date:y.date,flowDate:y.flowDate,dateMismatch:y.dateMismatch,priceCount:y.priceCount,flowCount:y.flowCount});
JS
```

实际输出：`{ date: '2026-10-21', flowDate: '2026-10-21', dateMismatch: false, priceCount: 187, flowCount: 185 }`。

前后变化：只新增 flowDate/dateMismatch 字段；原始观测金额、价格、日期、187/185点数、空档保持不变。异日输入的原有回归测试仍保证 flowCount=0，不跨日拼接。

## drawPairedTrend

作用：在页面渲染上述对齐结果。输入为实际价格/资金 API 响应，以及当前选中的标的。

调用：打开页面，选择“上证指数 000001”，点击顶部“刷新”。该函数依赖页面 DOM 和 ECharts，由 `loadChart` 自动调用，不作为独立 Node 函数。

输出：若两源日期不同，图中直接列出价格日期、资金日期、接口错误和自动重试说明；下方来源信息仍显示资金实际 as_of，而不是“源时间未知”。若 source_type=sampled_snapshots，标题及点数明确标记“本站采集点，非完整分钟历史”。

前后变化：原来只有笼统日期冲突提示；现在说明具体日期和失败原因。没有把旧资金重贴今日时间，也没有为了连续曲线补零或跨缺口补值。线上新版本的端到端读取在发布后单独验证；本地纯函数及既有对齐测试已执行，不将内部预览网络失败当成真实行情成功。
