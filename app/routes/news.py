from app.services.news_service import get_news_items
from app.utils.helpers import get_int_param, parse_query_params


def get_news(environ):
    params = parse_query_params(environ)
    page = get_int_param(params, "page", default=1, minimum=1, maximum=100)
    limit = get_int_param(params, "limit", default=10, minimum=1, maximum=50)
    news = get_news_items(page=page, limit=limit)

    return {
        "page": page,
        "count": len(news),
        "items": news,
    }
