# Find a scene on demand

Run from the project folder:

```cmd
py gardens_renderer.py --find-scene "Time Manor" --open --output output
```

Or start the prompt and enter scene names one at a time:

```cmd
py gardens_renderer.py --find-scene --open --output output
```

Type `q` to quit. The `--open` option opens the folder containing the images.
Without it, the command prints their locations. Existing named exports are
preferred when opening a folder.

## Names and existing images

Search accepts the official in-game chapter-list title, the official scene
caption, an internal scene ID or a verified replay title. It ignores letter case,
normalizes Unicode apostrophes and collapses whitespace. It does not silently
choose a similar spelling or a partial match. Suggestions are displayed for a
name that was not found.

If different scenes share a title, the command lists their IDs and chapters.
Run it again with the desired internal ID. Replayed appearances of the same ID
resolve to the original scene rather than creating another scene.

The finder checks known locations within the selected project output:

- Active passed batch images, including newer versions under `_new_versions`.
- Named exports, including a custom destination recorded by the main exporter.
- Individual scene renders with `scene_images_receipt.json` receipts.
- Previous successful recoveries in `output/misc`.

Automatic reuse requires all three PNGs and matching recorded hashes. Named
copies can be reused after their raw scene files or input caches are removed.
Untracked older PNGs without image proofs and cached bundles alone are not
treated as completed reconstructions. The finder does not search unrelated
folders across the computer.

## Recover missing images

When no verified copy exists, an online search refreshes the production catalogue
snapshot and attempts every matching bundle version. Official production
metadata can identify new primary-chapter scenes outside the original CSV. Name
enumeration does not fetch Item Managers for unrelated scenes; only the selected
scene's manager is requested and validated.

The finder reads verified main-cache inputs directly. Missing bundles, managers
and metadata go into `output/misc/_cache`. Main scene folders, corrected scene
order, the main checkpoint and main batch/naming reports are left untouched.

New images are organized as:

```text
output/misc/
  Suzhou Gardens--<scene-id-hash>/
    variant_<version-id>/
      scene_empty.png
      scene_complete.png
      scene_solution.png
      validation_report.json
      objects.json
      solution_objects.json
      worker_result.json
      worker.log
```

Directory titles are shortened when necessary to fit Windows paths; full names
remain in the index. Existing attempt directories are preserved. A retry after a
failure uses a new `_run_...` directory. Successful copies are reused on later
searches. Unchanged incomplete attempts are also reused as incomplete; updated
program code or changed inputs allow another attempt. Download `.part` files
remain resumable in the dedicated cache.

Use `--offline` to require already verified catalogue and asset caches. Online
missing-scene searches use the live production configuration. Ordinary scene
rendering remains the priority; unsupported FTD/STD/FTO scenes and genuine
missing-object or rendering failures stay incomplete. The console reports an
incomplete preview folder when images are available, and `--open` may open that
clearly labelled preview without claiming it passed.

## Misc indexes

- `misc_scene_index.json` records versions, attempts, status and image receipts.
- `misc_scene_index.csv` lists names, statuses and image locations.
- `last_lookup.json` records the latest recovery attempt.
- `_cache/finder_catalogue_report.json` records catalogue access and decoding.

Portable relative image receipts allow verified misc images to be found after
copying the output folder. The original worker paths remain as historical
provenance. Edited CSV indexes are preserved, with a warning in the JSON index.
A private lookup lock serializes concurrent recoveries without locking or
modifying the main batch checkpoint.

The [real finder validation](validation/scene_finder.json) records cached reuse,
real CDN downloads, both Hourglass Study versions and preservation of the main
run's files.
