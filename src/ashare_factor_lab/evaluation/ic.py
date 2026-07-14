"""Cross-sectional information-coefficient diagnostics."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from ..operators import cs_percentile_rank, purify

Array = np.ndarray


def forward_returns(close: Array, horizon: int = 1) -> Array:
    """Align simple forward returns with the signal date."""

    price = purify(close)
    if horizon <= 0:
        raise ValueError("horizon must be positive")
    out = np.full_like(price, np.nan)
    if horizon < price.shape[0]:
        out[:-horizon] = price[horizon:] / price[:-horizon] - 1.0
    return purify(out)


def information_coefficient(
    signal: Array,
    future_return: Array,
    *,
    rank: bool = False,
    min_assets: int = 20,
) -> Array:
    """Compute one Pearson or Spearman-style IC per date."""

    factor, target = np.broadcast_arrays(purify(signal), purify(future_return))
    if rank:
        factor = cs_percentile_rank(factor)
        target = cs_percentile_rank(target)
    output = np.full(factor.shape[0], np.nan)
    for row in range(factor.shape[0]):
        valid = np.isfinite(factor[row]) & np.isfinite(target[row])
        if np.sum(valid) < min_assets:
            continue
        x = factor[row, valid]
        y = target[row, valid]
        if np.std(x) <= 1e-12 or np.std(y) <= 1e-12:
            continue
        output[row] = np.corrcoef(x, y)[0, 1]
    return output


def cross_sectional_coverage(signal: Array) -> Array:
    """Fraction of assets with finite signal values for each date."""

    factor = purify(signal)
    return np.mean(np.isfinite(factor), axis=1)


@dataclass(frozen=True)
class ICSummary:
    mean_ic: float
    ic_std: float
    ic_ir: float
    positive_ratio: float
    observations: int

    def to_dict(self) -> dict[str, float | int]:
        return asdict(self)


def summarize_ic(ic: Array) -> ICSummary:
    """Summarize a daily IC series using the unannualized mean/std ICIR convention."""

    values = purify(ic).reshape(-1)
    valid = values[np.isfinite(values)]
    if valid.size == 0:
        return ICSummary(np.nan, np.nan, np.nan, np.nan, 0)
    std = float(np.std(valid, ddof=1)) if valid.size > 1 else np.nan
    ir = float(np.mean(valid) / std) if np.isfinite(std) and std > 1e-12 else np.nan
    return ICSummary(
        mean_ic=float(np.mean(valid)),
        ic_std=std,
        ic_ir=ir,
        positive_ratio=float(np.mean(valid > 0)),
        observations=int(valid.size),
    )
