from app.services.trade_service import get_trade_feed


def get_politician_summaries(pages=2, limit=10, search=None):
    summaries = {}
    query = (search or "").strip().lower()

    for trade in get_trade_feed(pages=pages, limit=pages * 96):
        name = trade.get("politician") or "Unknown"
        if query and query not in name.lower():
            continue

        entry = summaries.setdefault(
            name,
            {
                "politician": name,
                "trade_count": 0,
                "buy_count": 0,
                "sell_count": 0,
                "latest_disclosure": trade.get("published"),
                "sample_assets": [],
            },
        )
        entry["trade_count"] += 1

        trade_type = (trade.get("trade_type") or "").lower()
        if "purchase" in trade_type or "buy" in trade_type:
            entry["buy_count"] += 1
        if "sale" in trade_type or "sell" in trade_type:
            entry["sell_count"] += 1

        asset = trade.get("asset")
        if asset and asset not in entry["sample_assets"] and len(entry["sample_assets"]) < 3:
            entry["sample_assets"].append(asset)

    results = sorted(summaries.values(), key=lambda item: (-item["trade_count"], item["politician"]))
    return results[:limit]
