"""Cross-sectional signal post-processing utilities."""

from __future__ import annotations

import numpy as np

from .operators import cross_sectional_residual, cs_percentile_rank, purify

Array = np.ndarray


def winsorize_mad(value: Array, width: float = 5.0) -> Array:
    """Clip each cross-section to median ± ``width`` scaled median deviations."""

    array = purify(value)
    out = array.copy()
    for row in range(array.shape[0]):
        valid = np.isfinite(array[row])
        if np.sum(valid) < 3:
            continue
        median = np.median(array[row, valid])
        mad = np.median(np.abs(array[row, valid] - median))
        if mad <= 1e-12:
            continue
        robust_sigma = 1.4826 * mad
        lower, upper = median - width * robust_sigma, median + width * robust_sigma
        out[row, valid] = np.clip(array[row, valid], lower, upper)
    return purify(out)


def postprocess(
    value: Array,
    tradable: Array | None = None,
    industry_dummies: Array | None = None,
    log_size: Array | None = None,
    mad_width: float = 5.0,
    rank: bool = True,
) -> Array:
    """Apply masking, robust clipping, optional neutralization, and ranking.

    ``industry_dummies`` may have shape ``n_industries × n_assets`` for a single cross-section.
    """

    signal = purify(value)
    if tradable is not None:
        mask = np.broadcast_to(np.asarray(tradable, dtype=bool), signal.shape)
        signal = np.where(mask, signal, np.nan)
    signal = winsorize_mad(signal, mad_width)

    exposures: list[Array] = []
    if industry_dummies is not None:
        industries = np.asarray(industry_dummies, dtype=float)
        if industries.ndim != 2 or industries.shape[1] != signal.shape[1]:
            raise ValueError("industry_dummies must have shape n_industries × n_assets")
        exposures.extend(industries[index : index + 1] for index in range(industries.shape[0]))
    if log_size is not None:
        exposures.append(np.broadcast_to(purify(log_size), signal.shape))
    if exposures:
        signal = cross_sectional_residual(signal, exposures, intercept=True)
    return cs_percentile_rank(signal) if rank else purify(signal)
