"""Combat model primitives.

Mirrors the server's formulas from:
  sources/tools.cpp        (random_range + box_muller -> DISTRO_NORMAL)
  sources/weapons.cpp      (getMaxWeaponDamage, getMaxMeleeDamage, WeaponMelee::getWeaponDamage)
  sources/creature.cpp     (Creature::blockHit armor/defense pipeline)
  sources/combat.cpp       (COMBAT_FORMULA_LEVELMAGIC application)
  data/spells/scripts/...  (per-rune coefficients + UH callback)

All values returned here are POSITIVE magnitudes. Sign handling lives in
the simulator. The model is intentionally stateless so tests can pin seeds.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

import numpy as np


# ---------- RNG ----------

class ServerRNG:
    """Mirror of sources/tools.cpp random_range with DISTRO_NORMAL and DISTRO_UNIFORM.

    The server implements DISTRO_NORMAL via box_muller(0.5, 0.25) clamped to
    [0, 1] and rescaled to [lo, hi]. We use numpy standard_normal + clamp to
    reproduce the same family of draws (not the same bit sequence, but same
    distribution). Seeded for reproducibility.
    """

    def __init__(self, seed: int | None = None):
        self._rng = np.random.default_rng(seed)

    def uniform_int(self, lo: int, hi: int) -> int:
        if hi < lo:
            lo, hi = hi, lo
        return int(self._rng.integers(lo, hi + 1))

    def normal_int(self, lo: int, hi: int) -> int:
        if hi < lo:
            lo, hi = hi, lo
        if hi == lo:
            return lo
        t = 0.5 + 0.25 * self._rng.standard_normal()
        t = min(1.0, max(0.0, t))
        return int(lo + (hi - lo) * t)

    def normal_int_vec(self, lo: int, hi: int, n: int) -> np.ndarray:
        """Vectorised DISTRO_NORMAL draws."""
        if hi < lo:
            lo, hi = hi, lo
        if hi == lo:
            return np.full(n, lo, dtype=np.int64)
        t = 0.5 + 0.25 * self._rng.standard_normal(n)
        np.clip(t, 0.0, 1.0, out=t)
        return (lo + (hi - lo) * t).astype(np.int64)

    def random01(self) -> float:
        return float(self._rng.random())


# ---------- Melee damage ----------

def max_weapon_damage(level: int, skill: int, attack: int, factor: float = 1.0) -> int:
    """sources/weapons.cpp:152-157 (non-classic branch).

    max = ceil( 2 * (atk * (skill + 5.8)/25 + (level-1)/10.) / factor )
    """
    if attack <= 0:
        return 0
    return int(math.ceil((2.0 * (attack * (skill + 5.8) / 25.0 + (level - 1) / 10.0)) / factor))


def max_melee_damage(skill: int, attack: int) -> int:
    """sources/weapons.cpp:147-150. Used for monster melee ranges."""
    if attack <= 0:
        return 0
    return int(math.ceil(skill * (attack * 0.05) + attack * 0.5))


def melee_swing(level: int, skill: int, attack: int, rng: ServerRNG, factor: float = 1.0) -> int:
    """One physical swing, before defender block/armor. DISTRO_NORMAL(0, max)."""
    m = max_weapon_damage(level, skill, attack, factor)
    if m <= 0:
        return 0
    return rng.normal_int(0, m)


# ---------- Defender block (armor + defense) ----------

@dataclass
class BlockResult:
    damage: int
    blocked_by_defense: bool
    blocked_by_armor: bool


def apply_block(
    raw_damage: int,
    armor: int,
    defense: int,
    rng: ServerRNG,
    has_shield_block: bool = True,
    check_defense: bool = True,
    check_armor: bool = True,
    is_immune: bool = False,
) -> BlockResult:
    """sources/creature.cpp:961-1017 blockHit.

    Order: immunity -> defense roll (only if blockCount>0) -> armor roll.
    """
    if is_immune:
        return BlockResult(0, False, False)
    dmg = raw_damage
    blocked_def = False
    if check_defense and has_shield_block and defense > 0:
        lo = defense // 2
        hi = defense
        dmg -= rng.uniform_int(lo, hi)
        if dmg <= 0:
            return BlockResult(0, True, False)
    blocked_arm = False
    if check_armor and armor > 1:
        lo = int(math.ceil(armor * 0.475))
        hi = int(math.ceil((armor * 0.475) - 1 + lo))
        dmg -= rng.uniform_int(lo, hi)
        if dmg <= 0:
            return BlockResult(0, False, True)
    elif check_armor and armor == 1:
        dmg -= 1
        if dmg <= 0:
            return BlockResult(0, False, True)
    return BlockResult(max(0, dmg), False, False)


# ---------- Level-Magic rune damage ----------

def levelmagic_damage(level: int, maglv: int, mina: float, minb: float,
                      maxa: float, maxb: float, rng: ServerRNG) -> int:
    """sources/combat.cpp COMBAT_FORMULA_LEVELMAGIC.

    The coefficients in scripts are negative (damage). We take abs() and return
    a positive magnitude. Min < Max; min uses |maxa, maxb|, max uses |mina, minb|
    per the sign convention in combat.cpp.
    """
    B = level * 2 + maglv * 3
    # The coefficients in lua are: (mina, minb, maxa, maxb) = (small, small, big, big)
    # where abs(mina) < abs(maxa). Per combat.cpp, the rolled range is
    # [ B*|mina|+|minb|,  B*|maxa|+|maxb| ].
    lo = int(abs(mina) * B + abs(minb))
    hi = int(abs(maxa) * B + abs(maxb))
    if hi < lo:
        lo, hi = hi, lo
    return rng.normal_int(lo, hi)


# ---------- UH-rune healing ----------

def uh_rune_heal(level: int, maglv: int, rng: ServerRNG,
                 mult_min: float = 1.5, mult_max: float = 2.0,
                 floor_min: int = 200) -> int:
    """data/spells/scripts/healing/ultimate_healing_rune.lua.

    min = max(floor_min, (2L+3M) * mult_min)
    max = (2L+3M) * mult_max
    heal = random_range(min, max)     (DISTRO_UNIFORM by default)
    """
    B = level * 2 + maglv * 3
    lo = max(floor_min, int(B * mult_min))
    hi = int(B * mult_max)
    if hi < lo:
        hi = lo
    return rng.uniform_int(lo, hi)


# ---------- Element resistance ----------

def apply_element_pct(damage: int, elem_pct: int) -> int:
    """Monster <elements> table: value is the % of damage taken. 100 = neutral,
    0 = immune (handled via immunity), <100 = resistant, >100 = weak."""
    return int(damage * elem_pct / 100)


# ---------- Monster attack sampler ----------

def monster_attack_damage(atk: dict, rng: ServerRNG) -> int:
    """Sample an outgoing monster attack magnitude.

    - melee: uniform(0, max_melee_damage(skill, attack))  (DISTRO_NORMAL per combat.cpp)
    - ranged with explicit min/max: uniform(|min|, |max|) per FORMULA_VALUE path
    """
    if atk.get("name") == "melee":
        skill = int(atk.get("skill", 0))
        attack = int(atk.get("attack", 0))
        m = max_melee_damage(skill, attack)
        if m <= 0:
            return 0
        return rng.normal_int(0, m)
    mn = abs(int(atk.get("min", 0)))
    mx = abs(int(atk.get("max", 0)))
    if mx <= 0:
        return 0
    return rng.uniform_int(mn, mx)


# ---------- Helpers ----------

def expected_damage_per_swing(level: int, skill: int, attack: int, factor: float = 1.0) -> float:
    """Analytical mean of DISTRO_NORMAL(0, M): the box_muller distribution is
    symmetric around 0.5*M with variance (0.25*M)^2 (before clamping). The
    clamping makes it a truncated normal on [0,M] with mean very close to
    0.5*M. Close-form analysis gives mean = 0.5*M."""
    return 0.5 * max_weapon_damage(level, skill, attack, factor)


def area_tile_count(area: list[list[int]]) -> int:
    """Number of affected tiles (cells > 0) in a rune area grid. The central
    '3' cell is the caster-targeted tile; both 1s and 3s are hit tiles."""
    if not area:
        return 1
    return sum(1 for row in area for c in row if c > 0)
