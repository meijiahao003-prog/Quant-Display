# 宏观原则、适用环境与失效边界

依据为dalio-druckenmiller-kb-v2及trader-source-library-v1中相关正文已核验的公开资料。本技能编写于2026-10-21；该日复查了Bridgewater All Weather、Goldman Sachs 2021访谈页、Morgan Stanley 2026访谈页及Soros 2008文章。来源中作者的预测、统计及因果论证不因公开发表就成为客观真值。

下列原则为短意译，无逐字引语。**原则**概括来源中的思路，**A股应用、适用环境和失效边界**是我们的设计。不能将后者署名为原作者的A股交易法。版本与定位见 [source-map.json](source-map.json)，仅维护或查源时读取。

## 模块选择

| 方法ID与归属 | 用途及适用环境（我们的应用） | 失效或不适用边界（我们的应用） |
|---|---|---|
| MACRO-D：Dalio及Bridgewater相关材料，D1–D5 | 宏观/政策或盈利预期变化时，拆传导链、辨经济意外、检查共同风险暴露 | 没有预期基准便不能称超预期；传导未发生、受益对象错配或价格已计入时，具体机会论点不成立；长期债务分析不能预测本周买点 |
| MACRO-K：Druckenmiller，K1–K4 | 跨资产、公司信息与内部结构可核对时，寻找机会并检查实际反馈 | 价格变化可能有其他原因；单一代理或短时波动不足以否定整个宏观判断；若关键事实、预设交易条件或期限被破坏，原交易必须重估；集中仓位不能直接迁移 |
| MACRO-S：Soros，S1 | 价格、融资、抵押品或杠杆可能相互强化时，检查下跌反馈和流动性风险 | 缺少融资/被迫卖出证据时只是解释假设；政策或基本面可打断反馈；反身性不能给出泡沫顶部日期和幅度 |

三个模块不是三票。价格只被观测了一次，不因被多个框架引用就增强置信；具体交易边界优先按用户已约定计划处理，不能用宏观判断取消它。

## Dalio与Bridgewater相关框架

### D1：比较实际变化与已被计价的预期

原则：Bridgewater用增长和通胀相对市场定价的意外解释风险暴露；Dalio也讨论外推旧经验导致的判断偏差。不能只根据新闻“好/坏”决定资产方向。

A股应用：把政策/数据/盈利催化、可核验的此前预期及价格反应分列。没有预期证据就保留未知；发布后价格变动不能反过来证明事前共识。个股、行业与整个市场可能处于不同预期状态。

依据：D-S008，[The All Weather Story](https://www.bridgewater.com/research-and-insights/the-all-weather-story)，2012-01，开篇及 Balancing Growth and Inflation；D-S009，[Paradigm Shifts](https://www.linkedin.com/pulse/paradigm-shifts-ray-dalio/)，2019-07-17，Part I。

### D2：风险暴露不同于资金等分或择时

原则：All Weather是桥水团队平衡经济意外风险的被动配置探索，文章也区分现金、贝塔和阿尔法。机构配置思路不等于Dalio个人短线预测。

A股应用：检查多只持仓是否其实依赖同一个增长、融资或成本假设；同属A股的多个板块不等于复制跨资产全天候。复盘时以合适基准区分市场上涨与主动判断，不凭一段盈利宣称稳定阿尔法。

依据：D-S008，同上，Portfolio Building Blocks、Balancing Growth and Inflation及机构作者说明。文章作者为Paul Podolsky、Ryan Johnson、Owen Jennings，涉及Dalio及同事共同工作。

### D3：政策工具与传导对象要分开

原则：Dalio区分利率、资产购买与更直接作用于支出的财政货币协调；文章保留政策路径和时点的不确定性。

A股应用：对国内货币、财政、产业政策分别核对实施和传导，检查信用需求、支出与盈利能否接上。不能把中国工具机械替换成美联储标签，也不能从“宽松”直接推出买入结论。

依据：D-S011，[What Monetary Policy 3 Will Look Like](https://www.linkedin.com/pulse/what-monetary-policy-3-mp3-look-like-ray-dalio)，2016-02-18，Why Pushing on a String及末尾限定；D-S012，[MP3 and MMT](https://www.linkedin.com/pulse/its-time-look-more-carefully-monetary-policy-3-mp3-modern-ray-dalio/)，2019-05-01，Part 1及异议段。

### D4：检查支撑叙事的条件是否持续

原则：Dalio讨论旧范式失效与新范式变化的条件，反对直接外推近期最成功的关系；文章中的历史与未来情景应分开。

A股应用：对当前主线问政策是否兑现、需求/盈利是否支持、价格是否已经透支，以及什么变化会令观点失效。长期债务或秩序叙事只作背景，不能充当本周拐点计时器。

依据：D-S009，Part I与Part 2。2019年某个资产的观点不转成跨期永久持有指令。

### D5：平均数可能掩盖部门差异

原则：桥水联名研究讨论不同群体的资产负债表与支出能力，提示宏观平均值可能掩盖分布差异。

A股应用：消费、地产链、制造或金融板块需要相关部门证据；总体增长不能自动证明每条产业链改善，也不能用一家公司困境推导全市场需求。分布变量是景气解释背景，不是即时买卖信号。

依据：D-S013，[The Two Economies](https://www.bridgewater.com/our-biggest-economic-social-and-political-issue)，2017-10-23，联名作者、正文与脚注；原文统计不当作中国当前数据。

## Druckenmiller相关框架

### K1：允许选择机会，也允许不参与

原则：2021年访谈把跨资产机会、流动性、改变观点和避开难懂市场联系起来。他对日历年业绩也承认衡量窗口与运气的影响。

A股应用：用其他市场检验A股假设，但不因此扩大用户交易权限；机会不清楚时提出观察条件，不强行满仓。基金经理的集中持仓经历不产生用户适用的固定仓位比例或最大损失保证。

依据：K-S001，[Goldman Sachs访谈](https://www.goldmansachs.com/insights/talks-at-gs/stanley-druckenmiller.html)，录制2021-01-21，发布2021-02-03；[官方PDF](https://www.goldmansachs.com/pdfs/insights/goldman-sachs-talks/stanley-druckenmiller-f/transcript.pdf)，PDF第5–6页。

### K2：看融资用途与承债能力，不只看资金总量

原则：2016年演讲比较借款、经营现金流、资本开支和回购等用途；2020年访谈区分流动性与偿付能力，并讨论财政发行和央行购买节奏的条件影响。

A股应用：将融资可得性、融资成本、有效需求与偿付能力分开。信用扩张、银行间资金宽松和股票交易活跃是不同观察；低频数据不能单独解释当天涨跌。借款增加也可能支持有效投资，不能一律判为危机。

依据：K-S003，[Sohn 2016官方域名存档](https://web.archive.org/web/20160527064545/http://www.sohnconference.org:80/wp-content/uploads/2016/05/The_Endgame_Sohn.pdf)，PDF第4–6页；K-S010，[ECNY官方2020转录](https://econclubny.globalnorthstar.net/documents/20124/74672/2020DruckenmillerTranscript.pdf/f533ce0a-d3f6-4c91-4e26-b3dea49c7797)，PDF第5–10页。活动日期不自动等于转录首次公开日。

### K3：用公司信息与市场内部结构检验宏观

原则：2026年访谈说明，公司信息、领先/滞后行业和市场内部结构是其形成判断的输入。

A股应用：并列看指数、个股宽度、板块相对强弱与多日延续，防止少数权重股掩盖内部弱势。具体指标、样本与窗口是我们选择的，不是本人规定的量化阈值；少数公司也不能代表总需求。

依据：K-S006，[Morgan Stanley：Hard Lessons](https://www.morganstanley.com/insights/videos/hard-lessons/duquesne-stan-druckenmiller-iliana-bouzali)，2026-02-27，Pick One with Stan Druckenmiller的市场内部结构问答。回忆材料仅自其公开后可作当期文献。

### K4：预期反应与实际反馈不符时检查遗漏

原则：2021年访谈讨论实际组合反应偏离预想时的警觉；公开经历支持检验、修正判断，而不是为了自洽坚持旧观点。

A股应用：事先明确观察期限、受益链条与失效信号，再比较价格/宽度/相对强弱；没有用户真实组合数据就只讨论价格反馈。单次波动、除权或被动调仓可能是假背离，不把每根阴线都判成逻辑失效。

依据：K-S001，官方PDF第6页；K-S006，情绪、市场反馈与修正判断的相关问答。没有证据支持固定均线、神奇止损比例或永久重仓公式。

## Soros：反馈机制的补充

### S1：价格可能反过来改变融资与基本条件

原则：Soros讨论价格、抵押品、信用扩张与被迫平仓的相互影响，同时明确反身性更擅长解释，不能确定繁荣持续多久、幅度多大。

A股应用：在有相关披露时检查股权质押、融资、赎回或流动性压力如何影响相关证券；没有数据不虚构强平。区分“存在自我强化风险”与“已经确认拐点”，并检查政策或经营变化是否中断链条。这不是对每次上涨都贴泡沫标签。

依据：R-S004，[The Crisis & What To Do About It](https://www.georgesoros.com/2008/11/06/the_crisis_what_to_do_about_it/)，2008-11-06，作者官网转载原刊，第2节反馈机制、第3节预测局限。宏观解释文章，不是本人逐笔交易日志；没有读完《金融炼金术》之意。

## 明确不吸收的旧库内容

不使用未经核验的固定七阶段标签、五大力量0–10评分、15个独立资产的收益保证、所谓固定gross仓位上限；不把转载频次当独立证据。原库B05章号/章名错误、1992书章混入2000案例、80bn误译、机构13F归属个人实时决策等问题已隔离。

本技能可解释已观察盘面并组织条件判断；公开材料无法完整还原来源人物的实时组合、交易成本、私有信息、成交执行或完整错误样本。是否提升A股波段判断，需要未来独立评估，不能用来源名气代替检验。
