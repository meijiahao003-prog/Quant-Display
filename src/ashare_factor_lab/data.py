"""Data interfaces used by the public factor implementations.

The research layer deliberately knows nothing about databases, vendor SDKs, file paths, or
credentials. A production user should implement :class:`DataProvider` with point-in-time data.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np

Array = np.ndarray


class DataProvider(ABC):
    """Minimal data contract for factor computation.

    Daily arrays have shape ``time × assets``. Intraday arrays have shape
    ``days × bars × assets``. Fundamental arrays have shape ``periods × assets`` and must be
    aligned by public disclosure date rather than fiscal-period end date.
    """

    @abstractmethod
    def daily(self, name: str, lookback: int) -> Array:
        """Return the most recent ``lookback`` daily observations."""

    @abstractmethod
    def intraday(self, name: str, lookback_days: int) -> Array:
        """Return the most recent intraday observations as days × bars × assets."""

    @abstractmethod
    def fundamental(self, statement: str, field_name: str) -> Array:
        """Return point-in-time statement history ordered from oldest to newest."""


@dataclass
class InMemoryDataProvider(DataProvider):
    """Small adapter useful for tests, notebooks, and vendor-integration prototypes."""

    daily_fields: dict[str, Array] = field(default_factory=dict)
    intraday_fields: dict[str, Array] = field(default_factory=dict)
    fundamental_fields: dict[tuple[str, str], Array] = field(default_factory=dict)

    @staticmethod
    def _validate(name: str, value: Array, ndim: int) -> Array:
        array = np.asarray(value, dtype=float)
        if array.ndim != ndim:
            raise ValueError(f"{name!r} must be {ndim}D; received shape {array.shape}")
        return array

    def daily(self, name: str, lookback: int) -> Array:
        if name not in self.daily_fields:
            raise KeyError(f"Unknown daily field: {name}")
        array = self._validate(name, self.daily_fields[name], 2)
        if lookback <= 0 or array.shape[0] < lookback:
            raise ValueError(f"{name!r} has {array.shape[0]} rows; requested {lookback}")
        return array[-lookback:].copy()

    def intraday(self, name: str, lookback_days: int) -> Array:
        if name not in self.intraday_fields:
            raise KeyError(f"Unknown intraday field: {name}")
        array = self._validate(name, self.intraday_fields[name], 3)
        if lookback_days <= 0 or array.shape[0] < lookback_days:
            raise ValueError(f"{name!r} has {array.shape[0]} days; requested {lookback_days}")
        return array[-lookback_days:].copy()

    def fundamental(self, statement: str, field_name: str) -> Array:
        key = (statement, field_name)
        if key not in self.fundamental_fields:
            raise KeyError(f"Unknown fundamental field: {statement}.{field_name}")
        return self._validate(".".join(key), self.fundamental_fields[key], 2).copy()


class SyntheticDataProvider(InMemoryDataProvider):
    """Deterministic fake dataset for integration tests and demos.

    The generated relationships are arbitrary. They validate array shapes, missing-value
    handling, and factor execution; they are not a market simulation or performance sample.
    """

    def __init__(self, seed: int = 7, n_assets: int = 120) -> None:
        rng = np.random.default_rng(seed)
        n_days = 800

        market = rng.normal(0.0002, 0.009, size=(n_days, 1))
        beta = rng.uniform(0.6, 1.4, size=(1, n_assets))
        idiosyncratic = rng.normal(0.0, 0.014, size=(n_days, n_assets))
        returns = np.clip(market * beta + idiosyncratic, -0.15, 0.15)
        adj_close = 20.0 * np.exp(np.cumsum(returns, axis=0))
        overnight = rng.normal(0.0, 0.006, size=(n_days, n_assets))
        adj_open = np.vstack([adj_close[:1], adj_close[:-1]]) * (1.0 + overnight)

        float_shares = rng.lognormal(20.0, 0.7, size=(1, n_assets))
        float_shares = np.repeat(float_shares, n_days, axis=0)
        volume = rng.lognormal(15.5, 0.65, size=(n_days, n_assets))
        amount = volume * adj_close * rng.uniform(0.95, 1.05, size=(n_days, n_assets))
        market_cap = float_shares * adj_close

        early_vwap = adj_open * (1.0 + rng.normal(0.0, 0.003, size=adj_open.shape))
        morning_buy_ratio = rng.beta(3.0, 7.0, size=(n_days, n_assets))
        morning_sell_ratio = rng.beta(3.0, 7.0, size=(n_days, n_assets))

        one_minute_volume = rng.lognormal(8.0, 0.7, size=(20, 240, n_assets))
        buy_share = rng.beta(6.0, 6.0, size=one_minute_volume.shape)
        one_minute_buy = one_minute_volume * buy_share
        one_minute_sell = one_minute_volume - one_minute_buy

        retail_total = rng.lognormal(11.0, 0.8, size=(21, 48, n_assets))
        retail_share = rng.beta(5.5, 5.5, size=retail_total.shape)
        retail_buy = retail_total * retail_share
        retail_sell = retail_total - retail_buy

        institution_total = rng.lognormal(12.0, 0.9, size=(20, 48, n_assets))
        institution_share = rng.beta(5.0, 5.0, size=institution_total.shape)
        institution_buy = institution_total * institution_share
        institution_sell = institution_total - institution_buy

        daily_fields = {
            "adj_open": adj_open,
            "adj_close": adj_close,
            "daily_return": returns,
            "market_cap": market_cap,
            "volume": volume,
            "float_shares": float_shares,
            "traded_value": amount,
            "early_vwap_30m": early_vwap,
            "morning_active_buy_ratio": morning_buy_ratio,
            "morning_active_sell_ratio": morning_sell_ratio,
        }
        intraday_fields = {
            "active_buy_volume_1m": one_minute_buy,
            "active_sell_volume_1m": one_minute_sell,
            "volume_1m": one_minute_volume,
            "retail_active_buy_value_5m": retail_buy,
            "retail_active_sell_value_5m": retail_sell,
            "institution_active_buy_value_5m": institution_buy,
            "institution_active_sell_value_5m": institution_sell,
        }

        periods = 12
        assets = rng.lognormal(22.0, 0.8, size=(periods, n_assets))
        revenue = assets * rng.uniform(0.12, 0.35, size=assets.shape)
        operating_cost = revenue * rng.uniform(0.55, 0.9, size=assets.shape)
        operating_profit = revenue - operating_cost
        net_income = operating_profit * rng.uniform(0.55, 0.85, size=assets.shape)
        cfo = net_income * rng.uniform(0.7, 1.3, size=assets.shape)
        liabilities = assets * rng.uniform(0.2, 0.75, size=assets.shape)
        current_assets = assets * rng.uniform(0.2, 0.55, size=assets.shape)
        current_liabilities = liabilities * rng.uniform(0.3, 0.75, size=assets.shape)
        equity = assets - liabilities
        inventory = current_assets * rng.uniform(0.08, 0.35, size=assets.shape)
        receivables = current_assets * rng.uniform(0.08, 0.3, size=assets.shape)
        ebit = operating_profit * rng.uniform(0.9, 1.1, size=assets.shape)
        interest_expense = np.maximum(liabilities * rng.uniform(0.002, 0.015, size=assets.shape), 1.0)

        fundamental_fields = {
            ("income", "revenue"): revenue,
            ("income", "operating_cost"): operating_cost,
            ("income", "operating_profit"): operating_profit,
            ("income", "net_income"): net_income,
            ("income", "ebit"): ebit,
            ("income", "interest_expense"): interest_expense,
            ("cash_flow", "operating_cash_flow"): cfo,
            ("balance_sheet", "total_assets"): assets,
            ("balance_sheet", "total_liabilities"): liabilities,
            ("balance_sheet", "current_assets"): current_assets,
            ("balance_sheet", "current_liabilities"): current_liabilities,
            ("balance_sheet", "shareholders_equity"): equity,
            ("balance_sheet", "inventory"): inventory,
            ("balance_sheet", "accounts_receivable"): receivables,
        }

        super().__init__(daily_fields, intraday_fields, fundamental_fields)
