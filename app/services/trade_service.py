from functools import lru_cache

from app.utils.helpers import extract_symbol
from app.utils.scraper import scrape_trade


@lru_cache(maxsize=32)
def _get_trade_page(page):
    return tuple(tuple(cell.strip() for cell in row) for row in scrape_trade(page))


def normalize_trade(row):
    asset_name = row[1] if len(row) > 1 else None
    return {
        "politician": row[0] if len(row) > 0 else None,
        "asset": asset_name,
        "symbol": extract_symbol(asset_name),
        "published": row[2] if len(row) > 2 else None,
        "traded": row[3] if len(row) > 3 else None,
        "reported_after": row[4] if len(row) > 4 else None,
        "owner": row[5] if len(row) > 5 else None,
        "trade_type": row[6] if len(row) > 6 else None,
        "amount_range": row[7] if len(row) > 7 else None,
        "raw": row,
    }


def get_trades(page=1, limit=20):
    rows = _get_trade_page(page)
    return [normalize_trade(list(row)) for row in rows[:limit]]


def get_trade_feed(pages=1, limit=100):
    combined = []
    for page in range(1, pages + 1):
        combined.extend(get_trades(page=page, limit=96))
        if len(combined) >= limit:
            break
    return combined[:limit]
