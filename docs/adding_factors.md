# Adding a factor

The catalogue is organized by economic mechanism rather than by data frequency.

```text
src/ashare_factor_lab/factors/
├── microstructure/
├── risk/
├── trading_activity/
└── fundamental/
```

## Workflow

1. Choose the category that best matches the signal's economic mechanism.
2. Add a module implementing `Factor.compute(data)` and assign the next stable `factor_id`.
3. Export the class from the category package and the top-level factor registry.
4. Add a hypothesis, formula, timing assumptions, and falsification checks to `factor_cards.md`.
5. Append a full-sample row to `results/summary.csv` and annual rows to
   `results/annual_metrics.csv`.
6. Optionally add a full diagnostic panel as `fxx_<factor_name>.png`, matching the ID and
   machine-readable name in `summary.csv`.
7. Regenerate public assets:

```bash
pip install -e ".[reports]"
python tools/generate_result_assets.py
python -m unittest discover -s tests
```

## Category rules

| Category | Use for |
|---|---|
| `microstructure` | Intraday price formation, order flow, volume concentration, and trade-size proxies |
| `risk` | Beta, volatility, residual distributions, tail risk, and covariance structure |
| `trading_activity` | Turnover, liquidity, traded value, participation, and stability measures |
| `fundamental` | Point-in-time accounting, valuation, quality, growth, and balance-sheet signals |

Create a new category only when the mechanism does not fit any existing group. Keep data-vendor
field mappings in an adapter; factor modules should use semantic public field names.
