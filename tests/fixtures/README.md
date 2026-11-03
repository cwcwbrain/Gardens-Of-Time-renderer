AncientGizaRiverfront_items.json preserves the original Item Manager JSON fetched
from the scene CSV's URL on 2026-11-03:
https://prod-cdn-assets.gardensoftime-cloud.net/catalog/xml/en_us/text_manager_items_AncientGizaRiverfront.json

It contains 68 ordinary object names and five animation descriptors. Renderer
regression tests use this real metadata and the uploaded real Giza bundle without
requiring network access. It is not a synthetic catalogue fixture.

VictorianExteriorRedux1_items.json likewise preserves its CSV-listed endpoint:
https://prod-cdn-assets.gardensoftime-cloud.net/catalog/xml/en_us/text_manager_items_VictorianExteriorRedux1.json
It is used to ensure nested animations remain incomplete rather than being
silently omitted while the scene is reported as successful.
