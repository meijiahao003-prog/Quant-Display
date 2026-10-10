# 本地数据接入与研究复现

统一 CLI 处理股票或期货研究面板。上游先完成授权数据读取、按时点因子计算及资产身份核验；CLI 不自动连接数据库或导入原 Notebook 的私人配置。

## 环境

项目要求 Python 3.10 以上，建议新的 Python 3.11/3.12 虚拟环境：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[futures,futures-models,dev]'
python -m pytest -q
```

`futures` 提供表格、机器学习基础、序列化与列式数据依赖；`futures-models` 提供 XGBoost、LightGBM、PyTorch。CatBoost 单独安装：`python -m pip install -e '.[catboost]'`。只使用原股票因子时可轻量安装 `python -m pip install -e '.[dev]'`。

macOS 树模型需要可加载的 OpenMP 库。出现动态库错误时，安装适合 CPU 架构的 `libomp`，确认解释器与依赖架构一致。部分开发环境可临时使用 PyTorch wheel 附带的兼容 OpenMP 库验证，但不将本机动态库路径固化到源码或配置。包已安装不等于其原生库已成功导入。

依赖范围不等于精确锁定环境。正式研究需保存实际依赖版本、系统/CPU/加速器信息及环境快照；不同硬件、库版本和加速器不承诺逐位一致。

## 数据表契约

CSV 与 Parquet 使用同一组字段：

| 字段 | 要求 |
| --- | --- |
| `Date` | 午夜归一化交易日，期货夜盘不能盲目以自然日替代 |
| `Contract` | 非空资产 ID，三列样本键唯一 |
| `Datetime` | 特征信息截止及预测决策时刻 |
| 因子列 | 显式列入 dataset.features，不含目标或键字段 |
| 目标列 | 显式列入 dataset.target；训练必须有限，Test 可用 NaN 表示未知 |
| `feature_available_at` | 所有特征和上游变换最晚可用时间，不晚于 Datetime |
| `label_end` | 标签真正可用的最晚时点，含预测期限及额外发布/计算延迟，不早于 Datetime |

索引为 `Date, Contract, Datetime`。表内使用无时区时间戳，在配置 timezone 中声明交易所时区，不混用 UTC 与本地时间。财报、复权、主连判定、成交量汇总与历史成分股都需要真实可用时点证据；给泄漏数据补一列合法时间不能使其变成因果数据。

训练标签的缺失或非有限值在上游明确隔离。Test 标签允许 NaN，不能因未来结果缺失删除特征行；正负无穷仍拒绝。独立构造测试表时显式使用 `ResearchDataset.from_frame(..., allow_missing_labels=True)`；CLI 已启用这一测试选项。特征缺失按冻结状态处理，不利用未来样本估计填补值。分组需明确覆盖输入特征，未分组特征会报错。

原股票因子通过[快照桥接](../system/factor_bridge.md)形成面板；期货行情通过[连续合约接口](continuous.md)得到映射、原价与审计，再由调用方显式接入因子计算。两者按同一契约训练。

## 切分与运行

参考 [research.example.json](../../configs/research.example.json)。dataset.train_valid 与 dataset.test 必须是两个不同文件；路径相对配置文件目录解析。真实数据默认留在忽略目录 data/。

Fit/Valid/Test 使用有序、不重叠的半开区间 `[start, end)`。两个文件只包含各自声明的开发/测试时段。训练与选参的跨边界标签先 purge，再拟合处理器和模型。Test 预测保留已声明决策行，`label_end >= test_end` 的标签只从指标计算中遮罩；embargo 的单位显式给出，零值不等于证明所有标签独立。

```bash
quant-research run --config configs/research.example.json --output runs/real
```

在 Fit 拟合 Z-score 和分组 PCA，使用 Fit 训练、Valid 选轮数，再创建新模型，在 Fit+Valid 内训练固定轮数。最终阶段继续冻结同一套 Fit 处理器。所有模型冻结后才调用 Test loader，再用已有状态变换测试特征。

sampling_times 精确匹配时刻，不使用近邻。六时刻示例不代表适合每个市场；真实输入需按时区、交易时段、节假日和夜盘设置，必要时启用完整采样网格检查。

多模型 Test 指标是同一预留集的描述性比较。看 Test 后选模型、组因子或改规则，该区间就已参与研究。OOF、Ridge 与残差晋级使用[独立研究 API](training.md)并登记冻结时点；基础 CLI 不自动运行所有高级分支。

## 演示与产物

```bash
quant-research demo --output runs/demo
quant-research demo --models xgboost,lightgbm,transformer,itransformer --output runs/comparison
```

演示按种子生成虚拟资产和目标，工作日序列仅用于合成数据，不是官方日历。CLI 演示验证处理、训练和预测；连续合约、因子桥接另有合成回归测试，不能把它描述为真实数据全链路验收。

| 产物 | 用途 |
| --- | --- |
| config.json | 本次配置 |
| selection.json | 模型轮数与选择审计 |
| zscore_fit_state.csv 及状态文件 | Fit 参数与冻结处理状态 |
| models/、pca_states/ | 本地模型和变换状态 |
| evaluation/、model_comparison.csv | 预测评估，可能含标签，不能直接作为交易信号 |
| signals.csv | 不含未来标签的预测导出 |
| manifest.json | 成功状态、源码与输入指纹、依赖、切分/处理审计、产物哈希 |

输出必须是新目录，失败后也不覆盖重跑。存在部分文件不代表完成，以成功状态 manifest 为准。运行清单不代替原始快照、文件哈希、历史资产池或供应商修订记录。CSV 行情适配器记录实际输入字节哈希，模型流水线记录其使用的数据指纹，两者覆盖范围不同。

后续回测需补充真实计算及执行延迟，不能直接把当前 signal_available_at 等于决策时刻的研究假设当作可成交证明，详见[接入契约](../system/backtest_contract.md)。运行产物可能暴露研究信息，默认留在本地。
