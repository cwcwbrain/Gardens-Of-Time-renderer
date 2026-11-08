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

`catalogues.json` retains the 13 user-supplied URLs as a frozen fallback and now
points to the game's official loading configuration for automatic discovery:

```text
https://prod-cdn-assets.gardensoftime-cloud.net/unity6/webgl/StreamingAssets/GOT_RemoteConfig.json
```

Each online catalogue scan snapshots that JSON and reads `assetCatalogInfo`, selecting
`HO Scenes <number>` entries and joining their explicit `URLInfo_Prod.URL` and
`URLInfo_Prod.catalogName` fields. It does not guess filenames or require exactly
13 groups, so a future explicitly listed HO group can be discovered. Main-game,
ISO/garden and QA/dev/preproduction art catalogues are excluded. The `--update`
mode additionally reads the production MainProject catalogue for chapter metadata.
The selected production projects and excluded project names are listed in
`output/catalogue_discovery.json`.

Raw config snapshots and their hashes are retained under `output/remote_config/`.
The catalogue list is frozen for the invocation: a config update cannot alter an
ongoing run halfway through. Offline mode uses an integrity-checked config
snapshot. If discovery fails, the last verified snapshot is used; if unavailable,
the explicit manifest list is used. Every fallback records a warning and its
source; it is not presented as a freshly fetched configuration. A failed refresh
cannot destroy the previous validated config. Explicit custom manifests without
a `remote_config` field keep their original no-discovery behaviour. Use
`--remote-config https://.../config.json` to override the config endpoint.

The live config was checked on 2026-11-04: it lists 13 production HO catalogues,
including `catalog_13_p_2026.11.02.10.44.21.json`, which is newer than the original
fallback entry. All 13 discovered catalogues downloaded and parsed successfully;
the corrected CSV still matched 4,276 scenes uniquely and 205 with alternatives.
Do not replace program files while an existing all-scene run is active. Install
updates after it finishes; program changes can invalidate test checkpoints.

There is no catalogue filename guessing. `data/Sheet2_chronology_fixed.csv` is an unchanged copy of the
upload: **4,481 headerless unique rows**, starting with AncientGizaRiverfront.
The reader also accepts a header whose first column is `scene_id`, `scene`, or
`scene_identifier`. Repeated IDs are skipped after their first occurrence and
recorded without changing original rows. No chapter boundaries are invented;
`--chapter` needs authoritative chapter metadata before it can be implemented.

## Test every scene (Windows)

```cmd
py gardens_renderer.py --test-all --output output
```

`--all --render` is an equivalent entry point. The runner creates a complete roster
before starting work, preserving chronological order and all distinct versions.
The current CSV/catalogues produce **4,481 scenes and 4,689 variant tests**.
Existing verified downloads are reused. Each variant runs sequentially in a
separate Python process so crashes, unsupported data, and memory retained by
UnityPy do not stop the next scene. The default per-variant timeout is 300 seconds;
use `--test-timeout 600` to increase it.

Every variant gets a result:

- `passed`: bundle, Item Manager, reconstruction, visible-object and named-pointer
  checks passed; three current images and their metadata were verified.
  Extra scene objects without Item Manager entries are recorded as warnings;
  they do not block a pass when every findable entry is matched and visible.
- `incomplete`: assets were checked but the renderer lacks a required feature,
  scene type, or reliable object placement/identification.
- `failed`: download, worker crash, timeout, or another runtime failure.
- `blocked`: catalogue coverage or dependencies are unresolved; no render pass is
  inferred. Unsupported FTD/STD/FTO scenes are included in the roster and have
  their bundles and Item Manager checked, then are reported incomplete.
- `pending` / `running`: not finished. A scene with only some passing versions is
  `partial`; all of its versions remain visible in the per-variant report.

Reports are in the selected output folder:

- **`scene_test_report.csv`**: one row per unique scene, including pending/blocked
  scenes, match status, per-outcome version counts, and overall scene status.
- **`test_report.csv`**: one row per variant with reasons, source bundle URLs,
  image folder, object/pointer counts, worker log and elapsed time.
- **`all_scene_test_summary.json`** (also `summary.json`): totals, remaining work,
  `coverage_complete` and `all_passed`. Coverage completion is not the same as all
  scenes passing. Snapshot passes do not establish independent game pixel parity.
- **`batch/tests.sqlite3`**: durable checkpoints committed after every variant.
  Worker job/result JSON and logs are saved beside it.

CSV/summary reports refresh every ten results and at completion, pause, or Ctrl+C.
If Excel locks a CSV on Windows, testing continues; close the file to let the next
refresh replace it. The latest temporary CSV and SQLite checkpoints are retained.
Run only one test command against an output folder at a time.

**Resume:** press Ctrl+C to stop, then repeat the same command and output directory.
An interrupted worker is retried. Finished pass checkpoints are reused only when
the code/runtime/settings fingerprint and hashes of input assets, all three PNGs,
and validation/label metadata still match. Code/runtime/settings changes invalidate affected checkpoints. Changed asset,
image or metadata files invalidate passing checkpoints. Failed/incomplete results
are retained until code/runtime/settings change or you explicitly retry them
after fixing inputs or access:

```cmd
py gardens_renderer.py --test-all --retry-failed --output output
```

**Retry only incomplete versions after updating the renderer:**

```cmd
py gardens_renderer.py --retry-incomplete --output output
```

Use the **same output folder** as the finished run and keep its
`batch/tests.sqlite3` file. This mode selects only saved active variants with
status `incomplete`. Passed variants, including passed siblings in a partial
scene, keep their images and checkpoint rows unchanged even after a code update.
Failed, blocked, pending and running variants are also left alone. It uses the
saved bundle URLs and destinations, without rereading the spreadsheet or
discovering catalogues. A missing checkpoint stops the command; it never falls
back to a full run.

Aggregate CSV/JSON reports refresh. `test_report.csv` records each variant's
original or updated `validation_fingerprint` and whether it was selected for
this retry. The summary records the selection and preserved-pass counts. Retained
passes remain results from their original build, not new validation by the
updated renderer. The command exits successfully when all selected retries pass
(or none need retrying), even if unrelated variants still have other statuses.

Use `--plan-only` to preview the selection, or `--test-limit 4` to retry up to four
incomplete scenes. Interrupted or unattempted retries retain their incomplete
checkpoints; repeat the command to retry them. Run one command at a time against
the output folder. Unlike `--retry-incomplete`, the full `--test-all` mode still
invalidates old passing checkpoints when the renderer changes.

**Retry incomplete scenes and add newly published chapters/versions:**

```cmd
py gardens_renderer.py --update --output output
```

**One unattended command for the complete existing-run workflow:**

```cmd
py gardens_renderer.py --finish-all --output output
```

This refreshes catalogue discovery, retries incomplete versions, resumes saved
pending/running work, discovers new chapters/versions, writes official name
matches, reviews passing nested snapshots with the general pose selector,
organizes named exports by 100/10/individual chapters, then deletes only verified
obsolete incomplete previews. The pose review replaces only images whose pixels
change; unchanged passing scenes and failed/blocked saved results are preserved.
`pose_refresh_report.json` lists the actual changed scene IDs. Repeated reviews
reuse cached analysis for unchanged sources. See the [release change list](docs/scene-changes.md).
Use the latest program files beside the existing output
folder; relocation must already have succeeded after a move.

To also upgrade older passing solutions to the numbered-pointer layout:

```cmd
py gardens_renderer.py --finish-all --refresh-solutions --output output
```

The solution-layout step keeps empty and complete images unchanged. It rebuilds older
solution annotations from their verified cached assets, checks the native image
hashes, and publishes only the solution and its annotation metadata. Progress is
saved per version; repeating the command reuses solutions already upgraded.
Named copies and their index are refreshed afterward. See
[solution layout updates](docs/solution-layout-updates.md) for reports and recovery.
The preceding general pose review may correct native images when an improved
encoded animation pose changes their pixels. To run that review with the update
workflow separately, use `--update --refresh-poses --output output`.

On Windows it temporarily requests that the system stay awake while the command
runs, and restores the previous thread sleep state when finished. The display
may turn off. Keep the computer powered on and online; explicit sleep, shutdown
or a laptop lid sleep policy can still stop processing. No permanent power
settings are changed.

Known incomplete results return exit code 1 but still allow naming and safe
cleanup. Fatal errors and interruptions skip cleanup. `finish_all_report.json`
records both stages and their exit codes; the ordinary scene/name/cleanup reports
remain available. `--plan-only` never deletes previews. Repeat the same command
after an interruption to resume saved unfinished jobs. This workflow attempts
all supported work; unresolved or unsupported scenes remain explicitly incomplete.

After the current run finishes and you install the updated code, this command
refreshes the official remote configuration and production HO catalogues. It
also reads the production `Group00_VO_SO` static-data asset, using its explicit
chapter → ordered mission → scene references to append chapters beyond the
corrected CSV. The CSV and all previously saved scene orders stay unchanged.
Item Manager URLs follow the convention demonstrated in that CSV and must return
verified label JSON before a new scene becomes eligible. No labels are invented.

Only saved incomplete versions and newly discovered eligible versions run.
Previously passed images, validation fingerprints and checkpoint rows are
preserved. Other previously saved statuses are left alone. Missing new assets or
metadata reserve a blocked chronological slot; later updates can activate that
slot once verified resources appear. A new version of an existing scene is saved
under `scenes/_new_versions/<scene_id>/variant_<id>/`, keeping its previous files
in place. New scene IDs use their own normal scene output folders.

The check happens at the start of each invocation; an ongoing run does not add
chapters halfway through. Repeating the command discovers later chapters without
duplicating earlier variants. `--plan-only` registers/exports the update plan
without rendering, and `--test-limit` limits the selected scenes. Keep the same
output folder and its `batch/tests.sqlite3` file. The command does not launch a
full test roster or invalidate passing scenes because the renderer changed.

`chapter_discovery.json`, `update_scene_matches.json` and `update_discovery.json`
record source hashes, chapter/mission provenance, verified URLs and unavailable
resources. New blocked resources or failed reconstruction produce a nonzero exit
status. Automatic discovery does not guarantee every new scene can render;
unsupported scene modes and missing data remain explicit. Incomplete catalogue
coverage makes no new enrollments, while saved incomplete variants can still be
retried. The update reads MainProject's chapter metadata only, rather than
rebuilding the playable game.

**Find one scene by its in-game name:**

```cmd
py gardens_renderer.py --find-scene "Suzhou Gardens" --open --output output
```

The finder opens a verified existing copy when available. Otherwise it recovers
the selected scene into `output/misc`, reusing verified cached bundles and Item
Managers. Missing scene assets and refreshed catalogue metadata are saved there.
It does not change the main
scene folders, checkpoint or batch reports. All catalogue versions are attempted
when recovering a missing scene; ambiguous names are listed for you to choose by
internal scene ID.

For a prompt that lets you enter several names, omit the name:

```cmd
py gardens_renderer.py --find-scene --open --output output
```

Type `q` to quit. `--open` opens the image folder; it is optional. Exact internal
IDs, official captions and replay titles also work. See
[finding scenes on demand](docs/scene-finder.md) for cache, index and preview details.

**Match every scene ID to its actual in-game name without rendering images:**

```cmd
py gardens_renderer.py --match-names --output output
```

This writes `output/scene_name_matches.csv`, one row per scene in corrected
order, including scenes whose reconstruction is incomplete or has not started.
`in_game_name` is the official name in the game's chapter list; `scene_title`
retains the separate full scene caption. The CSV also records the chapter,
localization keys, replay aliases, match status and unresolved issues. Names are
joined through the official game metadata and localization, never inferred from
asset filenames. Missing or ambiguous names stay unmatched and give a nonzero
command exit status.

The matching command does not copy/export PNGs, run scene workers or index HO
scene bundles. It downloads only the metadata needed for naming. It works before
a first reconstruction run; if a checkpoint exists, it reads active saved scene
IDs too, including discovered later chapters, without changing the saved results.
`scene_names.json` retains the detailed mappings and source proof, and
`scene_name_match_report.json` summarizes coverage. The same CSV is written
automatically during named exports and at the end of `--update`. Run one command
at a time against an output folder.

**Official filenames are included in `--update`.** After retrying incomplete
scenes and adding new ones, it exports every active passed version into
`output/named_scenes` using the official English chapter-list title. For example:

```text
Giza Riverfront - Empty.png
Giza Riverfront - Complete.png
Giza Riverfront - Solution.png
```

The named files are independent copies. Original PNG paths, validation reports
and SQLite checkpoint rows stay intact, so future updates can still reuse them.
Chapter/scene order and stable version folders distinguish repeated titles and
multiple bundle matches. Named images are grouped by **100 chapters**, then
**10 chapters**, then the individual chapter. For example:

```text
named_scenes/
  Chapters 401-500/
    Chapters 461-470/
      Chapter 463 - <official chapter title>/
        <scene order> - <official scene title>/
          variant_<version ID>/
            <official scene title> - Empty.png
            <official scene title> - Complete.png
            <official scene title> - Solution.png
```

The ranges are fixed: chapter 100 belongs to `001-100` / `091-100`, and
chapter 101 belongs to `101-200` / `101-110`. Later chapters automatically use
the same hierarchy. Corrected scene order and every matched version are retained.

Existing unchanged named PNGs tracked by the export manifest are moved into the
new groups without creating another PNG copy. Their bytes and modification times
stay intact. Edited/untracked copies and old aliases for different official names
are preserved. A small migration journal lets an interrupted reorganization resume.
Only empty former chapter/scene/version folders are removed after publication;
folders containing notes or other files are retained.
The original render tree and checkpoint are never reorganized by named export.
The new manifest uses schema 2 and can read schema 1 exports; older code refuses
the new manifest rather than recreating the former folder layout.

Windows-invalid characters are escaped; deep paths can
shorten folder labels or long filenames while the exact official title remains
in the index. Very deep roots can use chapter numbers and scene order numbers
for those two folder levels. A shorter `--named-output` location keeps more
folder text visible. Grouping changes folder organization, not total image size
or OneDrive's requirement to finish downloading the files before a transfer.

To name already passing images without reconstructing or indexing scene bundles:

```cmd
py gardens_renderer.py --export-named --output output
```

An alternative destination can be specified with
`--named-output "Gardens of Time Scenes"`. Both export commands require the
existing output folder and its `batch/tests.sqlite3`. Strict `--retry-incomplete`
keeps its saved-roster-only behavior and does not create named exports.

`scene_names.json` records verified title mappings and source hashes;
`naming_report.json` records the export outcome. Inside the named folder,
`named_scene_index.csv` links readable filenames to IDs/versions and original
hashes, and `named_export_manifest.json` tracks owned copies. Only passed PNGs
with matching proof hashes are copied. Missing names, corrupt source images and
edited/untracked conflicting copies are reported rather than overwritten.
Repeating the export reuses identical copies without changing their PNG times.
Naming failure does not undo reconstruction; it produces a nonzero command exit
status and an explicit report. No naming pass runs for `--plan-only` or an
interrupted update. Offline mode requires verified cached naming resources.

**Remove obsolete incomplete previews from scenes that now pass:**

Run this after the renderer finishes, or stop it with Ctrl+C and wait for the
command prompt. First preview the exact eligible files:

```cmd
py clean_incomplete.py --output output
```

To delete the eligible previews:

```cmd
py clean_incomplete.py --output output --delete
```

The script uses the saved checkpoint, checks that each selected version is
passed, and verifies its three finished PNGs and three validation/label JSON
files against their recorded hashes. It removes only `scene_empty.png`,
`scene_complete.png`, `scene_solution.png` and their matching `.tmp` filenames
directly inside that version's `incomplete` folder. The finished files, named
exports, bundles, Item Managers and checkpoint rows stay intact. Incomplete or
failed sibling versions and unknown files/subfolders are preserved. Only empty
preview folders are removed; there is no recursive folder deletion.

`output/cleanup_incomplete_report.json` records the exact planned/deleted paths,
file bytes, skipped versions and retained files. Changed or missing passed proof
prevents deletion. A changed checkpoint aborts remaining cleanup; an interrupted
cleanup can repeat the same command. Reported bytes are file lengths, not a
measurement of free disk space. Relocate a moved output successfully before
using this script. No renderer processes should run against the same output
while cleaning.

**Move the existing run to another computer without losing completed scenes:**

1. Stop the laptop command with Ctrl+C and wait for the checkpoint-saved message
   and command prompt. Never copy a live SQLite checkpoint while workers run.
2. Copy the entire project folder, including **all of `output`**, to an external
   drive or directly to the desktop. Include the whole `output/batch` directory
   and any `tests.sqlite3-wal`/`tests.sqlite3-shm` files present. Avoid making a ZIP
   on a nearly full laptop; copy directly. Keep the laptop copy for now.
3. On the desktop, install Python and put the latest program files into the copied
   project, keeping its output folder. Reinstall requirements there rather than
   reusing a laptop virtual environment. A short folder on a drive with plenty of
   space, such as `D:\Gardens`, also helps Windows filename limits. Use a fully
   local folder outside OneDrive or other synchronized folders. If the copy is
   currently in OneDrive, select **Always keep on this device** and wait until
   all files finish downloading before copying the project to the local folder.
4. Open Command Prompt in that project folder. Enter each command separately;
   wait for it to finish successfully before entering the next one:

```cmd
py -m pip install -r requirements.txt
```

```cmd
py gardens_renderer.py --relocate-output --output output
```

Only when relocation reports `relocated` or `unchanged`, resume:

```cmd
py gardens_renderer.py --update --include-pending --output output
```

Relocation detects the laptop's saved root automatically. It verifies the copied
artifact hashes for all passed results, checks SQLite integrity, creates a
self-contained checkpoint backup inside the **desktop copy**, then rebases saved
operational paths in one transaction. Original images/JSON artifacts and their
hashes stay unchanged. No old laptop paths are opened or written. Windows drive,
UNC and POSIX paths are recognized. `--plan-only` verifies/previews relocation
without changing checkpoint rows or creating a backup.

`--include-pending` explicitly adds saved pending/running jobs to the usual
incomplete/new update selection. Passed images stay skipped even when computer,
Python version, paths or code differ. Failed/blocked jobs retain their existing
status. Without this flag, `--update` intentionally leaves previously pending
work alone. Do not use a full `--test-all` as the transfer resume command: that
mode invalidates older proofs when runtime/code/settings change.

A relocation mismatch stops before any checkpoint-path changes; recopy a missing
or damaged file and retry. Keep the laptop copy until relocation verification and
resumption succeed on the desktop. The latest report and unique pre-relocation
SQLite backup are under `output/batch`. Historical absolute strings inside
hash-protected JSON artifacts are retained; refreshed reports/exports use the
rebased ledger. External named-export folders must be copied separately or
regenerated. Allow additional desktop space for named image copies.

If SQLite reports **disk I/O error**, stop before updating. Check available disk
space and that every copied file is fully local and writable. Keep the original
copy and the entire `output/batch` directory, including any SQLite WAL/SHM files;
do not delete the checkpoint or create a fresh run. Try relocation from the
fully local project folder outside OneDrive. If it still fails, stop and retain
both copies for inspection. Any new transfer from the stopped original run should
go into a new empty local project folder, preserving the failed desktop copy.
The message alone cannot distinguish an incomplete transfer, synchronization,
permissions, or storage problems.

Updates, selective retries, and named exports check the existing database and
saved output roots before downloading any catalogues. A checkpoint that still
points to the old computer must be relocated successfully first. Updates and
retries also probe SQLite write access without changing any scene/job records.
These checks do not repair a damaged or inaccessible database, and storage can
still fail later during a run.

To process a few pending scenes per invocation, including every matched version
of each selected scene:

```cmd
py gardens_renderer.py --test-all --test-limit 4 --output output
```

Repeat to advance to the next pending scenes, or omit the limit to finish the
roster. `--plan-only` builds/exports the full roster without running workers;
`--offline` requires already verified cached catalogues, bundles and Item Manager.

A full run can take hours and use many GB for assets and PNGs. The indexed bundle
sizes alone total about 3.2 GB; rendered images require additional space. Exit `0`
means every test passed (or planning succeeded with `--plan-only`); `1` means
pending, blocked, incomplete or failed tests remain; Ctrl+C returns `130` with
checkpoints saved. Setup/input errors return `2`.

Development validation covered the full roster and a real four-scene/five-version
smoke run: four variant snapshots passed and the FTD variant was correctly
incomplete. The entire corpus has **not** been runtime-tested here. See the
[smoke report](docs/validation/all_scene_runner_smoke_2026-11-03.json).

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

The empty and complete scenes are **1280 x 758** with **68 identified/visible hidden objects**.
The solution adds a short numbered pointer beside each verified object and a
numbered index on the right with the exact Item Manager names. Pointer strokes,
arrowheads and number badges are checked for crossings and overlaps. It keeps
the scene at native scale on a wider canvas; Giza's annotated solution is
**1828 x 1254**. Labels use `beginner`, falling
back to `gold` only if the beginner label is empty. The report records both canvas
dimensions and the native scene rectangle.

The renderer decodes all 448 GAF frames and selects frame 80, the earliest frame in
which every named scene object reaches its maximum encoded opacity. Mutually exclusive animation
cells are not combined. Five time capsules are excluded. Both background art
and foreground occlusion pieces are retained; a second background-named piece
is not blindly discarded as a duplicate. Solution outlines use each object's
visible pixels after masking and occlusion. Arrow tips also land on visible
object pixels; label boxes are checked for overlap and bounds. Verified clue
labels come from Item Manager. `solution_objects.json` records names,
visible-pixel anchors, pointer endpoints, label boxes and label provenance.

`--frame 1` selects an exact one-based snapshot instead. `--offline` reuses
hash-verified bundles, catalogues and Item Manager JSON after an online run.
Use `--test-all` (or `--all --render`) for isolated, resumable tests across every
scene and version. Unsupported results remain explicitly incomplete.

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
sprite transforms, pivots, element scale, color transforms, transparency,
alpha masks and **coherent nested poses**. Nested scenes use encoded root frame 1,
composing parent transforms, opacity, masks and child depth. A general selector
can choose one complete encoded child pose that better fills enclosed cutouts in
stationary artwork. It does not resize, paint or omit parts. Unsupported animation
features keep the initial pose, with the reason recorded in the validation report.
A stop action with no target/arguments at the initial frame is accepted because
it does not change that pose. GAF parent colour transforms and sprite colour-matrix
filters, box blur and outer glow are supported. Elapsed-time playback, other
actions, drop shadows, inner/knockout glow, custom Unity
geometry or unresolved labels remain incomplete. Serialized Unity colour overrides remain unsupported.
Victorian now passes the snapshot checks with 69 named objects and eight nested
instances across its nine timelines. Its existing child poses remain unchanged
under the general selector. FTD/STD/FTO rendering remains unimplemented. See
[nested pose selection](docs/nested-pose-selection.md) for scope and validation.

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
matching. Alternative bundle/dependency sets or incompatible provider options
remain marked `ambiguous`, but **every resolved candidate is downloaded and, when
requested, rendered separately**. No version is silently chosen or discarded.
Identical URL/options sets across catalogues are equivalent. Dependency keys are resolved in their own catalogue.
Missing dependencies, cycles and unresolved runtime properties prevent a match
from being certified. A partial catalogue run is provisional and downloads no
scene bundles until the required catalogue coverage is complete.

HTTP relative paths resolve against the actual catalogue URL; Unity internal ID
prefixes are expanded. Properties such as `{RemoteRoot}` need verified mappings:
`--property "RemoteRoot=https://verified-host/path"`. Local runtime paths fail
closed. Unsupported catalogue formats are reported and their downloaded bytes
retained; only Unity's packed Addressables JSON format is currently implemented.
The packed JSON decoder has been validated against all 13 supplied live catalogues.

Exit codes: `0` means all selected scenes have resolved candidates and every
requested version downloaded/validated (and requested snapshots passed their checks); `1` means
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

The latest small sample also passes for Shadow of the Pyramid (67 objects),
Italian Scene (75), and both separately tested PyramidSideEgypt variants (49).
Those two Pyramid variants produce pixel-identical images for the tested pose;
the default catalogue selection remains ambiguous. See the portable
[experiment report](docs/validation/ordinary_sample_2026-11-03.json).

Next: broaden scene validation, implement elapsed nested playback where needed,
and resolve competing bundle versions before batch reconstruction. FTD follows
ordinary scenes; STD, FTO and Crystal assets remain later work.

## Reproduce the small-sample experiment

```cmd
py gardens_renderer.py --scene AncientGizaShadowOfThePyramid --scene ItalianSceneRedux1 --render --output output/small_batch
py render_scene.py --scene VictorianExteriorRedux1 --bundle samples/victorianexteriorredux1_assets_all_252d23914543848da6dcbc8871217a16.bundle --output output/small_batch
```

The standalone Victorian command uses the uploaded reference bundle. The main
CLI now also supports all of its matched versions directly:

```cmd
py gardens_renderer.py --scene VictorianExteriorRedux1 --render --output output
```

For multiple matches, each distinct URL/options set gets a stable output folder:

```text
output/scenes/VictorianExteriorRedux1/
  variants.json
  variant_8249e17279e07891/
    scene_empty.png
    scene_complete.png
    scene_solution.png
    validation_report.json
  variant_c8bf2b6de04fdf71/
    ...
```

The IDs shown are from the validated Victorian run. Folder IDs depend on verified
URLs/options, not catalogue enumeration order. Each version has its own images,
validation status and source mapping in `variants.json` and `scene_matches.json`.
Equivalent catalogue locations are merged; genuinely different matched versions
are kept, even when their rendered images are identical. Unique matches retain
the original `output/scenes/<scene_id>/scene_*.png` paths.

If one version fails or is unsupported, the others still run. The scene reports
`partial` when some snapshots pass; the command returns a nonzero exit status
unless every requested version passes. Shared bundles are downloaded once and
checked against each candidate's provider options independently. Rendering still
can use explicit `--scene` selections or the all-scenes test mode below.
Unsupported multi-bundle assembly or scene types stay incomplete. Earlier files at the scene root are preserved; use
the latest `variants.json` to locate current outputs.

PyramidSideEgypt's original diagnostic trial likewise tested both versions. The
evidence JSON records both bundle hashes and the limited image-comparison scope.

Background names accept the observed `bg01`, `bg_01`, and `bg_background` conventions. Item Manager
binding is exact after Unicode case-folding plus the existing underscore suffix
rule; a case collision still fails as ambiguous. Original keys/labels are retained.

Six [reviewed source aliases](docs/validation/reviewed_item_aliases.json) cover
confirmed naming differences in chapters 502, 509, 539, 560, 614 and 678. For
example, Easter Festive Mall's manager calls the Beach Ball `colourfulball`,
while both its GAF and Unity object data spell it `colourfullball`. Each exception
requires the exact reviewed bundle hash, canonical Item Manager hash, complete
manager key, object ID and unique encoded name. It is used only when ordinary
matching finds no candidate. Ambiguous matches, changed sources, missing labels
and conflicting assignments still fail; other spelling errors are never guessed.
Applied exceptions appear in `item_binding_overrides` and the object's metadata;
the original Item Manager label is used unchanged in the solution.

The [Corner Office investigation](docs/validation/corner_office_fashion_backstage.json)
adds a seventh reviewed match: `tableclock_cor_off_ini` identifies the animated
clock `anim_clock01` (object 152). Its exact source snapshot now renders all 70
clues, removes the clock from the empty image and labels it Table Clock in the
solution. FashionShowBackstage still has two clues entirely outside the encoded
1280×758 stage: Flip Flop (105) and Feather Boa (148). Its sole root frame and
serialized Unity transforms agree on those positions. All 70 clues match the
Item Manager, but only 68 are visible, so that version remains incomplete until
verified visible placements are available.


The next-build ordinary-scene fixes also decode GAF atlas v3 (tag 15), including
rotated sprites and separate horizontal/vertical scales, and intersect chained
masks with cycle detection. Identical serialized child-clip instances may share a
snapshot only when their settings, frame and object overrides agree. Referenced
GAF child timelines without serialized Unity clips use the encoded runtime
resource template; the validation report records this in `scope_source`.

Item Manager animation descriptors accept case variants and the observed
`animprojectorbeam` form when its base matches a named animation. Findable labels
require exact encoded key bindings or the reviewed source aliases above; the displayed Item Manager label can
differ from the encoded name (for example, `lemon` is labelled "Orange Slice"
in Hourglass Study). Unknown named visuals remain in the previews and in
`extra_named_objects` (also retained as `unresolved_named_objects` for older
readers). They produce warnings rather than blocking completion. Every findable
Item Manager entry must still be matched and visible, and unsupported rendering,
missing assets and invalid coverage still block a pass. Both verified clues and
unverified named objects are omitted from the empty image and retained in the
complete and solution images. Unverified objects keep their original art in the
solution, without orange outlines, pointers or labels. Only verified Item Manager
clues are highlighted and labelled. Unknowns are not counted as verified hidden
objects and never receive guessed official labels. Failed rerenders remove
stale final image filenames, keeping previews under `incomplete/`.

`solution_objects.json` contains verified clue callouts only. Reports retain
`unverified_named_pointers` for compatibility; new renders set it to zero, and
`named_pointers`/`total_named_pointers` both count the verified callouts.
`visible_unverified_object_ids` and `objects.json` retain visibility metadata for
unverified objects without labelling them. Their roles remain unconfirmed;
they are not asserted to be findable clues. `item_manager_complete`,
`findable_item_manager_entries` and `matched_item_manager_entries` report the
required-clue checks, while `warnings` records extra objects separately.
The empty-image exclusion policy and object IDs are recorded in
`empty_policy` and `empty_excluded_object_ids` in each validation report.
The [numbered-solution validation](docs/validation/numbered_solutions.json)
checks the new layout and completion rule in both Hourglass Study bundles and
Suzhou Gardens, with the extra objects retained as unmarked art.

Wait until an active Windows run finishes before replacing its code. After
updating, use `py gardens_renderer.py --retry-incomplete --output output` to
change only its incomplete variants while preserving other scene results.


Spatial filters use the reference Unity compatibility path's 32 uniformly spaced
bilinear box taps in atlas texels, premultiplied RGB, transparent padding, and
Adjust Color before Tint/object alpha. This is a CPU snapshot implementation;
comparison with the game's GPU output remains outside structural validation.
Filter details are recorded in `filter_backend`. The format/filter reference is
[StarlingGAFPlayer revision b738b19](https://github.com/CatalystApps/StarlingGAFPlayer/tree/b738b199245d6ca5e374d04ca2d4f6edd1767990/gaf/src/com/catalystapps/gaf).

When several instances have the *identical encoded name*, each gets the verified
Item Manager label and its own pointer. Distinct spellings that collide after
case folding remain ambiguous. Off-stage targets record their `encoded_bounds`
and remain incomplete; the renderer does not reposition them.

The [ordinary-scene regression report](docs/ordinary-scene-recovery.md) records the
151-version next-build test, its fixed cases, and every remaining scene grouped
by cause. It is not a claim that the full catalogue has passed.

The [live Chapter 748 update check](docs/incremental-updates.md) records discovery,
three ordinary-scene reconstructions, and preservation of passing outputs on a
repeated update.


The GAF decoder also supports the official uncompressed `FAG\0` signature as
well as compressed `CAG\0`. This fixes the reported 5.9 signature rejection;
length, compression, tag and scene-validation checks remain active. Keep a
running batch on its original code and use the updated `--update` command after
it finishes to retry those incomplete scenes from verified cached bundles.

The [7 November report analysis](docs/validation/ordinary_report_2026-11-07.md)
lists every remaining ordinary-scene version with its official name and cause.
Real-bundle follow-up tests verified 21 additional passes: 18 numbered-object
prefix links, one active-prefab root selection, one explicitly clamped atlas edge,
and one numeric background-copy name. Missing labels and positions remain
validation blockers. Use `--update --output output` after installing this build
to retry incomplete versions while preserving previously passed renders.
