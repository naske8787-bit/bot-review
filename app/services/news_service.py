from app.services.trade_service import get_trades


def get_news_items(page=1, limit=10):
    items = []
    for trade in get_trades(page=page, limit=limit):
        politician = trade.get("politician") or "A member of Congress"
        trade_type = (trade.get("trade_type") or "activity").lower()
        asset = trade.get("asset") or "an asset"
        items.append(
            {
                "headline": f"{politician} disclosed {trade_type} in {asset}",
                "summary": f"Filed activity for {asset} by {politician}.",
                "published": trade.get("published"),
                "traded": trade.get("traded"),
                "symbol": trade.get("symbol"),
            }
        )
    return items
