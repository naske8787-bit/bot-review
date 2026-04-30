from app.services.search_services.politician_service import get_politician_summaries
from app.utils.helpers import get_int_param, parse_query_params


def get_politicians(environ):
    params = parse_query_params(environ)
    pages = get_int_param(params, "pages", default=2, minimum=1, maximum=5)
    limit = get_int_param(params, "limit", default=10, minimum=1, maximum=50)
    search = params.get("search")
    politicians = get_politician_summaries(pages=pages, limit=limit, search=search)

    return {
        "pages_scanned": pages,
        "count": len(politicians),
        "politicians": politicians,
    }
