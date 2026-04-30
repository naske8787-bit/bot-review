from .news import get_news
from .politicians import get_politicians
from .sectors import get_sectors
from .trades import get_trades

ROUTES = {
    ("GET", "/trades"): get_trades,
    ("GET", "/politicians"): get_politicians,
    ("GET", "/sectors"): get_sectors,
    ("GET", "/news"): get_news,
}
