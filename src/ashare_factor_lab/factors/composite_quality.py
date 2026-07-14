"""Multi-dimensional point-in-time fundamental quality factor."""

from __future__ import annotations

import numpy as np

from ..data import DataProvider
from ..operators import cs_percentile_rank, purify, safe_divide
from .base import Factor, FactorMetadata


class CompositeQuality(Factor):
    """Profitability, growth, earnings-quality, efficiency, and safety composite."""

    metadata = FactorMetadata(
        name="composite_quality",
        category="fundamentals",
        direction="positive",
        description="Rank composite of five observable company-quality dimensions.",
    )

    @staticmethod
    def _rank_or_neutral(value: np.ndarray) -> np.ndarray:
        rank = cs_percentile_rank(purify(value))
        return purify(np.where(np.isfinite(rank), rank, 0.5))

    @classmethod
    def _mean_rank(cls, *values: np.ndarray) -> np.ndarray:
        return purify(sum(cls._rank_or_neutral(value) for value in values) / len(values))

    @staticmethod
    def _average(current: np.ndarray, lagged: np.ndarray) -> np.ndarray:
        return purify((current + lagged) / 2.0)

    @staticmethod
    def _ratio_abs_denominator(numerator: np.ndarray, denominator: np.ndarray) -> np.ndarray:
        return safe_divide(numerator, np.abs(denominator))

    @classmethod
    def _growth_acceleration(cls, value: np.ndarray) -> np.ndarray:
        if value.shape[0] < 9:
            raise ValueError("Growth acceleration requires at least nine quarterly observations")
        current, lag_4q, lag_8q = value[-1:], value[-5:-4], value[-9:-8]
        growth_now = cls._ratio_abs_denominator(current - lag_4q, lag_4q)
        growth_previous = cls._ratio_abs_denominator(lag_4q - lag_8q, lag_8q)
        return purify(growth_now - growth_previous)

    def compute(self, data: DataProvider) -> np.ndarray:
        revenue = data.fundamental("income", "revenue")
        operating_cost = data.fundamental("income", "operating_cost")
        operating_profit = data.fundamental("income", "operating_profit")
        net_income = data.fundamental("income", "net_income")
        ebit = data.fundamental("income", "ebit")
        interest_expense = data.fundamental("income", "interest_expense")
        operating_cash_flow = data.fundamental("cash_flow", "operating_cash_flow")
        total_assets = data.fundamental("balance_sheet", "total_assets")
        total_liabilities = data.fundamental("balance_sheet", "total_liabilities")
        current_assets = data.fundamental("balance_sheet", "current_assets")
        current_liabilities = data.fundamental("balance_sheet", "current_liabilities")
        equity = data.fundamental("balance_sheet", "shareholders_equity")
        inventory = data.fundamental("balance_sheet", "inventory")
        receivable = data.fundamental("balance_sheet", "accounts_receivable")

        latest_revenue = revenue[-1:]
        latest_operating_cost = operating_cost[-1:]
        latest_operating_profit = operating_profit[-1:]
        latest_net_income = net_income[-1:]
        latest_ebit = ebit[-1:]
        latest_interest_expense = interest_expense[-1:]
        latest_cfo = operating_cash_flow[-1:]
        latest_assets = total_assets[-1:]
        average_assets = self._average(latest_assets, total_assets[-5:-4])
        latest_liabilities = total_liabilities[-1:]
        latest_current_assets = current_assets[-1:]
        latest_current_liabilities = current_liabilities[-1:]
        average_equity = self._average(equity[-1:], equity[-5:-4])
        average_inventory = self._average(inventory[-1:], inventory[-5:-4])
        latest_receivable = receivable[-1:]
        average_receivable = self._average(latest_receivable, receivable[-5:-4])

        profitability = self._mean_rank(
            safe_divide(latest_cfo, latest_assets),
            safe_divide(latest_net_income, average_equity),
            safe_divide(latest_operating_profit, latest_assets - latest_current_liabilities),
        )
        growth = self._mean_rank(
            self._growth_acceleration(net_income),
            self._growth_acceleration(operating_profit),
            self._growth_acceleration(revenue),
        )
        earnings_quality = self._mean_rank(
            safe_divide(latest_cfo - latest_net_income, latest_assets),
            self._ratio_abs_denominator(latest_cfo, latest_net_income),
            -safe_divide(latest_receivable, latest_revenue),
        )
        operating_efficiency = self._mean_rank(
            safe_divide(latest_revenue, average_assets),
            safe_divide(latest_operating_cost, average_inventory),
            safe_divide(latest_revenue, average_receivable),
        )
        safety = self._mean_rank(
            -safe_divide(latest_liabilities, latest_assets),
            self._ratio_abs_denominator(latest_ebit, latest_interest_expense),
            safe_divide(latest_current_assets, latest_current_liabilities),
        )

        # Governance is omitted until a point-in-time governance dataset is available. Averaging
        # the five observable dimensions avoids pretending that a constant placeholder adds alpha.
        return purify(
            (profitability + growth + earnings_quality + operating_efficiency + safety) / 5.0
        )
