# Ordinary-scene report — 7 November 2026

The uploaded report has 4,701 version rows across 4,487 scene IDs, with no pending or running rows. Its totals are 2,248 passed, 1,763 incomplete and 690 failed. All 690 failed rows are nonordinary Item Manager HTTP 403 errors.

Official primary-mission metadata identifies `ch452TourofTromsoADealInProgressLoop` as STD (mode 8), although its suffix escaped the runtime classifier. This analysis excludes it along with the other FTD/STD/FTO scenes. The runtime classifier has not been changed by this fix. `PrehistoricCave` and `WelcomeIsland` remain ordinary because their first primary missions use mode 1.

Ordinary scenes therefore account for **2,410 versions / 2,294 scene IDs**: **2,248 passed versions, 162 incomplete versions, zero failed versions**. There are **2,142 fully passed scene IDs, three partial IDs and 149 fully incomplete IDs**. The three partial Chapter 746 scenes have a passing version plus an alternate bundle without embedded GAF.

[Full ordinary incomplete CSV, including official names and exact issues](ordinary_incomplete_2026-11-07.csv) · [Machine-readable analysis and validation evidence](ordinary_report_2026-11-07.json)

## Causes in the uploaded report

Groups overlap: a version can have missing labels as well as an unresolved binding.

| Cause | Versions | Scene IDs |
| --- | ---: | ---: |
| Named visuals without verified Item Manager labels | 129 | 123 |
| Item Manager binding | 41 | 40 |
| Invisible required targets | 9 | 7 |
| Bundle without embedded GAF | 4 | 4 |
| Targets outside the encoded stage | 2 | 2 |
| Background identification | 1 | 1 |
| Atlas rectangle sampling | 1 | 1 |
| Root timeline selection | 1 | 1 |

## Verified changes in the next build

Exact reported bundles and Item Managers were downloaded for 22 ordinary versions. **21 now pass the renderer’s snapshot validation**; the Commander in Chief scene still has a missing `greencurtain` link and remains incomplete. All 289 regression tests pass. The 97 previously fixed versions are also still reported passed with matching variant IDs and bundle URLs.

| Change | Newly passing versions |
| --- | ---: |
| Longest exact named-object prefix (`umbrella_1` versus `umbrella`) | 18 |
| Root selection from explicit active prefab ownership | 1 |
| Recorded Unity Clamp sampling for an atlas rectangle extending five pixels beyond a texture | 1 |
| Numeric background-repeat name (`bg02_1`) | 1 |

The Danish Bakery classification correction leaves all three PNG hashes identical. Other fixes keep encoded coordinates and verified labels. Unused GAF library timelines are retained, not removed. Unknown texture wrapping still fails rather than guessing edge pixels.

| Chapter | In-game name | Scene ID | Verified change |
| ---: | --- | --- | --- |
| 27 | Danish Bakery | `DanishBakery` | numeric background repeat |
| 376 | Place of Power | `ch376KingdomoftheWoodsPlaceofPower` | longest exact named prefix |
| 436 | Ancestors Altar | `ch436LatinAmericanCultureForebearsAltar` | longest exact named prefix |
| 437 | Veterans Day Ceremony | `ch437VeteransDayTraditionsVeteransDayCeremony` | serialized texture clamp |
| 499 | Cozy Tea Time | `ch499TessasTroublingTimesCozyTeaTime` | longest exact named prefix |
| 556 | Whale Watching | `ch556CarefulWhoYouMakeDealsWithWhaleWatching` | longest exact named prefix |
| 573 | Independence Day Fireworks | `ch573AFourthofJulySpectacleIndependenceDayFireworks` | longest exact named prefix |
| 575 | Inside the Longhouse | `ch575SagaoftheNorthInsidetheLonghouse` | longest exact named prefix |
| 598 | The Heart of the Holidays | `ch598ChristmasPresentHuntTheHeartOfTheHolidays` | active prefab root |
| 664 | Petal to the Festival | `ch664NotPastNorFuturePetaltotheFestival` | longest exact named prefix |
| 673 | Trials of the Gods | `ch673TheFirstFractureTrialsoftheGods` | longest exact named prefix |
| 674 | Sleeping Cats Garden | `ch674HistoryWakesSleepingCatsGarden` | longest exact named prefix |
| 674 | Nine Lives of Literature | `ch674HistoryWakesNineLivesofLiterature` | longest exact named prefix |
| 685 | Port of Summer | `ch685VeneerofHistoryPortofSummer` | longest exact named prefix |
| 691 | Amber Light Council | `ch691TheSummoningGlassAmberLightCouncil` | longest exact named prefix |
| 692 | Twilight Hooves Crossing | `ch692ChoiceoftheAgesTwilightHoovesCrossing` | longest exact named prefix |
| 697 | Embers of Sherwood | `ch697TheMerryMarksmenEmbersofSherwood` | longest exact named prefix |
| 702 | Grove of Growing Gifts | `ch702ChristmasCrisisGroveofGrowingGifts` | longest exact named prefix |
| 706 | Barn of Sleeping Spots | `ch706MomentsBetweenChaosBarnofSleepingSpots` | longest exact named prefix |
| 734 | Viking Grove of Traditions | `ch734NoLongeranIdeaVikingGroveofTraditions` | longest exact named prefix |
| 742 | Prost in Moselle Valley | `ch742EchoesoftheCelebrationProstinMoselleValley` | longest exact named prefix |

There are still 141 reported ordinary versions not certified by these fixes. The CSV distinguishes verified retests from cases not rerendered. NBSP-padded `coconutdrink` and `firepit` names have no matching Item Manager entry even after whitespace removal; neither was assigned a guessed label. Missing labels, absent GAF data, invisible targets and off-stage objects remain incomplete.

## Apply without repeating passed work

Keep the completed output and checkpoint. Install the updated program files, then run:

```cmd
py gardens_renderer.py --update --output output
```

This preserves passed source renders, retries saved incomplete versions and discovers new chapters/versions. No pending jobs remain in this uploaded snapshot, so `--include-pending` is unnecessary for this run. Unsupported scene modes can still be reported incomplete.

Validation checks structure, metadata, coverage and pointer consistency. These checks do not establish independent pixel parity with the game’s GPU renderer.
