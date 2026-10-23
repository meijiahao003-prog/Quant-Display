# 观市 · Market Lens

中国指数 / ETF 看盘工作台。重点展示价格走势、当日主力资金分时及大小单结构。右侧接入同项目的本地投资 Agent：三个 Skill 专家与主持人会审。

## 当前交付边界

- 在线站点：腾讯提供6个指数与10只ETF的报价/走势；东方财富当日资金分时接口为这些标的提供分钟累计净额，下方ETF排行和结构继续用快照。页面可见时每60秒刷新，无需Token。演示模式需手动切换。
- 本地项目：前端 + FastAPI / AKShare 真实行情适配 + LangGraph 规则工作流 + SQLite 样本 + scikit-learn 基线训练。本次新增资金分时代理与D1存储运行于线上Worker；本地Python资金适配仍是原有范围，不自动具备这套新接口。
- 腾讯报价及东方财富资金均已取得真实HTTP200响应；源时间、抓取时间和单位分别处理。输入输出和验证范围见 `docs/TENCENT_DATA.md` 与 `docs/EASTMONEY_FLOW.md`。真实源失败不会返回演示数据。
- 联合图直接补拉东方财富已有的当日分钟资金序列，指数与ETF按各自标的口径显示，D1保存真实返回值用于故障回退。缺失分钟留空；不代表机构身份或ETF申赎。接口函数、真实输入输出与边界见 `docs/INTRADAY_FLOW.md`。
- 投资 Agent：复用原 expert-council 的 Node + Codex app-server、ChatGPT 登录、独立研究与主持流程。新增实际 Skill 文本加载、冻结行情与资金分时传入、当前会审结果回传。模型在用户本机运行的 Agent 调用，在线 Worker 不持有模型账户；首次需要下载新版 Agent、登录与验证。
- 当前训练是人工标签模仿分类器，**不是 LLM 微调、投资专家蒸馏，也不是已验证的收益预测模型**。

## 在本地启动

作用：建立独立 Python 环境，安装已固定版本的依赖，启动同源网页与数据服务。
真实输入：本机 Python 3.11+、当前项目、可访问行情商的网络。不要求云端模型 API Key。

```bash
python -m venv .venv
```

macOS / Linux：

```bash
source .venv/bin/activate
```

Windows PowerShell：

```powershell
.venv\Scripts\Activate.ps1
```

```bash
python -m pip install -r backend/requirements.txt
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

打开 http://127.0.0.1:8000 。真实上游失败会显示不可用或缓存。接口文档为 http://127.0.0.1:8000/docs 。

实际输出：本次没有运行你电脑上的服务，不伪造行情输出。执行后页面显示真实来源状态，终端显示请求结果。
前后变化：新增虚拟环境和本地服务进程；读取行情不修改原始数据。保存标注才会增加 SQLite 记录；训练才会创建模型。

## 平台使用

1. 默认观察池为6个指数和10只ETF；名称 / 代码搜索、分类、自选、涨跌与资金排序。
2. 默认「行情 × 主力资金」联合图：上图价格，下图主力/超大单/大单净额；同一指数或ETF、同日按分钟对齐，十字光标及缩放联动。切换标的同时更新两部分。日K、周K保留价格视图；真实周K从日K按周聚合。
3. 联合图下方保留ETF资金排行与大小单结构。分钟曲线来自上游已有历史，不必等页面采满；相隔不超过3分钟的真实点连线，更长空档断开，缺口不补值。价格与资金各自源时间单列。实现、真实输入输出及边界见 `docs/PAIRED_CHART.md`。
4. 规则预警在网页打开时工作；真实模式每60秒检查；线上行情及资金服务缓存60秒，本地Python服务缓存90秒。网页关闭后不继续运行。重开页面会请求源端已有的当日资金历史；本次没有新增后台定时采集。
5. 右侧「投资 Agent」使用宏观环境、A 股盘面与波段、林奇基本面三个独立角色，实际加载上传的 Skill；宏观知识库提供15条已筛选的方法摘要。原规则读盘及训练表单已从网页移除。
6. 点击「送入投资 Agent」打开本机接入页，把当前选定标的、同一批快照、当日完整已取得的价格与资金序列、来源时间和问题冻结传入。在本地确认后开始最多三轮会审，结果按原资料批次回到侧栏，不随行情刷新改写。
7. 本机服务尚未启动或浏览器不允许窗口通信时，可导出资料，在本地接入页手动导入。现有本地工作站的历史与配置保存在 `.local`，升级时保留。细节与真实输入输出见 `docs/INVESTMENT_AGENT.md`。

## GitHub 选型（2026-10-21核查）

Stars为检索时页面显示的近似值，会变化，不是质量评分。

| 官方项目 | 约 Stars | 使用状态 | 用途 |
| --- | ---: | --- | --- |
| [Apache ECharts](https://github.com/apache/echarts) | 67.6k | 实际内置6.1.0 | 行情与资金可视化，Apache-2.0 |
| [AKShare](https://github.com/akfamily/akshare) | 23.0k | 后端真实调用 | 中国市场数据，MIT |
| [LangGraph](https://github.com/langchain-ai/langgraph) | 43.6k | 后端实际调用 | 三角色并行编排，MIT |
| [TradingAgents](https://github.com/TauricResearch/TradingAgents) | 112k | 架构参考，未整体安装 | 分析师/研究/风险分工，Apache-2.0 |
| [Microsoft Qlib](https://github.com/microsoft/qlib) | 49.6k | 后续扩展参考 | 数值特征、训练与回测，MIT |
| [OpenBB](https://github.com/openbq-org/OpenBB) | 74.5k | 后续扩展参考 | 多供应商数据平台，V5 Apache-2.0 |
| [FinGPT](https://github.com/AI4Finance-Foundation/FinGPT) | 21.4k | 后续扩展参考 | 金融语言任务与微调，不等同中国ETF读盘能力 |

没有将上述仓库的完整功能写成已经集成。选择轻量组合而非将多个大型研究框架硬拼到同一运行环境。

## 接口、真实数据例子与验证

详见 `backend/README.md`、`backend/EXAMPLES.md`、`backend/VALIDATION.md`。
`backend/diagnostics.py`逐步打印真实输入、输出、字段、行数和空值变化；未获取真实输入就显示失败，不编造输出。

可手动选择的合成演示情景固定在2026-10-20 14:30，不代表该日真实市场。真实数据无源时间时保持未知，抓取时间单列。
数据字段及函数依据 AKShare 官方文档：

- https://akshare.akfamily.xyz/data/fund/fund_public.html
- https://akshare.akfamily.xyz/data/index/index.html
- https://akshare.akfamily.xyz/data/stock/stock.html

## 代码与数据目录

- `public/`：前端文件、内置 ECharts及许可证；也由本地Python服务提供。
- `app/api/` 与 `lib/market-data.mjs`：线上同源行情代理与数据规范化。
- `lib/intraday-flow.mjs`、`lib/intraday-flow-store.ts`：当日资金分时解析、缓存与D1持久化；`examples/intraday-flow.mjs` 为可运行的真实历史摘录回放。
- `dist/`：由 Sites / Vinext 生成的部署产物，不手工编辑。
- `investment-agent/`：原专家圆桌、本地 Codex 桥、三个 Skill、精选知识库、看盘窗口接入与验证。
- `backend/`：保留的 Python 数据适配、FastAPI、规则接口、训练、诊断与测试；已不作为网页右侧读盘引擎。
- `backend/data/`：本机创建的数据库与模型，不纳入源码归档或版本控制。
- `THIRD_PARTY_NOTICES.md`：组件来源与许可说明。

对接你已有的QMT或ClickHouse时，可替换 `fetch_source / normalize_quotes` 适配层，保留接口字段、源时间、状态和单位。当前没有声称已读取你的QMT或数据库。

## 线上同源接口

`/api/health` 报告线上服务能力；`/api/snapshot` 返回观察池；`/api/history?symbol=510300&kind=etf&period=1m` 返回实际价格分时；`/api/flow-history?symbol=510300&kind=etf` 和 `?symbol=000300&kind=index` 优先返回东方财富已有的当日分钟资金，并保存真实序列。上游失败时回退到明确标记的已存数据。所有路径可在已登录的站点同一来源调用，无需将令牌写到前端。参数仅允许观察池内资产；不支持任意 URL 代理。

本次变化：主资金图从页面快照采样改为东方财富当日分钟历史，指数与ETF均可请求；源端缺数据时明确显示空缺或缓存。新分钟历史与旧ETF采样分表保存、不拼接。右侧摘要使用当前标的分时末点，Python训练逻辑保持不变。
