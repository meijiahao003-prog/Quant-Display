"""Vendor-neutral, close-to-next-session main/secondary futures construction.

The thirty-day setting is a delivery exclusion window, not a rolling score window.
All supplied sessions must be from an explicit exchange calendar. No live data,
credentials, database access, invented bars, or calendar inference is included.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

PRICE_COLUMNS = ("open", "high", "low", "close")
EXCHANGE_ALIASES = {
    "SHFE": "SHFE", "SHF": "SHFE", "SF": "SHFE",
    "DCE": "DCE", "DF": "DCE", "CZCE": "CZCE", "ZCE": "CZCE", "ZF": "CZCE",
    "INE": "INE", "GFEX": "GFEX", "GFE": "GFEX", "GF": "GFEX",
    "CFFEX": "CFFEX", "CFX": "CFFEX",
}


def _day(value) -> pd.Timestamp:
    if isinstance(value, (int, float, np.number)):
        raise TypeError("Dates must be explicit ISO strings or date objects, not numeric epochs")
    result = pd.Timestamp(value)
    if pd.isna(result) or result.tzinfo is not None or result != result.normalize():
        raise ValueError("Use nonmissing, timezone-naive session dates at midnight")
    return result


def _days(series: pd.Series) -> pd.Series:
    return series.map(_day).astype("datetime64[ns]")


def canonical_id(exchange: str, product: str, delivery_month) -> str:
    """Use verified full delivery year/month; ambiguous three-digit codes are rejected.

    A vendor symbol alone is not a historical identity. Resolve it against dated
    contract metadata upstream, never against the final date of the whole sample.
    """
    normalized_exchange = EXCHANGE_ALIASES.get(str(exchange).strip().upper())
    normalized_product = str(product).strip().upper()
    if normalized_exchange is None or re.fullmatch("[A-Z]+", normalized_product) is None:
        raise ValueError("An explicit supported exchange and alphabetic product are required")
    value = str(delivery_month)
    month = _day(pd.to_datetime(value, format="%Y%m") if re.fullmatch(r"\d{6}", value)
                 else delivery_month)
    if month.day != 1:
        raise ValueError("delivery_month must be the first calendar day of its month")
    return f"{normalized_exchange}:{normalized_product}:{month:%Y%m}"


def validate_calendar(calendar) -> pd.DatetimeIndex:
    """Validate an explicitly supplied ordered exchange calendar; never infer weekdays."""
    result = pd.DatetimeIndex([_day(value) for value in calendar])
    if len(result) < 2 or result.has_duplicates or not result.is_monotonic_increasing:
        raise ValueError("calendar must contain at least two unique, ascending exchange sessions")
    return result


def normalize_bars(raw: pd.DataFrame, *, column_map: dict | None = None,
                   volume_multiplier: float = 1.0,
                   open_interest_multiplier: float = 1.0) -> pd.DataFrame:
    """Normalize one immutable daily-bar snapshot with explicit unit multipliers.

    Missing numeric values remain missing. Malformed values and duplicate keys fail
    instead of silently selecting a vendor row. ``available_date`` defaults to the
    bar session, asserting availability at that session's close.
    """
    out = raw.rename(columns=column_map or {}).copy()
    required = {"date", "contract_id", "volume", "open_interest", *PRICE_COLUMNS}
    if out.columns.duplicated().any() or not required.issubset(out.columns):
        raise ValueError(f"Unique bar columns including {sorted(required)} are required")
    out["date"] = _days(out["date"])
    if out["contract_id"].isna().any() or out["contract_id"].astype(str).str.strip().eq("").any():
        raise ValueError("contract_id must be nonmissing")
    out["contract_id"] = out["contract_id"].astype(str)
    if out.duplicated(["contract_id", "date"]).any():
        raise ValueError("Duplicate contract/session bars require explicit upstream reconciliation")
    for col in (*PRICE_COLUMNS, "volume", "open_interest"):
        out[col] = pd.to_numeric(out[col], errors="raise").astype(float)
    if np.isinf(out[list(PRICE_COLUMNS)].to_numpy()).any():
        raise ValueError("Price fields must be finite or missing")
    for col, multiplier in (("volume", volume_multiplier),
                            ("open_interest", open_interest_multiplier)):
        if not np.isfinite(multiplier) or multiplier <= 0:
            raise ValueError("Unit multipliers must be positive and finite")
        out[col] *= multiplier
    if "available_date" not in out:
        out["available_date"] = out["date"]
    out["available_date"] = _days(out["available_date"])
    if (out["available_date"] < out["date"]).any():
        raise ValueError("Daily closing bars cannot be available before their session")
    if "suspend_flag" not in out:
        out["suspend_flag"] = 0
    out["suspend_flag"] = pd.to_numeric(out["suspend_flag"], errors="raise")
    if not out["suspend_flag"].isin([0, 1]).all():
        raise ValueError("suspend_flag must explicitly be 0 or 1")
    columns = ["date", "contract_id", *PRICE_COLUMNS, "volume", "open_interest",
               "available_date", "suspend_flag"]
    return out[columns].sort_values(["date", "contract_id"], kind="stable").reset_index(drop=True)


def normalize_catalog(catalog: pd.DataFrame) -> pd.DataFrame:
    """Validate dated metadata versions, including their provenance declarations."""
    required = {"contract_id", "exchange", "product", "delivery_month", "list_date",
                "last_trade_date", "known_at", "source"}
    if catalog.columns.duplicated().any() or not required.issubset(catalog.columns):
        raise ValueError(f"Catalog columns including {sorted(required)} are required")
    out = catalog.copy()
    if out.empty:
        raise ValueError("A dated contract catalog is required")
    for col in ("delivery_month", "list_date", "last_trade_date", "known_at"):
        out[col] = _days(out[col])
    for col in ("source", "contract_id", "exchange", "product"):
        if out[col].isna().any() or out[col].astype(str).str.strip().eq("").any():
            raise ValueError(f"Catalog {col} must be nonmissing")
    out["exchange"] = out["exchange"].astype(str).str.upper().map(EXCHANGE_ALIASES)
    out["product"] = out["product"].astype(str).str.upper()
    expected = out.apply(lambda r: canonical_id(r["exchange"], r["product"], r["delivery_month"]), axis=1)
    if not expected.eq(out["contract_id"]).all():
        raise ValueError("Catalog identity disagrees with explicit exchange/product/delivery month")
    if out.duplicated(["contract_id", "known_at"]).any():
        raise ValueError("Metadata versions need unique contract_id/known_at keys")
    if (out["list_date"] > out["last_trade_date"]).any():
        raise ValueError("list_date cannot follow last_trade_date")
    month_end = out["delivery_month"] + pd.offsets.MonthEnd(0)
    if (out["last_trade_date"] > month_end).any():
        raise ValueError("last_trade_date cannot follow the delivery month")
    out["redline_date"] = out["last_trade_date"] - pd.Timedelta(days=30)
    if (out["redline_date"] >= out["delivery_month"]).any():
        raise ValueError("The 30-day exclusion must precede the delivery month")
    if "first_excluded_date" in out:
        override = out["first_excluded_date"].notna()
        out.loc[override, "first_excluded_date"] = _days(out.loc[override, "first_excluded_date"])
        if (out.loc[override, "first_excluded_date"] > out.loc[override, "redline_date"]).any():
            raise ValueError("An explicit exclusion override may only make the redline earlier")
        out.loc[override, "redline_date"] = out.loc[override, "first_excluded_date"]
    columns = ["contract_id", "exchange", "product", "delivery_month", "list_date",
               "last_trade_date", "known_at", "source", "redline_date"]
    if "first_excluded_date" in out:
        columns.append("first_excluded_date")
    return out[columns].sort_values(["known_at", "contract_id"], kind="stable").reset_index(drop=True)


@dataclass(frozen=True)
class RoleConfig:
    oi_weight: float = 0.8
    confirm_days: int = 3
    switch_multiple: float = 1.2
    min_volume: float = 0.0
    min_open_interest: float = 0.0

    def __post_init__(self):
        if (not np.isfinite(self.oi_weight) or not 0 <= self.oi_weight <= 1
                or isinstance(self.confirm_days, bool)
                or not isinstance(self.confirm_days, (int, np.integer)) or self.confirm_days < 1
                or not np.isfinite(self.switch_multiple) or self.switch_multiple <= 1
                or not np.isfinite(self.min_volume) or self.min_volume < 0
                or not np.isfinite(self.min_open_interest) or self.min_open_interest < 0):
            raise ValueError("Invalid role weight, confirmation threshold, or admission minimum")


@dataclass(frozen=True)
class ContinuousConfig:
    main: RoleConfig = field(default_factory=RoleConfig)
    secondary: RoleConfig = field(default_factory=lambda: RoleConfig(confirm_days=5,
                                                                    switch_multiple=2.0))


def strategy_for_product(product: str) -> ContinuousConfig:
    """Retain the latest notebook's group weights; callers may override either role."""
    product = product.upper()
    weight = 0.8
    groups = [(0.30, "AU AG"), (0.50, "CU AL ZN PB NI SN SS BC"),
              (0.75, "BU SC LU SP MA PF EG EB PG PP L V RU NR BR SA LC SI PS")]
    for group_weight, symbols in groups:
        if product in symbols.split():
            weight = group_weight
    return ContinuousConfig(RoleConfig(oi_weight=weight),
                            RoleConfig(oi_weight=weight, confirm_days=5, switch_multiple=2.0))


@dataclass
class ContinuousResult:
    mapping: pd.DataFrame
    raw: pd.DataFrame
    adjusted: pd.DataFrame
    rolls: pd.DataFrame
    scores: pd.DataFrame


@dataclass
class _RoleState:
    current: str | None = None
    pending: str | None = None
    streak: int = 0

    def clear(self):
        self.pending, self.streak = None, 0


def _scores(day: pd.DataFrame, metadata: pd.DataFrame, decision, effective,
            role: str, cfg: RoleConfig) -> tuple[list[tuple[str, float]], pd.DataFrame]:
    latest = metadata.loc[metadata.known_at <= decision].drop_duplicates("contract_id", keep="last")
    view = day.merge(latest, on="contract_id", how="left", validate="many_to_one")
    checks = {
        "unknown_metadata": view.known_at.isna(),
        "not_listed": view.list_date > decision,
        "delivery_protection": view.redline_date.isna() | (view.redline_date <= effective),
        "late_bar": view.available_date > decision,
        "invalid_volume": ~np.isfinite(view.volume) | (view.volume < 0),
        "invalid_open_interest": ~np.isfinite(view.open_interest) | (view.open_interest < 0),
        "suspended": view.suspend_flag.ne(0),
        "volume_minimum": view.volume < cfg.min_volume,
        "open_interest_minimum": view.open_interest < cfg.min_open_interest,
    }
    view["reject_reason"] = ["|".join(k for k, mask in checks.items() if mask.iloc[i])
                             for i in range(len(view))]
    view["eligible"] = view.reject_reason.eq("")
    eligible = view.eligible
    oi_total, volume_total = view.loc[eligible, ["open_interest", "volume"]].sum()
    view["score"] = np.nan
    view.loc[eligible, "score"] = (
        cfg.oi_weight * (view.loc[eligible, "open_interest"] / oi_total if oi_total > 0 else 0.)
        + (1 - cfg.oi_weight)
        * (view.loc[eligible, "volume"] / volume_total if volume_total > 0 else 0.)
    )
    view["oi_denominator"], view["volume_denominator"] = oi_total, volume_total
    view["role"], view["decision_date"], view["effective_date"] = role, decision, effective
    ranked = view.loc[eligible].sort_values(["score", "delivery_month", "contract_id"],
                                          ascending=[False, False, True], kind="stable")
    return list(ranked[["contract_id", "score"]].itertuples(index=False, name=None)), view


def _top(ranked, excluded=()):
    return next((cid for cid, _ in ranked if cid not in excluded), None)


def _ordinary(state: _RoleState, ranked, cfg: RoleConfig, excluded=()):
    candidate = _top(ranked, {*excluded, state.current})
    scores = dict(ranked)
    if candidate is None:
        state.clear()
        return "HOLD_NO_CHALLENGER", candidate, 0
    if scores[candidate] > cfg.switch_multiple * scores[state.current]:
        state.streak = state.streak + 1 if candidate == state.pending else 1
        state.pending = candidate
    else:
        state.clear()
    streak = state.streak
    if streak >= cfg.confirm_days:
        state.current = candidate
        state.clear()
        return "ORDINARY_SWITCH", candidate, streak
    return ("CONFIRMING" if streak else "HOLD_BELOW_THRESHOLD"), candidate, streak


def build_continuous(bars: pd.DataFrame, catalog: pd.DataFrame, calendar,
                     config: ContinuousConfig | None = None) -> ContinuousResult:
    """Build one product from immutable daily bars and as-of metadata versions.

    Decisions use session T's complete close snapshot and take effect at the next
    supplied exchange session. Every calendar gap is processed: no absent day can
    silently extend a confirmation streak. No actual trade fills are asserted.
    """
    bars, catalog, calendar = normalize_bars(bars), normalize_catalog(catalog), validate_calendar(calendar)
    if bars.empty:
        raise ValueError("At least one daily bar is required")
    if catalog[["exchange", "product"]].drop_duplicates().shape[0] != 1:
        raise ValueError("Call build_continuous separately for each exchange/product")
    if not bars.contract_id.isin(catalog.contract_id).all():
        raise ValueError("Every bar identity must occur in the supplied metadata catalog")
    if not bars.date.isin(calendar).all() or bars.date.max() >= calendar[-1]:
        raise ValueError("Explicit calendar must cover all bars and the next session after the final bar")
    within_calendar = catalog.last_trade_date.between(calendar[0], calendar[-1])
    if not catalog.loc[within_calendar, "last_trade_date"].isin(calendar).all():
        raise ValueError("An in-range last_trade_date must be in the supplied exchange calendar")
    cfg = config or strategy_for_product(catalog["product"].iloc[0])
    states = {"main": _RoleState(), "secondary": _RoleState()}
    rows, audits = [], []
    initialized = False
    for i in range(calendar.searchsorted(bars.date.min()), calendar.searchsorted(bars.date.max()) + 1):
        decision, effective = calendar[i], calendar[i + 1]
        day = bars.loc[bars.date.eq(decision)]
        rankings = {}
        for role in states:
            rankings[role], audit = _scores(day, catalog, decision, effective, role, getattr(cfg, role))
            audits.append(audit)
        main, secondary = states["main"], states["secondary"]
        previous = {role: state.current for role, state in states.items()}
        detail = {role: ("HOLD", None, 0) for role in states}
        invalid = (main.current not in dict(rankings["main"])
                   or secondary.current not in dict(rankings["secondary"])
                   or main.current == secondary.current)
        hard_reset = False
        if not initialized or invalid:
            main.current = _top(rankings["main"])
            secondary.current = (_top(rankings["secondary"], {main.current})
                                 if main.current is not None else None)
            changed = any(previous[role] != state.current for role, state in states.items())
            hard_reset = initialized and changed
            reason = ("INITIALIZE" if not initialized else
                      "PAIR_HARD_RESET" if changed else "HOLD_INCOMPLETE_PAIR")
            for role, state in states.items():
                state.clear()
                detail[role] = (reason, state.current, 0)
            initialized = True
        else:
            detail["main"] = _ordinary(main, rankings["main"], cfg.main)
            if main.current != previous["main"]:
                secondary.current = _top(rankings["secondary"], {main.current})
                secondary.clear()
                detail["secondary"] = ("FOLLOW_MAIN_ROLL", secondary.current, 0)
            else:
                detail["secondary"] = _ordinary(secondary, rankings["secondary"], cfg.secondary,
                                                {main.current})
        for role, state in states.items():
            action, candidate, streak = detail[role]
            rows.append({"product": catalog["product"].iloc[0], "role": role, "date": effective,
                         "decision_date": decision, "contract_id": state.current,
                         "previous_contract_id": previous[role], "reason": action,
                         "candidate": candidate, "confirmation_streak": streak,
                         "hard_reset": hard_reset})
    mapping = pd.DataFrame(rows)
    raw = mapping.merge(bars, on=["date", "contract_id"], how="left", validate="many_to_one")
    raw["bar_present"] = raw.available_date.notna()
    raw["bar_available"] = raw.available_date.le(raw.date)
    raw.loc[~raw.bar_available, [*PRICE_COLUMNS, "volume", "open_interest"]] = np.nan
    adjusted, rolls = causal_ratio_adjust(raw, bars, calendar)
    return ContinuousResult(mapping, raw, adjusted, rolls, pd.concat(audits, ignore_index=True))


def causal_ratio_adjust(raw: pd.DataFrame, bars: pd.DataFrame,
                       calendar) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Anchor the first selected contract and accumulate only past roll ratios.

    At a roll effective on T+1, factor *= old_close(T) / new_close(T). History is
    unchanged when future rolls are appended. Missing/nonpositive bridges break
    the adjusted series from that point; raw prices remain available independently.
    """
    calendar = validate_calendar(calendar)
    bars = normalize_bars(bars)
    required = {"date", "role", "contract_id", *PRICE_COLUMNS}
    if not required.issubset(raw.columns) or raw.duplicated(["date", "role"]).any():
        raise ValueError("Unique date/role raw rows with OHLC columns are required")
    if not raw.date.isin(calendar).all():
        raise ValueError("All raw price sessions must occur in the explicit calendar")
    if "product" in raw and raw["product"].nunique() > 1:
        raise ValueError("Ratio adjustment must be called separately for each product")
    previous_day = dict(zip(calendar[1:], calendar[:-1]))
    closes = bars.loc[bars.available_date.le(bars.date)].set_index(["contract_id", "date"]).close
    rows, events = [], []
    for role, part in raw.groupby("role", sort=True):
        previous, factor, broken = None, 1., False
        for rec in part.sort_values("date").to_dict("records"):
            cid, date = rec["contract_id"], rec["date"]
            ratio, bridge, status = 1., pd.NaT, "SAME_CONTRACT"
            if pd.isna(cid):
                rows.append({**rec, "factor": np.nan, "daily_multiplier": np.nan,
                             "factor_status": "EMPTY_MAPPING",
                             **{f"{p}_adjusted": np.nan for p in PRICE_COLUMNS}})
                continue
            if previous is None:
                status = "INITIAL_ANCHOR"
            elif cid != previous:
                bridge = previous_day.get(date, pd.NaT)
                old, new = closes.get((previous, bridge), np.nan), closes.get((cid, bridge), np.nan)
                ratio = old / new if np.isfinite(old) and np.isfinite(new) and old > 0 and new > 0 else np.nan
                factor = factor * ratio
                broken = broken or not np.isfinite(factor) or factor <= 0
                factor = np.nan if broken else factor
                status = "BROKEN_BRIDGE" if broken else "BRIDGED"
                events.append({"role": role, "date": date, "bridge_date": bridge,
                               "old_contract": previous, "new_contract": cid, "old_close": old,
                               "new_close": new, "ratio": ratio, "factor": factor, "status": status})
            elif broken:
                status = "BROKEN_UPSTREAM"
            previous = cid
            rows.append({**rec, "factor": factor, "daily_multiplier": ratio,
                         "bridge_date": bridge, "factor_status": status,
                         **{f"{p}_adjusted": rec[p] * factor for p in PRICE_COLUMNS}})
    roll_columns = ["role", "date", "bridge_date", "old_contract", "new_contract", "old_close",
                    "new_close", "ratio", "factor", "status"]
    return pd.DataFrame(rows).sort_values(["date", "role"]).reset_index(drop=True), pd.DataFrame(events, columns=roll_columns)
