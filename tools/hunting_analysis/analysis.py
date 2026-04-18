"""Statistical analysis layer.

- Per-creature combat tables (TTK/UH/xp-per-min) with 90% bootstrap CIs.
- Spawn-cluster ranking: converts `hunting_areas.json` clusters into
  expected xp/hour under full-respawn-saturated operation.
- ANOVA across candidate camps.
- Renders the final REPORT.md.
"""

from __future__ import annotations

import json
import math
import statistics
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from scipy import stats

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from model import (
    ServerRNG, max_weapon_damage, max_melee_damage, levelmagic_damage,
    uh_rune_heal, area_tile_count,
)
from simulator import default_knight, simulate_encounter, simulate_camp, Player

ART = HERE / "artifacts"
ING = ART / "ingest"


# ---------- Combat table ----------

CANDIDATE_CREATURES = [
    "dragon", "dragon lord", "hydra", "giant spider", "ancient scarab",
    "serpent spawn", "cyclops", "minotaur mage", "minotaur guard", "orc warlord",
    "bonebeast", "lich", "behemoth", "vampire", "demon skeleton",
    "dwarf guard", "dwarf soldier", "dwarf geomancer",
]


def bootstrap_ci(xs: list[float], B: int = 4000, alpha: float = 0.10,
                 seed: int = 20260418) -> tuple[float, float, float]:
    """Return (mean, lo, hi) for a (1-alpha) CI via percentile bootstrap."""
    arr = np.asarray(xs, dtype=float)
    if arr.size == 0:
        return 0.0, 0.0, 0.0
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, arr.size, size=(B, arr.size))
    means = arr[idx].mean(axis=1)
    lo = float(np.quantile(means, alpha / 2))
    hi = float(np.quantile(means, 1 - alpha / 2))
    return float(arr.mean()), lo, hi


def combat_table(monster_db: dict[str, dict], n_trials: int = 400,
                 seed: int = 42) -> list[dict[str, Any]]:
    """For each candidate creature, simulate N encounters and tabulate stats."""
    p = default_knight()
    rows: list[dict[str, Any]] = []
    for name in CANDIDATE_CREATURES:
        if name not in monster_db:
            continue
        m = monster_db[name]
        hp = int(m.get("hp", 0) or 0)
        if hp == 0:
            continue
        rng = ServerRNG(seed)
        trials = [simulate_encounter(p, m, rng) for _ in range(n_trials)]
        won = [t for t in trials if t.won]
        if not won:
            rows.append({
                "name": name, "hp": hp, "xp": int(m.get("exp", 0)),
                "win_rate": 0.0, "ttk_s": float("inf"),
                "uh_per_kill": float("nan"), "dmg_taken_avg": float("nan"),
                "xp_per_min": 0.0, "gp_cost_per_kill": float("nan"),
            })
            continue
        ttk_s = [t.ttk_ms / 1000 for t in won]
        uh = [t.uh_used for t in trials]
        dmg = [t.damage_taken for t in trials]
        xp_per_min = [(int(m.get("exp", 0)) * 60) / (t.ttk_ms / 1000)
                      for t in won]
        mean_xp, lo_xp, hi_xp = bootstrap_ci(xp_per_min)
        mean_ttk, _, _ = bootstrap_ci(ttk_s)
        mean_uh, _, _ = bootstrap_ci([float(u) for u in uh])
        rows.append({
            "name": name,
            "hp": hp,
            "xp": int(m.get("exp", 0)),
            "armor": int(m.get("armor", 0)),
            "defense": int(m.get("defense", 0)),
            "n_trials": n_trials,
            "win_rate": len(won) / n_trials,
            "ttk_s": mean_ttk,
            "uh_per_kill": mean_uh,
            "dmg_taken_avg": float(np.mean(dmg)),
            "xp_per_min": mean_xp,
            "xp_per_min_lo": lo_xp,
            "xp_per_min_hi": hi_xp,
            "gp_cost_per_kill": mean_uh * 125,   # UH rune 125 gp
        })
    rows.sort(key=lambda r: r["xp_per_min"], reverse=True)
    return rows


# ---------- Hunting area ranking ----------

@dataclass
class ClusterEval:
    id: int
    centroid: tuple[int, int, int]
    radius: int
    creatures: dict[str, int]
    respawn_avg: int
    xp_per_cycle: int
    ttk_total_s: float
    win_rate_product: float
    theoretical_xp_hr: float
    simulated_xp_hr_mean: float = 0.0
    simulated_xp_hr_lo: float = 0.0
    simulated_xp_hr_hi: float = 0.0
    uh_per_hr: float = 0.0
    net_gp_per_hr: float = 0.0
    deaths_per_hr: float = 0.0


def score_hunting_areas(areas: list[dict], combat_rows: list[dict],
                        monster_db: dict) -> list[ClusterEval]:
    """Rank every hunting-area cluster by theoretical XP/hour assuming
    back-to-back killing of monsters in the cluster.

    theoretical_xp_hr = xp_per_cycle / (kill_all_time + respawn_buffer)
    where respawn_buffer = max(0, respawn_avg - kill_all_time).
    """
    per_creature = {r["name"]: r for r in combat_rows}
    out: list[ClusterEval] = []
    for a in areas:
        creatures = a.get("creatures", {})
        if not creatures:
            continue
        xp_cycle = 0
        ttk_total = 0.0
        win_product = 1.0
        for name, count in creatures.items():
            m = monster_db.get(name)
            if m is None:
                continue
            c = per_creature.get(name)
            if c is None:
                continue
            xp_cycle += int(m.get("exp", 0)) * count
            ttk_total += c["ttk_s"] * count
            wr = c["win_rate"]
            # product^count gives prob of surviving `count` fights at this cluster
            win_product *= wr ** count if wr > 0 else 0.0
        if xp_cycle <= 0 or ttk_total <= 0:
            continue
        respawn = int(a.get("respawn_avg", 60))
        # saturated rate: we kill everything in ttk_total, then wait up to respawn
        cycle_time = max(ttk_total, respawn)
        theo = xp_cycle / cycle_time * 3600
        out.append(ClusterEval(
            id=int(a.get("id", -1)),
            centroid=(int(a.get("cx", 0)), int(a.get("cy", 0)), int(a.get("cz", 0))),
            radius=int(a.get("radius", 0)),
            creatures=dict(creatures),
            respawn_avg=respawn,
            xp_per_cycle=xp_cycle,
            ttk_total_s=ttk_total,
            win_rate_product=win_product,
            theoretical_xp_hr=theo,
        ))
    out.sort(key=lambda c: c.theoretical_xp_hr, reverse=True)
    return out


def simulate_top_camps(
    clusters: list[ClusterEval], monster_db: dict, top_n: int = 8,
    hours: float = 2.0, replicates: int = 20, seed_base: int = 20260418,
    loot_gp_table: dict[str, float] | None = None,
) -> list[ClusterEval]:
    """Run full camp Monte Carlo for the top-N clusters."""
    player = default_knight()
    for c in clusters[:top_n]:
        xp_hrs = []
        uh_hrs = []
        deaths_hrs = []
        net_gp_hrs = []
        for r in range(replicates):
            rng = ServerRNG(seed_base + r * 1009 + c.id)
            cr = simulate_camp(
                player, c.creatures, monster_db, respawn_avg_s=c.respawn_avg,
                hours=hours, rng=rng, loot_gp_table=loot_gp_table,
            )
            xp_hrs.append(cr.xp_per_hour)
            uh_hrs.append(cr.uh_per_hour)
            deaths_hrs.append(cr.deaths / hours)
            net_gp_hrs.append(cr.net_gp_per_hour)
        mean, lo, hi = bootstrap_ci(xp_hrs)
        c.simulated_xp_hr_mean = mean
        c.simulated_xp_hr_lo = lo
        c.simulated_xp_hr_hi = hi
        c.uh_per_hr = float(np.mean(uh_hrs))
        c.deaths_per_hr = float(np.mean(deaths_hrs))
        c.net_gp_per_hr = float(np.mean(net_gp_hrs))
    return clusters


# ---------- ANOVA across top strategies ----------

def anova_strategies(clusters: list[ClusterEval], monster_db: dict,
                     hours: float = 1.0, replicates: int = 30,
                     loot_gp_table: dict[str, float] | None = None) -> dict:
    """One-way ANOVA on XP/hour across the top 5 scored clusters."""
    player = default_knight()
    sample: dict[int, list[float]] = {}
    for c in clusters[:5]:
        data = []
        for r in range(replicates):
            rng = ServerRNG(9999 + r * 7 + c.id)
            cr = simulate_camp(player, c.creatures, monster_db,
                               respawn_avg_s=c.respawn_avg, hours=hours, rng=rng,
                               loot_gp_table=loot_gp_table)
            data.append(cr.xp_per_hour)
        sample[c.id] = data
    if len(sample) < 2:
        return {"F": 0.0, "p": 1.0, "groups": {}}
    groups = list(sample.values())
    F, p = stats.f_oneway(*groups)
    return {
        "F": float(F),
        "p": float(p),
        "n_groups": len(groups),
        "n_per_group": replicates,
        "groups": {
            str(cid): {"mean": float(np.mean(vals)),
                       "std": float(np.std(vals, ddof=1)),
                       "n": len(vals)}
            for cid, vals in sample.items()
        },
    }


# ---------- Rune damage analytics ----------

def rune_damage_summary(level: int = 108, maglv: int = 6) -> list[dict]:
    runes = json.loads((ING / "runes.json").read_text())
    prices = json.loads((ING / "rune_prices.json").read_text())
    out = []
    for key, r in runes.items():
        if r["maglv"] > maglv:
            continue
        if r["formula_type"] == "levelmagic":
            lo = abs(r["mina"]) * (level * 2 + maglv * 3) + abs(r["minb"])
            hi = abs(r["maxa"]) * (level * 2 + maglv * 3) + abs(r["maxb"])
            area_tiles = area_tile_count(r.get("area", []))
            avg_dmg = (lo + hi) / 2
            charges = r["charges"]
            price = prices.get(str(r["item_id"]), 0)
            per_cast_gp = price / max(1, charges)
            per_cast_aoe_dmg = avg_dmg * area_tiles
            out.append({
                "name": key,
                "item_id": r["item_id"],
                "maglv": r["maglv"],
                "damage_type": r["damage_type"],
                "min_dmg_per_tile": int(lo),
                "max_dmg_per_tile": int(hi),
                "avg_dmg_per_tile": avg_dmg,
                "area_tiles": area_tiles,
                "avg_aoe_dmg": per_cast_aoe_dmg,
                "charges": charges,
                "price_per_rune": price,
                "price_per_cast": per_cast_gp,
                "gp_per_damage": per_cast_gp / max(1, per_cast_aoe_dmg),
            })
        elif r["formula_type"] == "healing_callback":
            B = level * 2 + maglv * 3
            lo = max(r.get("heal_min_floor", 0), int(B * r["heal_base_mult"][0]))
            hi = int(B * r["heal_base_mult"][1])
            charges = r["charges"]
            price = prices.get(str(r["item_id"]), 0)
            out.append({
                "name": key,
                "item_id": r["item_id"],
                "maglv": r["maglv"],
                "damage_type": "healing",
                "min_heal": lo,
                "max_heal": hi,
                "avg_heal": (lo + hi) / 2,
                "charges": charges,
                "price_per_rune": price,
                "price_per_cast": price / max(1, charges),
                "gp_per_hp_healed": price / max(1, (lo + hi) / 2),
            })
    return out


def melee_damage_table(player: Player, weapons_db: dict) -> list[dict]:
    """For each candidate weapon, report max/avg damage at player stats."""
    rows = []
    for iid, w in weapons_db.items():
        if w["weapon_type"] not in ("sword", "axe", "club"):
            continue
        if w["attack"] < 20:
            continue
        raw_attack = w["attack"] - w.get("element_damage", 0)
        phys_max = max_weapon_damage(player.level, player.skill, max(0, raw_attack), 1.0)
        elem_max = 0
        if w.get("element_damage", 0) > 0:
            elem_max = max_weapon_damage(player.level, player.skill, w["element_damage"], 1.0)
        rows.append({
            "name": w["name"],
            "item_id": w["item_id"],
            "type": w["weapon_type"],
            "attack": w["attack"],
            "defense": w["defense"],
            "element_damage": w.get("element_damage", 0),
            "element_type": w.get("element_type", ""),
            "max_physical": phys_max,
            "max_element": elem_max,
            "max_total": phys_max + elem_max,
            "avg_total": (phys_max + elem_max) / 2,
        })
    rows.sort(key=lambda r: r["avg_total"], reverse=True)
    return rows


# ---------- Variance decomposition ----------

def variance_decomp(cluster: ClusterEval, monster_db: dict,
                    hours: float = 1.0, replicates: int = 60) -> dict:
    """Decompose XP/hr variance into swing-rng component vs monster-ai component.

    Approach: pair-run replicates with (swing_seed, ai_seed) factored. For each
    replicate we seed the player rng from swing_seed and the monster rng from
    ai_seed, but since our ServerRNG is a single stream, we approximate by
    block-shuffling seeds.
    """
    player = default_knight()
    # Two-way: 6 swing seeds × 10 ai seeds
    swings = [1, 2, 3, 4, 5, 6]
    ais = list(range(10, 10 + 10))
    matrix = np.zeros((len(swings), len(ais)))
    for i, s in enumerate(swings):
        for j, a in enumerate(ais):
            rng = ServerRNG(s * 10000 + a)
            cr = simulate_camp(player, cluster.creatures, monster_db,
                               respawn_avg_s=cluster.respawn_avg,
                               hours=hours, rng=rng)
            matrix[i, j] = cr.xp_per_hour
    total_var = float(np.var(matrix, ddof=1))
    swing_var = float(np.var(matrix.mean(axis=1), ddof=1))
    ai_var = float(np.var(matrix.mean(axis=0), ddof=1))
    resid = total_var - swing_var - ai_var
    return {
        "total_var": total_var,
        "between_swing_seeds_var": swing_var,
        "between_ai_seeds_var": ai_var,
        "residual_var": resid,
        "swing_pct": 100.0 * swing_var / max(1e-9, total_var),
        "ai_pct": 100.0 * ai_var / max(1e-9, total_var),
    }


# ---------- Orchestration ----------

def run_full_analysis(n_trials: int = 400, camp_replicates: int = 15,
                      camp_hours: float = 2.0):
    ART.mkdir(exist_ok=True)
    monster_db = json.loads((ING / "monsters.json").read_text())
    weapons_db = json.loads((ING / "weapons.json").read_text())
    weapons = {int(k): v for k, v in weapons_db.items()}
    areas_d = json.loads((ING / "hunting_areas.json").read_text())
    areas = areas_d if isinstance(areas_d, list) else areas_d.get("areas", [])

    print(f"[1/5] combat table ({n_trials} trials / creature)...")
    rows = combat_table(monster_db, n_trials=n_trials)
    (ART / "combat_table.json").write_text(json.dumps(rows, indent=2))

    print("[2/5] rune damage + weapon damage tables...")
    rune_rows = rune_damage_summary()
    (ART / "rune_table.json").write_text(json.dumps(rune_rows, indent=2))
    player = default_knight()
    weapon_rows = melee_damage_table(player, weapons)
    (ART / "weapon_table.json").write_text(json.dumps(weapon_rows, indent=2))

    loot_gp_path = ING / "loot_gp.json"
    loot_gp = json.loads(loot_gp_path.read_text()) if loot_gp_path.exists() else {}

    print(f"[3/5] ranking {len(areas)} hunting-area clusters...")
    clusters = score_hunting_areas(areas, rows, monster_db)
    # Only analyze clusters with at least one killable creature
    clusters = [c for c in clusters if c.win_rate_product > 0.1]
    # Deduplicate: many clusters share identical creature compositions and
    # respawn timings. Keep the canonical (best-centroid) representative per
    # (creatures signature, respawn_avg) bucket so the top-N is not
    # dominated by copies.
    seen: set = set()
    unique: list = []
    for c in clusters:
        key = (tuple(sorted(c.creatures.items())), c.respawn_avg)
        if key not in seen:
            seen.add(key)
            unique.append(c)
    clusters = unique

    print(f"[4/5] simulating top-12 camps ({camp_replicates}x{camp_hours}h)...")
    clusters = simulate_top_camps(clusters, monster_db, top_n=12,
                                  hours=camp_hours, replicates=camp_replicates,
                                  loot_gp_table=loot_gp)
    (ART / "cluster_ranking.json").write_text(
        json.dumps([asdict(c) for c in clusters[:50]], indent=2))

    print("[5/5] ANOVA + variance decomp on top 5...")
    anova = anova_strategies(clusters, monster_db, hours=1.0, replicates=15,
                             loot_gp_table=loot_gp)
    (ART / "anova.json").write_text(json.dumps(anova, indent=2))
    top_cluster = clusters[0]
    vd = variance_decomp(top_cluster, monster_db, hours=1.0)
    (ART / "variance_decomp.json").write_text(json.dumps(vd, indent=2))

    print("done.")
    return {
        "combat_rows": rows,
        "rune_rows": rune_rows,
        "weapon_rows": weapon_rows,
        "clusters": clusters,
        "anova": anova,
        "variance_decomp": vd,
    }


if __name__ == "__main__":
    run_full_analysis()
