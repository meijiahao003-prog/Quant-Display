"""Command-line entry points for the shared quantitative research pipeline."""
import argparse
import json
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Quant research: features → frozen models → prediction evaluation")
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo", help="run reproducible synthetic research (no market returns)")
    demo.add_argument("--models", default="xgboost")
    demo.add_argument("--seed", type=int, default=7)
    demo.add_argument("--output", type=Path, required=True)
    run = sub.add_parser("run", help="run against separate local train-valid and untouched Test files")
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    from .pipeline import load_dataset, run_research
    if args.command == "demo":
        from .synthetic import make_demo
        train, test_loader, cfg = make_demo(args.seed, args.models.split(","))
        result = run_research(train, test_loader, cfg, args.output, synthetic=True)
    else:
        cfg = json.loads(args.config.read_text(encoding="utf-8"))
        data = cfg["dataset"]
        train_path, test_path = (args.config.parent / data[k] for k in ("train_valid", "test"))
        if train_path.resolve() == test_path.resolve():
            raise ValueError("train-valid and Test must be separate files")
        kwargs = {"features": data["features"], "target": data["target"]}
        train = load_dataset(train_path, **kwargs)
        result = run_research(train, lambda: load_dataset(test_path, **kwargs, allow_missing_labels=True), cfg, args.output)
    print(json.dumps({"status": result["status"], "synthetic": result["synthetic"],
                      "models": result["models"], "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
