import csv
import unittest
from pathlib import Path

from ashare_factor_lab.factors import ALL_FACTORS


ROOT = Path(__file__).resolve().parents[1]


class ResultCatalogTests(unittest.TestCase):
    def test_summary_has_one_row_per_registered_factor(self) -> None:
        with (ROOT / "results" / "summary.csv").open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))

        expected_ids = [factor_type.metadata.factor_id for factor_type in ALL_FACTORS]
        self.assertEqual([row["factor_id"] for row in rows], expected_ids)
        self.assertTrue({row["status"] for row in rows} <= {"passed", "diagnostic"})

    def test_annual_catalog_covers_every_registered_factor(self) -> None:
        with (ROOT / "results" / "annual_metrics.csv").open(
            encoding="utf-8", newline=""
        ) as handle:
            factor_ids = {row["factor_id"] for row in csv.DictReader(handle)}

        expected_ids = {factor_type.metadata.factor_id for factor_type in ALL_FACTORS}
        self.assertEqual(factor_ids, expected_ids)


if __name__ == "__main__":
    unittest.main()
