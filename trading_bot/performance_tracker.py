import csv
import os
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
TRADE_LOG_PATH = os.path.join(LOG_DIR, "trade_log.csv")
EQUITY_LOG_PATH = os.path.join(LOG_DIR, "equity_log.csv")


class PerformanceTracker:
    TRADE_HEADERS = [
        "timestamp",
        "action",
        "symbol",
        "qty",
        "price",
        "notional",
        "cash_balance",
        "predicted_change_pct",
        "sentiment",
        "buy_signals",
        "sell_signals",
        "note",
    ]
    EQUITY_HEADERS = [
        "timestamp",
        "portfolio_value",
        "cash_balance",
        "buying_power",
        "open_positions",
        "note",
    ]

    def __init__(self, trade_log_path=TRADE_LOG_PATH, equity_log_path=EQUITY_LOG_PATH):
        self.trade_log_path = trade_log_path
        self.equity_log_path = equity_log_path

        os.makedirs(os.path.dirname(self.trade_log_path), exist_ok=True)
        os.makedirs(os.path.dirname(self.equity_log_path), exist_ok=True)

        self._ensure_csv(self.trade_log_path, self.TRADE_HEADERS)
        self._ensure_csv(self.equity_log_path, self.EQUITY_HEADERS)

    @staticmethod
    def _timestamp():
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _ensure_csv(path, headers):
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            with open(path, "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=headers)
                writer.writeheader()
            return

        try:
            with open(path, "r", newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                existing_headers = list(reader.fieldnames or [])
                rows = list(reader)
        except Exception:
            return

        if existing_headers == list(headers):
            return

        # Keep existing data while upgrading header schema for new fields.
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row.get(k, "") for k in headers})

    @staticmethod
    def _append_row(path, headers, row):
        with open(path, "a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers)
            writer.writerow(row)

    def record_trade(self, action, symbol, qty, price, cash_balance=None, analysis=None, note=""):
        analysis = analysis or {}
        qty = int(qty)
        price = float(price)

        row = {
            "timestamp": self._timestamp(),
            "action": str(action).upper(),
            "symbol": str(symbol).upper(),
            "qty": qty,
            "price": f"{price:.4f}",
            "notional": f"{qty * price:.4f}",
            "cash_balance": "" if cash_balance is None else f"{float(cash_balance):.4f}",
            "predicted_change_pct": f"{float(analysis.get('predicted_change_pct', 0.0)):.4f}",
            "sentiment": int(analysis.get("sentiment", 0)),
            "buy_signals": int(analysis.get("buy_signals", 0)),
            "sell_signals": int(analysis.get("sell_signals", 0)),
            "note": note,
        }
        self._append_row(self.trade_log_path, self.TRADE_HEADERS, row)

    def record_equity_snapshot(self, broker, note="cycle"):
        details = broker.get_account_details() if hasattr(broker, "get_account_details") else {}

        buying_power = float(details.get("buying_power", broker.get_account_balance()))
        cash_balance = float(details.get("cash", buying_power))
        portfolio_value = (
            float(details.get("portfolio_value", 0.0))
            if details
            else float(broker.get_portfolio_value()) if hasattr(broker, "get_portfolio_value") else cash_balance
        )
        open_positions = int(broker.get_open_positions_count()) if hasattr(broker, "get_open_positions_count") else 0

        row = {
            "timestamp": self._timestamp(),
            "portfolio_value": f"{portfolio_value:.4f}",
            "cash_balance": f"{cash_balance:.4f}",
            "buying_power": f"{buying_power:.4f}",
            "open_positions": open_positions,
            "note": note,
        }
        self._append_row(self.equity_log_path, self.EQUITY_HEADERS, row)
        return row
