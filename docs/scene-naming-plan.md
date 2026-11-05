# Official scene naming plan

The final naming pass follows reconstruction and validation; it does not rename
files under a running batch or invalidate passed scene checkpoints.

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

The proposed default is the name in the chapter list. Examples:

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

Proposed final filenames are `<official title> - Empty.png`,
`<official title> - Complete.png`, and `<official title> - Solution.png`, grouped
by chapter and scene order. Distinct matched bundle versions need distinct
version folders; repeated display titles must not overwrite each other. Preserve
the exact title in the index and replace only characters Windows disallows in a
filename. Internal scene and variant IDs remain the stable update/checkpoint keys.

The naming exporter is a subsequent step. The audit preview retains both official
name sets, chapter/mission provenance and IDs; it does not move or rename any PNGs.
A separate named export can preserve the validated source files and hashes.
