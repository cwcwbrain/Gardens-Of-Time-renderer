# Incremental chapter updates

After the current test run finishes and the program files have been updated:

```cmd
py gardens_renderer.py --update --output output
```

Keep the original output folder and `batch/tests.sqlite3`. The command retries
saved incomplete variants and discovers new scenes/versions while preserving
passed output files and checkpoint rows. It does not revalidate old passes under
the new renderer. `--retry-incomplete` remains the strict saved-roster-only option.

The update takes a fresh snapshot of the official GOT_RemoteConfig.json, refreshes
production HO catalogues even when their URL is unchanged, and reads the
production MainProject catalogue for `Prod/Group00_VO_SO`. Only its explicit remote
metadata bundles are needed. The local Unity MonoScripts dependency is reported
and omitted because decoding the embedded value type tree executes no game code;
other unresolved dependencies still block chapter discovery.

The metadata joins `storyVOs.mission_ids` to `missionVOs.mid`, then `scene_id` to
`sceneVOs.sid`. Chapter numbers and ordered mission references supply chronology;
catalogue enumeration does not. Future chapter-number gaps or malformed references
block unverified appends. Original CSV rows and saved scene ordinals stay intact.
Replayed scene IDs are deduplicated. A newly published variant of an existing ID
uses `scenes/_new_versions/<id>/variant_<variant>/` so its original folder stays in
place. New scene IDs use their own folders.

Item Manager paths are derived from the single convention evidenced by the
corrected CSV, then accepted only after validated JSON with object labels is
retrieved. An unavailable Manager or bundle receives a blocked chronological
placeholder. Only placeholders created by this update workflow can be retired
when a real eligible match later appears; legacy results stay untouched.

Each invocation freezes its discovery list. Repeat the command to pick up later
chapters; it does not add scenes midway through a running batch. `--plan-only`
registers the update plan without rendering; `--test-limit N` limits the selected
scenes. Offline mode uses verified caches and cannot detect newly published data.
Unavailable discovery resources and unsupported render features remain explicit,
and a normal update exits nonzero when newly required resources remain blocked.

## Live validation on 2026-11-04

The [machine-readable evidence](validation/chapter_update_2026-11-04.json) records
the official sources, exact variant IDs and image hashes. This was an isolated
smoke test, not a fresh reconstruction of all catalogue scenes.

- Chapter 748 supplied six scenes, appended at orders 4482–4487 in mission order.
- Veterans Breakfast Diner, Veterans Classic Car Drive and Main Street Salute
  passed snapshot validation with 60, 60 and 59 named object pointers respectively.
- The STD, FTD and FTO scenes remain incomplete because those modes are not
  implemented. Their availability is not presented as a successful reconstruction.
- All 4,689 pre-existing planned job rows remained unchanged.
- After a reporting-code change, a repeated offline update added no variants and
  preserved the three passing checkpoint rows and all nine passing PNGs, including
  file hashes and modification times. Preservation across a renderer identity
  change is also covered by automated integration tests.
- A covering SQLite index reduced the per-scene summary queries for this roster
  from 18.106 seconds to 0.009 seconds in the measured run.

These checks establish automatic discovery and preservation for the observed
production formats. Structural snapshot validation does not establish independent
pixel-for-pixel parity with the running game's GPU, or guarantee every future
asset format will be supported.
