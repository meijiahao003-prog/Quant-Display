"""Data-vendor-neutral A-share factor research toolkit."""

from .data import DataProvider, InMemoryDataProvider, SyntheticDataProvider

__all__ = ["DataProvider", "InMemoryDataProvider", "SyntheticDataProvider"]
