# Third-party notices

This project bundles Apache ECharts 6.1.0 from its official npm distribution.
- Project: https://github.com/apache/echarts
- Distribution: https://registry.npmjs.org/echarts/-/echarts-6.1.0.tgz
- License: Apache License 2.0, copied to `public/vendor/ECHARTS-LICENSE.txt`.
- Upstream NOTICE is included at `public/vendor/ECHARTS-NOTICE.txt`.

Python dependencies are installed from the pinned requirements, and retain their own licenses. AKShare, LangGraph, FastAPI, and scikit-learn are used as libraries. No model weights are bundled. No TradingAgents, Qlib, OpenBB, FinGPT or Lightweight Charts source was copied. Those repositories appear as clearly labeled research references only.

The user interface and gateway integration are new project code. Synthetic demo data is generated locally and is not exchange data. Third-party data rights and access conditions remain those of each provider.

The hosted quote proxy calls Tencent public quote and history feeds. Field semantics were checked against easyquotation and AKShare upstream implementations; no provider JavaScript is evaluated. The maintained Sites/Vinext starter and its build integration are retained with included notices.
