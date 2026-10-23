"""HTTP API views for Scout."""
from __future__ import annotations

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from . import api, database
from .const import API_BASE


def async_setup_views(hass: HomeAssistant) -> None:
    # Specific routes before parameterized ones.
    hass.http.register_view(ScoutStatusView)
    hass.http.register_view(ScoutRefreshView)
    hass.http.register_view(ScoutItemsView)
    hass.http.register_view(ScoutItemListingsView)
    hass.http.register_view(ScoutItemView)
    hass.http.register_view(ScoutListingStatusView)
    hass.http.register_view(ScoutListingView)


def _hass(request):
    return request.app["hass"]


async def _body(request) -> dict:
    try:
        data = await request.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


class _View(HomeAssistantView):
    requires_auth = True

    def error(self, msg: str, status: int = 400):
        return self.json({"error": msg}, status_code=status)


# ---------------------------------------------------------------------------
# GET  /api/scout/status   — watchlist summaries, deals feed, config flags
# POST /api/scout/refresh  — check eBay now, return the fresh status
# ---------------------------------------------------------------------------

class ScoutStatusView(_View):
    url = f"{API_BASE}/status"
    name = "api:scout:status"

    async def get(self, request):
        return self.json(await api.async_status(_hass(request)))


class ScoutRefreshView(_View):
    url = f"{API_BASE}/refresh"
    name = "api:scout:refresh"

    async def post(self, request):
        return self.json(await api.async_refresh(_hass(request)))


# ---------------------------------------------------------------------------
# POST   /api/scout/items                  — add an item
# POST   /api/scout/items/{id}/listings    — log a find by hand
# GET    /api/scout/items/{id}             — item + every listing (history)
# PUT    /api/scout/items/{id}             — edit (partial)
# DELETE /api/scout/items/{id}             — remove item and its listings
# ---------------------------------------------------------------------------

class ScoutItemsView(_View):
    url = f"{API_BASE}/items"
    name = "api:scout:items"

    async def post(self, request):
        hass = _hass(request)
        try:
            data = api.clean_item(await _body(request))
        except ValueError as err:
            return self.error(str(err))
        item_id = await hass.async_add_executor_job(database.create_item, hass, data)
        return self.json(await api.async_item_detail(hass, item_id))


class ScoutItemListingsView(_View):
    url = f"{API_BASE}/items/{{item_id}}/listings"
    name = "api:scout:item_listings"

    async def post(self, request, item_id):
        hass = _hass(request)
        if not await hass.async_add_executor_job(database.get_item, hass, int(item_id)):
            return self.error("Item not found", 404)
        try:
            data = api.clean_manual(await _body(request))
        except ValueError as err:
            return self.error(str(err))
        await hass.async_add_executor_job(database.add_manual, hass, int(item_id), data)
        return self.json(await api.async_item_detail(hass, int(item_id)))


class ScoutItemView(_View):
    url = f"{API_BASE}/items/{{item_id}}"
    name = "api:scout:item"

    async def get(self, request, item_id):
        detail = await api.async_item_detail(_hass(request), int(item_id))
        return self.json(detail) if detail else self.error("Item not found", 404)

    async def put(self, request, item_id):
        hass = _hass(request)
        current = await hass.async_add_executor_job(database.get_item, hass, int(item_id))
        if not current:
            return self.error("Item not found", 404)
        body = await _body(request)
        try:
            # Validate against the merged result so a lone target edit is
            # still checked against the stored max.
            api.clean_item({**current, **body})
            data = api.clean_item(body, partial=True)
        except ValueError as err:
            return self.error(str(err))
        await hass.async_add_executor_job(database.update_item, hass, int(item_id), data)
        return self.json(await api.async_item_detail(hass, int(item_id)))

    async def delete(self, request, item_id):
        hass = _hass(request)
        if not await hass.async_add_executor_job(database.delete_item, hass, int(item_id)):
            return self.error("Item not found", 404)
        return self.json({"ok": True})


# ---------------------------------------------------------------------------
# POST   /api/scout/listings/{id}/status  — {user_status: null|dismissed|wrong}
# PUT    /api/scout/listings/{id}         — edit a hand-logged find
# DELETE /api/scout/listings/{id}         — delete a hand-logged find
# ---------------------------------------------------------------------------

class ScoutListingStatusView(_View):
    url = f"{API_BASE}/listings/{{listing_id}}/status"
    name = "api:scout:listing_status"

    async def post(self, request, listing_id):
        hass = _hass(request)
        status = (await _body(request)).get("user_status")
        if status not in (None, "dismissed", "wrong"):
            return self.error("user_status must be null, dismissed or wrong")
        if not await hass.async_add_executor_job(database.set_user_status, hass, int(listing_id), status):
            return self.error("Listing not found", 404)
        return self.json({"ok": True})


class ScoutListingView(_View):
    url = f"{API_BASE}/listings/{{listing_id}}"
    name = "api:scout:listing"

    async def put(self, request, listing_id):
        hass = _hass(request)
        try:
            data = api.clean_manual(await _body(request))
        except ValueError as err:
            return self.error(str(err))
        if not await hass.async_add_executor_job(database.update_manual, hass, int(listing_id), data):
            return self.error("Only hand-logged finds can be edited", 404)
        return self.json({"ok": True})

    async def delete(self, request, listing_id):
        hass = _hass(request)
        if not await hass.async_add_executor_job(database.delete_manual, hass, int(listing_id)):
            return self.error("Only hand-logged finds can be deleted", 404)
        return self.json({"ok": True})
