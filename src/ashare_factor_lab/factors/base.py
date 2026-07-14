"""Shared factor interface and metadata."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np

from ..data import DataProvider


@dataclass(frozen=True)
class FactorMetadata:
    name: str
    category: str
    direction: str
    description: str


class Factor(ABC):
    """Base class for a single-date cross-sectional factor."""

    metadata: FactorMetadata

    @abstractmethod
    def compute(self, data: DataProvider) -> np.ndarray:
        """Return one row with shape ``1 × n_assets``."""
