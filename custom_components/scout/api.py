"""Refresh orchestration, eBay + Ollama calls, alerts, and panel payloads."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from urllib.parse import quote_plus

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from . import database, ebay, review
from .const import (
    CONDITIONS,
    DOMAIN,
    EBAY_MARKETPLACE,
    EBAY_PAGE_LIMIT,
    EBAY_SCOPE,
    EBAY_SEARCH_URL,
    EBAY_TOKEN_URL,
    EVENT_DEAL,
    FETCH_TIMEOUT_SECONDS,
    HISTORY_DAYS,
    LINK_SOURCES,
    MANUAL_SOURCES,
    NOT_THE_ITEM,
    OLLAMA_MODEL,
    OLLAMA_TIMEOUT_SECONDS,
    REVIEWS_PER_REFRESH,
)

_LOGGER = logging.getLogger(__name__)
_LOCK = asyncio.Lock()
_NET_ERRORS = (aiohttp.ClientError, asyncio.TimeoutError)


def _conf(hass: HomeAssistant) -> dict:
    return hass.data[DOMAIN]


def _run(hass: HomeAssistant, fn, *args):
    return hass.async_add_executor_job(fn, hass, *args)


# ── eBay ─────────────────────────────────────────────────────────────────

async def _ebay_token(hass: HomeAssistant) -> str:
    conf = _conf(hass)
    token, expires = conf.get("ebay_token") or (None, 0)
    if token and time.time() < expires - 120:
        return token
    session = async_get_clientsession(hass)
    async with session.post(
        EBAY_TOKEN_URL,
        auth=aiohttp.BasicAuth(conf["ebay_app_id"], conf["ebay_cert_id"]),
        data={"grant_type": "client_credentials", "scope": EBAY_SCOPE},
        timeout=aiohttp.ClientTimeout(total=FETCH_TIMEOUT_SECONDS),
    ) as resp:
        if resp.status != 200:
            # The body names the problem (bad keys, keyset not enabled for production).
            raise ValueError(f"eBay token HTTP {resp.status}: {(await resp.text())[:200]}")
        body = await resp.json()
    conf["ebay_token"] = (body["access_token"], time.time() + int(body.get("expires_in", 7200)))
    return body["access_token"]


async def _ebay_search(hass: HomeAssistant, item: dict) -> list[dict]:
    token = await _ebay_token(hass)
    session = async_get_clientsession(hass)
    async with session.get(
        EBAY_SEARCH_URL,
        params={**ebay.build_params(item), "limit": str(EBAY_PAGE_LIMIT)},
        headers={"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": EBAY_MARKETPLACE},
        timeout=aiohttp.ClientTimeout(total=FETCH_TIMEOUT_SECONDS),
    ) as resp:
        if resp.status == 401:
            _conf(hass).pop("ebay_token", None)  # expired early; next refresh gets a new one
        if resp.status != 200:
            raise ValueError(f"eBay search HTTP {resp.status}: {(await resp.text())[:200]}")
        payload = await resp.json()
    return ebay.parse_search(payload, item["exclude_terms"])


# ── Review (Ollama) ──────────────────────────────────────────────────────

async def _ask_model(hass: HomeAssistant, prompt: str) -> str:
    session = async_get_clientsession(hass)
    async with session.post(
        f"{_conf(hass)['ollama_host'].rstrip('/')}/api/chat",
        json={
            "model": OLLAMA_MODEL,
            "messages": [
                {"role": "system", "content": review.SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "format": "json",
            "stream": False,
        },
        timeout=aiohttp.ClientTimeout(total=OLLAMA_TIMEOUT_SECONDS),
    ) as resp:
        resp.raise_for_status()
        body = await resp.json()
    return body["message"]["content"]


def _history_totals(listings: list[dict]) -> list[float]:
    """Asking prices that count toward an item's price history."""
    cutoff = (datetime.now() - timedelta(days=HISTORY_DAYS)).isoformat()
    return [
        l["total"] for l in listings
        if l["first_seen"] >= cutoff
        and l["verdict"] not in NOT_THE_ITEM
        and l["user_status"] != "wrong"
    ]


async def _review_pending(hass: HomeAssistant, items: dict[int, dict]) -> None:
    """Flag and (if forge is configured) model-review new in-range listings.
    Without a model, listings get rule flags only and verdict 'unsure'. If
    forge is unreachable they stay pending and retry next refresh."""
    pending = await _run(hass, database.pending_review, REVIEWS_PER_REFRESH)
    if not pending:
        return
    by_item: dict[int, list[dict]] = {}
    for l in await _run(hass, database.get_listings, None):
        by_item.setdefault(l["item_id"], []).append(l)
    use_model = bool(_conf(hass).get("ollama_host"))

    for l in pending:
        item = items.get(l["item_id"])
        if not item:
            continue
        others = [o for o in by_item.get(l["item_id"], []) if o["id"] != l["id"]]
        flags = review.rule_flags(l, review.typical_price(_history_totals(others)))
        await _run(hass, database.set_flags, l["id"], flags)
        if not use_model:
            await _run(hass, database.set_review, l["id"], "unsure", "No model configured — check by eye")
            continue
        try:
            text = await _ask_model(hass, review.build_prompt(item, l, flags))
        except (*_NET_ERRORS, KeyError, ValueError) as err:
            _LOGGER.warning("Scout: review skipped, forge unreachable — %s", err)
            return
        verdict, notes = review.parse_verdict(text)
        await _run(hass, database.set_review, l["id"], verdict, notes)


# ── Alerts ───────────────────────────────────────────────────────────────

def _deal_message(item: dict, l: dict) -> str:
    ship = " + shipping" if l["shipping"] is None else " shipped"
    parts = [f"{item['name']}: ${l['total']:,.0f}{ship} (target ${item['target_price']:,.0f})"]
    if l.get("condition"):
        parts.append(l["condition"])
    if l.get("verdict_notes"):
        parts.append(l["verdict_notes"])
    if l["flags"]:
        parts.append("⚠ " + "; ".join(l["flags"]))
    return " · ".join(parts)


async def _send_alerts(hass: HomeAssistant, items: dict[int, dict]) -> None:
    """One alert per listing, the first time it's at or under target.
    With a model, only 'match' verdicts alert; without one, every reviewed
    in-target listing does (flags included in the message)."""
    use_model = bool(_conf(hass).get("ollama_host"))
    notify = _conf(hass).get("notify_service") or ""
    for l in await _run(hass, database.get_listings, None):
        item = items.get(l["item_id"])
        if (
            not item or not item["active"] or l["source"] != "ebay" or l["state"] != "active"
            or l["notified_at"] or l["user_status"] or not l["reviewed_at"]
            or l["total"] > item["target_price"]
            or (use_model and l["verdict"] != "match")
        ):
            continue
        message = _deal_message(item, l)
        hass.bus.async_fire(EVENT_DEAL, {
            "item": item["name"], "title": l["title"], "total": l["total"],
            "target": item["target_price"], "url": l["url"], "verdict": l["verdict"],
        })
        if notify.startswith("notify."):
            await hass.services.async_call("notify", notify.split(".", 1)[1], {
                "title": "Scout: deal found",
                "message": message,
                # clickAction = Android Companion, url = iOS
                "data": {"clickAction": l["url"], "url": l["url"]},
            }, blocking=False)
        else:
            await hass.services.async_call("persistent_notification", "create", {
                "title": "Scout: deal found",
                "message": f"{message}\n\n[{l['title']}]({l['url']})",
                "notification_id": f"scout_{l['id']}",
            }, blocking=False)
        await _run(hass, database.mark_notified, l["id"])


# ── Refresh ──────────────────────────────────────────────────────────────

async def async_refresh(hass: HomeAssistant) -> dict:
    """Search eBay for every active item, review what's new, send alerts.
    A failure on one item doesn't stop the others; errors are collected
    into meta.last_error. Overlapping calls wait on one lock."""
    async with _LOCK:
        conf = _conf(hass)
        items = {i["id"]: i for i in await _run(hass, database.get_items)}
        errors: list[str] = []
        searched = new = 0

        if conf.get("ebay_app_id") and conf.get("ebay_cert_id"):
            for item in items.values():
                if not (item["active"] and item["ebay_enabled"]):
                    continue
                try:
                    records = await _ebay_search(hass, item)
                    new += await _run(hass, database.upsert_ebay, item["id"], records)
                    searched += 1
                except (*_NET_ERRORS, KeyError, ValueError) as err:
                    errors.append(f"{item['name']}: {type(err).__name__}: {err}")
                    if "token" in str(err):
                        break  # bad keys fail every item the same way
        try:
            await _review_pending(hass, items)
            await _send_alerts(hass, items)
        except (*_NET_ERRORS, ValueError) as err:
            errors.append(f"Review/alerts: {err}")

        await _run(hass, database.set_meta, "last_refresh", dt_util.now().isoformat(timespec="seconds"))
        await _run(hass, database.set_meta, "last_error", " | ".join(errors))
        await _run(hass, database.set_meta, "last_summary",
                   f"Searched {searched} item{'' if searched == 1 else 's'} · "
                   f"{new} new listing{'' if new == 1 else 's'}")
        if errors:
            _LOGGER.warning("Scout: refresh had errors — %s", " | ".join(errors))
        else:
            _LOGGER.debug("Scout refresh ok: %s items", len(items))
    return await async_status(hass)


# ── Panel payloads ───────────────────────────────────────────────────────

def _links(item: dict) -> list[dict]:
    out = []
    for key in (k.strip() for k in item["link_sources"].split(",")):
        if key in LINK_SOURCES:
            label, tpl = LINK_SOURCES[key]
            out.append({"key": key, "label": label, "url": tpl.format(q=quote_plus(item["query"]))})
    return out


def _item_summary(item: dict, listings: list[dict]) -> dict:
    totals = _history_totals(listings)
    live = [
        l for l in listings
        if l["state"] == "active" and not l["user_status"]
        and l["verdict"] not in NOT_THE_ITEM and l["total"] <= item["max_price"]
    ]
    best = min(live, key=lambda l: l["total"]) if live else None
    return {
        **item,
        "links": _links(item),
        "stats": {
            "points": len(totals),
            "median": review.typical_price(totals),
            "low": min(totals) if totals else None,
            "live": len(live),
        },
        "best": best,
    }


async def async_status(hass: HomeAssistant) -> dict:
    conf = _conf(hass)
    items = await _run(hass, database.get_items)
    listings = await _run(hass, database.get_listings, None)
    by_item: dict[int, list[dict]] = {}
    for l in listings:
        by_item.setdefault(l["item_id"], []).append(l)
    by_id = {i["id"]: i for i in items}

    deals = []
    for l in listings:
        item = by_id.get(l["item_id"])
        if (
            item and item["active"] and l["state"] == "active" and not l["user_status"]
            and l["verdict"] not in NOT_THE_ITEM and l["total"] <= item["max_price"]
        ):
            deals.append({**l, "item_name": item["name"], "target_price": item["target_price"],
                          "max_price": item["max_price"]})
    # Best value first: how far under target, as a fraction of target.
    deals.sort(key=lambda d: d["total"] / d["target_price"])

    return {
        "items": [_item_summary(i, by_item.get(i["id"], [])) for i in items],
        "deals": deals,
        "config": {
            "ebay": bool(conf.get("ebay_app_id") and conf.get("ebay_cert_id")),
            "model": bool(conf.get("ollama_host")),
            "notify": conf.get("notify_service") or "",
        },
        "link_sources": {k: v[0] for k, v in LINK_SOURCES.items()},
        "manual_sources": MANUAL_SOURCES,
        "last_refresh": await _run(hass, database.get_meta, "last_refresh"),
        "last_error": await _run(hass, database.get_meta, "last_error") or "",
        "last_summary": await _run(hass, database.get_meta, "last_summary") or "",
    }


async def async_item_detail(hass: HomeAssistant, item_id: int) -> dict | None:
    item = await _run(hass, database.get_item, item_id)
    if not item:
        return None
    listings = await _run(hass, database.get_listings, item_id)
    return {**_item_summary(item, listings), "listings": listings}


# ── Validation for panel writes ──────────────────────────────────────────

def _price(val, field: str, required: bool) -> float | None:
    if val in (None, ""):
        if required:
            raise ValueError(f"{field} is required")
        return None
    try:
        num = float(str(val).replace(",", "").replace("$", ""))
    except ValueError as err:
        raise ValueError(f"{field} must be a number") from err
    if num < 0:
        raise ValueError(f"{field} can't be negative")
    return num


def clean_item(data: dict, partial: bool = False) -> dict:
    out: dict = {}
    for f in ("name", "query"):
        if f in data or not partial:
            val = str(data.get(f) or "").strip()
            if not val:
                raise ValueError(f"{f} is required")
            out[f] = val
    for f in ("grp", "exclude_terms", "notes"):
        if f in data:
            out[f] = str(data[f] or "").strip()
    if "link_sources" in data:
        keys = data["link_sources"]
        keys = keys if isinstance(keys, list) else str(keys).split(",")
        out["link_sources"] = ",".join(k.strip() for k in keys if k.strip() in LINK_SOURCES)
    for f, req in (("min_price", False), ("target_price", True), ("max_price", True)):
        if f in data or not partial:
            out[f] = _price(data.get(f), f, req)
    if "condition" in data:
        if data["condition"] not in CONDITIONS:
            raise ValueError("condition must be any, used or new")
        out["condition"] = data["condition"]
    for f in ("ebay_enabled", "active"):
        if f in data:
            out[f] = bool(data[f])
    t, m, lo = out.get("target_price"), out.get("max_price"), out.get("min_price")
    if t is not None and m is not None and t > m:
        raise ValueError("Target price can't be above the max price")
    if lo is not None and m is not None and lo >= m:
        raise ValueError("Min price must be below the max price")
    return out


def clean_manual(data: dict) -> dict:
    source = data.get("source")
    if source not in MANUAL_SOURCES:
        raise ValueError("Pick where you found it")
    title = str(data.get("title") or "").strip()
    if not title:
        raise ValueError("Title is required")
    state = data.get("state") or "active"
    if state not in ("active", "sold", "gone"):
        raise ValueError("state must be active, sold or gone")
    seen_at = str(data.get("seen_at") or "").strip()
    if seen_at:
        try:
            datetime.fromisoformat(seen_at)
        except ValueError as err:
            raise ValueError("Date must be YYYY-MM-DD") from err
    return {
        "source": source,
        "title": title,
        "url": str(data.get("url") or "").strip(),
        "price": _price(data.get("price"), "Price", True),
        "shipping": _price(data.get("shipping"), "Shipping", False),
        "condition": str(data.get("condition") or "").strip(),
        "state": state,
        "note": str(data.get("note") or "").strip(),
        "seen_at": seen_at,
    }
