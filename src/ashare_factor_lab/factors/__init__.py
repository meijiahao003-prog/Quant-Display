"""Public factor catalogue."""

from .base import Factor, FactorMetadata
from .fundamental import CompositeQuality
from .microstructure import (
    InstitutionalTailAcceleration,
    OpenCloseVolumeConcentration,
    OvernightTailFlowShock,
    RetailFlowSynchronization,
    VwapFlowDivergence,
)
from .risk import MixedHorizonIdiosyncraticSkewness
from .trading_activity import DollarVolumeStability, TurnoverStability

ALL_FACTORS = (
    OvernightTailFlowShock,
    OpenCloseVolumeConcentration,
    RetailFlowSynchronization,
    InstitutionalTailAcceleration,
    VwapFlowDivergence,
    MixedHorizonIdiosyncraticSkewness,
    DollarVolumeStability,
    TurnoverStability,
    CompositeQuality,
)

FACTOR_GROUPS = {
    "microstructure": ALL_FACTORS[:5],
    "risk": ALL_FACTORS[5:6],
    "trading_activity": ALL_FACTORS[6:8],
    "fundamental": ALL_FACTORS[8:],
}

__all__ = [
    "ALL_FACTORS",
    "FACTOR_GROUPS",
    "CompositeQuality",
    "DollarVolumeStability",
    "MixedHorizonIdiosyncraticSkewness",
    "Factor",
    "FactorMetadata",
    "InstitutionalTailAcceleration",
    "OpenCloseVolumeConcentration",
    "OvernightTailFlowShock",
    "RetailFlowSynchronization",
    "TurnoverStability",
    "VwapFlowDivergence",
]
