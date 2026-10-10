# 本次整理的验证记录

验证对象：公开仓库基线 `6eebad739dc37f0976ab79dfb261461276478d43` 上新增的统一量化研究系统。日期：2026-10-10。所有新增模型试验均使用合成数据，没有连接真实交易数据库、供应商账号或执行原始 Notebook。

## 已运行的检查

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
