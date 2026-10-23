"""eBay Browse API helpers — request building and result parsing.

No HA imports, so this is testable standalone. Network calls live in api.py.

Field mapping follows eBay's documented ItemSummary schema and has NOT yet
been checked against a real response (no keys as of 2026-10-23). Each
listing's source JSON is kept in listings.raw so the first live refresh can
be compared against this mapping before anything depends on it.
"""
from __future__ import annotations

import json


def _num(val) -> float | None:
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def build_params(item: dict) -> dict:
    """Search params for one watchlist item. Fixed-price only: an auction's
    current bid says little about where it will end, so it would trigger
    false alerts and skew price history."""
    lo = item.get("min_price")
    price = f"[{'' if lo is None else f'{lo:g}'}..{item['max_price']:g}]"
    filters = [
        f"price:{price}",
        "priceCurrency:USD",
        "buyingOptions:{FIXED_PRICE}",
        "deliveryCountry:US",
    ]
    if item.get("condition") == "used":
        filters.append("conditions:{USED}")
    elif item.get("condition") == "new":
        filters.append("conditions:{NEW}")
    return {
        "q": item["query"],
        "filter": ",".join(filters),
        "sort": "newlyListed",
    }


def excluded(title: str, exclude_terms: str) -> bool:
    low = title.lower()
    return any(t and t in low for t in (s.strip().lower() for s in exclude_terms.split(",")))


def _shipping(summary: dict) -> float | None:
    """Cheapest fixed shipping cost, or None when it's calculated at checkout
    (needs a buyer zip) or missing."""
    costs = []
    for opt in summary.get("shippingOptions") or []:
        if opt.get("shippingCostType") == "CALCULATED":
            continue
        val = _num((opt.get("shippingCost") or {}).get("value"))
        if val is not None:
            costs.append(val)
    return min(costs) if costs else None


def parse_search(payload: dict, exclude_terms: str = "") -> list[dict]:
    """Turn a search response into listing records, dropping excluded titles
    and anything without a usable price."""
    records = []
    for s in payload.get("itemSummaries") or []:
        title = s.get("title") or ""
        price = _num((s.get("price") or {}).get("value"))
        if not s.get("itemId") or price is None or excluded(title, exclude_terms):
            continue
        seller = s.get("seller") or {}
        loc = s.get("itemLocation") or {}
        records.append({
            "external_id": s["itemId"],
            "title": title,
            "url": s.get("itemWebUrl"),
            "image_url": (s.get("image") or {}).get("imageUrl"),
            "price": price,
            "shipping": _shipping(s),
            "currency": (s.get("price") or {}).get("currency") or "USD",
            "condition": s.get("condition"),
            "seller": seller.get("username"),
            "seller_score": int(seller["feedbackScore"]) if seller.get("feedbackScore") is not None else None,
            "seller_pct": _num(seller.get("feedbackPercentage")),
            "location": ", ".join(p for p in (loc.get("city"), loc.get("stateOrProvince"), loc.get("country")) if p) or None,
            "listed_at": s.get("itemCreationDate"),
            "raw": json.dumps(s),
        })
    return records
