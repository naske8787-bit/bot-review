from data_fetcher import fetch_quiver_data


def test_endpoint(name, endpoint, entitlement_limited=False):
    print(f"\nTesting {name} endpoint: {endpoint}")
    data = fetch_quiver_data(endpoint=endpoint)
    if data is not None:
        size = len(data) if hasattr(data, "__len__") else "some"
        print(f"Success: Received {size} records.")
    elif entitlement_limited:
        print("Skipped: Endpoint appears tier-limited for this account.")
    else:
        print("Failed: No data returned (check endpoint path/plan access).")


def _build_endpoints():
    # Keep this list to public endpoints that are known to respond in this account.
    return [
        (
            "Live Congress Trading",
            "https://api.quiverquant.com/beta/live/congresstrading?normalized=true",
            False,
        ),
        (
            "Bulk Congress Trading",
            "https://api.quiverquant.com/beta/bulk/congresstrading",
            True,
        ),
        (
            "Historical Congress Trading (AAPL)",
            "https://api.quiverquant.com/beta/historical/congresstrading/AAPL",
            False,
        ),
        (
            "Live House Trading",
            "https://api.quiverquant.com/beta/live/housetrading",
            False,
        ),
        (
            "Historical House Trading (AAPL)",
            "https://api.quiverquant.com/beta/historical/housetrading/AAPL",
            False,
        ),
        (
            "Live Senate Trading",
            "https://api.quiverquant.com/beta/live/senatetrading",
            False,
        ),
        (
            "Historical Senate Trading (AAPL)",
            "https://api.quiverquant.com/beta/historical/senatetrading/AAPL",
            False,
        ),
        (
            "Live Lobbying",
            "https://api.quiverquant.com/beta/live/lobbying",
            False,
        ),
        (
            "Historical Lobbying (AAPL)",
            "https://api.quiverquant.com/beta/historical/lobbying/AAPL",
            False,
        ),
    ]


def main():
    endpoints = _build_endpoints()
    for name, endpoint, entitlement_limited in endpoints:
        test_endpoint(name, endpoint, entitlement_limited=entitlement_limited)


if __name__ == "__main__":
    main()
