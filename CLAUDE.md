# Scout — Claude Project Conventions

Scout is a Home Assistant custom integration that watches for used gear: a watchlist with price targets, automatic eBay searches, listing review by a local model on forge, phone alerts, price history, and quick-search links for sites it can't check automatically. It's built to be reused for any future purchase, not one shopping trip.

v1 scope (2026-10-23): eBay is the only automatic source. Deferred: HA sensors, more automatic sources, auctions.

## Stack

- **Backend:** Python, SQLite (`database.py`), HA's shared aiohttp session for eBay and Ollama
- **Frontend:** Vanilla JS, no frameworks or CDN; hand-built SVG price chart. Two-file iframe auth pattern: `scout-panel.js` passes `hass.auth.data.access_token` to `scout-app.js` via `postMessage`; every fetch sends `Authorization: Bearer`. Panel host uses `height: 100vh`.
- **Deploy:** GitHub `awendellpass/ha-scout` → HACS custom repository → restart HA. Ask before pushing.

## Configuration

```yaml
scout:
  ebay_app_id: !secret scout_ebay_app_id     # both or neither (vol.Inclusive)
  ebay_cert_id: !secret scout_ebay_cert_id
  ollama_host: http://<forge tailscale ip>:11434
  notify_service: notify.mobile_app_<phone>  # else persistent_notification
```

A bare `scout:` line is valid. eBay keys live in `secrets.yaml`, which Claude never reads or edits. Andy adds them.

## File Structure

```
custom_components/scout/
  __init__.py   — config schema, DB init, static path, views, panel, refresh timers
  manifest.json
  const.py      — URLs, model, thresholds, quick-link templates
  seeds.py      — starter watchlist, inserted once (meta.seeded)
  ebay.py       — search params + response parsing (no HA imports)
  review.py     — rule flags, model prompt, verdict parsing (no HA imports)
  database.py   — SQLite: items, listings, meta
  api.py        — eBay/Ollama calls, refresh, alerts, panel payloads, input validation
  views.py      — REST endpoints
  panel.py      — sidebar panel
  frontend/
    scout-panel.js  — HA web component (iframe + token postMessage)
    scout-app.html  — markup + all CSS
    scout-app.js    — app logic + SVG chart
```

## Sources (checked 2026-10-23)

- **eBay Browse API:** `item_summary/search`, client-credentials token (`api_scope`), `X-EBAY-C-MARKETPLACE-ID: EBAY_US`. Filter = `price:[min..max],priceCurrency:USD,buyingOptions:{FIXED_PRICE},deliveryCountry:US` (+ `conditions:{USED|NEW}`), `sort=newlyListed`, 50 per item. Production keys need the marketplace-account-deletion opt-out. Whether Browse search is open to a new production keyset by default wasn't confirmed (eBay's docs page 403'd); the token error body will say.
- **⚠ The eBay field mapping in `ebay.py` follows eBay's documented ItemSummary schema and has not been checked against a real response.** Every listing's source JSON is saved in `listings.raw`. On the first live refresh, compare a few `raw` rows against the mapping (especially `shippingOptions`, `seller.feedbackPercentage` type, `itemLocation`, `itemCreationDate`) before trusting it.
- **Blocked (403 to automated requests):** MPB, KEH, Cloudy Nights, Craigslist. Craigslist also dropped RSS (~2020). Facebook Marketplace has no API and scraping breaks its terms. These are quick-search links only; don't build scrapers for them.
- **Astromart** has an official classifieds RSS (`/rss/classifieds`) but it returned an empty body on 2026-10-23, and full ads are members-only. Re-test from the Pi before adding it as a source.
- Quick-link URL patterns for MPB and KEH search are unverified; Cloudy Nights and Astromart links open the classifieds front page (no search URL).

## Data rules

- **items** are user-owned after the one-time seed. Never re-apply `seeds.py` on startup.
- **listings:** an eBay re-sighting updates only source fields (title, price, shipping, seller, raw, last_seen, state). `flags`, `verdict`, `reviewed_at`, `notified_at` and `user_status` are never touched by a refresh. A successful search marks that item's unseen eBay listings `gone`; a failed search changes nothing.
- Hand-logged finds (`source` != `ebay`) are stored as `verdict = 'match'`, can be edited/deleted, and count toward price history. eBay listings can only be dismissed or marked "not this item".
- **Price history** = listings first seen in the last 180 days, excluding verdicts accessory / incomplete / wrong_item and `user_status = 'wrong'`. Typical = median, shown once there are 5+ points.
- `total` = price + shipping (unknown shipping counts as 0 and gets a flag). Computed, not stored.

## Review + alerts

- Only active eBay listings with total ≤ the item's max are reviewed, at most 20 per refresh. Rule flags first, then the model (`llama3.1:8b`, same as Loam) with the item's notes. If forge is down, the rest stay pending and retry next refresh.
- No `ollama_host` → verdict `unsure`, and every in-target listing alerts with its flags.
- An alert fires once per listing (`notified_at`) when total ≤ target, it's not dismissed, and (with a model) the verdict is `match`. A listing that later drops into target will alert then. Also fires the `scout_deal` event for automations.
- Android Companion uses `data.clickAction`; iOS uses `data.url`. Both are sent.

## API Endpoints

```
GET    /api/scout/status                 — items (with stats/best/links), deals, config, last_refresh/error
POST   /api/scout/refresh                — search now, returns status
POST   /api/scout/items                  — add item
POST   /api/scout/items/{id}/listings    — log a find by hand
GET    /api/scout/items/{id}             — item + all listings
PUT    /api/scout/items/{id}             — partial edit (validated against the merged item)
DELETE /api/scout/items/{id}             — item + its listings
POST   /api/scout/listings/{id}/status   — {user_status: null|dismissed|wrong}
PUT    /api/scout/listings/{id}          — edit a hand-logged find
DELETE /api/scout/listings/{id}          — delete a hand-logged find
```

## Conventions

- No debug logging left at warning level (refresh success logs at `debug`; errors are warning)
- Schema changes need a migration in `init_db` plus a `SCHEMA_VERSION` bump
- Hard refresh (Ctrl+Shift+R) after frontend changes
