import requests
from bs4 import BeautifulSoup

def scrape_trade(page_number):
    """
    Scrapes a single page of trade data from Capitol Trades.

    Args:
        page_number (int): The page number to scrape.

    Returns:
        List[List]: A list of raw trade rows, where each row is a list of cell values.
    """
    # Define the base URL with a placeholder for the page number
    base_url = "https://www.capitoltrades.com/trades?pageSize=96&page={}"
    url = base_url.format(page_number)

    # Make the HTTP GET request
    response = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
    if response.status_code != 200:
        raise Exception(f"Failed to fetch page {page_number}. Status code: {response.status_code}")

    # Parse the HTML content with BeautifulSoup
    soup = BeautifulSoup(response.text, "html.parser")

    # Extract trade rows
    trade_rows = soup.select("tbody > tr")  # Locate all <tr> directly under <tbody>
    raw_trades = []
    for row in trade_rows:
        # Extract each cell's text and preserve spacing between nested elements
        cells = [cell.get_text(" ", strip=True) for cell in row.find_all("td")]
        raw_trades.append(cells)

    return raw_trades
