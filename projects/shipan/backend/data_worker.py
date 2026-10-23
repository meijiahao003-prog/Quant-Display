"""Bounded subprocess for the real AKShare upstream. No demo or fallback prices.

Real-input example (requires internet, not a recorded result):
    python data_worker.py etf
Output is the actual upstream columns/rows; errors return a nonzero process code.
"""
import contextlib
import json
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def read_source(command, symbol=None, period="day"):
    """Read an AKShare DataFrame. Parameters select a fixed provider function.

    Real input / runnable example: python data_worker.py etf
    Actual output is printed by that command; no stored market output is invented.
    Transformation: source rows/columns/numeric values stay unchanged; only JSON
    serialization converts timestamps to ISO and NaN to null.
    """
    import akshare as ak
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    start = (now - timedelta(days=400)).strftime("%Y%m%d")
    end = now.strftime("%Y%m%d")
    if command == "etf":
        return ak.fund_etf_spot_em()
    if command == "index":
        return ak.stock_zh_index_spot_em(symbol="沪深重要指数")
    if command == "market_flow":
        return ak.stock_market_fund_flow()
    if command == "history_etf":
        if period == "day":
            return ak.fund_etf_hist_em(symbol=symbol, period="daily", start_date=start, end_date=end, adjust="")
        return ak.fund_etf_hist_min_em(symbol=symbol, period=period.removesuffix("m"), adjust="")
    if command == "history_index":
        if period == "day":
            return ak.index_zh_a_hist(symbol=symbol, period="daily", start_date=start, end_date=end)
        return ak.index_zh_a_hist_min_em(symbol=symbol, period=period.removesuffix("m"))
    raise ValueError("Unsupported source operation")


if __name__ == "__main__":
    try:
        with contextlib.redirect_stdout(sys.stderr):
            frame = read_source(*sys.argv[1:])
        # pandas converts timezone timestamps to UTC ISO, preserving the instant.
        print(frame.to_json(orient="records", date_format="iso", force_ascii=False))
    except Exception as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {str(exc)[:300]}"}, ensure_ascii=False))
        sys.exit(1)
