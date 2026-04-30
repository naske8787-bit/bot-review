import importlib.util
import json
import os
import time
from typing import Dict, Iterable, List, Set

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCORECARD_SCRIPT = os.path.join(ROOT, "scripts", "setup_scorecard.py")
SCORECARD_JSON = os.path.join(ROOT, "scripts", ".setup_scorecard_latest.json")


def _load_scorecard_module():
    spec = importlib.util.spec_from_file_location("setup_scorecard_runtime_module", SCORECARD_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load scorecard script: {SCORECARD_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_or_load_setup_scorecard(force: bool = False, max_age_seconds: int = 1800) -> Dict:
    needs_refresh = force or not os.path.exists(SCORECARD_JSON)
    if not needs_refresh and max_age_seconds > 0:
        age = time.time() - os.path.getmtime(SCORECARD_JSON)
        needs_refresh = age > max_age_seconds

    if needs_refresh:
        module = _load_scorecard_module()
        module.main()

    if not os.path.exists(SCORECARD_JSON):
        return {"stocks": [], "crypto": [], "top_stock_candidates": [], "top_crypto_candidates": []}

    try:
        with open(SCORECARD_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"stocks": [], "crypto": [], "top_stock_candidates": [], "top_crypto_candidates": []}


def select_active_candidates(payload: Dict, asset_class: str, limit: int = 5, min_score: float = 0.0) -> List[Dict]:
    if asset_class == "stock":
        rows = list(payload.get("top_stock_candidates") or payload.get("stocks") or [])
    elif asset_class == "crypto":
        rows = list(payload.get("top_crypto_candidates") or payload.get("crypto") or [])
    else:
        rows = []

    ranked = [
        row for row in rows
        if bool(row.get("passed", False)) and float(row.get("score", -9999.0)) >= float(min_score)
    ]
    ranked.sort(key=lambda row: float(row.get("score", -9999.0)), reverse=True)
    return ranked[: max(1, int(limit))]


def candidate_symbol_set(rows: Iterable[Dict]) -> Set[str]:
    return {str(row.get("symbol") or "").upper() for row in rows if row.get("symbol")}
