# Scout — used-gear watchlist for Home Assistant

A Home Assistant custom integration that watches for used gear at the price you want. Keep a watchlist, let Scout search eBay on a schedule, have a local model on your own server check each listing, and get a phone alert when a real match drops under your target.

## Features

- **Watchlist:** per item, set search terms, words to skip, a min / target / max price, condition, and a plain-English note on what counts as the right item
- **eBay search** every 3 hours (fixed-price listings only, since an auction's current bid isn't its final price)
- **Listing review:** rule-based flags (new seller, low feedback, far under the usual price, no shipping listed, ships from abroad) plus an optional local Ollama model that labels each listing match / accessory / incomplete / wrong item / suspect / unsure
- **Alerts:** once per listing, when it's at or under target and the model calls it a match. Sent to a phone through a `notify` service, or to HA's notification bell
- **Price history:** every listing ever seen, charted against your target and max, with a typical (median) price once there's enough data
- **Quick-search links:** one-tap saved searches on sites Scout can't check automatically (MPB, KEH, Cloudy Nights, Astromart, Craigslist, Facebook Marketplace). Log what you find there and it joins the price history

## Installation (HACS)

1. HACS → ⋮ → Custom repositories → add `https://github.com/awendellpass/ha-scout` as an **Integration**
2. Install **Scout**
3. Add to `configuration.yaml`. A bare `scout:` line is enough to start:
   ```yaml
   scout:
     # optional: turns on automatic eBay searches
     # ebay_app_id: !secret scout_ebay_app_id
     # ebay_cert_id: !secret scout_ebay_cert_id
     # optional: turns on listing review
     # ollama_host: http://<forge tailscale ip>:11434
     # optional: phone alerts instead of the HA bell
     # notify_service: notify.mobile_app_<your phone>
   ```
4. Restart Home Assistant. Scout appears in the sidebar with the binoculars icon.

### eBay keys

1. Create an account at [developer.ebay.com](https://developer.ebay.com) and create a **Production** keyset.
2. eBay requires every app to either receive marketplace account-deletion notices or opt out. Scout stores no eBay user data, so choose the **exemption / opt-out**.
3. Copy the **App ID (Client ID)** and **Cert ID (Client Secret)** into `secrets.yaml` as `scout_ebay_app_id` and `scout_ebay_cert_id`, then uncomment the two lines above.

## Stack

- Python backend, SQLite (`/config/scout.db`)
- Vanilla JS frontend served as an HA iframe panel (token passed in via `postMessage`)
- eBay Browse API (client-credentials token), Ollama `/api/chat`
- No external Python dependencies
