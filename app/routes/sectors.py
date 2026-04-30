from app.services.search_services.sector_service import get_sector_summaries
from app.utils.helpers import get_int_param, parse_query_params


def get_sectors(environ):
    params = parse_query_params(environ)
    pages = get_int_param(params, "pages", default=2, minimum=1, maximum=5)
    limit = get_int_param(params, "limit", default=10, minimum=1, maximum=50)
    sectors = get_sector_summaries(pages=pages, limit=limit)

    return {
        "pages_scanned": pages,
        "count": len(sectors),
        "sectors": sectors,
    }
