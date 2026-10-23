# 投资 Agent 与观市接入

本次复用同项目 `expert-council-v0.1.zip` 的实际 Node / Codex app-server 实现，保留独立首轮、主持人、最多三轮质询、历史、停止与报告导出。在线右栏的旧规则分析、Ollama 选择和训练表单已移除。Python 旧接口保留供已有本地项目兼容，不再是网页读盘入口。

## 三个方法包如何使用

| 平台席位 | 用户文件 | 职责 |
| --- | --- | --- |
| 宏观环境 | dalio-druckenmiller-market.zip，v1 | 政策、信用、预期及风险传导 |
| A 股盘面与波段 | a-share-market-skill-v2.zip，v2 | 价格、资金、结构与触发／失效条件 |
| 公司基本面 | lynch-thinking.zip | 企业逻辑、盈利、负债、估值及反证 |

两个宏观相关包使用相同原技能名称，分别放在独立外层目录，逐文件校验哈希，原文保留。Node 实际读取 SKILL.md 与 Markdown 参考文档并注入对应专家上下文；不是只改角色名称。二者重叠的方法与来源不增加独立证据权重。林奇框架用于指数/ETF时，应说明基本面适用范围和缺口。

dalio-druckenmiller-kb-v2.zip 是支持资料库，不是第四位专家。默认只加载15条包内标记 `content_verified` 的投资研究摘要，排除仅元数据、线索和隔离旧记录。摘要属于方法参考；本次未重新核验原始网页，未训练模型、未实现历史时点回测。

## 用户实际操作

1. 在观市右栏「连接与启动说明」下载投资 Agent，解压。
2. 使用 Node.js 22.16+。macOS/Linux 运行 `bash start.sh`；Windows PowerShell 运行 `./start.ps1`。首次按已有锁文件安装 Codex 依赖。也可在解压目录执行 `npm ci`、`npm start`。
3. 打开本地工作站，沿用原有 ChatGPT 登录、模型选择和真实连接验证。升级旧安装时先停止旧进程，保留原 `.local` 目录，替换源码再启动。
4. 在观市选择标的，写问题，点击「送入投资 Agent」。网页冻结当前快照，并获取该标的已取得的当日价格和资金分钟序列。
5. 本地接入窗口显示资料与连接状态。点击「开始会审」后真实调用模型；各轮专家意见和主持结论返回原侧栏。最多三轮，不要求达成共识。
6. 若弹窗或窗口通信被浏览器限制，点击「导出本次资料」，到本地工作站的「观市看盘接入」导入 JSON。报告和原始会审仍留在本地。

这次没有在用户电脑启动进程、登录账户或执行真实模型会审。网页不会把 ChatGPT 订阅直接当作云端 API，也不要求填写模型 API Key。

## 输入、调用、输出与前后变化

| 处理 | 作用与真实输入 | 可运行调用 | 输出及变化 |
| --- | --- | --- | --- |
| `freezeCouncilBundle` | 点击时保存标的、快照、问题；并发读取本站 price/flow 接口 | 页面「导出本次资料」或「送入投资 Agent」 | 有时间和批次的 JSON。源数据不被改写，缺失不补造；UI标明来源与点数 |
| `buildCouncilBundle` | 输入真实 snapshot、selected、price、flow | `node --test tests/council-client.test.mjs` | 白名单字段、分离的源时间、最多260点/序列；排除令牌等连接配置；返回深拷贝 |
| `councilOrigin` / `newCouncilId` / `acceptsCouncilMessage` | 本机地址、随机数服务、浏览器 MessageEvent | 同上协议测试；页面连接按钮 | 只允许 loopback HTTP；使用安全随机通道；精确核对来源、窗口、通道，拒绝其它消息 |
| `initInvestmentPanel` 及内部发送、展示处理 | 本次冻结资料与本地回传的结构化 run | 页面按钮；本地开始/停止 | 显示对应批次的三专家意见、分歧、缺口；新批次清空旧结果。行情自动刷新不重写已冻结结果 |
| 本地 `validateBundle` / `publicRun` | 资料 JSON、当前 run | `node --test investment-agent/tests/market-lens-protocol.test.mjs` | 校验后接收；回传白名单结构，不传账户、CSRF、提示词、线程、其它历史 |
| `loadMarketLensProfile` | 原包文本、manifest、15条摘要 | `node examples/investment-agent.mjs` | 三位专家实际方法文本+校验哈希；不运行上传包内脚本 |
| `validateMarketLensBundle` / `compactMarketHistory` / `createMarketLensResearch` | 有来源日期的价格与资金序列 | 同上真实记录回放 | 拒绝错标的/跨日拼接；无损列式编码全部点；产生18份证据和固定来源ID |
| `POST /api/market-lens/runs` | bundle、轮数、已验证模型 | 本地接入页「开始会审」 | 保留原登录、CSRF、验证、模型匹配和单任务门槛；新建 `.local` 会审记录。真实模型执行本轮未运行 |
| `python scripts/package-investment-agent.py` | 已审查的投资 Agent 源码和方法文件 | 项目根目录运行该命令 | 更新网页可下载 ZIP；排除 `.local`、依赖、凭据，源码不变 |

本地服务器更细接口、函数和审计见 `investment-agent/docs/integration.md`。

## 已执行的真实数据回放

输入文件 `examples/fixtures/council-market-real.json` 来自本轮实际站点响应。它是固定历史记录，**不是新的实时行情请求**。价格与资金各自源时间均为 `2026-10-21T14:05:00+08:00`。

```bash
node examples/investment-agent.mjs
```

实际输出要点：

```json
{
  "selected": {"symbol":"510300","kind":"etf","name":"沪深300ETF华泰柏瑞"},
  "pricePoints":187,
  "flowPoints":185,
  "expertLoadedChars":[11553,23699,6976],
  "knowledgeSummaries":15,
  "materials":18,
  "evidenceChars":47053,
  "inputUnchanged":true,
  "modelCalls":0
}
```

前后变化：输入原值与分钟数量不变。重复字段名转成列名与值数组，必要时用存在位图区分原本缺失和原本 null；没有删掉时间点。资料进入三专家提示词的行为由注入式协议测试验证，不据此声称真实模型已输出会审意见。

已通过：投资 Agent 65 项测试、网页资料/通道4项测试、原资金/联合图15项回归检查、TypeScript 检查和 JavaScript 语法检查。浏览器已检查侧栏、启动说明和资料导出；内部预览无法访问行情源时明确为空，真实数据导入另用实际生产响应验证。用户本机浏览器的弹窗通信和登录后真实模型会审尚未执行。
