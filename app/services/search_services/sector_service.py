from app.services.trade_service import get_trade_feed
from app.utils.helpers import infer_sector


def get_sector_summaries(pages=2, limit=10):
    summary = {}

    for trade in get_trade_feed(pages=pages, limit=pages * 96):
        sector = infer_sector(trade.get("asset"))
        entry = summary.setdefault(
            sector,
            {
                "sector": sector,
                "trade_count": 0,
                "symbols": set(),
            },
        )
        entry["trade_count"] += 1
        symbol = trade.get("symbol")
        if symbol:
            entry["symbols"].add(symbol)

    results = []
    for item in summary.values():
        results.append(
            {
                "sector": item["sector"],
                "trade_count": item["trade_count"],
                "symbols": sorted(item["symbols"])[:5],
            }
        )

    results.sort(key=lambda item: (-item["trade_count"], item["sector"]))
    return results[:limit]
