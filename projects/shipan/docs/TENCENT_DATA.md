# 腾讯免费盘中行情接入

本版线上服务读取腾讯公开行情。无需 Token。它不是交易所授权 Level-2 接口，也没有供应商提供的延迟或可用性承诺。60 秒是本平台刷新/缓存间隔，不是上游更新保证。

本次实际读取成功：2026-10-21，北京时间 10:15 左右，16 个观察标的全部返回有效价格。下面的数字是当次响应记录，不是当前报价，也不是测试数据。再次运行会打印运行时真实响应。

| 内容 | 实际请求 | 格式与边界 |
| --- | --- | --- |
| 指数、ETF 报价 | `https://qt.gtimg.cn/q=sh000001,sz399001,sz399006,sh000300,sh000905,sh000688,sh510300,sh510500,sz159915,sh588000,sh512480,sh512660,sh518880,sh513100,sh510050,sh512010` | GB18030 文本；只解析白名单标识，不执行返回的 JavaScript |
| 当日分时 | `https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=sh510300` | JSON；价格采样点、累计量额，不冒充分钟 OHLC |
| 日 K | `https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?param=sh510300,day,,,240,` | JSON；未复权日线；当日未收盘时最后一根会变化 |
| 主力净流入 | 此腾讯报价源不提供 | `main_net=null`；不以成交额、委差或挂单量替代 |

接口映射参考开源项目原始代码：

- https://github.com/shidenggui/easyquotation/blob/master/easyquotation/tencent.py ：报价字段 3 最新价、30 时间、32 涨跌幅、37 成交额（万元）。
- https://github.com/akfamily/akshare/blob/main/akshare/stock_feature/stock_hist_tx.py ：日线字段 0/1/2/3/4/5/7/8 的映射，成交额乘 10,000；本次也核对了已安装的 AKShare 源码。
- https://github.com/akfamily/akshare/blob/main/akshare/index/index_stock_zh.py ：腾讯历史 K 线端点。

## 1. 读取、解析报价：`parseSourceTime` / `parseQuoteText`

**作用**：前者严格校验腾讯 14 位时间，后者把字符串字段转成 16 个观察标的的固定结构。未返回的观察标的保留行，缺失值保持 `null`。参数 `text` 是真实 HTTP 响应文本；`value` 是字段 30。

**真实输入**：父任务实际返回的上证指数报价含 `price="3732.74"`、`timestamp="20261021101130"`；ETF 510300 含 `price="4.218"`、`amount="114521"`。下面示例会重新读取真实源，打印原始内容和解析结果。

**调用示例**（在项目根目录复制运行，Node.js 22+）：

```bash
node --input-type=module - <<'JS'
import {parseQuoteText,parseSourceTime} from './lib/market-data.mjs';
const response=await fetch('https://qt.gtimg.cn/q=sh000001,sh510300',{signal:AbortSignal.timeout(12000)});
if(!response.ok) throw new Error(`HTTP ${response.status}`);
const raw=new TextDecoder('gb18030').decode(await response.arrayBuffer());
console.log('真实输入:',raw);
const rows=parseQuoteText(raw);
console.log('时间解析:',parseSourceTime('20261021101130'));
console.table(rows.filter(x=>x.price!==null));
console.log('前后变化:',{sourceRecords:(raw.match(/v_(?:sh|sz)\d{6}=/g)||[]).length,outputRows:rows.length,validPriceRows:rows.filter(x=>x.price!==null).length,missingRows:rows.filter(x=>x.price===null).length,amountUnit:'CNY',mainFlow:'null'});
JS
```

**实际输出与执行状态**：这个两代码示例未单独重跑；已执行第 4 节的完整模块真实请求和六项离线测试。确定性的已观测字段转换为：`20261021101130 → 2026-10-21T10:11:30+08:00`，`"4.218" → 4.218`，`"114521" 万元 → 1,145,210,000 元`。

**前后变化**：数值字符串变为 number；金额由万元转元；源时间保留 UTC+8；缺失不是 0；输出始终是 16 行白名单观察池；未请求或缺失行都为空。交易量暂保留上游原值并标记 `source_raw_unverified`。

## 2. 分时处理：`parseHistory(payload, item, '1m')`

**作用**：把腾讯当日分时原文转成绘图价格点。`payload` 是 JSON 响应，`item` 是 `UNIVERSE` 中一个白名单对象。

**真实输入**（本次收到的前两条）：

```json
{"date":"20261021","data":["0930 4.234 53836 22794162.00","0931 4.229 388694 164481136.00"]}
```

**调用示例**：

```bash
node --input-type=module - <<'JS'
import {UNIVERSE,parseHistory} from './lib/market-data.mjs';
const item=UNIVERSE.find(x=>x.symbol==='510300');
const response=await fetch(`https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=${item.code}`,{signal:AbortSignal.timeout(12000)});
if(!response.ok) throw new Error(`HTTP ${response.status}`);
const input=await response.json();
console.log('真实输入前两条:',input.data[item.code].data.data.slice(0,2));
const output=parseHistory(input,item,'1m');
console.table(output.bars.slice(0,2));
console.log('前后变化:',{inputRows:input.data[item.code].data.data.length,outputRows:output.bars.length,unit:output.unit,as_of:output.as_of,note:output.note});
JS
```

**实际输出**（已通过完整模块实际请求得到）：

| time | close | open / high / low | volume / amount | cumulative_volume | cumulative_amount |
| --- | ---: | --- | --- | ---: | ---: |
| 2026-10-21 09:30 | 4.234 | null | null | 53836 | 22794162 |
| 2026-10-21 09:31 | 4.229 | null | null | 388694 | 164481136 |

当次模块返回 47 条价格点，末点时间 `2026-10-21T10:16:00+08:00`。这是上游分时桶的时间标签，不能据此宣称该分钟已完成。再次读取条数会变化。

**前后变化**：时间补上源响应给定的日期；累计量额保持原数值并注明单位尚未独立核实；不会用相邻价格拼造 OHLC；不会把累计成交量当作每分钟成交量。错误、重复或缺失时间点会被剔除/按时间去重，剩余点按时间排序。

## 3. 日线处理：`parseHistory(payload, item, 'day')`

**作用**：只接受未复权 `day` 字段，校验 OHLC 价格结构，转换成交额单位。参数含义同上。

**真实输入**（先前 120 日请求的第一条）：

```json
["2026-04-22","4.760","4.811","4.813","4.756","10125926.00",{},"2.42","485159.31","0.000","0.000"]
```

**调用示例**：

```bash
node --input-type=module - <<'JS'
import {UNIVERSE,parseHistory} from './lib/market-data.mjs';
const item=UNIVERSE.find(x=>x.symbol==='510300');
const response=await fetch(`https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?param=${item.code},day,,,240,`,{signal:AbortSignal.timeout(12000)});
if(!response.ok) throw new Error(`HTTP ${response.status}`);
const input=await response.json();
console.log('真实输入前两条:',input.data[item.code].day.slice(0,2));
const output=parseHistory(input,item,'day');
console.table(output.bars.slice(0,2));
console.log('前后变化:',{inputRows:input.data[item.code].day.length,outputRows:output.bars.length,unit:output.unit,adjustment:output.adjustment});
JS
```

**实际输出**（已通过完整模块请求 240 日得到 241 条；上游额外包含当日未完成 K 线）：

| time | open | close | high | low | volume 原值 | amount 元 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2025-10-23 | 4.687 | 4.713 | 4.719 | 4.642 | 6993428 | 3267824400 |
| 2025-10-24 | 4.728 | 4.770 | 4.772 | 4.727 | 8653036 | 4110591100 |

**前后变化**：OHLC 数值不复权、不改价；金额万元 × 10,000 成为元；成交量仍保留源单位；剔除无效日期/坏价/结构错误记录；按日期去重排序。不能把返回条数固定认为等于请求条数。

## 4. 线上接口函数：`getHealth` / `getSnapshot` / `getHistory`

**作用**：`getHealth()` 返回服务能力（不代表供应商在线）；`getSnapshot()` 请求固定观察池；`getHistory(symbol,kind,period)` 接受六位代码、`index|etf`、`1m|day`。只允许观察池代码，不能传入任意 URL。

**真实输入**：请求的是第 1 节实际腾讯 URL；完整模块包含每次最多 12 秒的请求超时、60 秒内存缓存、同键并发请求合并。以下命令已执行过，结果如下。

**调用示例**：

```bash
node --input-type=module - <<'JS'
import {getHealth,getSnapshot,getHistory} from './lib/market-data.mjs';
console.log('服务能力:',getHealth());
const snapshot=await getSnapshot();
console.log('报价输出:',{status:snapshot.status,source:snapshot.source,as_of:snapshot.as_of,fetched_at:snapshot.fetched_at,errors:snapshot.errors});
console.table(snapshot.instruments.slice(0,7));
console.log('报价前后变化:',{expectedRows:16,outputRows:snapshot.instruments.length,validRows:snapshot.instruments.filter(x=>x.price!==null).length});
for(const period of ['1m','day']){
  const history=await getHistory('510300','etf',period);
  console.log('走势输出:',{period,status:history.status,as_of:history.as_of,rows:history.bars.length,error:history.error});
  console.table(history.bars.slice(0,2));
}
JS
```

**实际输出摘要**：报价 `status=live`、`16/16` 有效、`errors=[]`，抓取时间 `2026-10-21T02:15:52.659Z`。上证指数 `3734.96`，涨跌幅 `-0.01%`，源时间 `2026-10-21T10:15:48+08:00`；510300 `4.215`，涨跌幅 `-0.12%`，同一源时间。分时返回 47 点，日线返回 241 条。此为历史执行记录，不是承诺固定结果。

**前后变化**：服务抓取时间 `fetched_at` 与行情源时间 `as_of` 分开；缓存不刷新原抓取时间。上游失败时，有最后有效快照则 `status=cached, stale=true`，没有则 `status=unavailable` 并保留空值。第一轮失败不会被误标为有效缓存。真实价格不会回退成演示数据。主力资金始终缺失。

## 5. 传输、缓存及失败校验：`createMarketDataClient`

**作用**：通过 `fetchImpl` 注入 HTTP 传输、通过 `now` 注入毫秒时钟，离线检查失败恢复与缓存隔离。线上使用默认 Fetch 和真实时钟；测试使用显式合成协议样本，测试样本不进入产品。

**输入与调用示例**：

```bash
node --test tests/market-data.test.mjs
```

**实际输出**：6 项测试通过，0 项失败。覆盖 null 与 0、金额换算、坏时间与截断响应、非白名单拒绝、分钟点与 OHLC 分离、未复权 OHLC 检查、并发合并、过期失败保留原时间、失败结果不冒充缓存，以及调用者修改返回值不会污染缓存。

**前后变化**：测试注入值保持为测试数据；不发外部请求、不写数据库、不覆盖真实样本。

内部函数 `n / positive / nonnegative / dateOf / emptyRow` 负责数值、日期和空行校验；`read` 负责有界超时读取和编码；`cached` 负责 TTL、同键请求合并和明确的失败状态。它们的行为由上述可运行的真实例子与失败测试一起验证，不单独对产品暴露。

主力资金、样本存储、模型训练、Ollama 推理仍由完整 Python 服务提供。线上轻量行情代理的 health 中这四项能力明确为 false；本次接通真实行情不代表完成模型训练。
