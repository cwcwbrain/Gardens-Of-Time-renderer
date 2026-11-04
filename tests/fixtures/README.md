AncientGizaRiverfront_items.json preserves the original Item Manager JSON fetched
from the scene CSV's URL on 2026-11-03:
https://prod-cdn-assets.gardensoftime-cloud.net/catalog/xml/en_us/text_manager_items_AncientGizaRiverfront.json

It contains 68 ordinary object names and five animation descriptors. Renderer
regression tests use this real metadata and the uploaded real Giza bundle without
requiring network access. It is not a synthetic catalogue fixture.

VictorianExteriorRedux1_items.json likewise preserves its CSV-listed endpoint:
https://prod-cdn-assets.gardensoftime-cloud.net/catalog/xml/en_us/text_manager_items_VictorianExteriorRedux1.json
It validates the eight encoded nested initial poses and 69 named object pointers;
unsupported nested rendering must remain incomplete rather than being omitted.


GOT_RemoteConfig_HO.json preserves the hidden-object portion of the public loading
configuration fetched on 2026-11-04 from:
https://prod-cdn-assets.gardensoftime-cloud.net/unity6/webgl/StreamingAssets/GOT_RemoteConfig.json
Tests use its actual production/QA/dev fields to ensure only production HO URLs
are discovered, including the updated catalogue 13 filename.


YucatanCenote.gaf is the embedded compressed GAF asset from the exact Yucatan
bundle variant in the user's chapter 82 report, downloaded on 2026-11-04.
It tests atlas-v3/tag-15 decoding against real data: 76 root atlas elements,
including rotated entries, and separate nested atlas scopes. It contains no
texture pixels and is used offline.
