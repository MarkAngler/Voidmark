"""Monte Carlo combat simulator.

Two levels:

  simulate_encounter  - one player vs one monster, tick-based (500 ms)
  simulate_camp       - one player vs one cluster (N monsters + respawn) for T hours

Both return dataclasses with raw counters; statistical aggregation is done in
analysis.py via bootstrap CIs and ANOVA.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from model import (
    ServerRNG, max_weapon_damage, max_melee_damage,
    apply_block, levelmagic_damage, uh_rune_heal, monster_attack_damage,
    apply_element_pct,
)

TICK_MS = 500   # scheduler granularity we use for timer accumulation
T_MAX_MS = 180_000   # 3-minute cap per single encounter (guard)


@dataclass
class Player:
    level: int = 108
    skill: int = 96          # sword
    maglv: int = 6
    max_hp: int = 0          # computed from vocation defaults
    max_mana: int = 0
    armor: int = 0           # worn armor rating
    defense: int = 0         # shield defense
    elements: dict[str, int] = field(default_factory=dict)  # % taken; default 100
    # Inventory / combat config
    weapon_attack: int = 46  # giant sword default
    weapon_element: int = 0
    weapon_element_type: str = ""
    attack_factor: float = 1.0
    attack_cooldown_ms: int = 2000   # player attack speed
    uh_threshold: float = 0.55       # cast UH when hp < this fraction of max
    uh_price_gp: int = 125
    use_runes: bool = False
    rune_cfg: dict[str, Any] = field(default_factory=dict)   # see rune_configs()


def knight_hp(level: int) -> int:
    """Knight vocation default HP: 185 + 15 per level. (data/xml/vocations.xml)"""
    return 185 + 15 * level


def knight_mana(level: int) -> int:
    """Knight default mana: 35 + 5 per level."""
    return 35 + 5 * level


def default_knight(level: int = 108, skill: int = 96, maglv: int = 6) -> Player:
    p = Player(level=level, skill=skill, maglv=maglv)
    p.max_hp = knight_hp(level)      # L108 -> 1805
    p.max_mana = knight_mana(level)  # L108 -> 575
    # Knight endgame set: knight armor 13 + knight helmet 6 + knight legs 7 +
    # soldier boots 3 = armor 29; steel shield defense 28; small tweaks for
    # sensitivity analysis later.
    p.armor = 29
    p.defense = 28
    p.elements = {}
    return p


# ---------- Rune tactics ----------

def should_use_aoe(monsters_in_area: int, rune_cost_gp: float,
                   xp_per_kill_avg: int, dmg_per_tile: float,
                   fresh_hp: int) -> bool:
    """Use AoE only if expected XP / rune cost beats a threshold.

    Cheap heuristic: profitable when N monsters * P(kill-contribution) * XP > k*cost.
    We use a conservative threshold: AoE contributes effectively when
    (N * dmg_per_tile) / fresh_hp > 0.15 (each cast meaningfully dents a
    freshly-spawned group)."""
    if monsters_in_area < 2:
        return False
    return (monsters_in_area * dmg_per_tile) / max(1, fresh_hp) > 0.15


# ---------- Encounter ----------

@dataclass
class EncounterResult:
    won: bool = False
    ttk_ms: int = 0
    damage_taken: int = 0
    uh_used: int = 0
    runes_used: dict[str, int] = field(default_factory=dict)
    end_hp: int = 0
    end_mana: int = 0
    xp: int = 0


def simulate_encounter(
    player: Player, monster: dict, rng: ServerRNG,
    starting_hp: int | None = None, shield_block_per_turn: int = 2,
    monster_block_per_turn: int = 2,
) -> EncounterResult:
    """Single-monster fight. Returns outcome and counters.

    Timing model:
      - Player attacks every `attack_cooldown_ms` ms.
      - Monster each <attack> fires on its own interval with `chance`.
      - Monster <defense name="healing"> fires on its interval with chance.
      - Player casts UH rune (1s gcd) when hp < threshold * max_hp. No mana cost
        for runes (only the gp price is tracked).
    """
    hp_m = int(monster.get("hp", 0) or 0)
    max_hp_m = hp_m
    hp_p = starting_hp if starting_hp is not None else player.max_hp
    mana_p = player.max_mana
    damage_taken = 0
    t = 0
    swing_timer = 0
    heal_timer_by_idx: dict[int, int] = {}
    atk_timer_by_idx: dict[int, int] = {}
    uh_timer = 0  # internal rune cooldown (~1000 ms)
    shield_charges = shield_block_per_turn
    shield_reset_timer = 2000
    monster_block_charges = monster_block_per_turn
    monster_block_reset = 2000

    uh_used = 0
    runes_used: dict[str, int] = {}

    # Parse monster capabilities
    attacks = monster.get("attacks", [])
    healing = monster.get("healing", [])
    immunities = set(monster.get("immunities", []))
    elements = monster.get("elements", {})

    is_immune_phys = "physical" in immunities or "paralyze" in immunities and False

    # Player physical element handling: no creature we hunt is immune to physical
    # except a few shields/ghosts; for our candidates (DL/Hydra/Dragon/Scarab/
    # GiantSpider), physical is baseline.
    phys_resist = int(elements.get("physical", 100))

    while hp_m > 0 and hp_p > 0 and t < T_MAX_MS:
        # --- Player swing
        if swing_timer <= 0:
            raw = rng.normal_int(0, max_weapon_damage(player.level, player.skill,
                                                      player.weapon_attack,
                                                      player.attack_factor))
            # Some monsters lightly resist/weak vs physical, scale accordingly
            raw = apply_element_pct(raw, phys_resist)
            # Monster block pipeline: defense roll first (shield-ish) then armor.
            # Monster has only `monster_block_per_turn` shield blocks per 2s window.
            has_block = monster_block_charges > 0
            if has_block:
                monster_block_charges -= 1
            b = apply_block(raw, armor=int(monster.get("armor", 0)),
                            defense=int(monster.get("defense", 0)),
                            rng=rng, has_shield_block=has_block,
                            check_defense=has_block, check_armor=True)
            hp_m -= b.damage
            # Weapon element (fire sword etc.) fires as a second roll
            if player.weapon_element > 0:
                raw_e = rng.normal_int(0, max_weapon_damage(player.level, player.skill,
                                                             player.weapon_element,
                                                             player.attack_factor))
                epct = int(elements.get(player.weapon_element_type, 100))
                if player.weapon_element_type in immunities:
                    pass
                else:
                    hp_m -= apply_element_pct(raw_e, epct)
            swing_timer = player.attack_cooldown_ms

        # --- Rune cast (one action per tick). Single-target HMM on cooldown,
        # used by knights for extra DPS when mana/rune policy allows.
        if player.use_runes and uh_timer <= 0 and hp_p >= 0.7 * player.max_hp and hp_m > 0:
            cfg = player.rune_cfg
            if cfg.get("single_target") == "hmm":
                # HMM: (2L+3M)*[0.2,0.4] energy. Most candidates are neutral.
                d = levelmagic_damage(player.level, player.maglv, -0.2, 0, -0.4, 0, rng)
                epct = int(elements.get("energy", 100))
                if "energy" not in immunities:
                    hp_m -= apply_element_pct(d, epct)
                runes_used["hmm"] = runes_used.get("hmm", 0) + 1
                uh_timer = 1000

        # --- Monster outgoing attacks
        for i, atk in enumerate(attacks):
            interval = int(atk.get("interval", 2000))
            atk_timer_by_idx[i] = atk_timer_by_idx.get(i, 0) - TICK_MS
            if atk_timer_by_idx[i] <= 0:
                atk_timer_by_idx[i] = interval
                chance = int(atk.get("chance", 100))
                if rng.uniform_int(1, 100) <= chance:
                    dmg = monster_attack_damage(atk, rng)
                    if dmg <= 0:
                        continue
                    # Player armor blocks only physical
                    if atk.get("name") == "melee":
                        if shield_charges > 0:
                            shield_charges -= 1
                            b2 = apply_block(dmg, armor=player.armor,
                                             defense=player.defense, rng=rng,
                                             has_shield_block=True,
                                             check_defense=True, check_armor=True)
                        else:
                            b2 = apply_block(dmg, armor=player.armor, defense=0,
                                             rng=rng, has_shield_block=False,
                                             check_defense=False, check_armor=True)
                        taken = b2.damage
                    else:
                        # Elemental attacks: apply player element resistance
                        elem_key = atk.get("name", "")
                        epct = int(player.elements.get(elem_key, 100))
                        taken = int(dmg * epct / 100)
                    hp_p -= taken
                    damage_taken += taken

        # --- Monster self-heal
        for i, h in enumerate(healing):
            interval = int(h.get("interval", 1000))
            heal_timer_by_idx[i] = heal_timer_by_idx.get(i, 0) - TICK_MS
            if heal_timer_by_idx[i] <= 0:
                heal_timer_by_idx[i] = interval
                if rng.uniform_int(1, 100) <= int(h.get("chance", 0)):
                    amt = rng.uniform_int(int(h.get("min", 0)), int(h.get("max", 0)))
                    hp_m = min(max_hp_m, hp_m + amt)

        # --- Player UH
        if hp_p < player.uh_threshold * player.max_hp and uh_timer <= 0:
            heal = uh_rune_heal(player.level, player.maglv, rng)
            hp_p = min(player.max_hp, hp_p + heal)
            uh_used += 1
            uh_timer = 1000

        # --- Advance timers
        swing_timer -= TICK_MS
        uh_timer -= TICK_MS
        shield_reset_timer -= TICK_MS
        monster_block_reset -= TICK_MS
        if shield_reset_timer <= 0:
            shield_charges = shield_block_per_turn
            shield_reset_timer = 2000
        if monster_block_reset <= 0:
            monster_block_charges = monster_block_per_turn
            monster_block_reset = 2000
        t += TICK_MS

    won = hp_m <= 0
    return EncounterResult(
        won=won, ttk_ms=t,
        damage_taken=damage_taken, uh_used=uh_used, runes_used=runes_used,
        end_hp=max(0, hp_p), end_mana=mana_p,
        xp=int(monster.get("exp", 0)) if won else 0,
    )


# ---------- Camp ----------

@dataclass
class CampResult:
    hours: float
    xp: int = 0
    kills: int = 0
    deaths: int = 0
    uh_used: int = 0
    runes_used: dict[str, int] = field(default_factory=dict)
    damage_taken: int = 0
    time_idle_ms: int = 0
    gp_loot: int = 0

    @property
    def xp_per_hour(self) -> float:
        return self.xp / self.hours if self.hours else 0.0

    @property
    def uh_per_hour(self) -> float:
        return self.uh_used / self.hours if self.hours else 0.0

    @property
    def net_gp_per_hour(self) -> float:
        return (self.gp_loot - self.uh_used * 125) / self.hours if self.hours else 0.0


def simulate_camp(
    player: Player,
    creatures: dict[str, int],   # {creature_name: count}
    monster_db: dict[str, dict],
    respawn_avg_s: int,
    hours: float,
    rng: ServerRNG,
    travel_between_ms: int = 500,   # walking between corpses
    loot_gp_table: dict[str, float] | None = None,
) -> CampResult:
    """Steady-state camp simulation over T hours.

    Simplifies away positioning: we assume the knight always has a monster
    adjacent when the camp has live creatures. When all are dead we idle
    until the next respawn. This overestimates xp/hour slightly by ignoring
    travel inside the cluster, which we approximate with travel_between_ms.
    """
    total_ms = int(hours * 3600 * 1000)
    t = 0

    # Initial population
    pool: list[str] = []
    for name, count in creatures.items():
        pool.extend([name] * count)
    alive = list(pool)
    dead_queue: list[tuple[int, str]] = []  # (respawn_at_ms, name)

    result = CampResult(hours=hours)
    hp_p = player.max_hp

    while t < total_ms:
        # Resolve respawns up to now
        respawned_now = [n for ra, n in dead_queue if ra <= t]
        dead_queue = [(ra, n) for ra, n in dead_queue if ra > t]
        alive.extend(respawned_now)

        if not alive:
            # wait for next respawn
            if not dead_queue:
                result.time_idle_ms += total_ms - t
                break
            next_ra = min(ra for ra, _ in dead_queue)
            idle = next_ra - t
            if t + idle > total_ms:
                result.time_idle_ms += total_ms - t
                break
            result.time_idle_ms += idle
            t = next_ra
            # hp regen during idle (rough knight regen): 1 hp per 3s
            regen_hp = idle // 3000
            hp_p = min(player.max_hp, hp_p + regen_hp)
            continue

        name = alive.pop(0)
        monster = monster_db.get(name)
        if monster is None or int(monster.get("hp", 0)) == 0:
            # unknown/summon-only or noop creature; count quickly
            result.kills += 1
            dead_queue.append((t + respawn_avg_s * 1000, name))
            continue

        enc = simulate_encounter(player, monster, rng, starting_hp=hp_p)
        t += enc.ttk_ms + travel_between_ms
        result.damage_taken += enc.damage_taken
        result.uh_used += enc.uh_used
        for k, v in enc.runes_used.items():
            result.runes_used[k] = result.runes_used.get(k, 0) + v
        if enc.won:
            result.kills += 1
            result.xp += enc.xp
            if loot_gp_table is not None:
                result.gp_loot += int(loot_gp_table.get(name, 0))
            dead_queue.append((t + respawn_avg_s * 1000, name))
            hp_p = enc.end_hp
        else:
            result.deaths += 1
            # Dying: pay a time+gp cost; we respawn at full hp and lose 20 min
            # of buffer; for our L108 with amulet of loss this is 10% exp back
            # plus depot recovery. We just bump time by 5 min and reset hp.
            t += 300_000
            hp_p = player.max_hp

    return result
