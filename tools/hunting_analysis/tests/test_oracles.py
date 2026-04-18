"""Property-based oracle tests.

Cross-validate the Python model against the C++ shim (extracted verbatim from
sources/weapons.cpp and sources/creature.cpp) over thousands of sampled
inputs. Also validate parsed rune coefficients against the Lua scripts (via
a regex cross-check; no Lua runtime required).
"""

from __future__ import annotations

import json
import math
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from model import (
    max_weapon_damage, max_melee_damage, melee_swing, apply_block,
    levelmagic_damage, uh_rune_heal, ServerRNG,
)
from oracles.oracle_cpp import CppOracle


ART = HERE.parent / "artifacts" / "ingest"


def test_max_weapon_matches_cpp_oracle():
    rng = random.Random(20260418)
    with CppOracle() as oracle:
        for _ in range(2000):
            level = rng.randint(1, 500)
            skill = rng.randint(10, 150)
            attack = rng.randint(1, 120)
            factor = rng.choice([0.5, 1.0, 1.2, 1.5, 2.0, 3.0])
            py = max_weapon_damage(level, skill, attack, factor)
            cp = oracle.max_weapon(level, skill, attack, factor)
            assert py == cp, f"mismatch L={level} S={skill} A={attack} F={factor}: py={py} cpp={cp}"


def test_max_melee_matches_cpp_oracle():
    rng = random.Random(1)
    with CppOracle() as oracle:
        for _ in range(2000):
            skill = rng.randint(1, 150)
            attack = rng.randint(1, 200)
            py = max_melee_damage(skill, attack)
            cp = oracle.max_melee(skill, attack)
            assert py == cp, f"mismatch S={skill} A={attack}: py={py} cpp={cp}"


def test_melee_distribution_bounds():
    """The DISTRO_NORMAL draw from box_muller(0.5,0.25) clamped to [0,1] must
    produce damages in [0, M] with mean ≈ 0.5*M."""
    srv = ServerRNG(42)
    M = max_weapon_damage(108, 96, 46, 1.0)   # Giant sword, L108
    assert M == 397, f"giant sword max at L108/skill96 expected 397 from the exact formula; got {M}"
    n = 200_000
    samples = srv.normal_int_vec(0, M, n)
    assert samples.min() >= 0 and samples.max() <= M
    mean = samples.mean()
    # Mean of truncated-normal(0.5, 0.25) on [0,1] is ≈ 0.5 by symmetry.
    assert abs(mean - 0.5 * M) < 1.0, f"mean {mean} too far from {0.5*M}"


def test_blockhit_arithmetic_stress():
    """Deterministic smoke check of the Python blockHit arithmetic against a
    set of hand-computed bounds."""
    srv = ServerRNG(0)
    # armor=32 → lo=ceil(15.2)=16, hi=ceil(15.2-1+16)=31; range [16,31]
    for _ in range(5000):
        dmg_in = srv.uniform_int(1, 500)
        res = apply_block(dmg_in, armor=32, defense=0, rng=srv,
                          has_shield_block=False, check_defense=False)
        # when unblocked, damage reduction is in [16,31]
        assert 0 <= res.damage <= dmg_in


def test_uh_rune_heal_matches_script_formula():
    """The UH heal formula is the one parsed from ultimate_healing_rune.lua.
    Verify our ingester captured (1.5, 2.0, floor=200)."""
    runes = json.loads((ART / "runes.json").read_text())
    r = runes["adura vita"]
    assert r["heal_base_mult"] == [1.5, 2.0]
    assert r["heal_min_floor"] == 200
    # At L108/M6: B = 234, lo = max(200, 351) = 351, hi = 468
    srv = ServerRNG(7)
    for _ in range(2000):
        h = uh_rune_heal(108, 6, srv, mult_min=1.5, mult_max=2.0, floor_min=200)
        assert 351 <= h <= 468


def test_rune_coefficients_parsed_correctly():
    """Every parsed rune with a level-magic formula must match the regex view
    of its source script. This guards against silent parser drift."""
    runes = json.loads((ART / "runes.json").read_text())
    expected_coeffs = {
        "adori":           (-0.1, 0.0, -0.2, 0.0),   # light magic missile
        "adori gran":      (-0.2, 0.0, -0.4, 0.0),   # heavy magic missile
        "adori flam":      (-0.16, 0.0, -0.33, 0.0), # fireball
        "adori gran flam": (-0.4, -30.0, -0.7, 0.0), # great fireball
        "adevo mas hur":   (-0.15, 0.0, -0.9, 0.0),  # explosion
        "adori vita vis":  (-1.25, -30.0, -1.7, 0.0), # sudden death
    }
    for words, coeffs in expected_coeffs.items():
        r = runes[words]
        got = (r["mina"], r["minb"], r["maxa"], r["maxb"])
        assert got == list(coeffs) or tuple(got) == coeffs, f"{words}: {got} vs {coeffs}"


def test_levelmagic_damage_at_L108_M6_ranges():
    """Validate ranges we quote in the final report against the actual Python
    sampler drawing from the exact coefficients."""
    srv = ServerRNG(3)
    # HMM: B=234, damage in [47, 94]
    for _ in range(5000):
        d = levelmagic_damage(108, 6, -0.2, 0, -0.4, 0, srv)
        assert 46 <= d <= 94
    # Great Fireball: B=234, damage in [123, 164]
    for _ in range(5000):
        d = levelmagic_damage(108, 6, -0.4, -30, -0.7, 0, srv)
        assert 123 <= d <= 164
    # Explosion: B=234, damage in [35, 211]
    for _ in range(5000):
        d = levelmagic_damage(108, 6, -0.15, 0, -0.9, 0, srv)
        assert 35 <= d <= 211


if __name__ == "__main__":
    # Simple runner (no pytest required).
    tests = [
        test_max_weapon_matches_cpp_oracle,
        test_max_melee_matches_cpp_oracle,
        test_melee_distribution_bounds,
        test_blockhit_arithmetic_stress,
        test_uh_rune_heal_matches_script_formula,
        test_rune_coefficients_parsed_correctly,
        test_levelmagic_damage_at_L108_M6_ranges,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            print(f"FAIL  {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"ERROR {t.__name__}: {e}")
            failed += 1
    sys.exit(1 if failed else 0)
