"""Regenerate public result charts and per-factor reports from sanitized CSV files."""

from __future__ import annotations

import csv
import os
import tempfile
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "quant-factor-mpl"))

import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
FIGURES = RESULTS / "figures"
REPORTS = RESULTS / "factor_reports"

CATEGORY_COLORS = {
    "microstructure": "#2563EB",
    "risk": "#7C3AED",
    "trading_activity": "#0F766E",
    "fundamental": "#C2410C",
}

NUMERIC_SUMMARY = {
    "ic",
    "ic_5d",
    "rank_ic",
    "percentile_ic",
    "ic_ir",
    "ic_5d_ir",
    "rank_ic_ir",
    "percentile_ic_ir",
    "coverage",
    "days",
    "max_ic_correlation",
    "max_rank_ic_correlation",
}
NUMERIC_ANNUAL = NUMERIC_SUMMARY - {
    "max_ic_correlation",
    "max_rank_ic_correlation",
}


def read_rows(path: Path, numeric_fields: set[str]) -> list[dict[str, str | float]]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows: list[dict[str, str | float]] = list(csv.DictReader(handle))
    for row in rows:
        for field in numeric_fields & row.keys():
            row[field] = float(row[field])
    return rows


def configure_axes(ax: plt.Axes) -> None:
    ax.set_facecolor("#FAFAFA")
    ax.grid(axis="x", color="#D1D5DB", linewidth=0.7, alpha=0.65)
    ax.set_axisbelow(True)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.tick_params(axis="y", length=0)


def save_figure(fig: plt.Figure, filename: str) -> None:
    FIGURES.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGURES / filename, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def metric_overview(summary: list[dict[str, str | float]]) -> None:
    labels = [f"{row['factor_id']}  {row['display_name']}" for row in summary]
    y = np.arange(len(summary))
    width = 0.24
    fig, ax = plt.subplots(figsize=(12.5, 7.2))
    ax.barh(y - width, [row["ic"] for row in summary], width, label="IC", color="#2563EB")
    ax.barh(y, [row["ic_5d"] for row in summary], width, label="5-day IC", color="#14B8A6")
    ax.barh(y + width, [row["rank_ic"] for row in summary], width, label="Rank IC", color="#F59E0B")
    ax.set_yticks(y, labels)
    ax.invert_yaxis()
    ax.set_xlabel("Cross-sectional correlation")
    ax.set_title(
        f"Predictive metrics across {len(summary)} factors",
        loc="left",
        fontsize=16,
        weight="bold",
        pad=34,
    )
    ax.text(
        0,
        1.01,
        "Full-sample evaluator outputs; higher values indicate stronger forward association.",
        transform=ax.transAxes,
        color="#4B5563",
    )
    ax.legend(frameon=False, ncol=3, loc="lower right")
    configure_axes(ax)
    save_figure(fig, "predictive_metrics.png")


def information_ratio_overview(summary: list[dict[str, str | float]]) -> None:
    labels = [f"{row['factor_id']}  {row['display_name']}" for row in summary]
    y = np.arange(len(summary))
    width = 0.32
    fig, ax = plt.subplots(figsize=(12.5, 7.2))
    ax.barh(y - width / 2, [row["ic_ir"] for row in summary], width, label="ICIR", color="#4F46E5")
    ax.barh(
        y + width / 2,
        [row["rank_ic_ir"] for row in summary],
        width,
        label="Rank ICIR",
        color="#06B6D4",
    )
    ax.set_yticks(y, labels)
    ax.invert_yaxis()
    ax.set_xlabel("Mean IC / standard deviation of IC")
    ax.set_title("Signal consistency", loc="left", fontsize=16, weight="bold", pad=34)
    ax.text(
        0,
        1.01,
        "Unannualized IC information ratios from the same evaluation window.",
        transform=ax.transAxes,
        color="#4B5563",
    )
    ax.legend(frameon=False, ncol=2, loc="lower right")
    configure_axes(ax)
    save_figure(fig, "information_ratios.png")


def annual_heatmap(
    summary: list[dict[str, str | float]], annual: list[dict[str, str | float]]
) -> None:
    years = sorted({int(row["year"]) for row in annual})
    by_key = {(row["factor_id"], int(row["year"])): row["ic"] for row in annual}
    matrix = np.full((len(summary), len(years)), np.nan)
    for row_index, factor in enumerate(summary):
        for column_index, year in enumerate(years):
            matrix[row_index, column_index] = by_key.get((factor["factor_id"], year), np.nan)

    fig, ax = plt.subplots(figsize=(10.5, 7.0))
    image = ax.imshow(matrix, cmap="YlGnBu", vmin=0.0, vmax=max(0.05, np.nanmax(matrix)))
    ax.set_xticks(np.arange(len(years)), years)
    ax.set_yticks(
        np.arange(len(summary)),
        [f"{row['factor_id']}  {row['display_name']}" for row in summary],
    )
    ax.set_title("Annual IC stability", loc="left", fontsize=16, weight="bold", pad=18)
    for row_index in range(matrix.shape[0]):
        for column_index in range(matrix.shape[1]):
            value = matrix[row_index, column_index]
            label = "—" if np.isnan(value) else f"{value:.3f}"
            ax.text(column_index, row_index, label, ha="center", va="center", fontsize=9)
    ax.tick_params(length=0)
    ax.spines[:].set_visible(False)
    colorbar = fig.colorbar(image, ax=ax, fraction=0.03, pad=0.03)
    colorbar.set_label("IC")
    save_figure(fig, "annual_ic_heatmap.png")


def write_factor_reports(
    summary: list[dict[str, str | float]], annual: list[dict[str, str | float]]
) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    annual_by_factor = {
        factor_id: [row for row in annual if row["factor_id"] == factor_id]
        for factor_id in {row["factor_id"] for row in summary}
    }
    for factor in summary:
        factor_id = factor["factor_id"]
        panel_filename = f"{factor_id.lower()}_{factor['factor_name']}.png"
        status_copy = (
            "Passed the configured research evaluation gate."
            if factor["status"] == "passed"
            else "Retained as a diagnostic research case; not every configured check passed."
        )
        annual_lines = [
            "| Year | IC | 5-day IC | Rank IC | ICIR | Coverage | Days |",
            "|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for row in annual_by_factor[factor_id]:
            annual_lines.append(
                f"| {int(row['year'])} | {row['ic']:.4f} | {row['ic_5d']:.4f} | "
                f"{row['rank_ic']:.4f} | {row['ic_ir']:.4f} | {row['coverage']:.2%} | "
                f"{int(row['days'])} |"
            )
        report = f"""# {factor_id} — {factor['display_name']}

- **Category:** `{factor['category']}`
- **Evaluation window:** {factor['start_date']} to {factor['end_date']}
- **Research status:** `{factor['status']}` — {status_copy}

## Full-sample summary

| IC | 5-day IC | Rank IC | ICIR | Rank ICIR | Coverage | Days |
|---:|---:|---:|---:|---:|---:|---:|
| {factor['ic']:.4f} | {factor['ic_5d']:.4f} | {factor['rank_ic']:.4f} | {factor['ic_ir']:.4f} | {factor['rank_ic_ir']:.4f} | {factor['coverage']:.2%} | {int(factor['days'])} |

## Annual diagnostics

{chr(10).join(annual_lines)}

## Archived evaluation panel

The panel below is retained as supplied by the original research evaluator. Historical labelling
is preserved; no raw market data or factor parquet is distributed.

![{factor['display_name']} evaluation panel](../full_reports/{panel_filename})
"""
        filename = f"{factor_id.lower()}_{factor['factor_name']}.md"
        (REPORTS / filename).write_text(report, encoding="utf-8")


def main() -> None:
    summary = read_rows(RESULTS / "summary.csv", NUMERIC_SUMMARY)
    annual = read_rows(RESULTS / "annual_metrics.csv", NUMERIC_ANNUAL)
    metric_overview(summary)
    information_ratio_overview(summary)
    annual_heatmap(summary, annual)
    write_factor_reports(summary, annual)
    print(f"Generated 3 charts and {len(summary)} factor reports.")


if __name__ == "__main__":
    main()
