/* =======================================================================
   HuntingCalc — Knight exp/hour statistical engine
   All formulas match the OTX 7.72 C++ server implementation.
   ======================================================================= */

const HuntingCalc = (() => {
    "use strict";

    /* ---- Server config constants (from config.lua) ---- */
    const TURN_MS = 2000;        // attackspeed for all vocations
    const TURN_S = TURN_MS / 1000;

    /* ---- Knight vocation constants (from vocations.xml) ---- */
    const KNIGHT_BASE_HP = 185;
    const KNIGHT_HP_PER_LVL = 15;
    const KNIGHT_BASE_MANA = 35;
    const KNIGHT_MANA_PER_LVL = 5;
    const KNIGHT_HP_REGEN_TICKS = 6;   // Knight: 6 ticks, EK: 4 ticks
    const EK_HP_REGEN_TICKS = 4;
    const HP_REGEN_AMOUNT = 1;          // 1 HP per regen event
    const MANA_REGEN_TICKS = 6;
    const MANA_REGEN_AMOUNT = 2;        // 2 mana per regen event
    const TICK_MS = 2000;               // 1 tick = 2 seconds

    /* ---- Exori (Berserk) constants ---- */
    const EXORI_MIN_LEVEL = 35;
    const EXORI_MAGIC_LEVEL_REQ = 5;
    const EXORI_MANA_FACTOR = 4;        // mana cost = level * 4
    const EXORI_AOE_TILES = 8;          // 3x3 area minus center

    /* ---- Rune definitions ---- */
    // COMBAT_FORMULA_LEVELMAGIC coefficients: (mina, minb, maxa, maxb)
    // Formula: BASE = level*2 + magLevel*3
    //          min_dmg = abs(BASE * mina + minb)
    //          max_dmg = abs(BASE * maxa + maxb)
    const RUNES = {
        gfb:       { a: -0.4,  b: -30, c: -0.7,  d: 0, element: "fire",     aoe: 37 },
        explosion: { a: -0.15, b: 0,   c: -0.9,  d: 0, element: "physical", aoe: 5  },
        sd:        { a: -1.25, b: -30, c: -1.7,  d: 0, element: "physical", aoe: 1  },
        avalanche: { a: -0.4,  b: -30, c: -0.7,  d: 0, element: "ice",      aoe: 37 },
        thunderstorm: { a: -0.4, b: -30, c: -0.7, d: 0, element: "energy", aoe: 37 },
    };

    /* ---- Healing method definitions ---- */
    const HEAL_METHODS = {
        food:    { label: "Food only",  usesRune: false, usesMana: false },
        exura:   { label: "Exura",      usesRune: false, usesMana: true, mana: 25 },
        ih_rune: { label: "IH Rune",    usesRune: true,  usesMana: false },
        uh_rune: { label: "UH Rune",    usesRune: true,  usesMana: false },
    };

    /* ================================================================
       DAMAGE FORMULAS
       ================================================================ */

    /**
     * Classic melee max damage (classicDamageOnWeapons = true).
     * Source: weapons.cpp Weapons::getMaxWeaponDamage
     */
    function calcMeleeMax(attackSkill, weaponAttack, attackFactor) {
        attackFactor = attackFactor || 1.0;
        return Math.ceil(
            ((attackSkill * (weaponAttack * 0.0425) + (weaponAttack * 0.2)) / attackFactor) * 2
        );
    }

    function calcMeleeAvg(attackSkill, weaponAttack, attackFactor) {
        return calcMeleeMax(attackSkill, weaponAttack, attackFactor) * 0.5;
    }

    /**
     * Berserk (exori) damage range.
     * Source: berserk.lua — COMBAT_FORMULA_LEVELMAGIC, -1.4, 0, -1.65, 0
     */
    function calcBerserkDamage(level, magicLevel) {
        const base = level * 2 + magicLevel * 3;
        const min = base * 1.4;
        const max = base * 1.65;
        return { min, max, avg: (min + max) / 2 };
    }

    function calcBerserkManaCost(level) {
        return level * EXORI_MANA_FACTOR;
    }

    /**
     * Rune damage using the caster's own level + magic level.
     * Source: Lua scripts — COMBAT_FORMULA_LEVELMAGIC coefficients
     */
    function calcRuneDamage(level, magicLevel, runeType) {
        const rune = RUNES[runeType];
        if (!rune) return null;
        const base = level * 2 + magicLevel * 3;
        const min = Math.abs(base * rune.a + rune.b);
        const max = Math.abs(base * rune.c + rune.d);
        return { min, max, avg: (min + max) / 2, aoe: rune.aoe, element: rune.element };
    }

    /**
     * Monster melee max damage (same classic formula, monster uses its own skill/attack).
     */
    function calcMonsterMeleeMax(skill, attack) {
        return Math.ceil(
            ((skill * (attack * 0.0425) + (attack * 0.2)) / 1.0) * 2
        );
    }

    /* ================================================================
       DEFENSE FORMULAS
       ================================================================ */

    /**
     * Player defense value.
     * Source: player.cpp Player::getDefense
     */
    function calcDefense(shieldSkill, shieldDefense, defenseFactor) {
        defenseFactor = defenseFactor || 1.0;
        const defenseValue = 5 + shieldDefense; // baseDefense = 5
        return Math.ceil(
            (shieldSkill * (defenseValue * 0.015) + (defenseValue * 0.1)) * defenseFactor
        );
    }

    /**
     * Armor damage reduction range.
     * Source: creature.cpp blockHit
     */
    function calcArmorReduction(totalArmor) {
        if (totalArmor <= 0) return { min: 0, max: 0, avg: 0 };
        if (totalArmor === 1) return { min: 1, max: 1, avg: 1 };
        const min = Math.ceil(totalArmor * 0.475);
        const max = Math.ceil(totalArmor * 0.95 - 1);
        return { min, max, avg: (min + max) / 2 };
    }

    /* ================================================================
       HEALING FORMULAS
       ================================================================ */

    function calcHealAmount(level, magicLevel, method) {
        const base = level * 2 + magicLevel * 3;
        switch (method) {
            case "exura":
                return { min: Math.max(15, base * 0.10), max: base * 0.35 };
            case "ih_rune":
                return { min: base * 0.4, max: base * 0.75 };
            case "uh_rune":
                return { min: Math.max(200, base * 1.5), max: base * 2.0 };
            default:
                return { min: 0, max: 0 };
        }
    }

    function calcHpRegen(premium) {
        // HP per 2-second turn
        const ticks = premium ? EK_HP_REGEN_TICKS : KNIGHT_HP_REGEN_TICKS;
        const regenIntervalMs = ticks * TICK_MS;
        return (HP_REGEN_AMOUNT / regenIntervalMs) * TURN_MS;
    }

    function calcManaRegen() {
        // Mana per 2-second turn
        const regenIntervalMs = MANA_REGEN_TICKS * TICK_MS;
        return (MANA_REGEN_AMOUNT / regenIntervalMs) * TURN_MS;
    }

    /* ================================================================
       MONSTER DAMAGE CALCULATION
       ================================================================ */

    /**
     * Average damage a monster deals to the knight per 2-second turn.
     * Applies player defense (for physical) and armor reduction.
     * Non-physical damage bypasses defense/armor.
     */
    function calcMonsterDPT(monster, playerDefense, playerArmor) {
        const armorRed = calcArmorReduction(playerArmor);
        const avgBlock = playerDefense * 0.75;
        let totalPhysical = 0;
        let totalMagical = 0;

        const attacks = monster.attacks || [];
        for (const atk of attacks) {
            // How many times this attack fires per turn
            const firesPerTurn = (TURN_MS / atk.interval) * (atk.chance / 100);

            let avgDamage;
            if (atk.name === "melee") {
                const maxHit = calcMonsterMeleeMax(atk.skill, atk.attack);
                avgDamage = maxHit * 0.5;
            } else if (atk.min !== undefined && atk.max !== undefined) {
                avgDamage = (Math.abs(atk.min) + Math.abs(atk.max)) / 2;
            } else {
                continue; // firefield etc. with no min/max — skip
            }

            const isPhysical = atk.name === "melee" || atk.name === "physical";

            if (isPhysical) {
                // Apply defense block + armor reduction per hit
                const reducedDmg = Math.max(0, avgDamage - avgBlock - armorRed.avg);
                totalPhysical += reducedDmg * firesPerTurn;
            } else if (atk.name === "manadrain") {
                // Mana drain doesn't reduce HP directly, but we track it
                // as a small effective cost (lost mana → fewer heals/spells)
                continue;
            } else {
                // Magical damage — no defense/armor reduction
                // Ranged/AoE: reduce probability the knight is in the area
                let hitFactor = 1.0;
                if (atk.length) {
                    // Directional wave — knight may not be in the wave's path
                    hitFactor = 0.4;
                } else if (atk.range && atk.range > 1) {
                    if (atk.radius && atk.radius > 3) hitFactor = 0.5;
                    else hitFactor = 0.7;
                }
                totalMagical += avgDamage * firesPerTurn * hitFactor;
            }
        }

        return { physical: totalPhysical, magical: totalMagical, total: totalPhysical + totalMagical };
    }

    /**
     * Monster max single hit (for danger assessment).
     */
    function calcMonsterMaxHit(monster) {
        let maxHit = 0;
        for (const atk of (monster.attacks || [])) {
            let hit;
            if (atk.name === "melee") {
                hit = calcMonsterMeleeMax(atk.skill, atk.attack);
            } else if (atk.max !== undefined) {
                hit = Math.abs(atk.max);
            } else {
                continue;
            }
            if (hit > maxHit) maxHit = hit;
        }
        return maxHit;
    }

    /* ================================================================
       EFFECTIVE HP (accounting for monster self-healing)
       ================================================================ */

    function calcEffectiveHP(monster, playerDPT) {
        const healing = monster.healing || [];
        if (healing.length === 0) return monster.hp;

        let healPerTurn = 0;
        for (const h of healing) {
            const firesPerTurn = (TURN_MS / h.interval) * (h.chance / 100);
            const avgHeal = (h.min + h.max) / 2;
            healPerTurn += avgHeal * firesPerTurn;
        }

        const netDPT = playerDPT - healPerTurn;
        if (netDPT <= 0) return Infinity; // Can't out-damage healing

        return monster.hp * (playerDPT / netDPT);
    }

    /* ================================================================
       COMPOSITE: AREA EVALUATION
       ================================================================ */

    /**
     * Build a knight profile from input parameters.
     */
    function makeProfile(opts) {
        return {
            level: opts.level || 50,
            weaponSkill: opts.weaponSkill || 60,
            magicLevel: opts.magicLevel || 4,
            shieldSkill: opts.shieldSkill || 50,
            weaponAttack: opts.weaponAttack || 30,
            shieldDefense: opts.shieldDefense || 23,
            totalArmor: opts.totalArmor || 25,
            premium: opts.premium !== false,
            huntStyle: opts.huntStyle || "melee",       // melee, melee_exori, melee_rune
            runeType: opts.runeType || "gfb",           // gfb, explosion, sd, avalanche, thunderstorm
            healMethod: opts.healMethod || "exura",     // food, exura, ih_rune, uh_rune
        };
    }

    /**
     * Calculate player's average damage per turn against a specific monster.
     */
    function calcPlayerDPT(profile, monster) {
        const meleeAvg = calcMeleeAvg(profile.weaponSkill, profile.weaponAttack, 1.0);

        // Monster armor/defense reduce player's physical damage too
        const monArmor = calcArmorReduction(monster.armor || 0);
        const monDef = monster.defense || 0;
        const monBlock = monDef * 0.5 * 0.75; // rough avg: defense/2 to defense, take 75% of midpoint

        let dpt;

        switch (profile.huntStyle) {
            case "melee_exori": {
                if (profile.level < EXORI_MIN_LEVEL) {
                    // Fall back to melee only
                    dpt = Math.max(0, meleeAvg - monBlock - monArmor.avg);
                    break;
                }
                const berserk = calcBerserkDamage(profile.level, profile.magicLevel);
                const berserkCost = calcBerserkManaCost(profile.level);
                const manaPool = KNIGHT_BASE_MANA + profile.level * KNIGHT_MANA_PER_LVL;
                const manaRegen = calcManaRegen();
                // How many turns between berserks to sustain mana?
                // Each berserk costs berserkCost mana, regen gives manaRegen/turn
                // Sustainable frequency: cast every ceil(berserkCost / manaRegen) turns
                // But also limited by pool — can burst from pool then sustain
                const sustainedInterval = Math.max(1, Math.ceil(berserkCost / manaRegen));
                // Blend: melee every turn, berserk every sustainedInterval turns
                const meleeNet = Math.max(0, meleeAvg - monBlock - monArmor.avg);
                const berserkNet = Math.max(0, berserk.avg - monArmor.avg);
                // On berserk turns, knight deals both melee AND berserk to main target
                // Plus berserk hits additional targets (handled in area evaluation)
                dpt = meleeNet + (berserkNet / sustainedInterval);
                break;
            }
            case "melee_rune": {
                const rune = calcRuneDamage(profile.level, profile.magicLevel, profile.runeType);
                if (!rune) {
                    dpt = Math.max(0, meleeAvg - monBlock - monArmor.avg);
                    break;
                }
                // Rune costs a turn (noAttackHealingSimultaneus applies to all actions)
                // Pattern: melee, rune, melee, rune... → 50% turns melee, 50% rune
                const meleeNet = Math.max(0, meleeAvg - monBlock - monArmor.avg);
                let runeNet;
                if (rune.element === "physical") {
                    runeNet = Math.max(0, rune.avg - monArmor.avg);
                } else {
                    // Check monster element resistance
                    const resist = (monster.elements || {})[rune.element] || 0;
                    runeNet = rune.avg * (1 - resist / 100);
                }
                // Alternating: avg DPT = (melee + rune) / 2 turns = avg per turn
                dpt = (meleeNet + runeNet) / 2;
                break;
            }
            default: // melee only
                dpt = Math.max(0, meleeAvg - monBlock - monArmor.avg);
        }

        return dpt;
    }

    /**
     * Evaluate a single hunting area for a knight profile.
     */
    function evaluateArea(area, profile, monsters) {
        const maxHP = KNIGHT_BASE_HP + profile.level * KNIGHT_HP_PER_LVL;
        const hpRegen = calcHpRegen(profile.premium);
        const defense = calcDefense(profile.shieldSkill, profile.shieldDefense, 1.0);
        const healAmounts = calcHealAmount(profile.level, profile.magicLevel, profile.healMethod);
        const avgHeal = (healAmounts.min + healAmounts.max) / 2;

        let totalExpHour = 0;
        let totalKillsHour = 0;
        let worstDanger = "safe";
        let sustainable = true;
        let totalRunesHour = 0;
        let totalTimeUsed = 0;
        let dominantCreature = "";
        let dominantExp = 0;
        let maxSingleHit = 0;

        const creatureResults = [];

        for (const [name, count] of Object.entries(area.creatures)) {
            const mon = monsters[name];
            if (!mon || !mon.hp || mon.hp <= 0 || !mon.exp) continue;

            const playerDPT = calcPlayerDPT(profile, mon);
            if (playerDPT <= 0) {
                sustainable = false;
                worstDanger = "lethal";
                continue;
            }

            const effectiveHP = calcEffectiveHP(mon, playerDPT);
            if (!isFinite(effectiveHP)) {
                sustainable = false;
                worstDanger = "lethal";
                continue;
            }

            let combatTurns = effectiveHP / playerDPT;
            // Flee penalty: monsters that run add ~3 extra turns for chasing
            if (mon.runonhealth && mon.runonhealth > 0) {
                combatTurns += 3;
            }
            const monsterDPT = calcMonsterDPT(mon, defense, profile.totalArmor);
            const damagePerKill = monsterDPT.total * combatTurns;

            // Healing turns per kill
            let healingTurns = 0;
            const netDamagePerKill = damagePerKill - (hpRegen * combatTurns);

            if (netDamagePerKill > 0 && profile.healMethod !== "food") {
                if (avgHeal > 0) {
                    healingTurns = netDamagePerKill / avgHeal;
                } else {
                    healingTurns = Infinity;
                }
            } else if (netDamagePerKill > maxHP) {
                sustainable = false;
            }

            const totalTurns = combatTurns + healingTurns;
            if (!isFinite(totalTurns) || totalTurns <= 0) {
                sustainable = false;
                worstDanger = "lethal";
                continue;
            }

            const killTimeSec = totalTurns * TURN_S;
            const killsPerHourRaw = 3600 / killTimeSec;
            // Cap by respawn rate
            const respawnKillsHour = count * (3600 / (area.respawn_avg || 60));
            const killsHour = Math.min(killsPerHourRaw, respawnKillsHour);
            const expHour = killsHour * mon.exp;

            totalExpHour += expHour;
            totalKillsHour += killsHour;
            totalTimeUsed += killsHour * killTimeSec;
            if (HEAL_METHODS[profile.healMethod].usesRune) {
                totalRunesHour += healingTurns * killsHour;
            }

            if (expHour > dominantExp) {
                dominantExp = expHour;
                dominantCreature = name;
            }

            const monMax = calcMonsterMaxHit(mon);
            if (monMax > maxSingleHit) maxSingleHit = monMax;

            // Danger per creature
            const healOverhead = avgHeal > 0
                ? 1 - (healingTurns / totalTurns)
                : (netDamagePerKill <= 0 ? 1 : 0);

            let danger;
            if (monsterDPT.total * 2 > maxHP || !isFinite(healingTurns)) {
                danger = "lethal";
            } else if (monMax > maxHP * 0.6) {
                danger = "lethal";
            } else if (healOverhead < 0.1 || monMax > maxHP * 0.3) {
                danger = "dangerous";
            } else if (healOverhead < 0.5) {
                danger = "moderate";
            } else {
                danger = "safe";
            }

            const dangerOrder = { safe: 0, moderate: 1, dangerous: 2, lethal: 3 };
            if (dangerOrder[danger] > dangerOrder[worstDanger]) {
                worstDanger = danger;
            }

            creatureResults.push({
                name,
                count,
                exp: mon.exp,
                hp: mon.hp,
                expHour: Math.round(expHour),
                killsHour: Math.round(killsHour * 10) / 10,
                killsHourRaw: killsHour,
                ttk: Math.round(killTimeSec * 10) / 10,
                danger,
            });
        }

        // Time-budget cap: total kill time cannot exceed 3600 seconds
        if (totalTimeUsed > 3600) {
            const scale = 3600 / totalTimeUsed;
            totalExpHour *= scale;
            totalKillsHour *= scale;
            totalRunesHour *= scale;
            for (const cr of creatureResults) {
                cr.killsHourRaw *= scale;
                cr.killsHour = Math.round(cr.killsHourRaw * 10) / 10;
                cr.expHour = Math.round(cr.killsHourRaw * cr.exp);
            }
        }

        // AoE bonus for exori or AoE runes
        let aoeFactor = 1;
        if (profile.huntStyle === "melee_exori" && profile.level >= EXORI_MIN_LEVEL) {
            const totalCreatures = Object.values(area.creatures).reduce((a, b) => a + b, 0);
            // Estimate: berserk hits up to min(nearby creatures, 8) targets
            // In dense areas, assume 2-4 extra targets get AoE damage
            const berserk = calcBerserkDamage(profile.level, profile.magicLevel);
            const berserkCost = calcBerserkManaCost(profile.level);
            const manaRegen = calcManaRegen();
            const sustainedInterval = Math.max(1, Math.ceil(berserkCost / manaRegen));
            const densityFactor = Math.min(4, Math.max(1, totalCreatures / (area.spawn_ids || []).length));
            // Extra targets get berserk damage every sustainedInterval turns
            // This is a rough multiplier on total exp
            if (densityFactor > 1) {
                aoeFactor = 1 + (densityFactor - 1) * 0.15; // Conservative: 15% bonus per extra target
            }
        } else if (profile.huntStyle === "melee_rune") {
            const rune = RUNES[profile.runeType];
            if (rune && rune.aoe > 1) {
                const totalCreatures = Object.values(area.creatures).reduce((a, b) => a + b, 0);
                const densityFactor = Math.min(6, Math.max(1, totalCreatures / (area.spawn_ids || []).length));
                if (densityFactor > 1) {
                    aoeFactor = 1 + (densityFactor - 1) * 0.2;
                }
            }
        }

        totalExpHour = Math.round(totalExpHour * aoeFactor);

        if (worstDanger === "lethal") sustainable = false;

        return {
            areaId: area.id,
            expHour: totalExpHour,
            killsHour: Math.round(totalKillsHour * 10) / 10,
            danger: worstDanger,
            sustainable,
            healMethod: profile.healMethod,
            runesHour: Math.ceil(totalRunesHour),
            dominantCreature,
            maxSingleHit,
            maxHP,
            creatures: creatureResults,
        };
    }

    /**
     * Rank all hunting areas for a knight profile.
     * Returns areas sorted by exp/hour descending, with evaluation data.
     */
    function rankAreas(areas, profile, monsters) {
        const results = [];
        for (const area of areas) {
            const totalCreatures = Object.values(area.creatures).reduce((a, b) => a + b, 0);
            if (totalCreatures === 0) continue;

            const evaluation = evaluateArea(area, profile, monsters);
            if (evaluation.expHour <= 0) continue;

            results.push({
                area,
                eval: evaluation,
            });
        }

        results.sort((a, b) => b.eval.expHour - a.eval.expHour);
        return results;
    }

    /* ================================================================
       PUBLIC API
       ================================================================ */

    return {
        // Formulas
        calcMeleeMax,
        calcMeleeAvg,
        calcBerserkDamage,
        calcBerserkManaCost,
        calcRuneDamage,
        calcMonsterMeleeMax,
        calcDefense,
        calcArmorReduction,
        calcHealAmount,
        calcHpRegen,
        calcManaRegen,
        calcMonsterDPT,
        calcMonsterMaxHit,
        calcEffectiveHP,
        calcPlayerDPT,

        // Composite
        makeProfile,
        evaluateArea,
        rankAreas,

        // Constants
        RUNES,
        HEAL_METHODS,
        EXORI_MIN_LEVEL,
        KNIGHT_BASE_HP,
        KNIGHT_HP_PER_LVL,
        KNIGHT_BASE_MANA,
        KNIGHT_MANA_PER_LVL,
    };
})();
