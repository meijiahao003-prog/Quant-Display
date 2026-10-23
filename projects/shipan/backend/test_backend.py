"""OFFLINE synthetic fixtures only: not market data, not investment evidence.

Run: python -m unittest test_backend.py
Creates a disposable temporary database; never touches actual user samples.
"""
import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

os.environ["MARKET_LENS_DATA"] = tempfile.mkdtemp(prefix="market-lens-unit-")
from fastapi.testclient import TestClient
import app


class BackendTests(unittest.TestCase):
    """Offline contract tests. Invoke via unittest command above; output pass/fail,
    never simulated results presented as real. Disposable fixture rows only.
    """
    def setUp(self):
        """Isolate every test DB. Input temporary directory; output empty DB path.
        Run `python -m unittest test_backend`; no user data changes.
        """
        app.DATA = Path(tempfile.mkdtemp(prefix="market-lens-case-"))
        app.DB_PATH = app.DATA / "test.sqlite3"
        app._snapshots.clear()
        self.client = TestClient(app.app)

    def fixture(self, index=0, mode="delayed"):
        """Explicit synthetic fixture for offline tests, never a real quote.
        `python -m unittest test_backend` invokes this; no live data substituted.
        """
        observed = (datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(days=index)).isoformat()
        if mode == "demo":
            return {"mode": "demo", "status": "demo", "source": "SYNTHETIC_TEST_ONLY", "instruments": [{"symbol": "510300", "name": "测试夹具", "status": "demo", "price": 1.0}]}
        responses = {
            "etf": {"rows": [{"代码": "510300", "名称": "SYNTHETIC_TEST_ONLY", "最新价": 1.0, "涨跌幅": (index % 3)-1, "主力净流入-净占比": None if index % 4 == 0 else (index % 3)-1, "更新时间": observed}], "status": "delayed", "fetched_at": observed, "error": None},
            "index": {"rows": [], "status": "unavailable", "fetched_at": None, "error": "offline test only"},
            "market_flow": {"rows": [], "status": "unavailable", "fetched_at": None, "error": "offline test only"},
        }
        # Register through production snapshot() using mocked upstream only.
        with patch("app.fetch_source", side_effect=lambda command: responses[command]):
            return app.snapshot()

    def test_null_zero_and_missing(self):
        """Synthetic missing vs zero contract. Run unittest; pass/fail only."""
        rows = app.normalize_quotes([{"代码":"510300", "最新价":0, "涨跌幅":None}], "etf", "delayed", "2025-01-01T00:00:00Z")
        self.assertEqual(len(rows), 10)
        self.assertEqual(rows[0]["price"], 0)
        self.assertIsNone(rows[0]["change_pct"])
        self.assertIsNone(rows[0]["main_net"])
        self.assertEqual(rows[1]["status"], "unavailable")

    def test_demo_and_duplicate_rejected(self):
        """Synthetic boundary test; zero demo rows persist. Run unittest."""
        payload = {"snapshot": self.fixture(mode="demo"), "selected_symbol":"510300", "label":"up"}
        self.assertEqual(self.client.post("/api/samples", json=payload).status_code, 422)
        payload["snapshot"]["mode"] = "delayed"
        payload["snapshot"]["status"] = "delayed"
        payload["snapshot"]["snapshot_id"] = "forged-id"
        self.assertEqual(self.client.post("/api/samples", json=payload).status_code, 422)
        payload["snapshot"] = self.fixture()
        self.assertEqual(self.client.post("/api/samples", json=payload).status_code, 201)
        self.assertEqual(self.client.post("/api/samples", json=payload).status_code, 409)
        self.assertEqual(self.client.get("/api/samples").json()["count"], 1)
        exported = self.client.get("/api/samples/export")
        self.assertEqual(len(exported.text.splitlines()), 1)

    def test_client_cannot_change_registered_quote(self):
        """Forged client numbers never enter training storage. Run unittest.
        Server snapshot survives client mutation; local synthetic fixture only.
        """
        snap = self.fixture(index=3)
        server_quote = next(r for r in snap["instruments"] if r["symbol"] == "510300")
        original_price = server_quote["price"]
        server_quote["price"] = 999999
        server_quote["change_pct"] = 999
        response = self.client.post("/api/samples", json={"snapshot": snap, "selected_symbol": "510300", "label": "up"})
        self.assertEqual(response.status_code, 201)
        exported = json.loads(self.client.get("/api/samples/export").text.strip())
        saved = next(r for r in exported["snapshot"]["instruments"] if r["symbol"] == "510300")
        self.assertEqual(saved["price"], original_price)
        self.assertNotEqual(saved["change_pct"], 999)
        self.assertEqual(exported["provenance"], "server_snapshot_v1")

    def test_unavailable_abstains(self):
        """No invented analysis when inputs missing. Run unittest; pass/fail only."""
        result = self.client.post("/api/analyze", json={"snapshot":{"mode":"delayed","instruments":[]}, "selected_symbol":"510300"})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["status"], "unavailable")
        self.assertEqual(result.json()["agents"][0]["stance"], "待数据")
        self.assertEqual(self.client.post("/api/analyze", json={"snapshot":{}, "selected_symbol":"510300", "mode":"ollama"}).status_code, 503)

    def test_train_minimum_and_time_split(self):
        """Synthetic training smoke test, NOT real validation performance.
        Run unittest; actual model/report saved only in disposable temp folder.
        """
        self.assertEqual(self.client.post("/api/train").status_code, 422)
        for i in range(60):
            response = self.client.post("/api/samples", json={"snapshot":self.fixture(i), "selected_symbol":"510300", "label":["down","neutral","up"][i%3]})
            self.assertEqual(response.status_code, 201)
        response = self.client.post("/api/train")
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual((result["train_rows"], result["validation_rows"]), (48,12))
        self.assertEqual(result["target"], "human_annotation_not_future_return")
        self.assertEqual(self.client.get("/api/models").json()["count"], 1)
        self.assertEqual(self.client.get("/api/samples").json()["count"], 60)
        prediction = self.client.post("/api/predict", json={"snapshot": self.fixture(61), "selected_symbol": "510300"})
        self.assertEqual(prediction.status_code, 200, prediction.text)
        self.assertAlmostEqual(sum(prediction.json()["class_probabilities"].values()), 1)

    def test_source_failure_is_not_demo(self):
        """Mocked process failure retains null/unavailable. Run unittest only."""
        app._cache.clear()
        with patch("app.subprocess.run", side_effect=OSError("offline test only")):
            result = self.client.get("/api/snapshot").json()
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(len(result["errors"]), 3)
        self.assertTrue(all(r["price"] is None for r in result["instruments"]))
        self.assertIsNone(result["fetched_at"])
        self.assertIsNotNone(result["served_at"])

    def test_bearer_authentication_and_cors(self):
        """Offline test token only; no real credentials. Run unittest.
        Missing/wrong token rejects; correct token accepts; preflight succeeds.
        """
        with patch("app.ACCESS_TOKEN", "unit-test-token-not-a-secret"):
            missing = self.client.get("/api/health", headers={"Origin": "http://localhost:8000"})
            self.assertEqual(missing.status_code, 401)
            self.assertEqual(missing.headers["access-control-allow-origin"], "http://localhost:8000")
            self.assertEqual(self.client.get("/api/health", headers={"Authorization": "Bearer wrong"}).status_code, 401)
            self.assertEqual(self.client.get("/api/health", headers={"Authorization": "Bearer unit-test-token-not-a-secret"}).status_code, 200)
            preflight = self.client.options("/api/health", headers={"Origin": "http://localhost:8000", "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "Authorization"})
            self.assertEqual(preflight.status_code, 200)
            self.assertIn("Authorization", preflight.headers["access-control-allow-headers"])
        with patch("app.ACCESS_TOKEN", ""):
            self.assertEqual(self.client.get("/api/health").status_code, 200)

    def test_cached_source_timestamp_is_preserved(self):
        """Offline cached fixture never becomes a new fetch. Run unittest.
        Old source acquisition times persist; only served_at reflects now.
        """
        old = "2025-01-01T00:00:00+00:00"
        newer = "2025-01-02T00:00:00+00:00"
        responses = {
            "index": {"rows": [{"代码": "000001", "最新价": 1.0}], "status": "cached", "fetched_at": old, "error": "offline fixture"},
            "etf": {"rows": [{"代码": "510300", "最新价": 1.0, "更新时间": old}], "status": "cached", "fetched_at": old, "error": "offline fixture"},
            "market_flow": {"rows": [{"日期": "2025-01-01", "主力净流入-净额": 0}], "status": "cached", "fetched_at": newer, "error": "offline fixture"},
        }
        with patch("app.fetch_source", side_effect=lambda command: responses[command]):
            result = self.client.get("/api/snapshot").json()
        self.assertEqual(result["fetched_at"], newer)
        self.assertEqual(result["market_flow_as_of"], "2025-01-01")
        self.assertEqual(result["market_flow_fetched_at"], newer)
        self.assertNotEqual(result["served_at"], newer)
        self.assertEqual(result["instruments"][0]["fetched_at"], old)


if __name__ == "__main__":
    unittest.main(verbosity=2)
