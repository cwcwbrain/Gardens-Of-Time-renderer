# Scene changes — 2026-11-08

The user confirmed that the previous Windows run has finished. The general
nested-pose update is approved for release; all 434 regression tests passed.

| Scene | Internal ID | Change |
| --- | --- | --- |
| Highland Games | `HighlandGames` | The general selector finds a complete encoded bagpipe-player pose that fills the cheek cutout. All 59 clues are retained; no encoded transforms or pixels are altered and no artwork is omitted. |

This is a general rendering change with no scene-name, object-ID, bundle-hash or
frame-number whitelist. Other scenes with the same cutout-animation structure
are eligible. The actual changed scenes in an existing output are determined by
staging and comparing all three images; `pose_refresh_report.json` lists them.
The known regression cases and their outcomes are recorded separately.

The unreleased Loch Ness artwork-omission exception has been withdrawn following
the user's requirement for general fixes. Its water-layer issue remains under
investigation; it is not treated as solved by choosing a different nested pose.

Corner Office's Table Clock fix was already pushed in `d8798c1`. Fashion Show
Backstage still has two offstage clues and has no verified placement correction.

`--finish-all --output output` resumes the normal
workflow and reviews saved nested snapshots before exporting named images.
Images whose pixels remain identical, and their checkpoint rows, are preserved;
incomplete and newly discovered scenes remain eligible for the usual update work.
