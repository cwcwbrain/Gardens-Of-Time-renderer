# GAF snapshot implementation notes

The renderer consumes embedded GAF bytes directly from the uploaded Unity bundle.
It does not infer positions by scanning for plausible float sequences. Format
references consulted (Cocos2d's public GAF reader/runtime):

- https://github.com/cocos2d/cocos2d-html5/blob/develop/external/gaf/Library/GAFTags.js
- https://github.com/cocos2d/cocos2d-html5/blob/develop/external/gaf/Library/GAFDataReader.js
- https://github.com/cocos2d/cocos2d-html5/blob/develop/external/gaf/Library/GAFAssetPreload.js

The decoder supports the official little-endian signatures `CAG\0` (zlib
compressed, integer `0x00474143`) and `FAG\0` (uncompressed, integer `0x00474146`).
The previously accepted `GAF\0` spelling remains a raw compatibility alias. The
body layout is unchanged between compressed and uncompressed version 5.9 assets.
A real compressed Yucatan payload and its inflated FAG wrapper decode to the
same complete Movie in regression tests; malformed wrappers remain rejected.
Declared lengths must match the body or include the ten-byte header.
All fields and tag payloads are bounded and consumed exactly. The decoder handles
stage, timeline, atlas, sprite/mask definitions, named parts, sequences, frame
deltas, color transforms and actions. Unsupported tag IDs are retained as issues.
Unknown filter encodings fail closed. Frame deltas persist until changed; alpha
zero removes a display-list instance. Inactive alternative animation cells are
not missing placements and are never combined into one frame.

Atlas tables are scoped to timelines. The texture is selected by its atlas ID,
source name, shared-resource PPtr and matching Texture2D name. Sprite origins,
pivots and element scale come from the atlas element; the six-value frame matrix
then transforms that geometry into stage coordinates. Alpha masks are transformed
in the same coordinate system. Stage clear color comes from the stage tag.
No atlas-content score or black-region heuristic is used.

Atlas rectangles outside a texture are accepted only when the overflowing axes
explicitly use serialized Unity `Clamp` wrap mode. Actual border texels are
sampled before packed-sprite rotation, without changing coordinates, pivots or
the native texture dimensions. Unknown, Repeat or Mirror wrapping remains
unsupported for such rectangles. Sampling is recorded as
`atlas_sampling=serialized_clamp_edge` in the object report.

A single timeline not referenced by any type-2 timeline instance is the root.
When unused library timelines leave several graph roots, a unique active,
enabled, parentless Unity clip on the actual prefab container root may establish
the scene root. Local asset/GameObject references, component membership and an
explicit parentless Transform are required; missing or ambiguous evidence fails.
Library timelines remain in the decoded movie. The selected matching
Unity clip provides scale/CSF and its object metadata. The initial implementation
accepts scale=CSF=pixels-per-unit=1 with no root flip/pivot override. Nested initial poses are now rendered recursively with composed parent matrices,
opacity, masks and child depth. Empty atlas tables are allowed for timeline-only
containers. Serialized child clips must start at frame 1 with unmodified geometry
and color settings. Nested scenes require root frame 1; elapsed playback is not
simulated. Only empty-scope, no-argument stop actions at frame 1 are accepted.
Other actions, custom Unity sprite geometry/clip colors, inner/knockout glow,
drop shadows, and multi-bundle assembly remain unsupported. Their presence
cannot produce a validated result.

Non-animation Item Manager keys match encoded names exactly after case-folding,
or via an underscore-delimited scene suffix. Identical encoded names on several
instances receive the same verified label; distinct case-colliding names remain
ambiguous. Where both `umbrella` and `umbrella_1` exist, the longest exact named
prefix owns `umbrella_1_<scene>`; the shorter name is not an alternate alias.
Background names recognize bg, bg01, numeric repeats such as bg02_1, and the bg_ prefix. Ambiguous/unmatched keys and unclassified
named scene objects are issues. The real Giza metadata contains 68 findable-object
keys and five anim_ descriptors. timecap instances are excluded as gameplay art.
Root masks are not painted as visible sprites. Unnamed decorative cells remain
at their decoded position and depth; they are not invented as findable objects.

For non-nested automatic snapshots, maximize identified-object coverage, named-object
visibility, then the number of objects at their own maximum encoded opacity.
Choose the earliest frame with the best score. This supports intentionally
translucent art without inventing visibility. Giza selects frame 80. `--frame`
requests an exact one-based frame instead. Nested snapshots instead use the encoded initial frame throughout the tree.
The decoder always reads the entire file, independently of the chosen snapshot.

Background occlusion pieces are retained in depth order. Identical background
references/transforms are flagged for review rather than removed by filename.
The empty image omits identified hidden objects; complete includes them; both
retain decorative animation cells in the chosen snapshot. Solution contours
highlight only visible object pixels after masks and foreground occlusion.
Named callouts are laid out in separate side columns, preserving the original
scene scale inside the larger solution canvas. Arrow tips select actual visible
object pixels nearest the visible bounding-box centre, avoiding transparent
holes. Labels use the exact Item Manager beginner text, or gold when beginner
is empty. Label boxes are nonoverlapping and bounded. The solution report stores
scene-local anchors and solution-canvas label/pointer coordinates.

Labels, object IDs, visible bounds, per-object status and provenance hashes are
written in the output JSON reports.

Validation checks missing/ambiguous metadata, unknown features, atlas rectangles,
affine matrices, stage coverage, blank/uniform images, invisible required objects,
duplicate backgrounds and solution count. A failed result is written under
incomplete/, with status incomplete and a nonzero command exit code. The latest
report lists its current files. These checks validate a recovered static snapshot;
they do not establish pixel-for-pixel parity with the running game.


## Ordinary-scene recovery update

Atlas v3/tag 15 has separate X/Y scale, a rotation flag and linkage string after
the optional scale-nine rectangle. Rotated crops are restored before applying
the encoded pivot and scale. The real Yucatan fixture covers this format.

Repeated serialized child clips can share a timeline scope only when their
settings, internal frame and object overrides agree. Referenced timelines without
a serialized clip use the GAF runtime template and shared asset resources; the
report records this choice. Conflicting serialized instances remain incomplete.
Parent colour multipliers multiply and offsets add through the nested tree.
Masks may reference other masks; their alpha intersects recursively and cycles
fail validation.

The observed colour-matrix, blur and outer-glow filters are supported. Filters
cascade from parent timelines to child sprites (local effects followed by parent
effects), alongside the encoded transform and colour cascade. The CPU filter path
follows the reference Unity compatibility shaders: retain the last spatial and
Adjust Color records; use 32 bilinear box taps per axis in atlas texels; process
premultiplied RGB; pad transparent edges and adjust the pivot; apply Adjust Color
before Tint/object alpha. Glow uses the encoded BGRA colour, strength and opacity.
Inner/knockout glow and drop shadows stay unsupported. Software sampling is
reported explicitly and has not been independently compared pixel-for-pixel with
the game's GPU renderer.

Additional format/filter reference:
https://github.com/CatalystApps/StarlingGAFPlayer/tree/b738b199245d6ca5e374d04ca2d4f6edd1767990/gaf/src/com/catalystapps/gaf

Named objects missing from Item Manager remain validation blockers; their artwork
is retained in incomplete previews and no label is invented. Objects outside the
encoded stage are reported with their unclipped affine bounds. They are not moved
inside the viewport. Starting a rerender invalidates old final image filenames,
even if the new input fails before rendering.


The FAG correction addresses the reported `Unsupported GAF signature/version
b'FAG\x00' (5, 9)` error. It does not transform cached bundles or skip any scene
validation. Leave an active run on its original code; after it finishes, the
updated `--update` command retries affected incomplete jobs from verified caches.

The user reported this across scenes after Chapter 462. The two exact versions
available for the boundary probe (one ordinary scene each from Chapters 462 and
463, p9 catalogue) both use CAG and decode successfully. That probe does not
establish a universal encoding transition at the stated chapter. Exact affected
bundle identification remains useful for checking additional reconstruction
limitations after the header fix.
