from app.services.trade_service import get_trades as fetch_trades
from app.utils.helpers import get_int_param, parse_query_params


def get_trades(environ):
    params = parse_query_params(environ)
    page = get_int_param(params, "page", default=1, minimum=1, maximum=100)
    limit = get_int_param(params, "limit", default=20, minimum=1, maximum=96)
    trades = fetch_trades(page=page, limit=limit)

    return {
        "page": page,
        "limit": limit,
        "count": len(trades),
        "trades": trades,
    }
