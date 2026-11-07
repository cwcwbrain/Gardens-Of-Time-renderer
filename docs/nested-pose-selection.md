# General static pose selection

Previously every nested animation used its first encoded frame. That can show a
poor intermediate join: a moving part may not fully cover a cutout in the static
body artwork. Highland Games exposes this as a circular edge around the piper's
cheek.

The general selector identifies enclosed transparency in stationary sprites and
checks whether moving artwork substantially fills it. It evaluates complete
encoded child poses, selecting the earliest pose with the smallest remaining
alpha deficit. Initially visible named parts must remain present and at least
as opaque. The algorithm has no scene-name, object-ID, bundle-hash or fixed-frame
exceptions. It never paints pixels, alters encoded transforms, omits animation
families or combines cells from different frames.

Only supported leaf sprite animations are considered. Masks, actions, filters,
tints, text, nested containers and inherited compositing states retain their
initial pose. Bounded frame, pixel and work budgets prevent unbounded analysis;
a budget limit retains frame 1 rather than publishing a partially searched pose.
The renderer records the choice, score, supported regions and decline reason in
`nested_pose_selections`, alongside `pose_selection_version`.

The Highland Games regression automatically selects encoded child frame 20;
that number is a result of the search, not a production rule. It retains all
59 verified clue bindings. The Giza and Victorian sample poses remain unchanged.
This is a static snapshot policy, not elapsed-time game playback or independent
GPU pixel-parity validation. The separate Loch Ness water-layer issue is not
resolved by this rule.

[Real-source checks](validation/highland_games_cheek.json) include the complete
Highland Games reconstruction and a generic saved-output review that changed
Highland Games while preserving Corner Office and Giza files and checkpoint rows.

## Reviewing an existing output

`--finish-all --output output` includes the pose review. Alternatively:

```cmd
py gardens_renderer.py --update --refresh-poses --output output
```

The review checks saved passing nested scenes using unchanged, verified cached
inputs. It stages a new reconstruction and checks source identity, root frame,
dimensions and every verified clue binding. If all three images have identical
pixels, their files and the scene's checkpoint row remain unchanged. The external
analysis cache records the checked source-validation hash and selector version.

When pixels change, only changed images and necessary metadata are published
through a recovery journal. Identical files retain their hashes and timestamps.
Missing or edited inputs are preserved and reported. Interrupted publication is
recovered before continuing; interrupted analysis is periodically checkpointed.
Named exports are refreshed afterward.

`pose_refresh_report.json` records actual `changed_scene_ids`, per-version
outcomes and cached analyses. `--plan-only` reports candidates without rendering
or publishing. A plan cannot claim which images will change before comparison.
