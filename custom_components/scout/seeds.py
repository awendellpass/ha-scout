"""Starter watchlist, inserted once on a fresh install (meta.seeded).

After that the panel owns every item — nothing here is re-applied on restart.
Prices come from the 2026-10-23 used-market check (SA 2i used $200–300,
Rokinon 135 used $354–399 at MPB/KEH, GTi head $539–579 new); the wheel
numbers are a starting guess.
"""

ASTRO_LINKS = "ebay,cloudynights,astromart,craigslist,facebook"
LENS_LINKS = "ebay,mpb,keh,cloudynights,craigslist,facebook"
BIKE_LINKS = "ebay,craigslist,facebook"

SEED_ITEMS = [
    {
        "name": "Star Adventurer 2i",
        "grp": "Astro",
        "query": "Sky-Watcher Star Adventurer 2i",
        "exclude_terms": "for parts, parts only, broken, not working, as-is, wedge only, bracket only",
        "min_price": 120, "target_price": 250, "max_price": 300,
        "condition": "any",
        "link_sources": ASTRO_LINKS,
        "notes": (
            "Want the Star Adventurer 2i tracker head itself (Wi-Fi model). Kits that add the "
            "equatorial wedge, counterweight bar and dec bracket are better. The wedge, bracket, "
            "counterweight, polar scope or ball head sold alone is an accessory. The original "
            "Star Adventurer (not 2i), Star Adventurer Mini and Star Adventurer GTi are wrong items."
        ),
    },
    {
        "name": "Star Adventurer GTi",
        "grp": "Astro",
        "query": "Sky-Watcher Star Adventurer GTi",
        "exclude_terms": "for parts, parts only, broken, not working, as-is",
        "min_price": 250, "target_price": 425, "max_price": 500,
        "condition": "any",
        "link_sources": ASTRO_LINKS,
        "notes": (
            "Want the Star Adventurer GTi GoTo mount head, ideally with its counterweight kit. "
            "A tripod, pier extension or counterweight sold alone is an accessory. The "
            "Star Adventurer 2i or Mini is a wrong item."
        ),
    },
    {
        "name": "iOptron SkyGuider Pro",
        "grp": "Astro",
        "query": "iOptron SkyGuider Pro",
        "exclude_terms": "for parts, parts only, broken, not working, as-is",
        "min_price": 200, "target_price": 375, "max_price": 450,
        "condition": "any",
        "link_sources": ASTRO_LINKS,
        "notes": (
            "Want the iOptron SkyGuider Pro tracker (the iPolar version is fine). The older "
            "SkyTracker or SkyTracker Pro is a wrong item. The mounting arm, counterweight or "
            "polar scope sold alone is an accessory."
        ),
    },
    {
        "name": "Rokinon 135mm f/2 (EF)",
        "grp": "Astro",
        "query": "Rokinon 135mm f/2 Canon",
        "exclude_terms": "for parts, parts only, broken, not working, as-is, sony, nikon, fuji, micro four thirds",
        "min_price": 150, "target_price": 330, "max_price": 375,
        "condition": "any",
        "link_sources": LENS_LINKS,
        "notes": (
            "Manual-focus 135mm f/2 ED UMC lens, sold as Rokinon or Samyang, in Canon EF mount. "
            "The cine version (135mm T2.2) is the same optics and also a match. Any other mount "
            "(Sony E, Nikon F, Fuji X, Canon RF, Micro Four Thirds) is a wrong item, as is the "
            "Samyang 135mm f/1.8 AF. A cap, hood or case alone is an accessory."
        ),
    },
    {
        "name": "27\" alloy wheelset",
        "grp": "Bike",
        "query": "27 inch alloy wheelset road",
        "exclude_terms": "for parts, parts only, broken, 700c, 26 inch, 26\", tubular",
        "min_price": 40, "target_price": 100, "max_price": 175,
        "condition": "any",
        "link_sources": BIKE_LINKS,
        "notes": (
            "Front + rear pair of 27-inch (ISO 630, 27 x 1-1/4 or 27 x 1-1/8) alloy-rim road "
            "wheels for a 1980 Schwinn Le Tour. The rear hub must take a threaded freewheel, not "
            "a cassette/freehub; rear spacing 120 or 126mm (not yet measured), front 100mm. "
            "700c (622), 26-inch, steel rims and tubular rims are wrong items. A single wheel is "
            "incomplete."
        ),
    },
]
