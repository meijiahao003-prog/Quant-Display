# 逐步运行真实输入与输出

以下例子均在 `backend/` 目录、安装依赖后运行。**真实上游联调尚未完成**：开发环境的网络请求被取消，未获得可核实的行情响应。本文不编造任何市场数值。每段代码读取你的实际 AKShare 响应或本地 SQLite 数据，并打印真实输入和实际输出；没有数据就明确输出空值/错误。输出较多时打印前五行。

## 1. 获取源数据：`data_worker.read_source` / `app.fetch_source`

**作用**：`command` 选择ETF、指数、市场资金或历史行情；`symbol` 是真实六位代码；`period` 是 `day/1m/5m/15m`。`fetch_source` 增加缓存、超时和来源状态。

**真实输入**：AKShare 当次上游响应；脚本展示真实字段和前五行，不预设价格。

**调用示例与实际输出打印**：

```bash
python diagnostics.py source
```

**实际输出**：尚未执行成功；运行后显示原始行数、前五行、状态和错误。

**前后变化**：字段和值不变；DataFrame转JSON，时间转ISO，NaN转null。失败时没有新行情行；有缓存时保留旧数据和旧获取时间。

## 2. 规范化快照：`app.number` / `app.normalize_quotes` / `app.snapshot`

**作用**：`number(value)` 将真实来源数值规范为有限float或None，零仍为零。`normalize_quotes(rows, kind, status, fetched_at)` 筛选观察池、映射字段；`snapshot()` 汇总独立指数、ETF、日频市场资金请求。

**真实输入**：当次ETF和指数返回的真实行。指数缺少源时间时保留null。

**调用示例与实际输出打印**：

```bash
python diagnostics.py snapshot
```

**实际输出**：尚未取得可核实上游结果。脚本显示源前五行、完整16标的状态和转换后行数。

**前后变化**：观察池固定6指数+10 ETF；其余源行过滤；缺失标的补一条明确不可用的空值行，未编造价格。名称/代码映射，数字不缩放。ETF和指数价格、涨跌幅保留实际数值。大盘资金单独保存，绝不充当单指数资金流。

## 3. 历史K线：`app.history`

**作用**：`symbol` 为观察池代码，`kind` 是etf/index，`period` 为day/1m/5m/15m。调用对应真实AKShare函数。

**真实输入**：510300的真实历史响应。

**调用示例与实际输出打印**：

```bash
python diagnostics.py history
```

**实际输出**：尚未完成上游验证。脚本打印原始/结果前五行、实际错误及前后行数。

**前后变化**：日期/时间→time，开高低收→open/high/low/close，按时间排序；缺OHLC的行不绘图。有效价格不复权、不重采样、不插值；量额按已核实的单位元数据标记。

## 4. 网关状态：`app.health`

**作用**：报告进程、配置、观察池和可用能力。它不代表行情源已连通。

**真实输入**：本地服务配置与已安装模块。

**调用示例与实际输出打印**：

```bash
curl http://127.0.0.1:8000/api/health
```

**实际输出**：由正在运行的服务返回；没有运行服务时连接失败。

**前后变化**：只读，不产生行情或修改数据库。

## 5. 规则与模型分析：`app.rule_analysis` / `app.graph_analysis` / `app.analyze`

**作用**：把快照送入走势、资金和风险三个透明规则角色；LangGraph安装时实际通过其图执行；未安装则`orchestration=direct`。`mode=ollama`只调用已配置本地模型。

**真实输入**：当次实际snapshot；不可用时角色弃权。

**调用示例与实际输出打印**：

```bash
python diagnostics.py analyze
```

**实际输出**：脚本打印真实请求与实际响应。`engine=deterministic_rules`代表规则，不冒充大模型；`orchestration=langgraph`代表实际使用该库。未配置Ollama时选择该模式会明确503。

**前后变化**：输入行情不变；新增三个解释、证据列表和综合摘要；没有修改权重、生成训练收益或下单。

## 6. 人工标注保存：`app.save_sample`

**作用**：将你明确选择的真实快照读盘标签追加到SQLite。`label`是 `up/neutral/down`，含义为你的当前读盘判断；`note`记录理由。它不是未来实现收益标签。

**真实输入**：实时执行代码取得的真实延迟快照；首先打印，随后由你输入标签。此示例尚未执行，不会偷偷替你标注。

**调用示例与实际输出打印**：

```python
import json
import app

snap = app.snapshot()
selected = next(r for r in snap['instruments'] if r['symbol'] == '510300')
print('真实输入：', json.dumps(selected, ensure_ascii=False, indent=2))
print('保存前数量：', app.samples()['count'])
label = input('输入你的读盘标签 up/neutral/down：').strip()
note = input('输入标注理由：').strip()
try:
    result = app.save_sample(app.SampleRequest(snapshot=snap, selected_symbol='510300', label=label, note=note))
    print('实际输出：', result)
except app.HTTPException as exc:
    print('实际拒绝：', exc.status_code, exc.detail)
print('保存后数量：', app.samples()['count'])
```

**实际输出**：尚未执行；输出保存ID/计数或明确错误。

**前后变化**：成功+1行；服务端按`snapshot_id`读取注册的原始快照，忽略客户端改写的行情数值；源观测时间转UTC用于排序；同代码同时间重复标注409，未注册/过期ID或不可用数据422。仅客户端把demo改成delayed无法入库。不会用当前涨跌幅自动生成标签。

## 7. 样本读取与导出：`app.connection` / `app.samples` / `app.export_samples`

**作用**：`connection`创建一次必要表并打开本地库；`samples`列最近200条，`export_samples`导出全部JSONL。

**真实输入**：`backend/data/market-lens.sqlite3`中实际已保存记录。无记录就返回空列表。

**调用示例与实际输出打印**：

```bash
python diagnostics.py samples
curl http://127.0.0.1:8000/api/samples/export -o samples.jsonl
python -c "from pathlib import Path; p=Path('samples.jsonl'); lines=p.read_text().splitlines(); print('行数:',len(lines)); print('\n'.join(lines[:5]))"
```

**实际输出**：由你的实际样本决定；空数据库是count=0，导出零行，不生成示例样本。

**前后变化**：存储样本不增减；一记录转一JSONL行，原行情数值/空值/标签保留。

## 8. 训练特征：`app.training_features`

**作用**：取真实已存快照的 `[change_pct, main_pct]`；不取未来字段、价格或未核验单位的金额。

**真实输入**：前五条已保存的真实标注。

**调用示例与实际输出打印**：

```bash
python diagnostics.py features
```

**实际输出**：由实际记录打印；无样本时明确提示，没有替代数据。

**前后变化**：每个完整快照转2维特征；None保留至训练；原始数据不变。

## 9. 本地基线训练：`app.train`

**作用**：学习人工读盘标签。至少50真实样本、50独立观测时间；按时间组前80%训练后20%验证；仅训练区间拟合缺失值填充/标准化。

**真实输入**：你的SQLite全部实际样本。示例会先打印输入。

**调用示例与实际输出打印**：

```bash
# 只显示数据与处理说明，尚不写模型
python diagnostics.py train
# 明确运行真实本地拟合并保存成功模型
python diagnostics.py train --allow-write
```

**实际输出**：未对真实数据训练。数据不足返回422及实际数量；成功时打印真实训练/验证行数、切分时间、accuracy、balanced_accuracy、macro_f1和限制说明。

**前后变化**：样本不修改；成功新增一个joblib模型与一条指标记录。没有修改任何LLM权重。指标衡量人工标签模仿，不衡量未来收益。

## 10. 训练记录：`app.models`

**作用**：读取真实已完成训练的报告。

**真实输入**：本地模型注册表。

**调用示例与实际输出打印**：

```bash
python diagnostics.py models
```

**实际输出**：无模型时count=0。不会显示预设精度。

**前后变化**：只读；模型和样本数量不变。

## 11. 获取时间：`app.utc_now`

**作用**：返回本机当前UTC时间，仅用于获取/写入时间，不代替源时间。

**真实输入**：运行电脑的系统时钟。

**调用示例与实际输出打印**：

```bash
python -c "import app; print(app.utc_now())"
```

**实际输出**：运行当时的ISO时间，本文不预填。

**前后变化**：无行情变动，无数据库写入。

## 12. 使用本地训练结果：`app.predict`

**作用**：读取最近完成的本地标注模仿模型，对真实快照输出人工标签类别和类别概率。不会加载外部上传的模型文件。

**真实输入**：实际上游快照与实际已训练模型。

**调用示例与实际输出打印**：

```python
import json
import app
snap = app.snapshot()
print('真实输入：', json.dumps(snap, ensure_ascii=False, indent=2))
try:
    result = app.predict(app.AnalysisRequest(snapshot=snap, selected_symbol='510300'))
    print('实际输出：', json.dumps(result, ensure_ascii=False, indent=2))
except app.HTTPException as exc:
    print('实际拒绝：', exc.status_code, exc.detail)
```

**实际输出**：真实模型和上游联合运行尚未执行；无已训练模型返回404，无可用特征返回422。不会虚构预测。

**前后变化**：输入数据和模型权重不变；新增标签及类别概率。这些概率不是经过验证的未来收益概率。

## 13. 可选Bearer认证：`app.require_token`

**作用**：当服务启动环境设置`MARKET_LENS_TOKEN`时，校验每个`/api/*`请求的Authorization头。服务使用常量时间比较，不记录或返回令牌。CORS预检不访问业务数据。

**真实输入**：你在部署环境配置的令牌和正在运行的服务；示例不输出令牌。

**调用示例与实际输出打印**：

```python
import json
import os
import urllib.request
from urllib.error import HTTPError

token = os.environ.get('MARKET_LENS_TOKEN', '')
headers = {'Authorization': 'Bearer ' + token} if token else {}
print('真实输入：GET /api/health；令牌已配置=', bool(token))
request = urllib.request.Request('http://127.0.0.1:8000/api/health', headers=headers)
try:
    with urllib.request.urlopen(request, timeout=5) as response:
        print('实际输出：', response.status, json.loads(response.read()))
except HTTPError as error:
    print('实际输出：', error.code, error.read().decode())
```

**实际输出**：实际部署尚未执行此示例；正确令牌或未启用认证时返回服务状态，缺少/错误令牌返回401。离线认证与CORS边界测试已通过。

**前后变化**：请求业务数据和数据库不变；认证只决定是否继续处理请求。
