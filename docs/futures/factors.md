# 因子处理、采样、分组、压缩与质量检查

这些模块将原研究代码中的可复用步骤整理为包接口。公开版不包含私人数据、原机器目录、账户信息、Notebook 输出、真实因子分组清单或历史收益成绩。测试仅使用合成数据，证明指定工程约束，不能证明策略有效或整个研究过程不存在数据泄漏。

## 来源与责任边界

| 原材料 | 公开模块 | 负责的步骤 |
| --- | --- | --- |
| `factor_processing (5).py` | `futures_research.factor_processing` | Fit 区间均值/标准差拟合、冻结变换、审计 |
| `factor_sampling (5).py` | `futures_research.factor_sampling` | 精确盘中时刻抽样、特征可用时间校验、标签期限与边界清理 |
| `factor_grouping (6).py` | `futures_research.factor_grouping` | 从显式因子清单抽取互斥分组 |
| `factor_compression (6).py` | `futures_research.factor_compression` | 每组独立进行 Fit-only 中位数填补、无效列剔除与 PCA |
| `factor_series_qc_v2 (6).py` | `futures_research.factor_series_qc_v2` | 覆盖率、异常值、会话边界、自相关、前缀不变性检查 |
| `分组test.ipynb` | 本文中的研究边界说明 | 按已有组进行单因子 IC 与月度稳定性分析；不作为自动分组算法发布 |

`分组test.ipynb` 依赖外部分组与因子数据，包含既定组、单因子相关性、自然月统计和全样本汇总。仅凭它无法复原这些组的生成时间与依据，因此公开版要求显式提供分组，不能宣称该 Notebook 已经验证了分组的样本外有效性。如果实际分组是通过相关性聚类产生，必须仅用当前训练折的数据拟合并保存成员与配置，禁止使用最终测试期 IC 决定分组。

## 输入约定

- 标准化和抽样要求 `x: DataFrame` 与 `y: Series | DataFrame` 的索引逐值、逐序完全相同。索引为唯一的三层 `MultiIndex`，顺序固定为 `Date, Contract, Datetime`。模块不自动排序、取交集、填补错位标签。
- `Datetime` 表示作出预测的时刻；`Date` 可表示交易日，夜盘时不必等于自然日。不得把“bar 开始时间”冒充已知收盘价的可用时间。
- 特征列为数值；分组/PCA 要求字符串列名且唯一。训练和推理必须保持同一列顺序。
- `feature_available_at` 为每行所有原始数据和派生输入的最晚可用时间，`label_end` 为该行完整目标可以被观察到的时间；两者是与样本完全对齐的 Series。时间必须无缺失且使用同一时区。供应商修订、发布时间、计算延迟由调用方记录，库无法从价格矩阵推断。

## API 与执行顺序

### 1. 可用时间与时间切分

`validate_point_in_time(index, *, feature_available_at, label_end)` 检查：

- `feature_available_at <= Datetime`；
- `label_end >= Datetime`；
- 唯一索引、严格对齐、时间完整和时区一致。

返回元数据指纹及检查审计。`metadata_origin_verified=False` 明确表示这些声明未经过独立数据源核验。时间元数据必须覆盖实际参与标签计算的最长 horizon；例如目标使用未来多个 bar 的均价，则终点不能只填写第一个 bar。

`purged_time_split(index, *, intervals, label_end, feature_available_at, embargo='0s')` 返回 `SamplingSplit.masks` 和 `.audit`。`intervals` 是按时间顺序排列的 `{名称: (start, end)}`，区间端点包含在内，区间之间严格不重叠。一个样本仅在 `label_end <= 本区间end` 时保留；有下一分区时还要求 `label_end + embargo < 下一分区start`。掩码与输入位置一致，不改变样本顺序；审计记录每区候选数、保留数、清理数与保留索引指纹。空区间可能得到全 False，训练调用方必须检查是否有足够样本。这个掩码用于训练/评分资格，不能用于 Test 预测的选券资格：未来标签是否完成不得改变当时可预测的特征集合。

这里 `embargo` 是施加在前一区标签终点之后的额外实时时间缓冲，不是行数或交易日数。交易所日历、停牌和不规则间隔场景应提供实际 `label_end`，不能机械地把一行当一分钟。抽样本身不会重算标签，也不能把连续分钟标签的 horizon 直接当成抽样后的行数。

### 2. Fit-only 标准化

`process_factor_data(x, y, fit_start=..., fit_end=..., method='zscore')` 返回 `ProcessingResult(x, y, state, audit, ...)`。只有 Fit 区间参与均值和标准差估计，正负无穷视为缺失；常数列和 Fit 内不可估计的列默认变为全 NaN，可用 `method_params={'invalid_policy': 'raise'}` 改为报错。`y` 不做变换。

分别调用 `fit_zscore_state` 和 `transform_zscore` 可在训练与后续预测间复用状态。直接拟合也执行完整索引检查；变换前重新检查统计量指纹，发现 fitted Series 被修改会报错。默认输出 `float32`，如需更高数值精度可设 `float64`。该方法跨 Fit 的所有合约共用每列统计量；它不是逐合约标准化。

### 3. 精确抽样与显式分组

`sample_factor_data(x, y, sampling_times=['09:30', '13:30'], contracts=None)` 返回对齐子集。匹配本地墙钟时刻，支持秒/微秒，不做向最近一分钟靠拢。默认检查请求时刻/合约是否至少出现一次；如需每个交易日和合约都具备每个时刻，设 `strict_complete_grid=True`。输出审计记录缺失网格与配置/索引指纹。调用方应预先确定抽样方案，不能在测试期反复筛选采样点。

`extract_factor_groups(x, {'groups': {'G1': ['factor_a'], 'G2': ['factor_b']}})` 仅抽列，不训练聚类、不改值、不标准化。允许显式 CSV 因子清单；组间不得重叠，未分组列默认报错，可显式选择 `drop` 或 `passthrough`。输出的组成员与分组指纹可供后续审计。组数不限，不保留原研究中的私有成员清单。

### 4. 分组 PCA

`compress_factor_group(group_x, compression_plan)` 返回 `PCACompressionResult`。配置必须给出 `fit_start`、`fit_end`；可选 `group_name`、`component_rule`、`random_state`、`svd_solver`、`output_dtype`。默认按累计解释方差选择分量：

```python
plan = {
    'fit_start': '2024-01-01',
    'fit_end': '2024-06-30 23:59:59',
    'group_name': 'G1',
    'component_rule': {'mode': 'variance_ratio', 'value': 0.90},
    'random_state': 42,
    'svd_solver': 'full',
}
```

以上日期仅是接口示例。所有填补中位数、有效列筛选、PCA 方向和分量数都只由指定 Fit 数据决定。如果设置最大分量数后不能达到目标解释方差，审计中的 `target_reached` 为 False，不伪报达到阈值；固定分量数超过可用维度直接报错。PCA 数组状态只读，`transform_factor_group(new_x, state)` 不重新拟合，并校验列顺序和分组指纹。

不设置 `output_dir` 时不落盘；`save_pca_state(state, 'runs/pca')` 或显式 `output_dir` 可保存 JSON manifest 与 NumPy 数组，路径由调用方提供。组名称和列名称会进入产物，私有名称应在调用前另行脱敏，运行产物不应自动提交到公开仓库。

`load_pca_state(paths['directory'])` 可恢复 `save_pca_state` 返回的具体组目录，之后通过 `transform_factor_group` 在新数据上复用。恢复只读取 JSON 和 `allow_pickle=False` 的 NPZ，先校验 `compression_checksums.json` 中 manifest/数组文件的 SHA-256，再检查数组维度和特征/分量结构。旧目录缺少校验文件时拒绝加载，应从可信拟合状态重新保存。散列用于发现相对保存版本的损坏或变更，不是对产物作者身份的数字签名；如果攻击者同时替换数据与校验文件，校验并不提供真实性保证。

### 5. 因子质量检查

`run_factor_qc` 提供统计摘要、告警、滚动/分块/会话报告；`run_factor_frame_qc` 根据 manifest 检查整张因子表。异常比例或极高自相关是检查线索，不等于预测有效性结论；不同类型因子需要不同阈值。无有效 support 时不回退成未支持样本统计，base 表错位直接报错。会话自相关不得跨越中间的会话边界。

`test_prefix_invariance(factor_func, factor_args, cut_positions)` 在完整数据和多个截断历史上重新计算因子，比较过去的值。支持 ndarray、Series、DataFrame 及列表等时间参数；特殊常量向量应显式设置 `time_axis_arg_positions`。前缀输出长度必须严格等于截断长度，禁止 NumPy 广播造成假通过。原始输入在每次调用前复制，避免原地修改污染比较。建议对真实因子在多个会话/合约切换前后测试，而不只测试一个平滑序列。

该检查能暴露全样本标准化、居中窗口、未来填充等常见情况，但无法证明数据供应商在历史时刻的版本正确；任何通过结论只适用于实际测试的输入与截断点。

## 训练折与研究结论的边界

顶层 Fit-only 不自动意味着内部 OOF 无泄漏。如果先用整个 Fit 区间拟合标准化/PCA，再切较早 OOF 预测窗口，早期预测仍然接触了未来分布。需要对每一折重新拟合预处理、分组（如果是学习得到的）、填补和 PCA，再冻结变换该折验证集。变换结果的 attrs 包含 `processing_fit_start/end`、`compression_fit_start/end` 等来源信息，不能在拼接时丢弃后再声称审计完整。

滚动 QC 通常只使用已有前缀，但完整样本 QC、月度 IC、分组比较和阈值选择是回顾性分析。不得用最终测试期结果过滤因子、选择组数或挑选模型，再把同一测试期称为独立样本外结果。需要记录试验数量和选择规则，另留最终检验区间。

现有回归测试覆盖未来值扰动不影响 Fit 状态/训练变换、边界标签清理、延迟特征拒绝、错位拒绝、PCA 状态不可变和前缀测试识别未来依赖。尚不能替代真实数据的时间戳审计、交易成本、滑点、合约流动性、换月成交与多重检验控制。
