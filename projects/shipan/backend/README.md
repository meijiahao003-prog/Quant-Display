# 观市 · 本地数据与 Agent 网关

这是平台的真实行情、样本和模型服务。在线展示站本身是静态前端；运行此网关后可在同一网址打开完整前端，并读取本机的真实数据。Python 服务不会运行在静态托管站上。

## 启动

需要 Python 3.11+。从完整源码根目录执行：

```bash
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell 使用：.venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt
cd backend
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

浏览器打开 `http://127.0.0.1:8000`；交互式 API 文档在 `/docs`。此时前后端同源，不需要 CORS 配置。公网 HTTPS 展示站直接访问本机 HTTP 可能被浏览器拦截；优先使用本地完整站。如需独立部署网关，使用 HTTPS，并将 `MARKET_LENS_CORS` 设置为确切的前端域名，多个域名用逗号分隔。

独立部署时必须使用 HTTPS/TLS 并配置 `MARKET_LENS_TOKEN`：将足够长的随机令牌通过部署环境变量设置，再把同一令牌填入前端连接设置的 Bearer 访问令牌字段。令牌配置后，所有 `/api/*`（包括 `/api/health`）均需 `Authorization: Bearer <令牌>`；缺少或错误返回401。CORS预检支持Authorization头。未配置令牌时默认仅用于回环地址本机运行。不要把令牌写入源码仓库、截图或日志。

## 实际接入

| 层 | 实际实现 | 状态 |
|---|---|---|
| 行情 | AKShare：指数、ETF快照/分钟及日K、大盘资金历史 | 真实请求；上游可限流或不可用 |
| 服务 | FastAPI、Pydantic、SQLite、90秒缓存 | 可本地运行 |
| 编排 | LangGraph 执行三角色规则读盘步骤 | 实际库调用；规则输出清楚标识 |
| 本地模型 | Ollama `/api/chat` | 配置模型后启用，默认关闭 |
| 训练 | scikit-learn LogisticRegression | 人工读盘标签模仿基线，不是LLM微调 |

不需要云端模型 API key。规则模式不调用任何大模型；Ollama 模式使用用户本机已安装模型。

配置本地 Ollama（需自行安装并下载所选模型；下列模型名示意应换成 `ollama list` 中实际名称）：

```bash
export OLLAMA_MODEL='你的已安装模型名'
export OLLAMA_URL='http://127.0.0.1:11434'
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

该调用只做推理，不修改 Ollama/ChatGPT 权重，也不把 ChatGPT 订阅当作可调用的公共 API。

## 数据口径

- `fund_etf_spot_em` 当前底层源为 `push2delay.eastmoney.com`，一律标记延迟；延迟时长没有承诺。
- `fetched_at` 为成功获取来源数据的实际时间，缓存读取不会更新它；顶层取各成功来源获取时间的最大值，所有来源均未成功时为null。`served_at`只记录本次API响应组装时间。`as_of`/`source_time` 为上游提供的时间。指数快照无源时间时返回 `null`，不会拿获取时间冒充源时间。大盘资金另外提供`market_flow_as_of`与`market_flow_fetched_at`，不与报价时间混用。
- 主力净额是行情商按订单规模分类的估算口径，不是已识别机构账户流向，也不是 ETF 申赎现金流。
- ETF快照与大盘资金净额文档未明确金额单位：返回原值，`unit=source_raw_unverified`，不自动除以一亿。指数日K成交额文档明确元，标 `CNY`。
- 大盘资金流仅历史日频；不把该值分配给任一指数。
- 来源失败保留最后成功值，标 `cached` 并保留旧时间；首次失败为 `unavailable`，数值 `null`。子进程最多40秒（默认20秒）强制停止，避免无限等待。
- 指数/ETF各有固定观察池；源缺标的时仍保留列表行并标为不可用。当前没有全市场搜索，也没有逐笔/L2、真正机构持仓或ETF现金申赎服务。

官方来源：

- https://akshare.akfamily.xyz/data/fund/fund_public.html
- https://akshare.akfamily.xyz/data/index/index.html
- https://akshare.akfamily.xyz/data/stock/stock.html
- https://github.com/akfamily/akshare/blob/main/akshare/fund/fund_etf_em.py
- https://github.com/langchain-ai/langgraph

## 样本与训练

`POST /api/samples` 保存用户标注的涨/中性/跌读盘看法。标签不是系统自动生成的涨跌结果，不能当作未来收益目标。`GET /api/snapshot` 返回服务端注册的`snapshot_id`；保存样本时服务端按ID读取自己的原始快照，忽略客户端提交的行情数值。把演示数据的mode改成delayed、伪造ID或改报价都不能写入伪造行情。未注册/过期ID、不可用行情和重复标的时间戳会拒绝。样本只追加，数据库默认在 `backend/data/`，不会提交到源码仓库。

当前注册表为单进程内存最近256份快照；重启或超出窗口后须刷新真实快照再标注。部署保持默认单worker；多worker部署需先改为共享快照存储。训练仅纳入通过此流程写入的`provenance=server_snapshot_v1`样本，旧版未验证记录不会参与训练。

`POST /api/train` 要求至少50条真实样本与50个独立观测时间，且训练部分至少2个标签类别。按观测时间分组取前80%训练、后20%验证，同一时点的不同标的不跨区间。缺失填充和标准化只拟合训练集。特征仅为快照涨跌幅和行情商主力净占比；标签由用户提供。成功后保存本地模型和真实验证指标。

这些指标测的是对人工读盘标注的模仿程度；不能证明预测未来收益有效。未来真正训练读盘大模型仍需专家案例、统一标签、行情/图像与时间对齐、独立验证集和微调执行环境。此版本不包含GPU微调和自动下单。

## 可运行例子与检查

全部主要处理步骤的作用、真实输入、调用、实际输出打印以及前后变化见 `EXAMPLES.md`。以下命令只读取真实上游或本地已保存数据：

```bash
cd backend
python diagnostics.py snapshot
python diagnostics.py history
python diagnostics.py analyze
python diagnostics.py samples
python diagnostics.py features
```

脚本不会用生成数据替代真实结果。真实上游失败时打印错误/空值。单元测试另有明确标记的合成夹具，仅测试接口和边界，不是行情结果。

## 环境变量

| 变量 | 默认 | 用途 |
|---|---|---|
| `MARKET_LENS_DATA` | backend/data | SQLite与模型文件目录 |
| `MARKET_LENS_CACHE_TTL` | 90 | 获取间隔下限60秒 |
| `MARKET_LENS_SOURCE_TIMEOUT` | 20 | 单个上游子进程秒数，5–40 |
| `MARKET_LENS_CORS` | localhost:8000两种主机形式 | 前端确切Origin白名单 |
| `MARKET_LENS_TOKEN` | 未配置 | 配置后保护所有/api/*的Bearer令牌；独立部署须设置 |
| `OLLAMA_MODEL` | 未配置 | 本地已安装模型名称 |
| `OLLAMA_URL` | http://127.0.0.1:11434 | 仅服务管理员配置的本地模型地址 |

服务默认只绑定回环地址。若改为公开网络服务，必须启用HTTPS并配置上述令牌认证，另按部署需求加入配额；当前代码没有多用户账户系统。
