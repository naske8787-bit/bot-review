import json
import unittest
from unittest.mock import patch

from app.main import app
from app.services import trade_service


SAMPLE_ROWS = [
    [
        "Nancy Pelosi Democrat House CA",
        "Microsoft Corp MSFT:US",
        "08:00 Yesterday",
        "01 Apr 2026",
        "days 5",
        "Self",
        "buy",
        "15K–50K",
    ],
    [
        "Nancy Pelosi Democrat House CA",
        "NVIDIA Corp NVDA:US",
        "08:30 Yesterday",
        "02 Apr 2026",
        "days 4",
        "Self",
        "sell",
        "15K–50K",
    ],
    [
        "Dan Crenshaw Republican House TX",
        "Lockheed Martin LMT:US",
        "09:30 Yesterday",
        "03 Apr 2026",
        "days 3",
        "Spouse",
        "buy",
        "1K–15K",
    ],
]


class ApiRouteTests(unittest.TestCase):
    def setUp(self):
        trade_service._get_trade_page.cache_clear()

    def tearDown(self):
        trade_service._get_trade_page.cache_clear()

    def request(self, path, query=""):
        response_meta = {}

        def start_response(status, headers):
            response_meta["status"] = status
            response_meta["headers"] = dict(headers)

        body = b"".join(
            app(
                {
                    "REQUEST_METHOD": "GET",
                    "PATH_INFO": path,
                    "QUERY_STRING": query,
                },
                start_response,
            )
        )
        return response_meta["status"], json.loads(body.decode("utf-8"))

    def test_health_route(self):
        status, payload = self.request("/health")
        self.assertEqual(status, "200 OK")
        self.assertEqual(payload["status"], "ok")
        self.assertIn("timestamp", payload)

    @patch("app.services.trade_service.scrape_trade", return_value=SAMPLE_ROWS)
    def test_trades_route_returns_normalized_payload(self, _mock_scrape):
        status, payload = self.request("/trades", "limit=2")

        self.assertEqual(status, "200 OK")
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["trades"][0]["symbol"], "MSFT")
        self.assertEqual(payload["trades"][1]["trade_type"], "sell")

    @patch("app.services.trade_service.scrape_trade", return_value=SAMPLE_ROWS)
    def test_politicians_route_aggregates_activity(self, _mock_scrape):
        status, payload = self.request("/politicians", "pages=1&limit=2")

        self.assertEqual(status, "200 OK")
        self.assertEqual(payload["count"], 2)
        top = payload["politicians"][0]
        self.assertEqual(top["politician"], "Nancy Pelosi Democrat House CA")
        self.assertEqual(top["trade_count"], 2)
        self.assertEqual(top["buy_count"], 1)
        self.assertEqual(top["sell_count"], 1)

    @patch("app.services.trade_service.scrape_trade", return_value=SAMPLE_ROWS)
    def test_sectors_route_groups_symbols(self, _mock_scrape):
        status, payload = self.request("/sectors", "pages=1&limit=5")

        self.assertEqual(status, "200 OK")
        sectors = {item["sector"]: item for item in payload["sectors"]}
        self.assertEqual(sectors["Technology"]["trade_count"], 2)
        self.assertEqual(sectors["Technology"]["symbols"], ["MSFT", "NVDA"])
        self.assertEqual(sectors["Defense"]["symbols"], ["LMT"])

    @patch("app.services.trade_service.scrape_trade", return_value=SAMPLE_ROWS)
    def test_news_route_generates_headlines(self, _mock_scrape):
        status, payload = self.request("/news", "limit=2")

        self.assertEqual(status, "200 OK")
        self.assertEqual(payload["count"], 2)
        self.assertIn("Nancy Pelosi", payload["items"][0]["headline"])
        self.assertEqual(payload["items"][0]["symbol"], "MSFT")

    def test_invalid_limit_returns_400(self):
        status, payload = self.request("/trades", "limit=0")

        self.assertEqual(status, "400 Bad Request")
        self.assertIn("at least 1", payload["error"])

    def test_unknown_route_returns_404(self):
        status, payload = self.request("/missing")

        self.assertEqual(status, "404 Not Found")
        self.assertEqual(payload["error"], "Not Found")


if __name__ == "__main__":
    unittest.main()
