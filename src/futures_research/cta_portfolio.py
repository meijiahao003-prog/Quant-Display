"""Sector-neutral CTA signal portfolios and a deliberately bounded research P&L.

This is not an execution engine. Every non-overlapping, explicitly dated return
interval opens its target notionals and liquidates them at the interval end.
No position survives an unobserved overnight/session gap. Both entry and exit
costs, including the final liquidation, are charged. Returns must be raw simple
instrument returns, never the demeaned or standardized prediction target.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np
import pandas as pd


KEYS = ["Date", "Contract", "Datetime"]
BARS = ["Date", "Datetime"]


def _timestamps(frame: pd.DataFrame, columns: list[str]) -> None:
    for column in columns:
        frame[column] = pd.to_datetime(frame[column], errors="raise")
        if frame[column].isna().any() or frame[column].dt.tz is not None:
            raise ValueError(f"{column} must contain finite, timezone-naive timestamps")


def _panel(frame: pd.DataFrame, required: list[str], *, duplicate_keys=KEYS) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise ValueError("a non-empty long-form DataFrame is required")
    if frame.columns.has_duplicates or not set(required).issubset(frame.columns):
        raise ValueError(f"missing or duplicate columns; required fields: {required}")
    result = frame[required].copy()
    _timestamps(result, [c for c in BARS if c in required])
    if "Date" in required and not result.Date.eq(result.Date.dt.normalize()).all():
        raise ValueError("Date must be the normalized exchange trading date")
    for column in ("Contract", "sector", "model", "product", "role"):
        if column in required:
            if result[column].isna().any():
                raise ValueError(f"missing {column}")
            result[column] = result[column].astype(str)
            if result[column].str.strip().eq("").any():
                raise ValueError(f"empty {column}")
    if result.duplicated(duplicate_keys).any():
        raise ValueError(f"duplicate rows for keys {duplicate_keys}")
    if set(BARS).issubset(required):
        if result.groupby("Datetime")["Date"].nunique().gt(1).any():
            raise ValueError("a Datetime cannot refer to multiple trading dates")
    return result


def _finite(series: pd.Series, name: str) -> np.ndarray:
    try:
        values = series.to_numpy(dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric and finite") from exc
    if not np.isfinite(values).all():
        raise ValueError(f"{name} must be numeric and finite")
    return values


def _fixed_weights(values: Mapping[str, float], name: str, *, positive: bool) -> dict[str, float]:
    if not values or any(not isinstance(k, str) or not k.strip() for k in values):
        raise ValueError(f"{name} must be a non-empty mapping of named weights")
    result = {key: float(value) for key, value in values.items()}
    weights = np.asarray(list(result.values()))
    if (not np.isfinite(weights).all() or (weights < 0).any()
            or (positive and (weights == 0).any()) or not np.isclose(weights.sum(), 1., atol=1e-10, rtol=0)):
        raise ValueError(f"{name} must sum to one and be finite and {'positive' if positive else 'non-negative'}")
    return result


def ensemble_predictions(predictions: pd.DataFrame, *, model_weights: Mapping[str, float]) -> pd.DataFrame:
    """Rank each model within a sector/bar, then combine with frozen weights.

    Every configured model must cover exactly the same instrument/bar keys.
    Missing predictions are errors, not permission to change the ensemble.
    Extra input columns (including actual/future returns) are never inspected.
    The caller must freeze model weights before test evaluation; this function
    validates their structure but cannot infer their historical selection date.
    """
    frozen = _fixed_weights(model_weights, "model_weights", positive=True)
    data = _panel(predictions, KEYS + ["sector", "model", "prediction", "signal_available_at"],
                  duplicate_keys=KEYS + ["model"])
    data["prediction"] = _finite(data.prediction, "prediction")
    _timestamps(data, ["signal_available_at"])
    if (data.signal_available_at < data.Datetime).any():
        raise ValueError("signal availability cannot precede its decision timestamp")
    if set(data.model) != set(frozen):
        raise ValueError("prediction models must exactly match frozen model_weights")
    models_per_sample = data.groupby(KEYS, sort=False).model.nunique()
    if not models_per_sample.eq(len(frozen)).all():
        raise ValueError("all ensemble models must have identical sample coverage")
    if data.groupby(KEYS, sort=False).sector.nunique().gt(1).any():
        raise ValueError("models disagree on sample sector membership")
    rank_groups = BARS + ["sector", "model"]
    data["rank_prediction"] = data.groupby(rank_groups, sort=False).prediction.rank(method="average", pct=True)
    data["weighted_prediction"] = data.rank_prediction * data.model.map(frozen)
    # A cross-sectional rank needs every participating prediction, even if a
    # particular contract's own inference completed earlier than its peers.
    data["signal_available_at"] = data.groupby(BARS + ["sector"], sort=False).signal_available_at.transform("max")
    result = data.groupby(KEYS + ["sector"], as_index=False, sort=False).agg(
        prediction=("weighted_prediction", "sum"), signal_available_at=("signal_available_at", "max"))
    result = result.sort_values(["Datetime", "Date", "Contract"]).reset_index(drop=True)
    result.attrs["ensemble_audit"] = {
        "normalization": "within_sector_bar_average_percentile_rank",
        "model_weights": frozen,
        "coverage": "identical_across_all_models",
        "future_returns_used": False,
        "model_weight_selection_time": "caller_must_freeze_before_test",
    }
    return result


def _metadata_frame(metadata) -> tuple[pd.DataFrame, list[str]]:
    if isinstance(metadata, Mapping):
        frame = pd.DataFrame.from_dict(metadata, orient="index").rename_axis("Contract").reset_index()
    elif isinstance(metadata, pd.DataFrame):
        frame = metadata.copy()
    else:
        raise ValueError("contract_metadata must be a mapping or DataFrame")
    keys = [c for c in KEYS if c in frame.columns]
    if "Contract" not in keys or ("Datetime" in keys and "Date" not in keys):
        raise ValueError("metadata keys must be Contract, Date/Contract, or Date/Contract/Datetime")
    required = keys + ["sector", "product", "role"]
    if "metadata_available_at" in frame:
        required += ["metadata_available_at"]
    frame = _panel(frame, required, duplicate_keys=keys)
    if not set(frame.role) <= {"main", "secondary"}:
        raise ValueError("role must explicitly be main or secondary; suffix inference is unsupported")
    if "metadata_available_at" in frame:
        _timestamps(frame, ["metadata_available_at"])
    return frame, keys


def build_sector_weights(
    signals: pd.DataFrame,
    contract_metadata: Mapping | pd.DataFrame,
    *,
    sector_allocations: Mapping[str, float],
    long_quantile: float = .8,
    short_quantile: float = .2,
    contract_set: str = "both",
) -> pd.DataFrame:
    """Equal-weight long/short tails within sectors with fixed PORT capital.

    Each active sector has local gross exposure one and net exposure zero.
    Its contribution is multiplied by its predeclared capital allocation. An
    absent, flat, or single-instrument sector stays in cash; active sectors never
    absorb its allocation. Tied tail scores receive equal weights. Metadata can
    be static, date-specific, or bar-specific; use dated records when main and
    secondary roles change. Any supplied metadata availability must be no later
    than the portfolio signal, otherwise the metadata is rejected.
    """
    allocations = _fixed_weights(sector_allocations, "sector_allocations", positive=False)
    if not (0 <= short_quantile < .5 < long_quantile <= 1):
        raise ValueError("quantiles must satisfy 0 <= short < .5 < long <= 1")
    if contract_set not in {"main", "secondary", "both"}:
        raise ValueError("contract_set must be main, secondary, or both")
    data = _panel(signals, KEYS + ["sector", "prediction", "signal_available_at"])
    data["prediction"] = _finite(data.prediction, "prediction")
    _timestamps(data, ["signal_available_at"])
    if (data.signal_available_at < data.Datetime).any():
        raise ValueError("signal availability cannot precede its decision timestamp")
    metadata, metadata_keys = _metadata_frame(contract_metadata)
    metadata = metadata.rename(columns={"sector": "metadata_sector"})
    data = data.merge(metadata, on=metadata_keys, how="left", validate="many_to_one", indicator=True)
    if data._merge.ne("both").any():
        raise ValueError("every signal requires explicit contract metadata")
    if not data.sector.eq(data.metadata_sector).all():
        raise ValueError("signal sectors disagree with contract metadata")
    if not set(data.sector) <= set(allocations):
        raise ValueError("signals contain a sector without a fixed capital allocation")
    if "metadata_available_at" in data and (data.metadata_available_at > data.signal_available_at).any():
        raise ValueError("contract metadata was unavailable at signal time")
    # A role identifies one real contract per product/bar. Ambiguous duplicates
    # usually indicate a continuous alias and real contract were both supplied.
    if data.duplicated(BARS + ["product", "role"]).any():
        raise ValueError("multiple contracts claim the same product/role at one bar")
    if data.groupby(BARS + ["product"], sort=False).sector.nunique().gt(1).any():
        raise ValueError("one product cannot belong to multiple sectors at the same bar")
    data["sector_weight"] = 0.
    data["sector_active"] = False
    for _, group in data.groupby(BARS + ["sector"], sort=False):
        available_at = group.signal_available_at.max()
        data.loc[group.index, "signal_available_at"] = available_at
        eligible = group if contract_set == "both" else group[group.role.eq(contract_set)]
        if len(eligible) < 2 or eligible.prediction.nunique() < 2:
            continue
        low, high = eligible.prediction.quantile([short_quantile, long_quantile])
        if not low < high:
            continue
        short = eligible.index[eligible.prediction <= low]
        long = eligible.index[eligible.prediction >= high]
        if len(short) == 0 or len(long) == 0 or len(short.intersection(long)):
            continue
        data.loc[short, "sector_weight"] = -.5 / len(short)
        data.loc[long, "sector_weight"] = .5 / len(long)
        data.loc[group.index, "sector_active"] = True
    data["sector_allocation"] = data.sector.map(allocations)
    data["weight"] = data.sector_weight * data.sector_allocation
    columns = KEYS + ["sector", "product", "role", "prediction", "signal_available_at",
                      "sector_weight", "sector_allocation", "sector_active", "weight"]
    result = data[columns].sort_values(["Datetime", "Date", "Contract"]).reset_index(drop=True)
    result.attrs["portfolio_audit"] = {
        "contract_set": contract_set, "sector_allocations": allocations,
        "long_quantile": long_quantile, "short_quantile": short_quantile,
        "unavailable_or_degenerate_sector": "retain_allocation_in_cash",
        "role_source": "explicit_caller_metadata",
        "metadata_point_in_time_verified": "metadata_available_at" in data,
        "future_returns_used": False,
    }
    return result


@dataclass(frozen=True)
class ResearchBacktestResult:
    curves: pd.DataFrame
    metrics: dict
    audit: dict


def research_backtest(
    weights: pd.DataFrame,
    returns: pd.DataFrame,
    *,
    commission_bps: float = .2,
    slippage_bps: float = 1.,
    periods_per_year: float | None = None,
) -> ResearchBacktestResult:
    """Evaluate raw returns for non-overlapping intervals, with full liquidation.

    Weights are signed target notionals divided by equity *before* entry fees.
    For each interval: gross P&L=sum(w*r), entry turnover=sum(abs(w)), exit
    turnover=sum(abs(w*(1+r))). Both one-way costs are deducted from that same
    starting equity. Idle cash earns zero, financing/borrow/margin costs are
    excluded, and no contract multipliers or fill model are inferred. This is
    a research notional-return calculation, not futures settlement accounting.

    `returns` must contain Date, Contract, Datetime, return_start, return_end,
    and return. Optional label_available_at must be at/after return_end for
    every held instrument; without that field, availability is return_end.
    Before each next entry, the preceding held outcomes must be available so
    position sizing cannot compound equity that was not yet observable.
    Unknown/delayed outcomes of zero positions do not affect portfolio equity.
    Supply raw simple asset returns; statistical prediction labels are not P&L.
    """
    costs = np.asarray([commission_bps, slippage_bps], dtype=float)
    if not np.isfinite(costs).all() or (costs < 0).any():
        raise ValueError("costs must be finite, non-negative one-way basis points")
    if periods_per_year is not None and (not np.isfinite(periods_per_year) or periods_per_year <= 0):
        raise ValueError("periods_per_year must be explicitly positive and finite")
    w = _panel(weights, KEYS + ["weight", "signal_available_at"])
    w["weight"] = _finite(w.weight, "weight")
    _timestamps(w, ["signal_available_at"])
    if (w.signal_available_at < w.Datetime).any():
        raise ValueError("signal availability cannot precede its decision timestamp")
    return_columns = KEYS + ["return_start", "return_end", "return"]
    if "label_available_at" in returns:
        return_columns.append("label_available_at")
    r = _panel(returns, return_columns)
    _timestamps(r, ["return_start", "return_end"])
    if "label_available_at" in r:
        # Non-held outcomes may be unknown, just like non-held returns. The
        # actual holdings are joined below before enforcing availability.
        r["label_available_at"] = pd.to_datetime(r.label_available_at, errors="raise")
        if r.label_available_at.dt.tz is not None:
            raise ValueError("label_available_at must contain timezone-naive timestamps")
    else:
        r["label_available_at"] = r.return_end
    if (r.return_start < r.Datetime).any() or (r.return_end <= r.return_start).any():
        raise ValueError("returns require Datetime <= return_start < return_end")
    w_bars = w[BARS].drop_duplicates()
    r_bars = r[BARS].drop_duplicates()
    if not w_bars.Datetime.is_monotonic_increasing or not r_bars.Datetime.is_monotonic_increasing:
        raise ValueError("input bars must be in chronological order")
    if set(map(tuple, w_bars.to_numpy())) != set(map(tuple, r_bars.to_numpy())):
        raise ValueError("weights and returns must cover exactly the same bars")
    interval_counts = r.groupby(BARS)[["return_start", "return_end"]].nunique()
    if not interval_counts.eq(1).all().all():
        raise ValueError("every instrument in a bar must use the same return interval")
    intervals = r.groupby(BARS, sort=False)[["return_start", "return_end"]].first().reset_index()
    if (intervals.return_start.iloc[1:].reset_index(drop=True)
            < intervals.return_end.iloc[:-1].reset_index(drop=True)).any():
        raise ValueError("forward return intervals overlap or are out of order")
    data = w.merge(r.drop(columns=["return_start", "return_end"]), on=KEYS, how="left",
                   validate="one_to_one", indicator=True)
    data = data.merge(intervals, on=BARS, how="left", validate="many_to_one")
    if (data.return_start < data.signal_available_at).any():
        raise ValueError("return_start precedes signal availability")
    held = data.weight.ne(0)
    if data.loc[held, "_merge"].ne("both").any():
        raise ValueError("missing return for a held instrument")
    held_availability = data.loc[held, "label_available_at"]
    if held_availability.isna().any():
        raise ValueError("held instrument label_available_at must be finite")
    if (held_availability < data.loc[held, "return_end"]).any():
        raise ValueError("realized returns cannot be available before return_end")
    held_returns = _finite(data.loc[held, "return"], "held instrument return")
    if (held_returns < -1).any():
        raise ValueError("simple returns below -100% are unsupported by this notional research model")
    # Only zero positions can have unavailable outcomes. Their zero P&L follows
    # from zero notional; a held instrument is never silently filled with zero.
    data["held_return"] = 0.
    data.loc[held, "held_return"] = held_returns
    rate = float(costs.sum() / 1e4)
    nav, high_water, rows = 1., 1., []
    equity_available_at = None
    for (date, timestamp), group in data.groupby(BARS, sort=False):
        entry_time = group.return_start.iloc[0]
        if equity_available_at is not None and equity_available_at > entry_time:
            raise ValueError("previous held outcomes/equity are unavailable at the next return_start")
        group_held = group.weight.ne(0)
        equity_available_at = (group.loc[group_held, "label_available_at"].max()
                               if group_held.any() else group.return_end.iloc[0])
        target = group.weight.to_numpy()
        realized = group.held_return.to_numpy()
        entry_turnover = float(np.abs(target).sum())
        net_exposure = float(target.sum())
        if entry_turnover > 1. + 1e-10 or abs(net_exposure) > 1e-10:
            raise ValueError("CTA research portfolios require gross <= 1 and net exposure zero")
        if 1. - rate * entry_turnover <= 0:
            raise ValueError("entry costs exhaust research portfolio equity")
        gross_return = float(np.dot(target, realized))
        exit_turnover = float(np.abs(target * (1. + realized)).sum())
        turnover = entry_turnover + exit_turnover
        cost = rate * turnover
        net_return = gross_return - cost
        next_nav = nav * (1. + net_return)
        if not np.isfinite(next_nav) or next_nav <= 0:
            raise ValueError("research portfolio equity became non-positive or non-finite")
        nav = next_nav
        high_water = max(high_water, nav)
        rows.append({"Date": date, "Datetime": timestamp,
                     "return_start": group.return_start.iloc[0], "return_end": group.return_end.iloc[0],
                     "equity_available_at": equity_available_at,
                     "gross_return": gross_return, "entry_turnover": entry_turnover,
                     "exit_turnover": exit_turnover, "turnover": turnover,
                     "commission": float(costs[0] / 1e4 * turnover),
                     "slippage": float(costs[1] / 1e4 * turnover), "cost": cost,
                     "gross_exposure": entry_turnover, "net_exposure": net_exposure,
                     "idle_capital_fraction": max(0., 1. - entry_turnover),
                     "net_return": net_return, "nav": nav, "drawdown": nav / high_water - 1.})
    curves = pd.DataFrame(rows)
    metrics = {"period_count": len(curves), "total_return": float(nav - 1.),
               "max_drawdown": float(curves.drawdown.min()),
               "mean_period_net_return": float(curves.net_return.mean()),
               "mean_period_turnover": float(curves.turnover.mean()),
               "mean_period_gross_exposure": float(curves.gross_exposure.mean()),
               "positive_period_fraction": float(curves.net_return.gt(0).mean())}
    if periods_per_year is not None:
        volatility = float(curves.net_return.std(ddof=1)) if len(curves) > 1 else 0.
        metrics.update({"periods_per_year": float(periods_per_year),
                        "annualized_return": float(nav ** (periods_per_year / len(curves)) - 1.),
                        "annualized_volatility": float(volatility * np.sqrt(periods_per_year)),
                        "annualized_sharpe_zero_rf": (float(curves.net_return.mean() / volatility
                                                             * np.sqrt(periods_per_year)) if volatility > 0 else None)})
    audit = {
        "engine": "non_overlapping_interval_research_notional_returns",
        "executed_trades": False,
        "return_definition": "caller_supplied_raw_simple_instrument_return",
        "interval_policy": "common_cross_section_intervals_non_overlapping",
        "position_policy": "reset_to_cash_at_every_interval_end",
        "position_sizing": "target_notional_as_fraction_of_pre_entry_cost_equity",
        "equity_availability_policy": "held_outcomes_available_no_later_than_next_entry",
        "return_availability_default": "return_end_when_label_available_at_omitted",
        "zero_position_outcome_availability": "ignored",
        "exit_notional": "entry_notional_times_one_plus_raw_return",
        "final_liquidation_charged": True,
        "turnover_definition": "absolute_entry_plus_marked_exit_notional_over_start_equity",
        "commission_bps_one_way": float(costs[0]), "slippage_bps_one_way": float(costs[1]),
        "missing_held_return_policy": "error",
        "annualization": "explicit_periods_per_year" if periods_per_year is not None else "omitted",
        "excluded": ["fills", "margin", "funding", "borrow_cost", "contract_multiplier",
                     "intraperiod_path_and_liquidation", "continuous_rebalancing", "unobserved_gap_returns"],
    }
    return ResearchBacktestResult(curves, metrics, audit)
