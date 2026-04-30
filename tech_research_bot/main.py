#!/usr/bin/env python3
import json
import os
import re
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.error import URLError, HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

USER_AGENT = "tech-research-bot/1.0"
POLL_SECONDS = int(os.getenv("TECH_RESEARCH_POLL_SECONDS", "1800"))
TOP_N = int(os.getenv("TECH_RESEARCH_TOP_N", "12"))
MIN_PROB = float(os.getenv("TECH_RESEARCH_MIN_PROBABILITY", "0.65"))

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
SNAPSHOT_PATH = os.path.join(OUTPUT_DIR, "latest_research.json")

RSS_FEEDS = [
    "https://techcrunch.com/feed/",
    "https://www.theverge.com/rss/index.xml",
    "https://feeds.arstechnica.com/arstechnica/technology-lab",
    "https://venturebeat.com/category/ai/feed/",
    "https://news.mit.edu/rss/topic/artificial-intelligence2",
]

SEARCH_QUERIES = [
    "emerging technology breakthrough",
    "frontier AI model release",
    "semiconductor process breakthrough",
    "quantum computing enterprise adoption",
    "next generation battery commercialization",
    "robotics autonomy industrial deployment",
    "cybersecurity zero trust platform launch",
]

KEYWORD_WEIGHTS = {
    "artificial intelligence": 3.0,
    "generative ai": 2.8,
    "agentic": 2.5,
    "foundation model": 2.6,
    "semiconductor": 2.4,
    "chip": 1.7,
    "quantum": 2.6,
    "robotics": 2.2,
    "automation": 1.9,
    "autonomous": 2.0,
    "cybersecurity": 2.0,
    "zero trust": 2.1,
    "lithography": 2.2,
    "battery": 1.8,
    "solid-state": 2.3,
    "fusion": 2.6,
    "biotech": 1.8,
    "synthetic biology": 2.4,
    "edge computing": 1.7,
    "data center": 1.8,
    "hyperscaler": 1.6,
    "gpu": 2.0,
    "inference": 1.6,
    "api launch": 1.7,
    "production": 1.6,
    "commercial": 1.5,
    "enterprise adoption": 2.0,
    "regulatory approval": 2.0,
    "breakthrough": 1.7,
}

NEGATIVE_TERMS = {
    "rumor": 1.6,
    "speculation": 1.7,
    "lawsuit": 1.8,
    "delay": 1.4,
    "canceled": 2.0,
    "bankruptcy": 2.2,
    "hack": 1.6,
}

IMPACT_THEMES = {
    "ai_frontier": ["artificial intelligence", "generative ai", "foundation model", "agentic"],
    "compute_semiconductor": ["semiconductor", "chip", "lithography", "gpu", "data center"],
    "automation_robotics": ["robotics", "automation", "autonomous"],
    "quantum_nextgen": ["quantum", "fusion", "solid-state", "battery"],
    "security_infra": ["cybersecurity", "zero trust", "edge computing", "inference"],
}


def log(msg):
    print(str(msg), flush=True)


def _safe_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _extract_pub_dt(item):
    raw = item.get("published") or item.get("updated") or ""
    raw = _safe_text(raw)
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).astimezone(UTC)
    except Exception:
        return None


def _theme_for_text(text):
    lowered = text.lower()
    best_theme = "emerging_tech_general"
    best_hits = 0
    for theme, words in IMPACT_THEMES.items():
        hits = sum(1 for w in words if w in lowered)
        if hits > best_hits:
            best_hits = hits
            best_theme = theme
    return best_theme


def _score_item(title, summary):
    text = f"{title} {summary}".lower()
    score = 0.0
    reasons = []

    for kw, weight in KEYWORD_WEIGHTS.items():
        if kw in text:
            score += weight
            reasons.append(f"{kw}(+{weight:.1f})")

    for kw, weight in NEGATIVE_TERMS.items():
        if kw in text:
            score -= weight
            reasons.append(f"{kw}(-{weight:.1f})")

    if "enterprise" in text and "production" in text:
        score += 1.2
        reasons.append("enterprise+production(+1.2)")
    if "pilot" in text and "commercial" in text:
        score += 0.8
        reasons.append("pilot+commercial(+0.8)")

    # Logistic-like normalization to 0..1 (centered around score 3.0)
    probability = 1.0 / (1.0 + pow(2.718281828, -(score - 3.0) / 2.0))
    impact_score = max(0.0, min(10.0, score))
    return impact_score, max(0.0, min(1.0, probability)), reasons


def _fetch_url(url):
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=20) as resp:
        return resp.read()


def _parse_rss(feed_url):
    try:
        payload = _fetch_url(feed_url)
    except (URLError, HTTPError, TimeoutError) as e:
        log(f"feed fetch failed: {feed_url} ({e})")
        return []

    try:
        root = ET.fromstring(payload)
    except ET.ParseError as e:
        log(f"feed parse failed: {feed_url} ({e})")
        return []

    items = []
    for node in root.findall(".//item") + root.findall(".//{http://www.w3.org/2005/Atom}entry"):
        title = _safe_text((node.findtext("title") or node.findtext("{http://www.w3.org/2005/Atom}title") or ""))
        summary = _safe_text(
            node.findtext("description")
            or node.findtext("summary")
            or node.findtext("{http://www.w3.org/2005/Atom}summary")
            or ""
        )
        link = _safe_text(node.findtext("link") or "")
        if not link:
            atom_link = node.find("{http://www.w3.org/2005/Atom}link")
            if atom_link is not None:
                link = _safe_text(atom_link.attrib.get("href") or "")
        published = _safe_text(
            node.findtext("pubDate")
            or node.findtext("published")
            or node.findtext("updated")
            or node.findtext("{http://www.w3.org/2005/Atom}updated")
            or ""
        )
        if title:
            items.append({
                "title": title,
                "summary": summary,
                "link": link,
                "published": published,
                "source": feed_url,
            })
    return items


def _parse_google_news_query(query):
    q = quote(query)
    url = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
    return _parse_rss(url)


def collect_research_items():
    items = []
    seen = set()

    for feed in RSS_FEEDS:
        for item in _parse_rss(feed):
            key = (item.get("title") or "").lower()
            if not key or key in seen:
                continue
            seen.add(key)
            items.append(item)

    for query in SEARCH_QUERIES:
        for item in _parse_google_news_query(query):
            key = (item.get("title") or "").lower()
            if not key or key in seen:
                continue
            seen.add(key)
            items.append(item)

    return items


def build_snapshot():
    raw_items = collect_research_items()
    scored = []

    for item in raw_items:
        impact_score, probability, reasons = _score_item(item["title"], item.get("summary", ""))
        if probability < MIN_PROB:
            continue
        theme = _theme_for_text(f"{item['title']} {item.get('summary', '')}")
        published_dt = _extract_pub_dt(item)
        scored.append({
            "title": item["title"],
            "link": item.get("link", ""),
            "published": item.get("published", ""),
            "published_iso": published_dt.isoformat() if published_dt else "",
            "theme": theme,
            "impact_score": round(impact_score, 2),
            "probability_significant_impact": round(probability, 4),
            "rationale": reasons[:8],
            "source": item.get("source", ""),
        })

    scored.sort(
        key=lambda x: (
            float(x.get("probability_significant_impact") or 0.0),
            float(x.get("impact_score") or 0.0),
            str(x.get("published_iso") or ""),
        ),
        reverse=True,
    )

    top = scored[:TOP_N]
    avg_prob = sum(float(x.get("probability_significant_impact") or 0.0) for x in top) / len(top) if top else 0.0

    snapshot = {
        "generated_at": datetime.now(UTC).isoformat(),
        "min_probability_threshold": MIN_PROB,
        "candidate_count": len(top),
        "avg_probability": round(avg_prob, 4),
        "methodology": "keyword-weighted impact scoring with probability normalization and source deduplication",
        "top_candidates": top,
    }
    return snapshot


def persist_snapshot(snapshot):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2)


def run_once():
    snapshot = build_snapshot()
    persist_snapshot(snapshot)

    count = int(snapshot.get("candidate_count") or 0)
    avg_prob = float(snapshot.get("avg_probability") or 0.0)
    log(f"research cycle complete: candidates={count}, avg_prob={avg_prob:.2%}, output={SNAPSHOT_PATH}")

    for idx, item in enumerate(snapshot.get("top_candidates") or [], start=1):
        log(
            f"{idx}. p={float(item.get('probability_significant_impact', 0.0)):.1%} "
            f"impact={float(item.get('impact_score', 0.0)):.2f} "
            f"theme={item.get('theme', 'unknown')} :: {item.get('title', '')}"
        )


if __name__ == "__main__":
    log("tech_research_bot started")
    while True:
        try:
            run_once()
        except Exception as e:
            log(f"research cycle failed: {e}")
        time.sleep(max(60, POLL_SECONDS))
