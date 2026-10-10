"""Pairwise cross-sectional rank IC must use the same surviving asset set."""
import numpy as np
import pytest

from ashare_factor_lab.evaluation import forward_returns, information_coefficient


def test_spearman_reranks_only_common_finite_assets():
    x = np.array([[0., 1., 2., 3.]])
    y = np.array([[0., np.nan, 1., 2.]])
    np.testing.assert_allclose(information_coefficient(x, y, rank=True, min_assets=3), [1.])
    # A value on a non-paired asset cannot change the rank IC.
    x[0, 1] = 1000.
    np.testing.assert_allclose(information_coefficient(x, y, rank=True, min_assets=3), [1.])


def test_ic_rejects_silent_broadcast_of_one_date_to_all_dates():
    with pytest.raises(ValueError, match="identical"):
        information_coefficient(np.ones((3, 30)), np.ones((1, 30)))


@pytest.mark.parametrize("value", [True, 1.5, 0])
def test_forward_returns_requires_explicit_positive_integer_horizon(value):
    with pytest.raises(ValueError, match="positive integer"):
        forward_returns(np.ones((3, 2)), horizon=value)
