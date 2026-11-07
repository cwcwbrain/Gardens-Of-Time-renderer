# Loch Ness water-layer investigation

The reported bands reproduce exactly in the current renderer's empty image and
also affect its complete and solution images. All 56 Item Manager clues match
and have visible markers; the structural checks did not detect this appearance
problem.

The broad pale rectangle comes from water-gradient object 122, using atlas
element 5 in `LochNess_2`. A smaller instance, object 113, appears near the horizon.
At the selected snapshot frame 29, wave cells 187 and 188 also draw bright water
strokes. The encoded gradient alpha repeats across rows, producing vertical
columns. Every crop lies inside its atlas. No GAF mask, color/filter effect,
custom material or external alpha texture establishes a different interpretation
of these layers in the checked bundle.

Unity's official Crunch decompressor, built from its `unity` branch at commit
`8708900eca8ec609d279270e72936258f81ddfb7`, produces exactly the same DXT5 bytes
as the installed decoder for all three textures. An independent Python BC3 alpha
decoder also agrees with UnityPy/Pillow across the entire second atlas. These
checks rule out a discrepancy in those decoding steps; they do not establish
whether the game applies an additional runtime correction.

A separate diagnostic preview omitted objects 113, 122, 187 and 188 at frame 29,
retaining all 56 findable clues, their original positions, all background layers
and the remaining scene artwork. This removes the bands and water-animation
strokes. It deliberately simplifies the static scene rather than recovering the
intended animation. The preview was labelled incomplete until the user explicitly
approved the clean static version on 2026-11-07.

An unreleased implementation applied that omission to the exact source bundle.
The user subsequently required general rendering fixes instead of scene-specific
exceptions. That implementation has been removed from the pending build.

The general nested-pose selector does not remove this water animation. The
water layers are part of a flat root animation, and no verified missing mask or
blend rule has been established from this bundle. This issue remains unresolved;
the clean diagnostic preview is not evidence that the intended animation has
been recovered. No automatic rule discards unnamed or pale decorative artwork.

Source hashes, layer coordinates, decoder comparisons and preview hashes are in
[the investigation record](validation/loch_ness_water_layers.json).
