"""Constants for the Scout integration."""

DOMAIN = "scout"
DB_NAME = "scout.db"
FRONTEND_PATH = "/scout_frontend"
API_BASE = "/api/scout"
EVENT_DEAL = "scout_deal"

# configuration.yaml keys read by CONFIG_SCHEMA in __init__.py. Real eBay
# values live in secrets.yaml (scout_ebay_app_id / scout_ebay_cert_id) and
# arrive via !secret — never put them here.
CONF_EBAY_APP_ID = "ebay_app_id"
CONF_EBAY_CERT_ID = "ebay_cert_id"
CONF_OLLAMA_HOST = "ollama_host"
CONF_NOTIFY_SERVICE = "notify_service"

# ── eBay Browse API ──────────────────────────────────────────────────────
# Client-credentials token (public data only), good for ~2h, cached in memory.
EBAY_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
EBAY_SCOPE = "https://api.ebay.com/oauth/api_scope"
EBAY_SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
EBAY_MARKETPLACE = "EBAY_US"
EBAY_PAGE_LIMIT = 50   # newest 50 per item per refresh
FETCH_TIMEOUT_SECONDS = 30

# ── Listing review (Ollama on forge) ─────────────────────────────────────
# Same model Loam uses, so it's already pulled on forge.
OLLAMA_MODEL = "llama3.1:8b"
OLLAMA_TIMEOUT_SECONDS = 120
REVIEWS_PER_REFRESH = 20   # caps forge time when a new item pulls in a backlog
VERDICTS = ("match", "accessory", "incomplete", "wrong_item", "suspect", "unsure")
# Listings with these verdicts aren't the item, so they stay out of price history.
NOT_THE_ITEM = ("accessory", "incomplete", "wrong_item")

# ── Rule-based flags (shown alongside the model's verdict) ───────────────
NEW_SELLER_SCORE = 10        # feedback count below this = new/thin account
LOW_FEEDBACK_PCT = 97.0
TOO_CHEAP_RATIO = 0.5        # under half the item's median asking price
MIN_POINTS_FOR_MEDIAN = 5

# ── Price history ────────────────────────────────────────────────────────
HISTORY_DAYS = 180

# ── Quick-search links ───────────────────────────────────────────────────
# Sites that block automated checks (403 from MPB, KEH, Cloudy Nights and
# Craigslist when tested 2026-10-23) or have no API (Facebook). The panel
# opens these as saved searches; finds get logged by hand. {q} = the item's
# search terms. Cloudy Nights and Astromart have no search URL, so they
# open the classifieds front page.
LINK_SOURCES = {
    "ebay": ("eBay", "https://www.ebay.com/sch/i.html?_nkw={q}&LH_BIN=1"),
    "mpb": ("MPB", "https://www.mpb.com/en-us/search?q={q}"),
    "keh": ("KEH", "https://www.keh.com/shop/catalogsearch/result/?q={q}"),
    "cloudynights": ("Cloudy Nights", "https://www.cloudynights.com/classifieds/"),
    "astromart": ("Astromart", "https://www.astromart.com/classifieds"),
    "craigslist": ("Craigslist", "https://minneapolis.craigslist.org/search/sss?query={q}"),
    "facebook": ("Marketplace", "https://www.facebook.com/marketplace/minneapolis/search?query={q}"),
}
# Where a hand-logged listing can come from (link sources + a catch-all).
MANUAL_SOURCES = [k for k in LINK_SOURCES if k != "ebay"] + ["other"]

CONDITIONS = ("any", "used", "new")
DEFAULT_EXCLUDES = "for parts, parts only, broken, not working, as-is"

REFRESH_INTERVAL_HOURS = 3
STARTUP_DELAY_SECONDS = 30
