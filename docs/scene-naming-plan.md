# Official scene-name exports

The naming pass follows reconstruction and validation. It is now included at
the end of `--update`, and is available separately:

```cmd
py gardens_renderer.py --export-named --output output
```

It creates independent copies under `output/named_scenes`, leaving source PNGs
and checkpoint rows unchanged. Run it after the current batch finishes. It never
reconstructs scenes or changes their validated source paths.

The official English localization resources are explicitly listed in the
production `catalogAssets_en_us` asset:

- `xml/en_us/text_manager_scenes.json`: full scene titles keyed by scene ID.
- `xml/en_us/text_manager_missions.json`: chapter-list titles keyed by mission name.
- `xml/en_us/text_manager_stories.json`: displayed chapter names keyed by story name.

Their verified CDN URLs use the same catalog root as the corrected spreadsheet.
The 2026-11-05 audit found official scene titles and unambiguous first-primary-
chapter mission titles for all **4,481** corrected scene IDs. They are different
names for **107** entries, so the mapping retains both fields rather than treating
a CamelCase identifier as a display title.

The default is the name in the chapter list. Examples:

| Scene ID | Chapter-list name | Full scene title |
| --- | --- | --- |
| AncientGizaRiverfront | Giza Riverfront | Ancient Giza - Riverfront |
| VictorianExteriorRedux1 | Time Manor | Victorian Manor Exterior |
| VictorianSceneRedone | Hourglass Study | Victorian Scene |

For naming, follow each primary story's explicit mission membership and numeric
mission `sort_order`, then join its `scene_id` to the scene table. Keep the first
primary-chapter occurrence to match the corrected CSV's replay deduplication.
This reproduces the CSV order without inversions. Mission ID list position alone
is unreliable in older chapters and is not used as display order.

One replay has two primary-chapter titles: RideOfTheValkyries is "Ride of the
Valkyries" in Chapter 107 and "Ride of the Valkyries Flashlight" in Chapter 117.
Its corrected first occurrence takes the Chapter 107 title. There are no tied or
conflicting earliest primary mission choices in this audit.

Final filenames are `<official title> - Empty.png`,
`<official title> - Complete.png`, and `<official title> - Solution.png`, grouped
by chapter and scene order. Distinct matched bundle versions need distinct
version folders; repeated display titles must not overwrite each other. Preserve
the exact title in the index and replace only characters Windows disallows in a
filename. Internal scene and variant IDs remain the stable update/checkpoint keys.

The exporter reads active passed variants from the SQLite checkpoint in read-only
mode. All three canonical PNGs must have the original recorded SHA-256 and valid
PNG structure. Missing/ambiguous names and broken proof hashes are skipped with
explicit issues; incomplete/failed/pending variants never enter the export.
Original bundles are not required just to copy already validated PNGs.

Named copies have separate file identities, so editing one does not edit its
source. Existing identical copies are reused without touching their modification
times. Only unchanged copies owned by the previous export manifest may be
replaced; user-edited or conflicting files remain in place with a reported issue.
Old aliases are not deleted automatically. Symlink/junction redirection and
export locations overlapping source/checkpoint/cache folders are rejected.

The export is grouped by chapter, corrected scene order and stable variant ID.
Windows reserved names, forbidden characters and UTF-16 path limits are handled;
folder labels are shortened first on deep paths, and the exact title remains in
the manifest. Use `--named-output` for a separate, shorter destination if desired.

`scene_names.json` records the metadata join and source hashes. The named folder's
`named_scene_index.csv` and `named_export_manifest.json` record filenames, original
IDs, variant identities, hashes and historical validation fingerprints. The outer
`naming_report.json` records the complete or partial export outcome. Plans and
interrupted updates do not start an automatic naming pass.

On 2026-11-05 the live metadata loader mapped 4,487 scenes (the corrected 4,481
plus all six Chapter 748 entries), with zero missing names. Nine PNGs from the
three validated ordinary Chapter 748 scenes exported successfully; the repeat
check and automatic update check are recorded in
[validation evidence](validation/official_name_export_2026-11-05.json).
