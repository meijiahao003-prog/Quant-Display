"""Runnable, real-input examples. Run from backend/; default is read-only.

Every command prints its actual input, call, output and before/after shape.
No representative market output is invented. Training is opt-in.
"""
import argparse
import json
import math
from pathlib import Path

import app

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("step", choices=["source", "snapshot", "history", "analyze", "samples", "features", "models", "train"], default="snapshot", nargs="?")
parser.add_argument("--allow-write", action="store_true")
args = parser.parse_args()
print("Step:", args.step)
print("Time:", app.utc_now())
if args.step in {"source", "snapshot"}:
    raw = app.fetch_source("etf")
    print("REAL INPUT rows:", len(raw["rows"]))
    print("REAL INPUT first 5:", json.dumps(raw["rows"][:5], ensure_ascii=False, indent=2))
    print("SOURCE status/error:", raw["status"], raw["error"])
    if args.step == "snapshot":
        output = app.snapshot()
        print("CALL: app.snapshot()")
        print("ACTUAL OUTPUT:", json.dumps(output, ensure_ascii=False, indent=2))
        etfs = [r for r in output["instruments"] if r["kind"] == "etf"]
        print("BEFORE/AFTER: ETF source rows", len(raw["rows"]), "-> watchlist rows", len(etfs))
        print("Raw column names mapped; only configured symbols retained; missing watchlist rows explicitly null; numeric values not scaled.")
    else:
        print("CALL: app.fetch_source('etf')")
        print("BEFORE/AFTER: unchanged source column names and rows; source NaN -> JSON null; no fabricated rows.")
elif args.step == "history":
    raw = app.fetch_source("history_etf", "510300", "day")
    print("REAL INPUT first 5:", json.dumps(raw["rows"][:5], ensure_ascii=False, indent=2))
    output = app.history("510300", "etf", "day")
    print("CALL: app.history('510300', 'etf', 'day')")
    print("ACTUAL OUTPUT:", json.dumps({**output, "bars": output["bars"][:5]}, ensure_ascii=False, indent=2))
    print("BEFORE/AFTER rows:", len(raw["rows"]), len(output["bars"]))
    print("Dates renamed time, OHLC field names changed, missing OHLC removed, valid prices unchanged; no resampling.")
elif args.step == "analyze":
    snap = app.snapshot()
    print("REAL INPUT:", json.dumps(snap, ensure_ascii=False, indent=2))
    request = app.AnalysisRequest(snapshot=snap, selected_symbol="510300", mode="rules")
    print("CALL: app.analyze(app.AnalysisRequest(snapshot=snap, selected_symbol='510300', mode='rules'))")
    print("ACTUAL OUTPUT:", json.dumps(app.analyze(request), ensure_ascii=False, indent=2))
    print("BEFORE/AFTER: snapshot unchanged; 3 descriptive roles added. No model weights or trading positions changed.")
elif args.step == "samples":
    print("REAL INPUT: local database", app.DB_PATH)
    print("CALL: app.samples()")
    result = app.samples()
    print("ACTUAL OUTPUT:", json.dumps({**result, "items": result["items"][:5]}, ensure_ascii=False, indent=2))
    print("BEFORE/AFTER: zero rows added or deleted; listing limited to 200 records.")
elif args.step == "features":
    with app.connection() as conn:
        rows = conn.execute("SELECT payload FROM samples ORDER BY observed_at LIMIT 5").fetchall()
    if not rows:
        print("ACTUAL OUTPUT: no real saved samples; no substitute data generated.")
    for r in rows:
        payload = json.loads(r["payload"])
        print("REAL INPUT:", json.dumps(payload, ensure_ascii=False))
        print("CALL: app.training_features(payload)")
        print("ACTUAL OUTPUT:", app.training_features(payload))
    print("BEFORE/AFTER: each snapshot -> [change_pct, main_pct]; amounts/prices excluded; missing retained None before model fitting.")
elif args.step == "models":
    print("REAL INPUT: local trained model registry", app.DB_PATH)
    print("CALL: app.models()")
    print("ACTUAL OUTPUT:", json.dumps(app.models(), ensure_ascii=False, indent=2))
    print("BEFORE/AFTER: no writes; reports are only from completed local fits.")
elif args.step == "train":
    print("REAL INPUT:", json.dumps(app.samples(), ensure_ascii=False, indent=2))
    if not args.allow_write:
        print("NOT EXECUTED: pass --allow-write to train and save a local model.")
    else:
        print("CALL: app.train()")
        try:
            print("ACTUAL OUTPUT:", json.dumps(app.train(), ensure_ascii=False, indent=2))
        except app.HTTPException as exc:
            print("ACTUAL OUTPUT:", exc.status_code, exc.detail)
    print("BEFORE/AFTER: source samples unchanged; a successful fit adds one local model/report. Imputation fits training partition only; chronological validation separate.")
