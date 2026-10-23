"""Scout — used-gear watchlist and deal alerts for Home Assistant."""
from __future__ import annotations

import logging
import os
from datetime import timedelta

import voluptuous as vol
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_call_later, async_track_time_interval

from . import api, database
from .const import (
    CONF_EBAY_APP_ID,
    CONF_EBAY_CERT_ID,
    CONF_NOTIFY_SERVICE,
    CONF_OLLAMA_HOST,
    DOMAIN,
    FRONTEND_PATH,
    REFRESH_INTERVAL_HOURS,
    STARTUP_DELAY_SECONDS,
)
from .panel import async_register_panel
from .views import async_setup_views

_LOGGER = logging.getLogger(__name__)

# A bare `scout:` line is enough to run (watchlist, quick links, hand-logged
# finds). eBay keys turn on automatic checks; ollama_host turns on listing
# review; notify_service sends alerts to a phone instead of the HA bell.
CONFIG_SCHEMA = vol.Schema(
    {DOMAIN: vol.Any(None, vol.Schema({
        vol.Inclusive(CONF_EBAY_APP_ID, "ebay"): cv.string,
        vol.Inclusive(CONF_EBAY_CERT_ID, "ebay"): cv.string,
        vol.Optional(CONF_OLLAMA_HOST): cv.string,
        vol.Optional(CONF_NOTIFY_SERVICE): cv.string,
    }))},
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    conf = config.get(DOMAIN) or {}
    hass.data[DOMAIN] = {
        "ebay_app_id": conf.get(CONF_EBAY_APP_ID, ""),
        "ebay_cert_id": conf.get(CONF_EBAY_CERT_ID, ""),
        "ollama_host": conf.get(CONF_OLLAMA_HOST, ""),
        "notify_service": conf.get(CONF_NOTIFY_SERVICE, ""),
    }
    await hass.async_add_executor_job(database.init_db, hass)

    frontend_dir = os.path.join(os.path.dirname(__file__), "frontend")
    await hass.http.async_register_static_paths(
        [StaticPathConfig(FRONTEND_PATH, frontend_dir, cache_headers=False)]
    )
    async_setup_views(hass)
    await async_register_panel(hass)

    async def _refresh(_now=None):
        await api.async_refresh(hass)

    async_call_later(hass, STARTUP_DELAY_SECONDS, _refresh)
    async_track_time_interval(hass, _refresh, timedelta(hours=REFRESH_INTERVAL_HOURS))

    _LOGGER.info("Scout loaded")
    return True
