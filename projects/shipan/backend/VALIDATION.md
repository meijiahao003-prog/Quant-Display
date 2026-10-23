# 已执行验证与边界

日期：2026-10-21（Asia/Shanghai）

## 已通过

- Python语法编译：`app.py`、`data_worker.py`。
- 实际安装导入依赖；`requirements.txt`固定到本次安装检查的版本。
- FastAPI `TestClient` 离线接口测试8项，全部通过：
  1. 原始零保持零；缺失值为null；观察池缺失行标不可用。
  2. 演示快照拒绝入库；相同标的同一时间重复标注409；JSONL一行一记录。
  3. 上游失败模拟返回unavailable与错误，全部价格为空，不替换为示例数据。
  4. 训练不足50独立时间戳时422；60条**合成测试夹具**能真实完成本地拟合，按时间分48/12，保存模型与报告，并可调用预测；不更改样本。
  5. 缺数据时规则角色弃权；无Ollama配置时503；实际LangGraph路径可执行。
  6. 配置Bearer令牌后：缺少/错误令牌401、正确令牌200；401保留CORS响应头；Authorization预检通过；不配置令牌仍允许本机默认模式。
  7. 缓存行情的顶层和逐标的`fetched_at`保留实际原获取时间；`served_at`独立；大盘资金另带日期与获取时间；首次全部失败`fetched_at=null`。
  8. 样本只接受服务端注册的`snapshot_id`：把demo模式改成delayed、伪造ID都拒绝；客户端修改已注册快照报价后，最终保存的仍是服务端原始值。测试通过mock上游再调用真实`snapshot()`注册，不直接伪造生产注册记录。

测试命令：

```bash
cd backend
python -m unittest test_backend.py -v
```

合成夹具仅存在独立临时数据库，没有导入用户数据库，没有作为真实行情或真实模型效果展示。此处不报告合成夹具的“精度”作为性能证据。

## 未验证 / 未完成

- **真实AKShare行情联调未完成。**执行真实上游快照请求时，运行环境返回 `network approval was cancelled before a decision was returned`。因此无法确认本地网络到东方财富上游的可用性；没有取得可核实实时/延迟行情结果。
- 真实行情接入后，请运行 `python diagnostics.py source`、`snapshot`、`history` 检查实际输入/输出和单位。所有例子会打印真实数据，无数据则明确失败。
- ETF/大盘资金净额和ETF量额单位仍为 `source_raw_unverified`，未做亿元换算。指数日K成交额根据官方文档标为CNY。
- 没有真实人工标注样本，所以没有真实训练指标、未来收益预测结果或回测结论。
- 没有本机Ollama模型，未做大模型推理端到端验证。
- 在线静态站不运行Python；本地完整服务或独立HTTPS网关才执行上述接口。
