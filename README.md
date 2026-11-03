# Gardens of Time scene image recovery

Phase 1 implements catalogue acquisition, a searchable bundle/dependency index,
chronology-preserving exact matching, and resumable bundle downloads. It does **not**
yet produce certified empty/complete/solution images or reconstruct the game.

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

These commands currently download/index assets only. Rendering is a later stage.
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
validated; `1` means catalogue/match/download failures or incomplete coverage;
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

Next: resolve competing bundle versions, then implement faithful multi-atlas and
timeline rendering and Item Manager identification before batch reconstruction.
FTD follows ordinary scenes; STD, FTO and Crystal assets remain later work.
