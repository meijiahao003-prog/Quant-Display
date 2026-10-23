# 东方财富当日资金分时

联合图改为直接请求东方财富已有的当日分钟资金序列，覆盖观察池内的指数和 ETF。打开页面时可补拉源端已经提供的早盘记录，不必从打开页面的时刻开始积累。公开源可能缺分钟、延迟或暂不可用，接口返回什么就展示什么，不承诺逐秒或完整全天记录。

上游为 `https://push2.eastmoney.com/api/qt/stock/fflow/kline/get`。2026-10-21 实际请求会以302跳转到 `push2delay.eastmoney.com` 的同一路径；客户端仅跟随这两个固定HTTPS域名，最多3跳，12秒总超时。延迟源不作逐秒实时承诺。参数 `klt=1,lmt=0,secid=市场.代码`。已取得的真实响应行依次是：**时间、主力、小单、中单、大单、超大单**。金额保留人民币元，均为当日累计净额；主力等于大单与超大单之和。指数使用供应商对应指数口径，ETF 是该 ETF 自身交易口径；不能解读为机构身份、ETF 申赎或总流入/总流出。

## 可运行的真实输入摘录

**输入来源**：2026-10-21 已取得的真实公开接口响应，下表和脚本每个标的仅摘录首、末两行。这些数值不是当前行情，缺失的中间行是示例省略，不能据此认定当时源端缺数。未访问用户 QMT 或私有数据库。

| 标的 | 上海时间 | 主力净额（元） | 大单净额（元） | 超大单净额（元） |
| --- | --- | ---: | ---: | ---: |
| 510300 ETF | 09:31 | 4918230 | 33773158 | -28854928 |
| 510300 ETF | 13:08 | -435354626 | 236046672 | -671401298 |
| 000300 指数 | 09:31 | 786258944 | -23740928 | 809999872 |
| 000300 指数 | 13:09 | -4140492008 | -3496042590 | -644449418 |

**运行入口**（项目根目录，Node22+，无需网络）：

```bash
node examples/intraday-flow.mjs
```

**实际输出**：上述命令已执行。ETF 与指数各保留2条；状态均为 `partial`，对应 `expected_minutes=128/129,missing_minutes=126/127`；原输入不变，单位为 `CNY`。对齐得到242个横轴位置，各2条实际资金点、2段单点线段，09:32仍为null。完整输入、解析对象和前后计数由脚本打印。

## 接口函数：作用、调用、输出与变化

以下离线调用均在上面的已执行脚本中；真实输入均为该日期摘录。

| 函数与参数 / 作用 | 可运行调用 | 实际输出 | 前后变化 |
| --- | --- | --- | --- |
| `resolveIntradayInstrument(symbol,kind?)`：固定观察池校验；省略kind时按代码解析 | `resolveIntradayInstrument('000300','index')` | `{symbol:'000300',name:'沪深300',kind:'index',exchange:'sh',code:'sh000300'}` | 代码变为明确类型/交易所对象；不修改行情；不匹配时拒绝 |
| `intradayFlowUrl(symbol,kind?)`：生成固定域名请求 | `intradayFlowUrl('510300','etf')` | `secid=1.510300,klt=1,lmt=0`，完整URL由脚本打印 | 增加白名单参数，不接受用户自定义上游URL |
| `parseIntradayFlow(payload,symbol,kind?)`：校验并标准化源记录 | 脚本内 `parseIntradayFlow(payload,'510300','etf')` | 2条；末条 `main_net=-435354626,as_of='2026-10-21T13:08:00+08:00'` | 数字串变number，字段命名，上海时间显式化；金额不缩放；增加覆盖统计 |
| `createIntradayFlowClient({fetchImpl,now})`：创建独立缓存客户端，两个参数可注入供回放 | 脚本内创建client，连续调用3次 `client.getIntradayFlow('510300','etf')` | `fetchCalls=2,hit=true,fallbackStatus='cached',stale=true` | 第二次命中60秒缓存；第三次明确注入故障，源时间、抓取时间、数值均保持不变；测试时钟不是实测抓取时间 |
| `getIntradayFlow(symbol,kind?)`：使用共享客户端实际请求上游 | `node examples/intraday-flow.mjs live 510300 etf` | 此命令本轮未执行成功验证；不得把离线回放称为联网成功 | 成功增加真实 `fetched_at/served_at`；失败返回标记的缓存或空数据，不生成行情 |

`createIntradayFlowClient` 返回的同名 `getIntradayFlow` 方法与导出函数具有相同参数和结果。每个标的缓存60秒、请求超时12秒、并发请求合并；缓存数量受16个固定标的限制。`fetched_at` 是成功取得响应的时间，`as_of` 是末条源数据时间，`served_at` 是本次服务响应时间，三者不混用。

## 每一步处理

这些内部步骤通过同一个可运行脚本触发；没有公开成独立接口。

| 步骤 / 作用 | 真实输入与可运行入口 | 实际输出 | 前后变化 |
| --- | --- | --- | --- |
| `readPayload`：限制响应并解析JSON | 脚本client读取装入真实摘录的 `Response` | 成功解析2条；这是离线Response回放 | 最多500KB；字符串转对象，不改变源值；网络并未发生 |
| 身份及结构检查 | 脚本parser输入 `code=510300,market=1,rc=0`、每行6字段 | 通过，`invalid_rows=0` | 拒绝错代码/市场/结构，不误配指数与ETF |
| `parseMinute`：交易日和交易时段检查 | `2026-10-21 09:31`，调用脚本parser | `2026-10-21T09:31:00+08:00` | 保留实际分钟，明确上海时区；非法日期/非交易时段不入序列 |
| `number` 与净额关系检查 | ETF末行 `-435354626,236046672,-671401298` | 主力与大单加超大单一致，金额原值保留 | 有限数字串转number；可选字段未知保留null；主力无效时丢弃该行 |
| 交易日筛选、分钟去重、排序 | 每个标的2条同日不同分钟，调用脚本parser | 两条按时间排序，`duplicate_rows=0,dropped_other_day_rows=0` | 多日输入只保留最新有效日；同分钟取源返回的最后一条，不跨日拼接 |
| 覆盖统计 | ETF两个摘录点，到13:08应有128个常规分钟 | `observed_minutes=2,missing_minutes=126` | 仅报告缺口，不补点；09:30/13:00可保留，常规计数按09:31及13:01起算 |
| `clone/iso`：隔离缓存对象并标明响应时间 | 脚本三次client回放，时钟是明确测试控制 | 故障后的源时间/成功抓取时间/序列均保留 | 返回副本，服务时间可更新；旧行情不会被包装成新源数据 |

## 同源路由与持久化

**作用与参数**：`GET /api/flow-history?symbol=510300&kind=etf` 默认510300；也支持 `symbol=000300&kind=index`。`GET(request)` 解析白名单参数，请求当日资金分时，成功后调用 `saveIntradayFlowHistory(payload)`；失败后调用 `getStoredIntradayFlowHistory(symbol,kind?)`。只有ETF才可最后回退到明确标记的旧版页面采样，两类表不会拼接。

**真实输入**：运行时东方财富JSON及该站点已保存的真实记录，不能预写其当前数值。

**可复制调用**（已登录站点的浏览器控制台，无需Token）：

```javascript
for (const [symbol,kind] of [['510300','etf'],['000300','index']]) {
  const response=await fetch(`/api/flow-history?symbol=${symbol}&kind=${kind}`);
  const data=await response.json();
  console.log('实际输入参数与响应', {symbol,kind,http:response.status,data});
  console.log('输出与变化', {
    status:data.status,stored:data.storage_status,rows:data.bars?.length,
    first:data.bars?.[0],last:data.bars?.at(-1),coverage:data.coverage,
    sourceTime:data.as_of,fetchedAt:data.fetched_at,servedAt:data.served_at,
  });
}
```

**执行状态**：本页编写时上述浏览器调用与生产D1读写尚未复验；离线parser/client/图表回放已执行。当前执行环境另一次直连尝试被代理以403拒绝，不能据此声称东方财富接口停用。发布后的真实站点验证需另外记录。

**前后变化**：成功请求会将已有分钟序列写入新表 `intraday_fund_flow_bars`，不是仅保存打开页面之后的点。保存失败单列 `storage_status/storage_error`，本次真实上游数据仍返回。该GET会缓存真实数据，不是纯数据库查询。

| 存储函数 / 作用与真实输入 | 可运行入口 | 实际输出或状态 | 前后变化 |
| --- | --- | --- | --- |
| `saveIntradayFlowHistory(payload)`：接收client实际返回的live/partial、CNY、instrument序列及fetched_at | 上方同源请求成功分支自动调用；需Worker的D1绑定 | 生产存储本页尚未复验；可观察 `storage_status`；函数本身返回 `{status,saved,...}` | 按kind、symbol、源分钟去重；只更新实际改值且取得时间不更旧的记录；缓存不重复写；保留30天 |
| `getStoredIntradayFlowHistory(symbol,kind?)`：读取同标的最新已存交易日 | 上方同源请求上游失败分支自动调用；成功分支不会强行触发故障 | 回退路径本页尚未在生产复验；有数据时标记cached/stale，空库不可用 | 最多500条，返回原源时间/成功取得时间与实际金额；不创造缺失分钟，不写数据 |
| 存储数值、分钟归组与分批步骤 | 同上实际payload；`finite/nullable/chinaDate` 校验金额和上海交易日 | 仅离线输入形状已验证；D1执行结果以运行时为准 | 可选字段null保留；每批80条写入；清理30天前记录；旧采样表不迁入新表 |

页面可见时每60秒更新，关闭页面后没有新增后台定时采集。本次完整序列来自源端当日回补；重开页面可以再次取得源端仍提供的当日历史，不保证之后还能补回往日。下方ETF排行和大小单结构继续使用独立快照接口，可能与分时末点有时间差，分别标注源时间。

## 观察池指数资金修复

2026-10-21 的修复前生产快照中，六个指数的 `main_net` 均为null，但每个指数的 `/api/flow-history` 均实际返回160个有效分钟（09:31—13:40），无缺点。原因是旧快照合并只处理ETF，详情图的数据没有回填观察池。现由快照服务独立取六个指数的分时末点，表格和摘要直接使用各自指数的资金；ETF排行范围仍为10只ETF。

### 真实输入、可复制调用与实际输出

输入保存在 `examples/fixtures/index-watchlist-real.json`，包括真实修复前快照及六个指数各自的末两条真实分钟记录。为了使例子精简，其余158分钟未放入示例文件，这不表示数据源缺失。未访问QMT或用户私有数据库，也未构造价格。该文件是2026-10-21的历史摘录，不是当前行情。

```bash
node examples/index-watchlist.mjs
```

该命令已执行成功，输入对象和全部六个输出行由脚本打印。实际核心输出（金额单位元）：

| 指数 | 修复前main_net | 修复后main_net | 资金源时间（北京时间） |
| --- | ---: | ---: | --- |
| 上证指数000001 | null | 1145311134 | 2026-10-21 13:40 |
| 深证成指399001 | null | -133977209 | 2026-10-21 13:40 |
| 创业板指399006 | null | -1424259522 | 2026-10-21 13:40 |
| 沪深300000300 | null | -4847244879 | 2026-10-21 13:40 |
| 中证500000905 | null | -128093164 | 2026-10-21 13:40 |
| 科创50000688 | null | 677806697 | 2026-10-21 13:40 |

前后变化：观察池16行→16行；有资金值的指数0只→6只；null→有限number，货币单位明确为CNY；价格、价格源时间及10只ETF资金排行数据保持不变。同步合并与注入真实摘录的异步加载输出一致。页面将元换算为亿元展示，不修改存储金额。

### 每个函数和处理步骤

下表中的可运行入口均为上面的 `node examples/index-watchlist.mjs`；内部函数由公开函数实际触发，不需要手动导出。线上联网调用则通过同源 `/api/snapshot`，离线回放不冒充联网成功。

| 函数/步骤与作用 | 真实输入 | 调用示例 | 已执行实际输出 | 前后变化 |
| --- | --- | --- | --- | --- |
| `sourceStamp(value)`：校验明确时区的源时间，提取上海交易日 | `2026-10-21T13:40:00+08:00`，价格源13:41:24 | 脚本的merge调用内部触发 | 两者同属2026-10-21，合并通过 | 不修改时间；不以服务响应时间代替源时间 |
| `projectIndexFlow(item,history)`：校验指数身份、范围、单位和日期，选择最新有效资金分钟 | 上证指数实际快照，以及13:39/13:40两条资金行 | 脚本的merge调用内部触发 | `main_net=1145311134,flow_status=live,flow_as_of=13:40` | 原null被实际末点替换；`main_pct`仍为null，不用不同源成交额推算 |
| `mergeWatchingSnapshot(quotes,flow,indexHistories)`：按指数/ETF分别合并；第三参数是六份实际分时历史 | 脚本打印的before和独立ETF flow与六份history | 脚本内 `mergeWatchingSnapshot(before,flow,fixture.index_histories)` | 上表六行；`prices_unchanged=true,etf_flows_unchanged=true` | 保留16行及ETF范围，增加每行资金来源/时间/缓存状态 |
| `getWatchingSnapshot({getQuotes,getEtfFlow,getIndexFlow}={})`：获取报价、ETF快照和六指数分时；可选加载函数仅用于可复核回放 | 真实摘录，加载器返回这些实际对象 | 脚本内带三项加载器调用，随后比较结果 | `async_replay_matches=true` | 三个指数并发加载；单指数失败不丢弃其余行情；普通调用沿用60秒缓存客户端 |
| `renderTable()`：将快照主力净额展示为亿元，添加每行源时间与缓存标记 | 合并后上证指数1145311134元，13:40源时间 | 打开站点观察池；或下方读取DOM例子 | 离线金额换算为11.45311134亿元，界面保留2位；生产DOM须在发布后核验 | 金额本身不改；null仍显示—，真实0仍显示0.00；缓存不是实时值 |
| `currentInstrumentFlow()`：详情尚未取得或切换日K时，可使用指数快照末点显示摘要 | 合并后的对应指数对象 | 点击观察池指数，再切换日K | 分支实现已检查；生产交互在发布后核验，不编造界面结果 | 保留标的、资金源时间和状态，不将ETF资金套给指数 |

已登录站点后可复制执行下面的真实数据核验。此代码会调用实际数据源；运行时输出为准，本文不预写未来结果。

```javascript
const snapshot = await fetch('/api/snapshot').then(response => response.json());
console.table(snapshot.instruments.filter(row => row.kind === 'index').map(row => ({
  symbol: row.symbol, name: row.name, main_net: row.main_net,
  flow_as_of: row.flow_as_of, flow_status: row.flow_status, unit: row.unit.main_net
})));
console.log('表格当前实际文本', document.querySelector('#watch-rows')?.innerText);
```

作用：前一部分核验六个指数API值，后一部分读取页面渲染文本。真实输入为本次API响应及当前DOM；调用前后不修改行情或页面；该GET可能更新服务的内存缓存，指数请求本身不额外批写D1。跨交易日资金不会显示为当日净额；上游失败时保留适配器内存中的真实缓存并明确标记，冷启动无缓存时保持缺失。
