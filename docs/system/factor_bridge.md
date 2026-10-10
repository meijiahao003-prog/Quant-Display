# 将原有因子库接入统一研究面板

`ashare_factor_lab` 中的因子实现针对一个时点的横截面，`Factor.compute(DataProvider)` 返回严格的 `(1, n_assets)` 数组。`quant_research.factor_bridge` 把每个时点的独立数据快照送入这些因子，再形成标准化、分组、模型训练可使用的面板；不更改原因子公式，不假定股票就是期货。

## 显式快照

`SnapshotDataProvider(provider, *, as_of, available_at, asset_ids, asset_class, snapshot_id)` 包装已经按时点限制的数据提供器。必须声明：

- `as_of`：本次预测决策时刻。
- `available_at`：这个快照所有可读数据的最晚可用时间，必须不晚于 `as_of`。
- `asset_ids`：数据数组最后一维的资产 ID 顺序，非空、唯一。
- `asset_class`：显式填写如 `equity` 或 `futures`，不根据 ID 猜测。
- `snapshot_id`：可追溯的快照版本标识，不得包含密钥；不同决策时点使用不同标识。

包装器检查请求返回数组的维数、资产列数和回看行数，按实际 daily/intraday/fundamental 读取记录字段、形状和数值 SHA-256。返回副本避免因子就地改写数据源。元信息能记录来源声明，但不能独立验证供应商历史版本，因此审计始终注明 `vendor_point_in_time_verified=False`。

**不得把完整历史的 `InMemoryDataProvider` 直接贴上早期 `as_of` 标签。** `available_at` 声明不会自动截断底层数据。必须在 `provider_factory` 内按当时已知数据构建快照，或实现真正的按时点查询提供器。财报须按披露时刻及当时版本选择；行情应考虑收盘/发布/处理延迟；后来的复权修订不能假装当时已知。

## 生成面板

```python
from quant_research.factor_bridge import SnapshotDataProvider, build_factor_panel

panel = build_factor_panel(
    {'factor_a': configured_factor},
    decision_times=decision_times,
    provider_factory=make_snapshot_for_decision,
)
x = panel.x
feature_available_at = panel.feature_available_at
```

`provider_factory(decision_timestamp)` 必须逐时点返回 `SnapshotDataProvider`，且 `as_of` 与传入时点严格相等。输入时点必须唯一、递增。快照资产集合允许随时点变化，以支持当时可交易资产池；不能通过事后存续股票清单人为消除退市资产。一个面板只允许一种显式资产类别，跨类别合并须由调用方明示完成。

面板索引固定为 `Date, Contract, Datetime`，其中 `Contract` 是为了对接既有流水线而使用的**资产 ID 兼容字段**，不代表股票变成期货合约。`Date` 在此适配器中等于决策时刻的自然日；期货夜盘交易日应由专用数据构造层定义。资产类别与桥接指纹保存在 `x.attrs` 和 `.audit`。每个因子输出必须恰好为 `(1, n_assets)`；长度相似的一维数组或单值不会自动广播。NaN 保留给后续明确缺失处理，无穷值报错。

返回 `FactorPanel(x, feature_available_at, audit)`，不自动构造标签。调用方应另外建立完全对齐的 `y` 和 `label_end`，再用 `validate_point_in_time` / `purged_time_split` 及训练模块管理可用时间、标签期限和时间切分。桥接模块的 read audit 与数值指纹不是数据存档替代品，复现还需要锁定代码、依赖、因子参数、原始快照和资产池版本。

## 验证范围

合成测试验证动态快照下扰动未来数据不会改变过去的因子值或审计指纹，并验证延迟数据、时点不匹配、重复快照标识、输出形状和资产列数错误会直接失败。这些测试不是真实市场绩效或数据供应商可用时间真实性的证据。外部全局变量、绕过 provider 的文件读取、非确定性因子代码及供应商修订仍需单独审计。

## 可执行的横向集成示例

安装研究与 XGBoost 依赖后运行：

```bash
python examples/run_factor_bridge_demo.py --output runs/equity-bridge
```

脚本使用固定种子生成 100 个交易日、12 个虚构股票的日线数据，以既有 `DollarVolumeStability` 的 3 日和 8 日窗口实例生成两个特征。每次构造 `InMemoryDataProvider` 都显式截到当前决策日，目标为下个交易日收盘收益并记录实际 `label_end`。输入区间采用半开边界，训练与验证尾部跨界标签由统一流水线清理。随后执行 Fit-only 标准化、XGBoost 验证期轮数选择、固定轮数最终训练和测试评估；该示例不训练 PCA。

测试面板与 `ResearchDataset` 只在流水线冻结所有模型之后的 `test_loader` 中构造。运行目录包含桥接读取审计、合成数据生成说明、模型、选择记录、测试指标、信号和文件哈希清单。默认拒绝覆盖已有运行目录，应给每次运行一个新目录。生成的信号只是未来回测接口输入，不含收益回测、交易成本或真实策略成绩。
