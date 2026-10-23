# 报价、价格走势与资金分时的持久回退

2026-10-23 修复前，价格只在服务实例内保存，实例回收后源失败就会清空；部分报价和较晚返回的旧响应也可能覆盖新值。现在 16 个标的分别存报价、分钟价格和日线价格，最多 48 个固定键。数据库写入按源时间排序，同源时间按实际取得时间排序，因此盘中日线可以修正，但较早请求不能覆盖较晚观测。缓存读出不当作新观测写入；本次真正从上游取得的旧日数据可以保存为旧日记录，但不能覆盖较新的源日。

`as_of`、`fetched_at` 保留各自原值；`today`、`source_date`、`freshness` 在读出时重新计算。昨日数据可以显示为昨日缓存，不会改成今日数值。资金排行仍使用独立资金源。

## 函数、输入和调用

| 函数或处理 | 目的与输入 | 可运行调用 | 输出与前后变化 |
| --- | --- | --- | --- |
| `asStoredMarket(payload, now)` | 接受已保存的真实观测，标为缓存并重算北京时间日期 | `node examples/durable-market.mjs` 间接调用 | 已执行；10 月 22 日价格在 10 月 23 日回放时为 `stale` / `source_status=cached`，源时间和取得时间不变 |
| `validQuote(row, item)` | 检查报价身份、有效正价格和源时间；供快照读写调用 | `node --test tests/durable-data.test.mjs` | 已执行；不把缺失报价与错误标的用于恢复 |
| `selectDurableSnapshot(upstream, stored, now)` | 对 16 个标的各自选完整的较新观测，失败或缺项时保留原行 | 同上测试 | 已执行合成边界输入；10:18 旧响应不能覆盖保存的 10:20 报价，另一个缺失标的也保留；不同日分别标记 |
| `selectDurableHistory(upstream, stored, now)` | 按相同标的、种类、周期选择整段真实序列，不跨日拼接 | `node examples/durable-market.mjs` | 下方真实保存输入回放已执行；原 69 个点保留，昨日日期不变 |
| `marketHistoryKey(symbol, kind, period)` | 校验固定观察池与 `1m` / `day` 周期，生成持久键 | 通过 `GET /api/history?symbol=510300&kind=etf&period=1m` | 输入固定，键为 `price:etf:510300:1m`；不允许任意源 URL。新部署后的数据库联调由主更新流程核验，本文不预报结果 |
| `readStoredMarket(key)` | 只读持久记录，不请求上游；`quote:snapshot` 组合 16 个独立报价键 | `GET /api/refresh` 或 `GET /api/refresh?symbol=510300&kind=etf` | 返回原始源时间、取得时间及缓存状态；新部署读回结果尚待主更新流程执行 |
| `readStoredMarketStatus()` | 一次读取最多 48 个键的日期、取得时间和点数摘要 | 后台更新状态检查调用 | 无上游请求，不将读取时间算作更新；新部署尚待执行 |
| `isPersistablePriceObservation(payload)` / `saveMarketRows(records)` | 区分真正新取得的上游响应与缓存读出，原子 upsert 拒绝覆盖更旧源时间 | `node --test tests/durable-data.test.mjs`；写入由两个加载器调用 | 已执行边界测试：10 月 23 日取得的 513100 旧日日线允许保留 10 月 22 日源日期；缓存、缺少取得时间与未来日不可写；较旧源日仍不能覆盖已有新日。新增迁移 `0002_pale_scream.sql` |
| `getDurableSnapshot()` | 请求腾讯报价，按标的保存后与持久记录比较 | `POST /api/refresh`、正常 `/api/snapshot` | 与页面和后台共用同一读写；部分响应不会清空其他已存报价。数据库写结果为 `storage_status` / `storage_saved` |
| `getDurableHistory(symbol, kind, period)` | 请求真实价格序列，写入并选择可用的新序列 | `GET /api/history?symbol=510300&kind=etf&period=1m` | 原先实例回收即无缓存，现在读取持久价格；旧序列保持旧日标签 |
| `getDurableFlowHistory(symbol, kind)` | 共用资金分时抓取、保存、已存分钟与已采样点回退 | `GET /api/flow-history?symbol=510300&kind=etf` | 即使上游返回成功也比较持久源时间，避免较旧的成功响应遮盖新记录；保存真实序列，缺口不补造 |

上述 HTTP 调用需要站点本身的访问授权；访问机制与后台运行步骤见后台更新说明。存储迁移只增加新表，不更改已有资金表及历史迁移。

## 真实保存输入的可运行回放

`examples/fixtures/price-refresh-510300-real.json` 是此前实际取得的腾讯 510300 价格序列，源时间 `2026-10-22T10:38:00+08:00`。以下是模拟源不可用后的回退测试，不是 10 月 23 日实时行情：

```bash
node examples/durable-market.mjs
```

实际输出：

```json
{
  "before": { "status": "unavailable", "bars": 0 },
  "after": {
    "status": "stale",
    "source_status": "cached",
    "source_date": "2026-10-22",
    "as_of": "2026-10-22T10:38:00+08:00",
    "fetched_at": "2026-10-22T02:37:32.601Z",
    "bars": 69,
    "bars_unchanged": true
  }
}
```

验证：`node --test tests/durable-data.test.mjs` 的 5 项通过；TypeScript 检查通过。数据库迁移已生成并检查为单条建表语句；托管数据库写入须在发布后再核验，单元测试不代表已上线。
