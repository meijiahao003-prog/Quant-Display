# GitHub 迁移记录

2026-10-23：观市第 17 版移植到 `Quant-Display/projects/shipan`，项目名「识盘」。源提交：`009891cb23ad0931e1a558fa0ec92a7fa2f66fcf`。

保留平台、行情接口、宏观报告 API、图表与专家 Agent 的现有逻辑和许可证。新增本地启动器、PowerShell 启动入口、本地 D1 配置及资料占位页面。主页下载链接指向 GitHub 源码目录。移除固定个人资料 ID；保留原有线上站点来源白名单以支持原观市与本地 Agent 通信。

## 可复现检查

作用：执行现有回归测试、检查本地迁移与生产构建。
真实输入：实际源码、锁定依赖、仓库已有测试夹具与 SQL；夹具不代表真实市场或投资结论。
调用示例：

```bash
cd projects/shipan
node --test tests/*.test.mjs
(cd investment-agent && node --test tests/*.test.mjs)
node scripts/shipan.mjs migrate
node scripts/run-framework.mjs build
```

实际输出：平台 `tests 141, pass 141, fail 0`；Agent `tests 65, pass 65, fail 0`；迁移返回 `No migrations to apply!`；构建返回 `Build complete`。
前后变化：测试不调用真实模型；迁移只写本地 D1；构建生成 `dist/`。行情数值与源日期处理逻辑保持不变，源码目录与显示名称改变。

运行启动器时已观察到 `Local: http://127.0.0.1:5173/`。未在用户设备运行，未验证真实上游的当前行情或 ChatGPT 登录。Windows 脚本未经 Windows 实机验证。

## 定时任务和部署

原 ChatGPT 自动化、个人资料空间和线上 D1 数据不是源码的一部分。迁移不移动它们、不停止原任务、不部署新网站。`public/macro-workflow.json` 是原流程的脱敏参考，需要重新绑定自己的资料和调度。普通更新应写入 API 数据，不应每天修改源码日期。

`start.sh` 仅绑定 `127.0.0.1`，开发服务使用模拟身份。不要将开发端口直接暴露公网。需要公网部署时，应为部署环境配置独立认证、数据库和任务调度。

HTTP 冒烟验证：主页、`/api/health`、`/api/macro` 均返回 200；新本地宏观响应 `brief=null, freshness=missing`，没有生成虚假报告。
