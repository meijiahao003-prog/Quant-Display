# 模型训练、OOF 与预测评估

这一层把研究脚本中的树模型、序列模型、分组集成和分层残差模型整理成可安装的 Python 模块。它输出预测、误差/相关性统计与来源记录；这些不是包含成交、滑点、成本、保证金或换月执行的交易回测。CTA 区间研究回测通过独立入口提供，完整执行与账户引擎后续接入。

## 入口与边界

| 模块 | 入口 | 行为 |
|---|---|---|
| `factor_model_training` | `make_training_config`、`train_factor_model` | XGBoost、LightGBM、CatBoost、iTransformer、Transformer；先选轮，再单独从头训练固定轮数 |
| `factor_model_testing` | `evaluate_factor_model` | 只接受绑定最终训练结果的模型；核对列顺序、训练边界后输出预测与 QC |
| `factor_ridge_stacking` | `run_walk_forward_oof` → `fit_ridge_meta_model` → `fit_final_base_models` → `evaluate_final_ridge_stack` | 分组 XGBoost 的时间顺序 OOF、标准化与固定 alpha Ridge、最终预测评估 |
| `factor_hierarchical_residual` | `generate_monthly_core_oof` → `run_residual_parameter_selection` → `run_monthly_residual_promotion` → `run_research_walk_forward_test` | 核心组 OOF、残差候选、独立时段晋级、按月更新研究评估 |
| `factor_runtime` | 指纹、线程约束、时间元信息上下文 | 记录输入/配置/源码哈希；默认单线程，可通过 `FUTURES_RESEARCH_THREADS` 设置 1–4 线程 |

模型代码中的 `itransformer` 把变量作为 token；`transformer` 把时间步作为 token。历史笔记标题不一定等于实际调用的架构，整理时以实现与 `config['method']` 为准。不能把两者的结果互换命名。

上述训练/评估入口除显式参数外，均通过装饰器接收两个必填关键字：

```python
result = train_factor_model(
    x, y, target="target", method="xgboost", training_stage="selection",
    training_iterations=50,
    fit_start=fit_start, fit_end=fit_end,
    valid_start=valid_start, valid_end=valid_end,
    config=config,
    label_end=label_end,                     # pandas Series，索引绑定样本
    feature_available_at=feature_available_at,
)
```

`x/y` 的索引必须完全一致，为 `Date, Contract, Datetime`。元信息可以覆盖完整开发/测试样本集合，内部只显式取当前切片；缺行、重复键、NaT、时区不一致或未来才可用的特征都会拒绝。元信息只包含时间，不应提前读取测试标签值来生成它。

`feature_available_at` 应是这一行所有特征输入的最晚可用时点，包含发布延迟、聚合、复权、主力判定等上游过程。`label_end` 同时充当标签可用时点；标签计算完成之后还有延迟时，应填更晚的真正可用时点。若另传 `ic_y` 且其预测期限不同，必须填训练标签和 IC 标签两者的最晚可用时间。不能以行时间或名义预测期限代替真实可用时点。

没有元信息时默认报错，不接受“未检查但继续训练”。通过时间检查只说明调用者声明的时间彼此相容，不证明这些声明真实，也不证明原始因子计算没有前视。

## 选择、最终训练与测试

单模型训练采用半开区间 `[start, end)`。在选择阶段，Fit 中 `label_end >= valid_start` 的行会被实际移除；Validation 中 `label_end >= valid_end` 的行也被移除。最终训练重新创建模型，在 Fit∪Validation 内固定轮数训练，并移除最终截止时仍不可用的标签。审计记录移除数量、元信息哈希与实际使用标签的最晚时点。purge 后空集合会报错。

测试必须晚于实际训练观测和训练标签的最晚可用时间，且不早于声明的最终冻结时点 `fit_as_of`。模型选择得到的 Validation 统计不能作为最终测试统计。最终轮数是明确输入；反复看测试再改参数的行为不在函数可观测范围内，必须由实验登记制度限制。

序列窗口按合约与时间排序，只包含窗口终点及其过去行，不跨合约；可限制跨交易日/交易时段。随机打乱已构造的训练窗口不改变窗口的过去信息约束。验证和测试单独建窗口，因此会丢弃每段最初不足窗口长度的行；目前没有注入前一段历史上下文。窗口长度计的是采样行数，非自然分钟数。

验证阶段对每轮实际模型计算 IC，避免将缓存的旧轮 IC 当作当前轮统计。通用 CLI 默认按 Valid RMSE 选轮数；CTA 默认按 Valid 原始收益 IC 选轮数。`ic_y` 与训练目标分别记录，Torch 独立 IC 标签按序列终点对齐。测试误差始终相对于训练目标，IC 相对于 `ic_y`（未提供时使用训练目标）。

Test 标签缺失、常数或越过声明评估截止时，仅令对应指标未定义；不允许通过未来标签是否可用改变预测面板。通用 CLI 与 CTA 保留 Test 决策行，遮罩不可评分的标签；训练和选参阶段仍要求有限标签并执行 purge。预测表 `target` 用于误差，`actual` 用于独立 IC；二者均不能用作选券条件。

## OOF、PCA 与超参数的时间顺序

Ridge 和分层残差的日期需要调用者显式传入。高级接口保留闭区间端点以兼容原研究流程，调用半开区间 trainer 时明确转换为 `end + 1ns`。`make_default_walk_forward_folds(training_start=..., first_predict_start=..., n_folds=...)` 根据明确日期生成年度窗口。每折或每月都从训练集合移除 `label_end >= prediction_start` 的标签。

固定基模型计划还必须声明 `plans_frozen_at`，且严格早于最早预测时点。残差晋级的参数必须在晋级时段开始之前冻结，晋级必须在保留测试开始前完成。因此，用同一验证期选参数再把这一期称为独立 OOF 晋级验证的旧调用方式会被拒绝。这是时间声明检查，不是对人工选参过程的独立取证。

Z-score/PCA 必须在各折允许使用的训练数据内拟合。输入携带 `processing_method` 或 `compression_method` 时，守卫要求存在对应的 `*_fit_end`，并拒绝在预测截止时点或之后拟合的转换。一次对完整开发期拟合 PCA 再做早期 OOF 仍然泄漏，不能因模型按时间划分而豁免。高级接口不会自动为每折重拟合任意外部变换：使用原始因子，或提供在最早 OOF 之前冻结的转换；需要逐折自适应转换时，应在外层逐折调用基础训练接口。

没有转换 attrs 的第三方数据仍需要上游来源审查，删除 attrs 不能让非因果特征变成因果特征。分组方案、特征筛选、缺失值处理、因子剔除和目标变换也必须遵守同样的时点约束。

Ridge 的 scaler 与系数只在基模型 OOF 表拟合；这一表上的 `ridge_fitted_prediction` **仍是元模型训练内拟合值**，不是整个集成模型的样本外结果。真正评估使用时间更晚、独立冻结的测试段。原分层研究曾复用验证数据且测试反馈影响设计，因此公开版保留相应历史限制标记，不继承任何“独立测试通过”或收益结论。

按月 research test 允许把已实现的过去测试标签加入下一月训练；这不是单次冻结模型的 holdout。每月先冻结预测，再调用标签 loader；后续拟合仍用实际 label_end 过滤尚未实现的标签。元信息必须覆盖 loader 可能返回的样本，缺失时拒绝继续。

模型“冻结”指本流程不再训练、更新或用 Test 调参，同时核验模型身份、配置和时间；不代表底层原生模型对象不可被外部代码修改。完整问题清单及兼容性变化见[全系统复核](../system/leakage_audit.md)。

## 可复现与验证范围

默认产物写入 `runs/`。原生模型、配置、列顺序、输入特征值、目标、元信息、源码与依赖版本进入审计；缓存绑定训练和预测特征值，而不只绑定索引。OOF 的预测、标签、折归属和时间元数据需匹配保存的审计指纹；晋级标签必须在 Test 前可得。分层缓存还绑定回调实现及依赖版本，可变外部状态变化时不得恢复旧缓存。相同索引下更改特征会使缓存失效。随机种子与线程设置有记录，但不同硬件、库版本和加速器不承诺逐位一致。

本层合成测试覆盖缺失时间证据、特征晚到、标签跨界 purge、预处理跨期、模型测试边界、序列窗口、分层月度缓存、分层残差选择/晋级/月度评估全流程，以及小型 XGBoost→OOF→Ridge→最终评估流程。测试数据为明确构造的虚拟序列，不能用来证明任何市场上的预测能力。真实数据、真实成本与实盘可交易性尚未审计。

```bash
python -m unittest discover -s tests/futures -p 'test_training*.py' -v
```

基础回归只需 NumPy/Pandas。完整集成需要 scikit-learn、joblib、XGBoost、PyArrow；序列测试需要 PyTorch。macOS 的树模型还需可加载的 OpenMP 运行库。CI/开发环境应核实实际 import 成功，而不是仅检查包名存在。

运行产物可能包含用户数据的特征名、样本时间、合约、预测与本机产物路径，应留在本地忽略目录。发布源码和合成演示不等于授权发布真实研究产物。
