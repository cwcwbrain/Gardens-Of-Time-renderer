# Gardens of Time scene image recovery

The project downloads/indexes scene bundles and now includes an opt-in static
renderer validated with **AncientGizaRiverfront**. It produces empty, complete
and solution images from real GAF timelines, atlas references and Item Manager
metadata. This is image recovery, not a playable game.

## Run (Windows)

Use Python 3.10 or later. From this checkout:

```cmd
py -m venv .venv
.venv\Scripts\activate
py -m pip install -r requirements.txt
py gardens_renderer.py --all --index-only --output "Gardens of Time Scenes"
py gardens_renderer.py --scene AncientGizaRiverfront --scene VictorianExteriorRedux1 --output "Gardens of Time Scenes"
```

Validate live catalogue matches and sample downloads before the full bundle batch:

```cmd
py gardens_renderer.py --all --output "Gardens of Time Scenes"
```

Those commands download/index assets only. Add `--render` for the single-scene
image workflow described below.
On Linux/macOS, use `python3` instead of `py` and `source .venv/bin/activate`.
The core indexer/downloader uses the standard library. UnityPy validates bundles;
Pillow and UnityPy also support the preserved experimental extraction scripts.

`catalogues.json` contains exactly the 13 URLs supplied by the user. There is no
filename guessing. `data/Sheet2_chronology_fixed.csv` is an unchanged copy of the
upload: **4,481 headerless unique rows**, starting with AncientGizaRiverfront.
The reader also accepts a header whose first column is `scene_id`, `scene`, or
`scene_identifier`. Repeated IDs are skipped after their first occurrence and
recorded without changing original rows. No chapter boundaries are invented;
`--chapter` needs authoritative chapter metadata before it can be implemented.

## Render the Giza scene (Windows)

```cmd
py gardens_renderer.py --scene AncientGizaRiverfront --render --output output
```

This downloads or reuses its verified bundle and Item Manager JSON, then writes:

```text
output/scenes/AncientGizaRiverfront/
  scene_empty.png
  scene_complete.png
  scene_solution.png
  validation_report.json
  objects.json
  solution_objects.json
```

The scene is **1280 x 758** with **68 identified/visible hidden objects**. The
renderer decodes all 448 GAF frames and selects frame 80, the earliest frame in
which every named scene object reaches its maximum encoded opacity. Mutually exclusive animation
cells are not combined. Five time capsules are excluded. Both background art
and foreground occlusion pieces are retained; a second background-named piece
is not blindly discarded as a duplicate. Solution outlines use each object's
visible pixels after masking and occlusion. All labels come from Item Manager.

`--frame 1` selects an exact one-based snapshot instead. `--offline` reuses
hash-verified bundles, catalogues and Item Manager JSON after an online run.
Render explicit `--scene` selections only: `--all --render` is deliberately
unavailable until more scene types pass validation.

To render an explicitly chosen uploaded bundle without indexing catalogues:

```cmd
py render_scene.py --scene AncientGizaRiverfront --bundle samples/ancientgizariverfront_assets_all_21adcb104e9c56b9574ba3de0a0956ab.bundle --output output/rendered
```

That command gets the Item Manager URL from the CSV. `--item-manager path.json`
uses an explicit local file instead. Output goes below the selected scene ID.

`validated_snapshot` means the structural/metadata checks passed: exact atlas
references, stage dimensions, background coverage, unique object mapping,
visible placements, and matching solution markers. It is **not** independently
verified pixel parity with the running game. Incomplete renders go under an
`incomplete/` subfolder and return a nonzero exit status. Read the latest
`validation_report.json` for the current run's status and file list; old outputs
from earlier runs may still exist.

The renderer currently supports ordinary, single-bundle root timelines with
sprite transforms, pivots, element scale, color transforms, transparency and
single-level alpha masks. Unsupported nested timeline rendering, actions,
filters, custom Unity geometry or unresolved labels are reported as incomplete.
Victorian's nine timelines decode, but its nested-animation reconstruction is
not yet certified. FTD/STD/FTO rendering remains unimplemented.

## Outputs and status

- `catalogues/`: original downloaded bytes, SHA-256 receipts, and interrupted `.part` files.
- `bundle_index.json`: bundle locations, exact source catalogue, keys, provider options,
  resolved URLs, and dependency keys. Search by scene/bundle ID.
- `location_index.json`: all asset locations needed to resolve dependency graphs.
- `scene_report.csv` / `scene_matches.json`: all selected scenes in original order,
  candidate roots, bundle/dependency lists, match status and transfer errors.
- `bundles/`: URL-hashed filenames avoid collisions; reports retain the original URLs.
- `catalogue_report.json`, `download_report.json`, `duplicate_scenes.json`, `summary.json`.

Matching uses exact scene keys, path components, stems, and the observed
`<scene>_assets_all_<32 hex>.bundle` convention. It does not use fuzzy substring
matching. Alternative bundle/dependency sets or incompatible provider options are
ambiguous, never automatically selected. Identical URL/options sets across
catalogues are equivalent. Dependency keys are resolved in their own catalogue.
Missing dependencies, cycles and unresolved runtime properties prevent a match
from being certified. A partial catalogue run is provisional and downloads no
scene bundles: uniqueness cannot be established with catalogues missing.

HTTP relative paths resolve against the actual catalogue URL; Unity internal ID
prefixes are expanded. Properties such as `{RemoteRoot}` need verified mappings:
`--property "RemoteRoot=https://verified-host/path"`. Local runtime paths fail
closed. Unsupported catalogue formats are reported and their downloaded bytes
retained; only Unity's packed Addressables JSON format is currently implemented.
The packed JSON decoder has been validated against all 13 supplied live catalogues.

Exit codes: `0` means all selected scenes matched uniquely and requested transfers
validated (and requested snapshots passed their checks); `1` means
catalogue/match/download failures, incomplete coverage or an incomplete render;
`2` means invalid inputs or a fatal setup error. Index-only success certifies no
bundle transfer or image rendering.

## Resume and verification

Repeat the same command and output directory to resume. A completed file is skipped
only if its URL receipt and recomputed SHA-256 match; bundle structure is rechecked.
Interrupted transfers use HTTP Range plus If-Range with a strong ETag or
Last-Modified. Servers returning a full response cause a clean restart. Without
an origin validator, partial data is restarted. Bad ranges, unexpected encodings,
length mismatches, HTML responses and malformed bundles are rejected. Final files
are promoted atomically after validation. Run one process per output directory.

Bundle validation checks UnityFS signature and embedded length, optional catalogue
bundle size, decompression and serialized object extents through UnityPy. The
local SHA-256 detects subsequent local changes; it is **not** an independently
published origin checksum. Unity Hash128 is a cache version, not file MD5, and
provider CRC is over uncompressed data. Both fields are retained; independent
Unity CRC verification remains to be implemented.

`--offline` requires hash-verified cached files. `--refresh-catalogues` explicitly
redownloads catalogues, retaining previous bytes under content-addressed
`.previous-*` names. Unverified/changed completed files are preserved and reported;
move them aside before retrying. Original user uploads are retained in `data/`,
`samples/`, and `legacy/`; the latter scripts are copied unchanged.

## Sample audit and diagnostics

```cmd
py audit_samples.py samples/ancientgizariverfront_assets_all_21adcb104e9c56b9574ba3de0a0956ab.bundle samples/victorianexteriorredux1_assets_all_252d23914543848da6dcbc8871217a16.bundle
py -m unittest discover -s tests -v
```

The render tests also exercise the real GAF timelines, both real atlas textures,
Item Manager bindings, affine pivots/scales, mask/visibility behavior and refusal
to certify incomplete scenes. See [implementation notes](docs/gaf-rendering.md).

The test HTTP server serves a real uploaded UnityFS sample with **synthetic**
Addressables catalogue records. Tests cover dependency decoding, exact/ambiguous
matches, CSV ordering, partial coverage, resume, changed origins, ignored/bad Range,
cache corruption, invalid bundles, and catalogue-to-bundle and offline CLI workflows.
They do not prove accessibility or matching against the 13 live catalogues.

Both supplied bundles extract without typetree/texture errors. Giza contains 84
GAF objects; Victorian contains 98; each has two atlases. The legacy scanner finds
81 and 82 heuristic placements respectively. Its preview renders are diagnostic
only: Victorian has seven invalid rectangles, full timelines are not decoded,
and Item Manager labels and atlas selection are not certified. The supplied
manager script also depends on absent modules (`renderer_ftd_test`,
`build_scenes_auto`, `build_three_scenes_ftd_manager`, `build_object_catalogue`).
It is preserved for reference rather than presented as a functioning pipeline.

## Latest live validation

All 13 supplied catalogues downloaded and parsed successfully after cloud network
access was enabled. The live index found 4,276 uniquely matched scenes and 205
ambiguous scenes, with no missing scene identifiers. Giza and the supplied
Victorian bundle version downloaded byte-for-byte identically to the uploaded
samples. Victorian also has a second catalogue-listed version, so the pipeline
correctly keeps its match ambiguous.

Cached downloads and generated reports are local outputs, excluded from Git.
Run the index-only command to reproduce reports on your computer. The code,
scene CSV, manifest and uploaded sample bundles are included in this repository.

Next: support nested timeline rendering and validate more ordinary scenes, then
resolve competing bundle versions before batch reconstruction. FTD follows
ordinary scenes; STD, FTO and Crystal assets remain later work.
