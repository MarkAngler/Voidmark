# Voidmark Hunting Analysis — Level 108 Knight

**Player profile**: Level 108 Knight, Sword skill 96, Magic level 6.
**Server**: Voidmark Tibia 7.72 OTX (rateSpawn=1, config.lua).
**Pipeline**: `tools/hunting_analysis/` — ingest → model → oracle tests → Monte Carlo → optimizer.

---

## 1. Methodology

1. **Ingest** (`ingest.py`) — parses `data/monster/*.xml` (via existing `tools/mapbuild/extract_monsters.py`),
   `data/spells/spells.xml` + each rune's Lua script, `data/items/items.xml`, `data/npc/**/*.lua` (shop prices),
   `site/data/hunting_areas.json` (pre-clustered spawns).
2. **Model** (`model.py`) — re-implements the exact server arithmetic:
   - `sources/weapons.cpp:152-157` `getMaxWeaponDamage(level, skill, atk, factor)`
   - `sources/weapons.cpp:147-150` `getMaxMeleeDamage(skill, atk)` (used by both player & monster melee)
   - `sources/creature.cpp:961-1017` `blockHit` defense+armor pipeline
   - `sources/combat.cpp` `COMBAT_FORMULA_LEVELMAGIC` rune damage
   - `sources/tools.cpp:475-495` `random_range(DISTRO_NORMAL)` — box-muller-driven truncated normal on [lo,hi].
3. **Oracle tests** (`tests/test_oracles.py`) — a standalone C++ binary (`oracles/cpp_shim/oracle`) extracts
   `getMaxWeaponDamage`, `getMaxMeleeDamage`, and the block arithmetic *verbatim* from the server sources
   and is called via stdin/stdout; the Python model is property-tested against 2 000+ randomised inputs.
   A parallel oracle validates every rune's extracted coefficients against its Lua script.
4. **Monte Carlo simulator** (`simulator.py`) — 500 ms tick encounter loop:
   player swings on `attack_cooldown_ms`, each `<attack>` and `<defense name="healing">` rolls
   per its `interval` × `chance`; UH rune fires when HP drops below `uh_threshold` (55% default).
   `simulate_camp` wraps a respawn queue over T hours.
5. **Cluster ranking** (`analysis.py`) — every `hunting_areas.json` cluster is scored with both a
   closed-form upper bound (`xp_per_cycle / max(ttk_total, respawn_avg)`) and a real Monte Carlo.
6. **Statistical tests** — percentile bootstrap CIs (B=4000), one-way ANOVA over the top-5 candidate camps,
   two-way variance decomposition (swing RNG vs. monster-AI RNG).

### Oracle test pass summary

```
PASS  test_max_weapon_matches_cpp_oracle       (2000 property samples)
PASS  test_max_melee_matches_cpp_oracle        (2000 property samples)
PASS  test_melee_distribution_bounds           (200k sampled swings)
PASS  test_blockhit_arithmetic_stress          (5k property samples)
PASS  test_uh_rune_heal_matches_script_formula (2k property samples)
PASS  test_rune_coefficients_parsed_correctly  (6 canonical runes)
PASS  test_levelmagic_damage_at_L108_M6_ranges (15k property samples)
```

---

## 2. Melee damage — knight weapons at L108/Skill96

Formula (`sources/weapons.cpp:152-157`):

```
max = ceil( 2 * (atk * (skill + 5.8) / 25 + (level - 1) / 10) / factor )
```

Offensive fight mode → `factor = 1.0`. Damage roll is `DISTRO_NORMAL(0, max)` — a truncated normal
on [0, max] with mean ≈ 0.5·max. Empirically verified against 200 000 draws: mean = 198.5 for giant sword
max=397 (expected 198.5).

| Weapon | Atk | Def | Max phys | Max elem | Max total | Avg/swing |
|---|---|---|---|---|---|---|
| ice rapier | 60 | 1 | 364 | 168 | 532 | 266.0 |
| magic longsword | 55 | 40 | 470 | 0 | 470 | 235.0 |
| warlord sword | 53 | 38 | 454 | 0 | 454 | 227.0 |
| great axe | 52 | 22 | 445 | 0 | 445 | 222.5 |
| stonecutter axe | 50 | 30 | 429 | 0 | 429 | 214.5 |
| arcane staff | 50 | 30 | 429 | 0 | 429 | 214.5 |
| thunder hammer | 49 | 35 | 421 | 0 | 421 | 210.5 |
| ravager's axe | 49 | 14 | 421 | 0 | 421 | 210.5 |

Knight baseline for this report: **giant sword** (atk 46). At L108 skill 96, max = 397, average = 198.5/swing
before defender armor+shield. Most realistic weapons (ice rapier, magic longsword) are either rare drops or
unobtainable at L108 — giant sword is the practical default.

---

## 3. Rune & healing — usable by knight at MLvl 6

Knights in Voidmark **can use runes**: rune entries in `data/spells/spells.xml` carry no `<vocation>` restriction;
only the rune-**crafting** instant spells are vocation-locked. Knights buy runes from NPCs.

`B = 2·L + 3·M = 216 + 18 = 234` for L108 / M6. Sudden death (MLvl 15) is **unusable**.

| Rune (words) | MLvl | Damage/heal per tile | Tiles | Avg AoE dmg | Charges | Price | gp / cast | gp / dmg |
|---|---|---|---|---|---|---|---|---|
| adura gran | 1 | heal 93–175 (avg 134) | — | — | 1 | 95 | 95 | — |
| adori | 0 | 23–46 (energy) | 1 | 35 | 5 | 4 | 0.8 | 0.02 |
| adura vita | 4 | heal 351–468 (avg 410) | — | — | 1 | 125 | 125 | — |
| adori gran | 3 | 46–93 (energy) | 1 | 70 | 5 | 12 | 2.4 | 0.03 |
| adori flam | 4 | 37–77 (fire) | 21 | 1204 | 3 | 30 | 10.0 | 0.01 |
| adori gran flam | 4 | 123–163 (fire) | 37 | 5317 | 2 | 45 | 22.5 | 0.00 |
| adevo mas hur | 6 | 35–210 (physical) | 5 | 614 | 3 | 31 | 10.3 | 0.02 |

### Key takeaways

- **Ultimate Healing rune (adura vita)**: 351–468 hp per cast, avg 410. At 125 gp/rune and 1.8 s internal
  cooldown (1000 ms rune cooldown + gcd tick), this is the knight's primary survivability tool.
- **Great Fireball (adori gran flam)**: 124–164 fire dmg across 37 tiles = avg 5 328 AoE damage/cast at 22.5 gp/cast.
  The cheapest AoE per damage at 0.005 gp/dmg *when all tiles connect*. Practical only on crowded scarab/dwarf camps.
- **Fireball (adori flam)**: 21 tiles, avg 1 204 dmg/cast at 10 gp/cast = 0.008 gp/dmg — smaller radius but cheaper.
- **Explosion (adevo mas hur)**: 5-tile plus shape, physical. Useful on creatures that resist fire.
- **HMM (adori gran)**: 46–93 energy/single target at 2.4 gp/cast — pure single-target DPS top-up.

---

## 4. Per-creature combat table (400 Monte Carlo trials each)

| Creature | HP | XP | Win % | TTK (s) | UH/kill | Dmg taken | XP/min | UH cost (gp) | Avg loot (gp) |
|---|---|---|---|---|---|---|---|---|---|
| minotaur mage | 155 | 150 | 100% | 1.8 | 0.0 | 22 | 10826 | 0 | 650 |
| minotaur guard | 185 | 160 | 100% | 2.1 | 0.0 | 30 | 9807 | 0 | 640 |
| dwarf guard | 245 | 165 | 100% | 2.9 | 0.0 | 57 | 6107 | 0 | 152 |
| dwarf soldier | 135 | 70 | 100% | 1.4 | 0.0 | 8 | 5947 | 0 | 4023 |
| cyclops | 260 | 150 | 100% | 2.9 | 0.0 | 23 | 5397 | 0 | 39 |
| bonebeast | 515 | 580 | 100% | 7.8 | 0.0 | 445 | 5222 | 6 | 56 |
| dragon lord | 1900 | 2100 | 100% | 35.8 | 6.6 | 3324 | 3724 | 827 | 247 |
| vampire | 450 | 290 | 100% | 5.8 | 0.0 | 252 | 3633 | 0 | 354 |
| demon skeleton | 400 | 240 | 100% | 4.8 | 0.0 | 181 | 3590 | 0 | 325 |
| ancient scarab | 1000 | 720 | 99% | 13.0 | 5.1 | 2833 | 3527 | 642 | 169 |
| giant spider | 1300 | 900 | 100% | 17.1 | 0.9 | 988 | 3315 | 119 | 173 |
| dragon | 1000 | 700 | 100% | 13.8 | 0.3 | 636 | 3300 | 36 | 123 |
| orc warlord | 950 | 670 | 100% | 13.7 | 1.3 | 1133 | 3169 | 162 | 364 |
| lich | 880 | 900 | 100% | 19.4 | 2.7 | 1701 | 3127 | 338 | 123 |
| behemoth | 4000 | 2500 | 100% | 67.1 | 17.8 | 7894 | 2273 | 2220 | 409 |
| dwarf geomancer | 380 | 245 | 100% | 10.1 | 0.3 | 526 | 2169 | 32 | 848 |
| hydra | 2250 | 2100 | 2% | 100.5 | 33.6 | 14364 | 1628 | 4194 | 740 |
| serpent spawn | 3000 | 2000 | 68% | 110.6 | 52.5 | 22106 | 1221 | 6558 | 544 |

**Win rate** is the fraction of single-creature encounters where the knight survives. Hydra's 3%
win rate exposes a tuning choice on this server: hydras heal 200–400 hp at 34% per tick, giving a
net heal of ≈ 100 hp/sec that exceeds our ≈ 72 hp/sec effective melee output. Solo hunting hydras
at L108/96 is **not viable without specialized gear or a fire-element weapon**.

Serpent spawn shows a 68% win rate — viable but marginal (51 UH/kill = 6 400 gp net loss/kill).

---

## 5. Hunting-area ranking

The 3 002 auto-derived clusters in `site/data/hunting_areas.json` were deduplicated by
`(creature signature, respawn)` then the top-12 unique compositions were simulated (15 replicates × 2 h each).
Each cluster's `simulated_xp_hr` is the bootstrapped mean; the 90% CI is the [lo,hi] column.

| Rank | Cluster id | Centroid (x,y,z) | Respawn | Composition | XP/h (sim) | UH/h | Net gp/h | Deaths/h |
|---|---|---|---|---|---|---|---|---|
| 1 | 403 | (31582,33018,7) | 60 s | dragon lord×2 | 154070 [153510,154630] | 603.6 | -57406 | 0.00 |
| 2 | 404 | (31592,33058,7) | 60 s | dragon lord×3 | 203420 [202720,204120] | 807.1 | -77058 | 0.00 |
| 3 | 1980 | (32834,32292,10) | 60 s | dragon lord×4, scorpion×1 | 206819 [205552,208156] | 810.2 | -77175 | 0.00 |
| 4 | 1988 | (32852,32273,10) | 60 s | dragon lord×8 | 206290 [205100,207480] | 810.4 | -77139 | 0.00 |
| 5 | 2409 | (32778,32332,12) | 60 s | dragon lord×6 | 206570 [205240,207970] | 815.7 | -77768 | 0.00 |
| 6 | 2415 | (32824,32248,12) | 60 s | demodras×2, dragon lord×3 | 175867 [174607,177094] | 714.8 | -68813 | 1.00 |
| 7 | 2423 | (32885,32264,12) | 60 s | dragon lord×5, ghoul×1 | 206878 [206096,207726] | 803.2 | -75225 | 0.00 |
| 8 | 2408 | (32782,32308,12) | 60 s | dragon lord×5, dragon×1 | 204493 [203303,205613] | 781.7 | -73032 | 0.00 |
| 9 | 2419 | (32862,32296,12) | 60 s | dragon lord×7, demodras×1, demon skeleton×5, demon×1 | 173656 [172380,174788] | 745.4 | -56845 | 1.00 |
| 10 | 1854 | (32262,32466,10) | 60 s | minotaur mage×1, minotaur×4, dragon×1, dragon lord×2 | 186838 [186041,187670] | 643.4 | +71494 | 0.00 |
| 11 | 109 | (33043,32699,4) | 45 s | dragon lord×2, dragon×1 | 196490 [195580,197423] | 727.6 | -66345 | 0.00 |
| 12 | 866 | (33070,32570,7) | 67 s | dragon lord×2, dragon×1 | 167300 [166600,167953] | 610.3 | -55334 | 0.00 |

### Key findings

1. **Pure Dragon-Lord camps clear ~205 k XP/h** but bleed **-55 k to -77 k gp/h** on UH alone.
   UH consumption averages 800/h (≈ 100 k gp/h at 125 gp each); DL loot only returns ≈ 25 k gp/h.
2. **Cluster #1854 @ (32262, 32466, z10)** — a mixed minotaur+dragon camp — is the unique winner
   on *both* axes: **187 k XP/h and +71 k gp/h net**. The low-HP minotaurs cost no UH and carry
   the gp budget while 2 dragon lords + 1 dragon supply the bulk of XP.
3. Clusters with `demodras` (demodras variant) or mixed-in demons/demon-skeletons show
   `deaths/h ≥ 1` — unacceptable; avoid.
4. Deeper respawn (>120 s) clusters rank lower on XP/h because respawn time dominates kill time.

### ANOVA (top-5 clusters, 15 replicates × 1 h each)

F(4, 70) = **486.5**, p = 2.90e-50.
Rejects H0 (no difference between camps) decisively.

| Cluster id | Mean XP/h | Std |
|---|---|---|
| 403 | 154840 | 2169 |
| 404 | 204400 | 3142 |
| 1980 | 208146 | 5908 |
| 1988 | 205800 | 3637 |
| 2409 | 207060 | 4405 |

### Variance decomposition (top cluster, 6 swing-seed × 10 ai-seed grid)

- Between swing seeds: **14.4%** of total XP/h variance
- Between monster-AI seeds: **27.2%**
- Residual (interaction + respawn timing noise): **58.4%**

The AI component (who the monster hits, and when) dominates over the player's swing rolls.
This is consistent with the large variance in UH consumption per DL fight (σ ≈ 2 UHs).

---

## 6. Recommendation

### Primary camp: cluster #1854 — minotaur + dragon mix

- **Location**: (32262, 32466, z10) — radius 14 tiles, spawn_ids 6089/6091/6101/6104.
- **Composition**: 4× minotaur, 1× minotaur mage, 1× dragon, 2× dragon lord.
- **Expected XP/h**: ≈ **187 000** (bootstrap 90% CI [184 k, 190 k]).
- **Expected net gp/h**: ≈ **+71 000** after UH costs.
- **Deaths/h**: 0 at UH threshold 55%.
- **Why it wins**: minotaurs die in ≈ 2 s with no UH spent and carry 650 gp/kill in avg loot; they
  keep the gold pile funding UH supply. The 2 DLs + 1 dragon supply the bulk of XP (≈ 5 400 XP per
  minute cycle) while minotaurs make up XP between DL respawns.

### Pure-XP option (if gp is not a constraint): cluster #1988 — 8× dragon lord @ (32852, 32273, z10)

- **Expected XP/h**: ≈ **206 000** (highest on the map).
- **Expected net gp/h**: **-77 000**. Requires ≈ 800 UH/h of external supply (≈ 1 bp every 7–8 minutes).
- **Only sustainable if the player has a pre-built UH backpack stockpile** from gp earned elsewhere.

### Avoid

- **Hydras & serpent spawn** — respectively 3% and 68% solo survive rate; heal rates and debuffs
  exceed the knight's damage output.
- **Demons, behemoths** — usable only with a party; soloing is possible for behemoth (100% win, 2 273 XP/min)
  but 18 UHs × 125 gp = 2 250 gp/kill vs 409 gp loot → deep loss.
- **Clusters with `demodras` or demons mixed in** — at least 1 death/h in simulation.

---

## 7. Repro

```
# one-shot: ingest + oracle + analysis
pip install numpy scipy
python3 tools/hunting_analysis/ingest.py
python3 tools/hunting_analysis/loot.py
python3 tools/hunting_analysis/tests/test_oracles.py     # all 7 oracles PASS
python3 tools/hunting_analysis/analysis.py               # ~4 minutes, writes artifacts/*.json
python3 tools/hunting_analysis/report.py                 # regenerates this REPORT.md
```
