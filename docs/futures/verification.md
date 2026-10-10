# 本次整理的验证记录

验证对象：公开仓库基线 `6eebad739dc37f0976ab79dfb261461276478d43` 上新增的统一量化研究系统。日期：2026-10-10。所有新增模型试验均使用合成数据，没有连接真实交易数据库、供应商账号或执行原始 Notebook。

## 初次系统整合检查（17 份来源）

| 检查 | 本地结果 / 边界 |
|---|---|
| 单元与集成回归 | 77 项测试、23 个子测试通过，覆盖原股票模块和新系统；准确命令见下 |
| 四模型完整编排 | XGBoost、LightGBM、Transformer、iTransformer 的选轮数、冻结重训、延后 Test 加载、预测/QC/manifest 输出均完成 |
| 股票因子横向接入 | 原 `DollarVolumeStability` 两个窗口 → 前缀数据快照 → 统一面板 → XGBoost 两阶段训练 → Test 完成 |
| 时间融合 | 真实后端计算的合成 XGBoost→OOF→Ridge→最终基模型→Test，以及分层核心/残差选择/晋级/测试通过 |
| 连续合约 | T+1、30 自然日红线、严格确认、主次互斥、缺失不造平、因果价格桥接与追加未来数据前缀不变性 |
| 预处理与缓存 | 验证期扰动不改变 Fit 状态，PCA 保存恢复与篡改检测，训练特征变化使 OOF 缓存失效 |
| 跨边界标签 | Fit→Valid 和 Valid→Test 都执行 purge/embargo；不依赖提前打开测试文件 |
| 脱敏 | 静态发布扫描无命中；对原件中提取的 18 个路径/连接/疑似敏感字面量做精确比对，新增公开代码和配置未保留它们 |
| 原件完整性 | 17 份原文件 SHA-256 与处理前一致；没有修改原件，没有发布原始 Notebook |
| 代码检查 | Ruff 所配置的错误规则与 Git diff 空白检查通过 |
| 打包 | 构建可安装 wheel 与源代码包；GitHub Actions 工作流已配置，尚不表示远端 CI 已执行 |

未覆盖：CatBoost 实际训练、CUDA/MPS、真实行情全量重算、所有数据供应商的时点真实性、回测执行/成本/盈亏、跨平台逐位复现。测试数量不能替代这些验证。

## 可复核命令

```bash
python -m pytest -q
python -m ruff check src/futures_research src/quant_research tests/futures tests/test_factor_bridge.py tools/check_public_release.py
python tools/check_public_release.py
python -m build --no-isolation
quant-research demo --models xgboost,lightgbm,transformer,itransformer --output runs/comparison
python examples/run_factor_bridge_demo.py --output runs/equity-bridge
```

本地环境为 Python 3.12.14、macOS arm64，精确依赖版本在 [`requirements/verified-macos-arm64-py312.txt`](../../requirements/verified-macos-arm64-py312.txt)。树模型验证通过临时动态链接配置复用 PyTorch wheel 的 OpenMP 运行库；未写入本机路径或更改系统级配置。

随机种子、处理器状态、数据指纹、源代码哈希和各产物 SHA-256 记录在每次本地 `runs/` 的 manifest 中。运行 ID、时间戳、绝对产物路径和计时会不同；这些本地运行产物未纳入公开 Git 仓库。合成输出中没有可解释为实际收益、风险溢价或模型优劣证据的分数。


## 商品期货 CTA 策略增量验证（S18）

2026-10-10 新增分板块训练策略，以来源 S18 为主要依据，默认标签按确认后的“未来 30 分钟收益减板块中位数”实现。此节记录增量完成后的检查，不覆盖原件真实业绩。

| 检查 | 结果与范围 |
|---|---|
| 完整回归 | 131 项测试、26 个子测试通过；新增标签/增强、组合/收益计算、编排与时点回归 |
| 默认策略演示 | 4 板块 LightGBM 选轮数、冻结重训、Test、排名信号、多空组合和研究曲线完成 |
| 四模型策略演示 | 4 板块 × XGBoost / LightGBM / Transformer / iTransformer，共 16 个最终模型完成训练、预测及固定权重集成 |
| 截面依赖 | 特征与标签传播板块同刻最晚时点；Fit-only 增强；未来扰动不改变既有截面 |
| 加载顺序 | 全部板块模型冻结后才调用 Test loader；仓位固定后才调用执行收益 loader |
| 历史身份 | Train/Valid 与 Test 均检查板块/角色映射的声明可用时间；未来映射拒绝回填历史 |
| 选券独立性 | 修改 actual 与 pairwise_finite 不改变仓位；序列暖启动仅根据预测是否可用确定覆盖 |
| 收益计算 | 拒绝重叠区间、提前信号、缺失持仓收益与资金耗尽；固定板块资金、双边成本含最终平仓 |
| 脱敏与来源 | 59 个公开文件静态扫描无命中；S18 的 4 个疑似路径字面量精确比较无命中；原文件 SHA-256 未变化 |
| Notebook 迁移 | 39 个单元格按来源归并为模块与文档；26 个含历史输出的单元格内容均未公开；原 Notebook 未执行、未入库 |
| 质量检查 | Ruff、Git 空白检查与新增文档相对链接检查通过 |

```bash
python examples/run_sector_cta_demo.py --output runs/sector-cta
python examples/run_sector_cta_demo.py --models xgboost,lightgbm,transformer,itransformer --output runs/sector-cta-four-models
```

演示合约、日期、特征与执行收益全部为合成数据。研究曲线仅验证非重叠区间的名义收益记账；手续费与滑点是示例常量，不是任何真实交易所费率。未验证真实因子有效性、历史身份真实性、真实成交、保证金、强平、容量、连续持仓或移仓。完整执行引擎仍待接入。


## 全系统数据泄漏与逻辑复核

基线为 `fe77ce2d48622fa43a17d4aae3574f5621aa22a0`。本轮[复核报告](../system/leakage_audit.md)列出可复现问题、修复、旧结果兼容性及未覆盖边界；上方旧版测试数量仅记录当时完成的检查。

| 检查 | 本轮结果 |
|---|---|
| 完整回归 | 200 项测试、26 个子测试通过；比基线增加 69 项回归 |
| 模型选择与口径 | 真实 XGBoost / LightGBM 各轮 IC 对照、CPU Torch 独立 IC 标签窗口对齐、误差与 IC 目标分离 |
| 未来扰动 | Test 标签值/缺失/延迟不改预测与仓位；未来合约不改 Fit 增广；未来预处理不能通过重复变换隐藏来源 |
| 融合研究 | 真实后端 OOF→Ridge 与分层核心/残差研究测试通过；补充晋级 label_end、OOF/折/计划/缓存绑定回归 |
| 四模型通用编排 | 选轮数、冻结重训、延后 Test、预测指标与审计产物完成 |
| 四板块 × 四模型 CTA | 16 个最终模型、排名融合、固定板块仓位及区间研究回测完成 |
| 回测记账 | 非重叠区间、双边费用、缺失持仓收益、报告截止、上一期权益可得性回归通过 |
| 独立因子诊断 | Rank IC 交集内重新排名、形状错配与收益 horizon 类型检查通过；未重算旧历史档案 |
| 发布质量 | Ruff、Git 空白检查、文档相对链接检查通过；67 个公开文件脱敏扫描零命中；wheel / 源代码包构建成功 |

```bash
python -m pytest -q
python -m ruff check src/futures_research src/quant_research tests/futures tests/test_factor_bridge.py src/ashare_factor_lab/evaluation/ic.py tests/test_ic_audit_regressions.py tools/check_public_release.py
python tools/check_public_release.py
python -m build --no-isolation
quant-research demo --models xgboost,lightgbm,transformer,itransformer --output runs/audit-general
python examples/run_sector_cta_demo.py --models xgboost,lightgbm,transformer,itransformer --output runs/audit-sector
```

本地为相同的 macOS arm64 / Python 3.12 环境。远端检查由仓库 GitHub Actions 对最终提交运行 Python 3.11 / 3.12；以相应提交的状态为准。所有数据均为合成数据；没有执行私人 Notebook、真实行情重算或实盘交易。代码审计和合成测试不能证明真实数据的时点真实性、交易可实现性或盈利能力。
