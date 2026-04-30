import json
import re
from urllib.parse import parse_qs


def json_response(start_response, payload, status="200 OK"):
    body = json.dumps(payload).encode("utf-8")
    headers = [
        ("Content-Type", "application/json; charset=utf-8"),
        ("Content-Length", str(len(body))),
    ]
    start_response(status, headers)
    return [body]


def error_response(start_response, message, status="400 Bad Request", details=None):
    payload = {"error": message}
    if details is not None:
        payload["details"] = details
    return json_response(start_response, payload, status=status)


def parse_query_params(environ):
    raw = parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=False)
    return {key: values[-1] for key, values in raw.items()}


def get_int_param(params, name, default, minimum=1, maximum=None):
    raw_value = params.get(name)
    if raw_value in (None, ""):
        return default

    value = int(raw_value)
    if value < minimum:
        raise ValueError(f"`{name}` must be at least {minimum}.")
    if maximum is not None and value > maximum:
        raise ValueError(f"`{name}` must be at most {maximum}.")
    return value


def extract_symbol(asset_name):
    match = re.search(r"\b([A-Z]{1,5})(?=:[A-Z]{2}\b)", asset_name or "")
    return match.group(1) if match else None


def infer_sector(asset_name):
    asset = (asset_name or "").lower()
    sector_keywords = {
        "Technology": ["apple", "microsoft", "nvidia", "tesla", "google", "amazon", "meta", "software", "semiconductor", "cloud"],
        "Finance": ["bank", "capital", "visa", "mastercard", "goldman", "jpmorgan", "financial"],
        "Healthcare": ["health", "pharma", "biotech", "medical", "drug", "therapeutics"],
        "Energy": ["energy", "oil", "gas", "chevron", "exxon", "petroleum", "solar"],
        "Defense": ["defense", "aerospace", "lockheed", "raytheon", "boeing", "northrop"],
        "Consumer": ["walmart", "costco", "coca-cola", "pepsi", "nike", "disney", "retail"],
    }

    for sector, keywords in sector_keywords.items():
        if any(keyword in asset for keyword in keywords):
            return sector
    return "Other"
