# Ordinary-scene recovery validation — 2026-11-04

Retested **151 bundle versions across 135 ordinary scene IDs** from the uploaded progress report. FTD, STD and FTO are excluded. This is the affected regression set, not the full catalogue.

**97 versions pass; 54 remain incomplete.** 87 scene IDs pass in every affected version; 0 pass in some affected versions; 48 pass in none.

Implemented atlas v3/rotation and anisotropic scale; equivalent or runtime-created nested clip scopes; parent colour and filter cascades; colour-matrix, box-blur and outer-glow effects; chained masks; animation descriptor matching; verified labels on identically named instances; expanded background naming; explicit off-stage bounds; stale final-output invalidation. No missing labels or coordinates were invented.

The remaining cases are release blockers for any claim that every ordinary scene passes. Unknown named artwork stays in incomplete previews. A missing Item Manager entry does not prove that artwork is decorative. Off-stage objects retain their encoded position. A bundle with no embedded GAF cannot be reconstructed by the current GAF renderer.

## Remaining causes

Groups can overlap. Counts below are unique scene IDs; individual version results and exact issues are in the linked evidence JSON.

### Named objects without verified Item Manager entries (45)

`VictorianSceneRedone`, `ChinaGardenRedux1`, `HangingGardensofBabylon`, `TowerOfBabel`, `ItalyWoodenCarriageInterior`, `AncientGreekTemple`, `GardenOfHesperides`, `victoriantraveltrunk`, `AlAzharUniversity`, `BattlefrontFrance1812`, `DaVinciStudioRedux1`, `ChinaRoyalPalaceRedux1`, `AuroraBorealis`, `ColosseumEmperorsBox`, `WildWestTown`, `Varanasi`, `TajMahal`, `GreatTrainRobbery`, `SheriffsOffice`, `GoldMine`, `SydneyHarbor`, `Uluru`, `GwensBalcony`, `AbandonedItalianScene`, `WinterPalace`, `PirateTavern`, `KremlinWall`, `PirateCave`, `NewYorkCityStreet`, `Neuschwanstein`, `MaidMariansTower`, `DanishBakery`, `PrehistoricCave`, `SagradaFamilia`, `VanityTable`, `BenFranklin`, `TravelingCircus`, `PaintedLadies`, `PotolaPalace`, `SkiLodgeInterior`, `CharlesBridge`, `Fondue`, `ReversibleDestinyLoft`, `HarajukuStreet`, `AliBaba40Thieves`.

### Bundle without embedded GAF asset (1)

`DaVinciStudioRedux1`.

### Required objects placed outside the encoded stage (2)

`PirateTavern`, `FashionShowBackstage`.

### Unresolved Item Manager binding (1)

`CornerOffice`.

### Required objects without visible pixels (1)

`TubbatahaReef`.

## Evidence and rerunning

The final automated suite passed all 75 tests, including the real Giza/Victorian renders and new atlas/filter/mask/metadata regressions.

[Machine-readable results](validation/ordinary_scene_recovery_2026-11-04.json) include chronological order, variant ID, original/current issues, source URLs, input hashes and renderer identity. All 286 required bundle/Item Manager URLs downloaded successfully for this regression set. Each render ran in an isolated process with a 300-second timeout; the main rerun used four workers, with up to two additional workers for targeted retries after the parent-filter fix.

Validated means structural/metadata/coverage checks pass, with visible labelled object pixels. It does not establish pixel-for-pixel equivalence with the game GPU. Filter sampling uses the documented CPU Unity compatibility implementation.

After an existing Windows test run has finished, update the repository and run:

```cmd
py -m unittest discover -s tests -v
py gardens_renderer.py --test-all --retry-failed --output output
```

Renderer changes invalidate earlier checkpoint proofs. The updated build can therefore retest previously passing scenes. Keep the current run on its original code until it finishes.
