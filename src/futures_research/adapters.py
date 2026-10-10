"""Local, explicit CSV ingestion and coverage inspection; no network or credentials."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path

import pandas as pd

from .continuous import normalize_bars, normalize_catalog, validate_calendar


@dataclass
class DailyInputs:
    bars: pd.DataFrame
    catalog: pd.DataFrame
    calendar: pd.DatetimeIndex
    input_sha256: dict[str, str]


def load_daily_inputs(bars_csv, catalog_csv, calendar_csv, *, column_map=None,
                      volume_multiplier=1.0, open_interest_multiplier=1.0) -> DailyInputs:
    """Read exactly three caller-selected local files and fingerprint the bytes read.

    Calendar CSV must contain a ``date`` column with explicit session dates. Source
    names/absolute paths are not returned in the manifest. CSV has no execution
    semantics; connection discovery and provider credentials are intentionally absent.
    """
    frames, fingerprints = {}, {}
    for name, path in (("bars", bars_csv), ("catalog", catalog_csv), ("calendar", calendar_csv)):
        data = Path(path).read_bytes()
        fingerprints[name] = sha256(data).hexdigest()
        frames[name] = pd.read_csv(BytesIO(data), dtype=str)
    if "date" not in frames["calendar"]:
        raise ValueError("Explicit calendar CSV needs a date column")
    return DailyInputs(
        normalize_bars(frames["bars"], column_map=column_map,
                       volume_multiplier=volume_multiplier,
                       open_interest_multiplier=open_interest_multiplier),
        normalize_catalog(frames["catalog"]),
        validate_calendar(frames["calendar"]["date"]),
        fingerprints,
    )


def audit_daily_coverage(bars: pd.DataFrame, catalog: pd.DataFrame, calendar) -> pd.DataFrame:
    """Audit bars for historically known, listed contracts on every supplied session.

    This checks market-data coverage, not selection eligibility, so contracts inside
    the thirty-day delivery exclusion still appear. Unknown-to-date metadata is
    reported separately and is never assumed to establish historical listing dates.
    """
    bars, catalog, calendar = normalize_bars(bars), normalize_catalog(catalog), validate_calendar(calendar)
    if not bars.date.isin(calendar).all():
        raise ValueError("Some input bars are absent from the supplied calendar")
    if not bars.contract_id.isin(catalog.contract_id).all():
        raise ValueError("Some input contract identities are absent from the catalog")
    lookup = bars.set_index(["contract_id", "date"]).available_date
    records = []
    for day in calendar:
        known = catalog.loc[catalog.known_at <= day].drop_duplicates("contract_id", keep="last")
        listed = known.loc[(known.list_date <= day) & (known.last_trade_date >= day)]
        for cid in listed.contract_id:
            available = lookup.get((cid, day), pd.NaT)
            status = ("MISSING_BAR" if pd.isna(available) else
                      "LATE_BAR" if available > day else "AVAILABLE")
            records.append({"date": day, "contract_id": cid, "status": status})
        observed_unknown = bars.loc[bars.date.eq(day)
                                    & ~bars.contract_id.isin(known.contract_id), "contract_id"]
        for cid in observed_unknown:
            records.append({"date": day, "contract_id": cid, "status": "UNKNOWN_METADATA"})
    return pd.DataFrame(records, columns=["date", "contract_id", "status"])
