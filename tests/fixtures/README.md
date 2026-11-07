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

EasterFestiveMall_items.json and EasterFestiveMall_names.json preserve the live
Item Manager and exact GAF root names for ch509EasterRingHuntEasterFestiveMall,
read on 2026-11-07. The manager's `colourfulball_509_eas_mal_ini` key labels a
"Beach Ball"; the bundle spells object 96 `colourfullball`. Tests exercise the
reviewed binding only for its exact bundle and canonical manager hashes.
Source URLs, hashes, sprite inspection and real reconstruction checks are in
docs/validation/reviewed_item_aliases.json. No texture pixels are included.

CornerOffice_items.json preserves its current Item Manager fetched on 2026-11-07:
https://prod-cdn-assets.gardensoftime-cloud.net/catalog/xml/en_us/text_manager_items_CornerOffice.json
CornerOffice_names.json contains the real bundle's root GAF names. The Manager's
Table Clock entry corresponds to object 152, an animated clock named
`anim_clock01`. Tests restrict this reviewed match to the exact source hashes.
Timeline references, rendered clock inspection and real reconstruction checks
are recorded in docs/validation/corner_office_fashion_backstage.json.

HighlandGames.gaf preserves the unchanged embedded GAF from the reported real
Highland Games bundle. HighlandGames_static.json contains the original serialized
root/child object metadata. HighlandGames_pose_alpha.png contains only the nine
bagpipe-animation sprites' alpha channels at their original atlas positions;
unused pixels are zero. These fixtures test the general cutout-coverage selector
without network access or a full color atlas. The production algorithm contains
no Highland Games IDs, source hashes or fixed frame number. Source validation is
recorded in docs/validation/highland_games_cheek.json.


chapter747_748.json contains the actual production chapter/mission/scene records
for chapters 747 and 748 from the MainProject Group00_VO_SO asset, read on
2026-11-04. Its source catalogue URL, bundle URL and SHA-256 are stored in the
fixture. The tests verify authoritative mission order, replay deduplication,
metadata validation and preservation of the corrected CSV rows.
