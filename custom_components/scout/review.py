"""Listing review: rule-based flags plus the prompt/parse for the local model.

No HA imports. The model call itself is in api.py.

Rules catch what's mechanical (thin seller history, a price far under the
item's norm); the model judges what needs reading — is this the actual item,
is something missing, does the wording look off.
"""
from __future__ import annotations

import json
from statistics import median

from .const import (
    LOW_FEEDBACK_PCT,
    MIN_POINTS_FOR_MEDIAN,
    NEW_SELLER_SCORE,
    TOO_CHEAP_RATIO,
    VERDICTS,
)


def rule_flags(listing: dict, typical: float | None) -> list[str]:
    flags = []
    score, pct = listing.get("seller_score"), listing.get("seller_pct")
    if score is not None and score < NEW_SELLER_SCORE:
        flags.append(f"New seller ({score} feedback)")
    if pct is not None and pct < LOW_FEEDBACK_PCT:
        flags.append(f"Low feedback ({pct:g}%)")
    if typical and listing["total"] < typical * TOO_CHEAP_RATIO:
        flags.append(f"Under half the usual price (${typical:,.0f})")
    if listing.get("shipping") is None:
        flags.append("Shipping not listed")
    loc = listing.get("location") or ""
    if loc and not loc.endswith("US"):
        flags.append(f"Ships from {loc}")
    return flags


def typical_price(totals: list[float]) -> float | None:
    return median(totals) if len(totals) >= MIN_POINTS_FOR_MEDIAN else None


SYSTEM_PROMPT = (
    "You check used-gear listings for a buyer. Decide whether a listing is the exact "
    "item they want, complete and plausibly legitimate. Be strict about model names "
    "and mounts. Respond with valid JSON only, no explanation."
)


def build_prompt(item: dict, listing: dict, flags: list[str]) -> str:
    ship = "not listed" if listing.get("shipping") is None else f"${listing['shipping']:,.2f}"
    seller = listing.get("seller") or "unknown"
    if listing.get("seller_score") is not None:
        seller += f" ({listing['seller_score']} feedback, {listing.get('seller_pct') or '?'}% positive)"
    return (
        f"WANTED: {item['name']}\n"
        f"Buyer's notes: {item['notes'] or 'none'}\n"
        f"Buyer's target price: ${item['target_price']:,.0f}\n\n"
        f"LISTING\n"
        f"Title: {listing['title']}\n"
        f"Price: ${listing['price']:,.2f}, shipping {ship}\n"
        f"Condition: {listing.get('condition') or 'not stated'}\n"
        f"Seller: {seller}\n"
        f"Location: {listing.get('location') or 'unknown'}\n"
        f"Automatic flags: {', '.join(flags) or 'none'}\n\n"
        "Return JSON with exactly these fields:\n"
        '  verdict — one of "match" (the wanted item, complete), "accessory" (a part or add-on '
        'for it, not the item), "incomplete" (the item but missing something the notes '
        'require), "wrong_item" (a different model, size or mount), "suspect" (looks like '
        'a scam or misrepresented), "unsure" (title too vague to tell)\n'
        "  notes — one short sentence saying why, naming anything included or missing\n"
    )


def parse_verdict(text: str) -> tuple[str, str]:
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return "unsure", "Model reply wasn't valid JSON"
    verdict = str(parsed.get("verdict", "")).strip().lower().replace(" ", "_")
    if verdict not in VERDICTS:
        verdict = "unsure"
    notes = str(parsed.get("notes") or "").strip()[:300]
    return verdict, notes
