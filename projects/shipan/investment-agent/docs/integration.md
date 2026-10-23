# 观市 × 投资专家 Agent 接入记录

本次在原 `expert-council-v0.1.zip` 源码上接入看盘资料与实际方法包。原来的 ChatGPT 登录、真实连接验证、三个独立研究线程、主持人、质询轮数、停止、历史和报告继续使用；没有换成规则分析，没有训练权重。

## 改动前后

| 范围 | 改动前 | 改动后 |
| --- | --- | --- |
| 看盘资料 | 手工粘贴材料 | 观市冻结当前标的的价格、资金与快照；本地窗口接收后开始会审 |
| 专家 | 默认基本面／量化／风控提示词 | 看盘入口固定使用宏观环境、A股波段、林奇基本面三个真实方法包；原一般研究入口和已保存专家设置不变 |
| Skill | 没有文件加载器 | Node 读取入口和全部 Markdown 参考文件，核对每个文件 SHA-256，再注入对应专家上下文 |
| 知识库 | 未连接 | 仅接入原包标记 `content_verified` 的15条投资研究编辑摘要；不载入旧隔离记录 |
| 资金解释 | 依用户文字而定 | 保留资金单位、源时间、缺失和演示标记；禁止把大单分类当机构身份或 ETF 申赎 |
| 来源追踪 | 材料编号与文字 | 另保存快照哈希、原 ZIP／文件哈希、包版本、资料编号和各序列日期 |

## 真实包与角色

原文件未改写。两个同名 `dalio-druckenmiller-market` Skill 置于不同父目录，避免互相覆盖。

| 角色 ID | 包路径 | 内容与边界 |
| --- | --- | --- |
| `macro_environment` | `expert-packages/macro-v1/dalio-druckenmiller-market/` | 补充的宏观 v1：环境、传导、预期与共同风险暴露 |
| `market_structure` | `expert-packages/market-v2/dalio-druckenmiller-market/` | A股 v2：盘面结构、价格／资金、波段条件与反证；宏观方法与上一席共享，不能算第二票 |
| `company_fundamentals` | `expert-packages/lynch/lynch-thinking/` | 林奇公司研究；只有指数／ETF价格资金时须保留基本面证据不足意见 |

`expert-packages/manifest.json` 记录每个原文件的哈希、字节数和是否进入推理上下文。JSON 来源索引与 `openai.yaml` 保留原件并验哈希；实际上下文读取 `SKILL.md` 与 Markdown references。

知识库保存为 `expert-packages/knowledge/verified-investment-summaries.jsonl`，15条＝Dalio 9＋Druckenmiller 6。保留来源 ID、作者／机构、链接、事件／发布日期、定位、摘要、证据说明与前视偏差说明。`content_verified` 是上传包的标记，未在本次接入中重新核验网页或全文；摘要不是作者原话，不是当前宏观事实。无 PIT 或训练批准；旧335条记录未接入。A股 v2 所提及的其他作者完整原库不在附件中，不能声称已经接入。

## 运行与升级

1. 保留旧项目的 `.local` 目录，停止旧服务。
2. 将升级源码放入原项目，保留 `.local`；使用 Node.js 22.16 或更新版本执行 `npm ci`、`npm start`。
3. 在本机 `http://127.0.0.1:4317/` 完成原有 ChatGPT 登录与真实连接验证。
4. 观市右侧连接该本机地址，打开本地接入窗口，接收冻结快照，点击开始三专家会审。

登录凭据、CSRF、账户字段和原有研究历史不发送给线上看盘页。看盘页拿到的是本次会审的最小输出。窗口关闭或服务器关闭就断开；这里没有后台定时会审、交易或通知。

这次开发环境**没有代替用户登录 ChatGPT，也没有执行真实模型推理**。协议测试中的模型返回均为明确的 TEST ONLY 夹具，不能当投资分析结果。

## HTTP 合同

服务器保留 loopback Host、同源 Origin 与 CSRF 保护，不增加通配 CORS。

- `GET /api/market-lens/profile`：返回三个实际包的名称、版本、文件哈希、知识库数量与限制，不返回提示词、CSRF或账户信息。
- `POST /api/market-lens/runs`：请求 `{bundle, maxRounds, model?}`；仍要求 `X-Expert-CSRF`、ChatGPT 登录、连接验证通过、验证模型匹配，且与一般研究共用一个启动锁。
- 会审状态、取消、历史和报告复用 `/api/runs/:id`、`/api/events`、`/api/runs/:id/cancel`、`/api/runs/:id/report`。
- 仅 `/market-lens.html` 允许带用户手势的顶层跨站 GET 导航（Fetch Metadata：navigate、document、`?1`）。所有 API、脚本资产和写入仍拒绝跨站请求。

`bundle` 合同：

```text
schema_version: market-lens.council.v1
bundle_id: 1–128位字母数字/下划线/短横线
analysis_asof: 带时区ISO时间
timezone: Asia/Shanghai
mode: real | demo
selected: {symbol: 六位代码, kind: index | etf | stock, name}
question: 非空，最多6000字
snapshot: 冻结JSON对象（selected报价、观察池及来源等）
price_history: {period: intraday | day | week, status, source, as_of,
                symbol?, kind?, bars: 正常对象数组}
fund_flow_history: {status, source, as_of, unit: CNY | 元,
                    symbol?, kind?, bars: 正常对象数组}
```

单条 bar 使用明确 `time/date/timestamp/as_of/datetime/minute`；只有 HH:mm 时必须有带日期的序列 `as_of/trade_date/trading_date`。输入≤250,000字节，每条序列≤260条。请求原HTTP上限350,000字节不变。日期、标的、资金单位不一致时明确拒绝；不会静默拼接。缺失序列保留空数组。日/周历史允许多日，并与盘中资金分开解释。

输入中的每个非历史字段留在 `ML-CONTEXT`；完整价格／资金为 `ML-PRICE`、`ML-FLOW`，15条知识摘要使用原 `D-S...`／`K-S...` 编号。总共18份材料。这个固定接入入口的材料上限为100,000字符；原一般研究入口仍为12份／60,000字符。超限拒绝且不截断证据。Codex 每条提示词200,000字符的上限仍存在。

## 序列压缩不删数据

正常对象 bars 在校验后可转为：

```json
{"encoding":"market-lens.columnar.v1","columns":["time","main_net"],"rows":[["09:31",0],["09:32",null]],"presence":["11","10"]}
```

`columns` 给出列名；每行与列顺序一一对应。若出现 `presence`，1表示原字段存在，0表示原字段缺失，0对应的 null 是占位。存在字段的 null 仍是原始 null。所有字段都存在时可省略 `presence`。转换不插值、不补分钟、不删行、不舍入、不改变元数据；压缩后若更大，则保留原对象形式。回归测试将压缩结果展开并逐字段比较原输入，包括零、负数、null和缺失字段。

## 已执行验证与真实输入例子

保存的真实读盘输入：`docs/market-lens-real-input.json`，来自本次生产行情读取，为沪深300ETF华泰柏瑞（510300），冻结时间 `2026-10-21T06:04:48.930Z`。这份文件只包含公开市场快照，未附账户或登录数据。

在项目目录运行以下命令可以重现**不调用模型**的导入结果：

```bash
node --input-type=module <<'JS'
import fs from 'node:fs';
import { loadMarketLensProfile, validateMarketLensBundle,
  compactMarketHistory, createMarketLensResearch } from './src/market-lens.mjs';
const bundle = JSON.parse(fs.readFileSync('docs/market-lens-real-input.json', 'utf8'));
const profile = loadMarketLensProfile();
const checked = validateMarketLensBundle(bundle);
const price = compactMarketHistory(checked.bundle.price_history);
const input = createMarketLensResearch({bundle, maxRounds: 1}, profile);
console.log(JSON.stringify({
  role_ids: profile.experts.map(x => x.id),
  knowledge_count: profile.sources.length,
  price_bars: bundle.price_history.bars.length,
  fund_bars: bundle.fund_flow_history.bars.length,
  price_encoding: price.bars.encoding,
  materials: input.materials.length,
  material_characters: input.materials.reduce((n, x) => n + x.text.length, 0)
}, null, 2));
JS
```

实际输出：

```json
{
  "role_ids": ["macro_environment", "market_structure", "company_fundamentals"],
  "knowledge_count": 15,
  "price_bars": 187,
  "fund_bars": 185,
  "price_encoding": "market-lens.columnar.v1",
  "materials": 18,
  "material_characters": 47053
}
```

实际价格材料从20,015字符降至10,319，资金材料从27,618降至15,452；全部187／185点逐字段还原一致。总输入56,991字节。详细实测哈希和统计在 `docs/market-lens-import-verification.json`。

`node --test tests/*.test.mjs` 已实际执行：65项通过、0失败（包括原45项、看盘后端15项和窗口协议5项）。覆盖真实包读取及篡改拒绝、187／185点真实输入、完整240点双序列压力夹具、日期／标的／单位／重复时间拒绝、跨站与CSRF、登录验证与模型门槛、共用启动锁、原专家配置保持、真实文件内容进入各角色提示词、主持人共享证据约束、报告来源和窗口输出去敏。240点压力数据及模型响应是清晰标注的测试夹具，不是实际行情或推理。

## 函数与处理的输入、调用、输出、副作用

| 函数/处理 | 用途与真实输入 | 可运行调用/验证 | 实际输出或未执行 | 前后变化 |
| --- | --- | --- | --- | --- |
| `loadMarketLensProfile` / 内部 `readVerified` | 读取本项目原方法文件、manifest和精选JSONL；核对文件哈希 | 上述命令；真实包及篡改测试 | 三个角色，15条摘要；A股方法说明23,699字符 | 只读文件→内存文本/元数据；无脚本执行 |
| `copyEvidence` / 内部 `visit`、`sha256`、`isObject`、`fail` | 真实bundle的JSON结构、大小与安全值检查，哈希与显式错误 | `validateMarketLensBundle(bundle)`；超限/恶意字段测试 | 输入56,991字节通过；超限HTTP413 | 生成独立快照；原对象未修改 |
| `sourceDay` / `assertIdentity` | 真实源时间转换为北京时间交易日，验证报价/序列标的 | `validateMarketLensBundle(bundle)`；混日/混标测试 | 实际交易日2026-10-21；错配HTTP400 | 保留源时间，增加检查；不合并、不改日期 |
| `validateMarketLensBundle` | 冻结价格资金、源状态、演示标记 | 上述真实输入命令 | 187／185点完整通过；合成与缺失状态被保留 | 输入→脱离原引用的bundle、notes、dates、哈希 |
| `compactMarketHistory` | 消除重复字段名而不丢数据 | 上述命令；展开对等测试 | 实际两序列分别10,319/15,452字符 | 表示方式改变；所有观测和字段不变 |
| `createMarketLensResearch` | 真实bundle＋已核验本地包→固定三专家输入 | 上述命令 | 18份材料，47,053字符，完整来源记录 | 原bundle只读；生成推理输入与marketLens元数据 |
| `GET profile` / `getMarketLensProfile` | 同源页面请求真实包元数据 | HTTP回归测试 | HTTP200，3席、15摘要；无CSRF/账户/提示词 | 首次读取后进程内缓存；不写账户或用户设置 |
| `POST market-lens/runs` / 原启动锁 | 接入bundle，复用登录/验证/模型/并发门槛 | HTTP回归测试；用户本机完成登录后由UI调用 | 夹具验证202、403、409；**真实账号会审未执行** | 验证成功才创建`.local/runs`记录；无交易 |
| `taskData` / `expertPrompt` / `moderatorPrompt` | 把冻结元数据、真实方法文字及来源材料送给对应角色 | 真实文件＋测试bridge回归 | 提示词实测含Skill正文与同源不能投票约束；**真实推理未执行** | 不变更输入；专家仍独立首轮，主持保留缺口 |
| `renderReport` 新增来源段 | 包含marketLens元数据的实际会审对象（模型部分使用测试夹具） | 报告回归测试 | 显示模式、快照/包哈希、15摘要及边界 | 仅生成文本；不写文件、不生成替代结论 |

新增代码只消费上传方法包，不注册或修改个人全局 Skill。所有账户凭据仍由原隔离 Codex 进程管理。无法登录、真实验证未通过或 Skill 校验失败时明确中止，不回退为规则报告。
