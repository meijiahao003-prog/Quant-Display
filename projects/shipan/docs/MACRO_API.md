# 宏观先导持久接口

网页读取 `/api/macro`；每日任务先按 `/macro-workflow.json` 更新宏观资料，再根据研究手册形成有来源的判断，写入同一接口。网页自身不调用模型，也不要求 API Key。投资建议仍使用已有本地专家工作站。

## 权限与数据边界

本站维持 owner-private，Sites 网关验证浏览器和服务访问。后台使用 `get_site` 本次返回的服务权限，仅向当前本站发送；不在源码、文件、日志或任务提示内保存凭据。POST 拒绝跨站 Origin。共享报告不依赖服务请求中不存在的访客身份。

报告按北京时间 `reportDate` 保存一行，`generatedAt` 统一为 UTC ISO 字符串用于原子排序。来源 `sources[].asOf` 单独保留实际源日期、年月、季度、年份或 `null`。报告今天生成，不意味着其中的月度、季度数据在今天发布。所有文本按纯文本供前端显示，链接限定为 HTTPS、本站路径或 Library 引用，不下载调用者传入的 URL。

来源缺失但仍足以形成有条件结论时，使用 `dataStatus: "partial"` 并明确风险。完全无法形成结论时，只写 `failure`；成功报告和更新状态存入不同表，失败不会删除旧结论。GET 总是返回最新报告日期的成功记录以及最新尝试状态；报告过日自动变成 `stale`，不会改写源日期。

## 实际输入、调用、输出和变化

以下操作读取本站当前的真实记录，未在用户浏览器执行；不编造其实际输出。在已登录本站的浏览器控制台复制运行，即可打印真实输入和实际输出。`raw` 是第一次 GET 的真实返回值。

```javascript
const raw = await fetch('/api/macro', {cache:'no-store'}).then(r=>r.json());
console.log('真实输入：当前已存宏观报告', raw.brief);
console.log('实际输出：报告日期/取得时间/来源日期/最新尝试', {
  reportDate:raw.brief?.reportDate, generatedAt:raw.brief?.generatedAt,
  sources:raw.brief?.sources, latestAttempt:raw.latestAttempt,
  freshness:raw.freshness
});
```

前后变化：GET 只读；报告、来源、行数均不变。`checkedAt` 为读取时间，`freshness` 随北京时间跨日计算；它们不进入报告来源。

### 同一真实报告重试示例

作用：验证每天同一 `generatedAt` 的重试不会新建记录或覆盖内容。参数为上面真实读回的 `raw.brief`，示例不会改变报告日期或数值。尚未在用户环境执行。

```javascript
if (!raw.brief) throw new Error('尚未保存有效报告；不能以空对象替代真实输入');
console.log('真实输入', raw.brief);
const repeat = await fetch('/api/macro', {
  method:'POST', headers:{'Content-Type':'application/json'},
  body:JSON.stringify({brief:raw.brief})
}).then(r=>r.json());
console.log('实际输出', repeat.write, repeat.brief);
console.log('前后变化', {
  sameReportDate:repeat.brief.reportDate===raw.brief.reportDate,
  sameGeneratedAt:repeat.brief.generatedAt===raw.brief.generatedAt,
  sameSources:JSON.stringify(repeat.brief.sources)===JSON.stringify(raw.brief.sources)
});
```

运行结果必须以控制台输出为准。对于未受其他并发更新影响的同一报告重试，`write.saved` 应为 `false`，每日行数保持不变；若另外一个任务已保存更新报告，读回值应为那个更新版本。

## 函数与处理对应关系

| 函数 / 步骤 | 作用和参数 | 可运行调用 / 输入 | 输出与前后变化 |
|---|---|---|---|
| `text(value,field,max)` | 检查非空纯文本和长度；field 是错误定位名 | 通过 `normalizeMacroWrite({brief:真实报告})` 运行 | 只去除首尾空白，不生成结论 |
| `date(value,field,today)` | 严格检查真实日历日期、不接受未来日 | 同上，读取 `reportDate` | YYYY-MM-DD 不变；非法或未来日期拒绝 |
| `timestamp(value,field,now,reportDate)` | 要求显式时区、不晚于当前、不早于报告日 | 同上，读取 `generatedAt` | 转 UTC ISO，代表的时刻不变 |
| `link(value,field)` | 拒绝脚本 URL、带凭据 URL、HTTP 和协议相对链接 | 同上，读取来源与手册链接 | 合法链接保持原文，不请求外部链接 |
| `items(value,field,max)` | 限制观察项、风险条数与每项长度 | 同上，读取 watchpoints/risks | 数组顺序和数量不变 |
| `sourcePeriod(value,field,today,now)` | 检查实际源日或源期；未知允许 null | 同上，读取 sources[].asOf | 保留原所属期，不能改成生成日期 |
| `normalizeMacroWrite(body,now)` | 验证 brief/failure 二选一，生成可持久化对象 | 上述 POST 经过此函数；测试命令见下 | 空值、错误结构、未来日期拒绝；不会写数据库 |
| `readMacroBody(request)` | 按实际流字节限制 128 KiB，解析 UTF-8 JSON | 上述 POST 请求；测试同时覆盖无 Content-Length | 保留 JSON 值，超限/损坏正文拒绝 |
| `writeMacroBrief(input)` | D1 原子 upsert，生成时间严格更晚才覆盖同日报告 | 上述真实报告重试 POST | 同一正文重复写入不增行；失败只改状态表 |
| `readMacroBrief()` | 读取最新报告及最新尝试，无上游请求 | 上述 GET | 原日期、文本、来源保持不变 |
| `macroResponse(brief,latestAttempt,now)` | 区分 today/stale/missing，携带动态协议 | 上述 GET | checkedAt 动态计算，报告内容不变 |
| GET / POST 路由 | 保留本站权限边界并返回真实持久状态 | 上述浏览器调用 | 错误分为 400 验证、403 Origin、503 存储；200 仍需核对 write 和读回日期 |

本地纯逻辑校验可运行：

```bash
node --test tests/macro-brief.test.mjs
```

测试数据明确是合成边界样本，不是市场数据或投资结论。当前已执行：9 项通过，覆盖来源日期独立保留、空报告、未来时间、链接验证、失败保留、北京时间跨日、正文大小、真实 SQLite 迁移和每日唯一键。部署后的真实写入与独立读回由主任务执行并另行记录，不把单元测试当作生产验证。

## 请求约定

- 成功写入：`{brief:{reportDate,generatedAt,title,stance,summary,dimensions,watchpoints,risks,sources,dataStatus,sourcePageUrl,handbookUrl}}`。
- `dimensions`：1—12 项 `{name,signal,evidence}`；`sources`：1—30 项 `{title,url,asOf}`。`summary` 最多 8000 字符，单维度证据最多 4000 字符。
- 失败写入：`{failure:{reportDate,attemptedAt,message}}`。必须记录实际失败步骤，不改造旧报告冒充今日。
- 来源 `asOf`：`YYYY-MM-DD`、带时区 ISO、`YYYY-MM`、`YYYY-Qn`、`YYYY` 或 `null`。
- GET：`{ok,brief,freshness,today,checkedAt,latestAttempt,protocol}`；POST 成功另带 `write:{saved,reportSaved,preservedBrief}`。
- 单纯晚到的旧日成功写入可以保留为历史日，但 GET 按报告日期排序，不能替换较新的报告日。

