# Numbered solution images

New renders put a small numbered badge and short pointer next to each verified
object. The right-side index lists the same numbers and the original Item Manager
names in alphabetical order. Longer indexes use two contiguous columns. Yellow
object outlines remain; there are no index-to-object leader lines.

Every pointer tip uses an actual visible pixel after masking and occlusion.
When clue targets are very close, a different visible pixel on the same object is
chosen. Object positions are unchanged. Pointer strokes, arrowheads, number
badges and index rows are checked for intersections and overlap. An arrangement
that cannot satisfy those checks is reported rather than published as passed.

Only findable Item Manager entries need labels. Additional named scene objects
without manager entries are nonblocking warnings. They stay absent from the
empty image and retain their original art, without callouts, in the complete and
solution images. Missing required entries, invisible required objects, missing
assets, unsupported rendering and invalid image coverage still block completion.

## Upgrade an existing run

After the active command finishes, replace the program files, including the
`gardens` folder, while keeping the existing `output` folder in place. Run:

```cmd
py gardens_renderer.py --finish-all --refresh-solutions --output output
```

The command retries unfinished work, then upgrades older passing solutions before
refreshing named copies and their index. New-layout solutions are reused on later
runs. The normal command without `--refresh-solutions` preserves passing renders.
`--update --refresh-solutions` is also supported. Plan-only runs publish no layout
changes.

Upgrading a solution rechecks its exact cached bundle and Item Manager using the
recorded snapshot frame. Work happens in a separate staging directory. The staged
empty and complete PNG hashes must match the saved native image proofs, and the
verified object IDs, manager keys and labels must stay the same. The command then
publishes only `scene_solution.png`, `solution_objects.json` and
`validation_report.json`. Native images, `objects.json`, scene order, job status
and historical render fingerprints are preserved. A separate amendment records
the solution update without claiming the native files were recertified.

Progress is saved per variant. The command creates one checkpoint backup and
uses a small publication journal with temporary old-file copies for interruption
recovery. Repeating the command recovers an interrupted publication before
processing more variants. Temporary rendering can take time; existing native
images are not moved or duplicated into another output tree.

Edited source artifacts, missing cached inputs, changed native images or changed
bindings are preserved and reported as skipped. Named exports replace only
unchanged files owned by their export manifest; edited named copies remain
conflicts for review.

## Reports

- `solution_refresh_report.json` records upgraded, reused and skipped versions.
- `validation_report.json` records `solution_layout`, `annotation_issues`,
  `item_manager_complete` and any nonblocking `warnings`.
- `solution_objects.json` records each verified label, number, visible anchor,
  number badge, pointer route and side-index row.
- `test_report.csv` separates errors from warnings.

The [real-scene validation record](validation/numbered_solutions.json) records
the sample scenes, crowded-target checks and preservation of existing native
images.
