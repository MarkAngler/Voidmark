#!/usr/bin/env python3
"""Statistical analysis of vocation balance.

Parses data/xml/vocations.xml and reproduces the engine's progression and
combat formulas (sources/iologindata.cpp, sources/vocation.cpp,
sources/combat.cpp, sources/player.cpp, sources/weapons.cpp) to produce a
Markdown report on stdout.

    python3 tools/balance/analyze_vocations.py
    python3 tools/balance/analyze_vocations.py --csv   # also write CSVs
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VOCATIONS_XML = REPO_ROOT / "data" / "xml" / "vocations.xml"
CSV_OUT_DIR = Path(__file__).resolve().parent / "out"

BASE_HP, BASE_MANA, BASE_CAP = 150, 0, 400
ROOK_GAIN_HP, ROOK_GAIN_MANA, ROOK_GAIN_CAP = 5, 5, 10

SKILL_BASE = {
    "fist": 50, "club": 50, "sword": 50, "axe": 50,
    "distance": 30, "shielding": 100, "fishing": 20,
}
MAGIC_BASE_TRIES = 1600

LEVEL_BENCHMARKS = [1, 8, 50, 100, 200, 400]
MAGIC_BENCHMARKS = [5, 10, 20, 30, 60]
SKILL_BENCHMARKS = [60, 80, 100, 120]

PRIMARY_SKILL = {
    "No Vocation": "fist",
    "Sorcerer": "club", "Master Sorcerer": "club",
    "Druid": "club", "Elder Druid": "club",
    "Paladin": "distance", "Royal Paladin": "distance",
    "Knight": "sword", "Elite Knight": "sword",
}

SPELL_BENCHMARKS = [(50, 30), (100, 60), (200, 80)]
WEAPON_BENCH_LEVEL = 100
WEAPON_BENCH_SKILL = 80
WEAPON_BENCH_ATTACK = 40
WAND_MIN, WAND_MAX = 8, 18  # representative wand of dragons-tier values


@dataclass
class Vocation:
    id: int
    name: str
    needpremium: int
    gaincap: int
    gainhp: int
    gainmana: int
    gainhpticks: int
    gainhpamount: int
    gainmanaticks: int
    gainmanaamount: int
    manamultiplier: float
    soulmax: int
    gainsoulticks: int
    fromvoc: int
    lessloss: int
    formula: dict = field(default_factory=dict)
    skill: dict = field(default_factory=dict)


def parse_vocations(path: Path) -> list[Vocation]:
    root = ET.parse(path).getroot()
    out = []
    for node in root.findall("vocation"):
        a = node.attrib
        v = Vocation(
            id=int(a["id"]),
            name=a["name"],
            needpremium=int(a.get("needpremium", 0)),
            gaincap=int(a["gaincap"]),
            gainhp=int(a["gainhp"]),
            gainmana=int(a["gainmana"]),
            gainhpticks=int(a["gainhpticks"]),
            gainhpamount=int(a["gainhpamount"]),
            gainmanaticks=int(a["gainmanaticks"]),
            gainmanaamount=int(a["gainmanaamount"]),
            manamultiplier=float(a["manamultiplier"]),
            soulmax=int(a["soulmax"]),
            gainsoulticks=int(a["gainsoulticks"]),
            fromvoc=int(a.get("fromvoc", a["id"])),
            lessloss=int(a.get("lessloss", 0)),
        )
        f = node.find("formula")
        if f is not None:
            v.formula = {k: float(val) for k, val in f.attrib.items()}
        s = node.find("skill")
        if s is not None:
            v.skill = {k: float(val) for k, val in s.attrib.items()}
        out.append(v)
    return out


# --- engine formulas ------------------------------------------------------

def stat_at_level(level: int, base: int, rook_gain: int, voc_gain: int) -> int:
    """Reproduces sources/iologindata.cpp:1629-1653.

    Levels 2..8 use the rook gain regardless of vocation; vocation gains
    only apply for level > 8.
    """
    if level <= 1:
        return base
    pre = min(7, level - 1)
    val = base + rook_gain * pre
    if level > 8:
        val += voc_gain * (level - 8)
    return val


def regen_per_sec(amount: int, ticks: int) -> float:
    return amount / ticks if ticks else 0.0


def cumulative_magic_mana(target_ml: int, mult: float) -> float:
    """Σ 1600 * mult^(m-1) for m=1..target_ml (vocation.cpp:548)."""
    if target_ml <= 0:
        return 0.0
    if abs(mult - 1.0) < 1e-12:
        return MAGIC_BASE_TRIES * target_ml
    return MAGIC_BASE_TRIES * (mult ** target_ml - 1) / (mult - 1)


def cumulative_skill_tries(target: int, base: int, mult: float) -> float:
    """Σ base * mult^(level-11) for level=12..target (vocation.cpp:525-537)."""
    if target <= 11:
        return 0.0
    n = target - 11
    if abs(mult - 1.0) < 1e-12:
        return base * n
    return base * mult * (mult ** n - 1) / (mult - 1)


def spell_max_damage(level: int, magic: int, mag_mult: float, factor: float = 1.0) -> float:
    """combat.cpp:76-82 + player.cpp:5220-5230 (factor=1.0 generic)."""
    raw = (level * 2 + magic * 3) * factor
    return raw * mag_mult


def weapon_max_damage(level: int, skill: int, attack: int,
                      attack_factor: float, mult: float) -> float:
    """weapons.cpp:152-158 (modern formula), times vocation multiplier."""
    base = 2 * (attack * (skill + 5.8) / 25 + (level - 1) / 10.0) / attack_factor
    return base * mult


# --- formatting helpers ---------------------------------------------------

def fmt(n) -> str:
    if isinstance(n, float):
        if n >= 1e9:
            return f"{n:.2e}"
        if n >= 1000:
            return f"{n:,.0f}"
        return f"{n:,.2f}"
    if isinstance(n, int):
        if abs(n) >= 1e9:
            return f"{n:.2e}"
        return f"{n:,}"
    return str(n)


def render_table(headers: list[str], rows: list[list]) -> str:
    str_rows = [[fmt(c) for c in row] for row in rows]
    widths = [len(h) for h in headers]
    for r in str_rows:
        for i, c in enumerate(r):
            widths[i] = max(widths[i], len(c))
    line = "| " + " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers)) + " |"
    sep = "|" + "|".join("-" * (w + 2) for w in widths) + "|"
    body = "\n".join(
        "| " + " | ".join(c.ljust(widths[i]) for i, c in enumerate(r)) + " |"
        for r in str_rows
    )
    return f"{line}\n{sep}\n{body}"


def secs_to_human(s: float) -> str:
    if s < 60:
        return f"{s:.0f}s"
    if s < 3600:
        return f"{s/60:.1f}m"
    if s < 86400:
        return f"{s/3600:.1f}h"
    return f"{s/86400:.1f}d"


# --- table builders -------------------------------------------------------

def build_raw_table(vocs):
    headers = ["voc", "prem", "gainHP", "gainMP", "gainCap",
               "hpTicks", "mpTicks", "manaMult", "soulMax", "soulTicks", "lessLoss"]
    rows = [[v.name, v.needpremium, v.gainhp, v.gainmana, v.gaincap,
             v.gainhpticks, v.gainmanaticks, v.manamultiplier,
             v.soulmax, v.gainsoulticks, v.lessloss] for v in vocs]
    return headers, rows


def build_skill_mult_table(vocs):
    headers = ["voc"] + list(SKILL_BASE.keys()) + ["exp"]
    rows = []
    for v in vocs:
        rows.append([v.name] + [v.skill.get(k, 1.0) for k in SKILL_BASE]
                    + [v.skill.get("experience", 1.0)])
    return headers, rows


def build_survivability_table(vocs):
    headers = ["voc", "level", "HP", "Cap", "HP/sec", "HP full(s)"]
    rows = []
    for v in vocs:
        hp_regen = regen_per_sec(v.gainhpamount, v.gainhpticks)
        for L in LEVEL_BENCHMARKS:
            hp = stat_at_level(L, BASE_HP, ROOK_GAIN_HP, v.gainhp)
            cap = stat_at_level(L, BASE_CAP, ROOK_GAIN_CAP, v.gaincap)
            rows.append([v.name, L, hp, cap, round(hp_regen, 3),
                         round(hp / hp_regen, 0) if hp_regen else float("inf")])
    return headers, rows


def build_mana_table(vocs):
    headers = ["voc", "level", "Mana", "MP/sec", "MP full(s)", "MP full"]
    rows = []
    for v in vocs:
        mp_regen = regen_per_sec(v.gainmanaamount, v.gainmanaticks)
        for L in LEVEL_BENCHMARKS:
            mp = stat_at_level(L, BASE_MANA, ROOK_GAIN_MANA, v.gainmana)
            t = mp / mp_regen if mp_regen else float("inf")
            rows.append([v.name, L, mp, round(mp_regen, 3),
                         round(t, 0), secs_to_human(t)])
    return headers, rows


def build_magic_cost_table(vocs):
    headers = ["voc", "manaMult"] + [f"ML {m} mana" for m in MAGIC_BENCHMARKS] \
              + [f"ML {m} regen-h" for m in MAGIC_BENCHMARKS]
    rows = []
    for v in vocs:
        mp_regen = regen_per_sec(v.gainmanaamount, v.gainmanaticks)
        manas = [cumulative_magic_mana(m, v.manamultiplier) for m in MAGIC_BENCHMARKS]
        hours = [(mp / mp_regen) / 3600 if mp_regen else float("inf") for mp in manas]
        rows.append([v.name, v.manamultiplier] + manas + hours)
    return headers, rows


def build_skill_cost_table(vocs):
    headers = ["voc", "skill", "mult"] + [f"to {s}" for s in SKILL_BENCHMARKS]
    rows = []
    for v in vocs:
        skill_name = PRIMARY_SKILL.get(v.name, "fist")
        mult = v.skill.get(skill_name, 1.0)
        base = SKILL_BASE[skill_name]
        tries = [cumulative_skill_tries(s, base, mult) for s in SKILL_BENCHMARKS]
        rows.append([v.name, skill_name, mult] + tries)
    return headers, rows


def build_spell_damage_table(vocs):
    headers = ["voc", "magMult"] + [f"L{L}/ML{M} max" for L, M in SPELL_BENCHMARKS]
    rows = []
    for v in vocs:
        mag = v.formula.get("magDamage", 1.0)
        dmg = [spell_max_damage(L, M, mag) for L, M in SPELL_BENCHMARKS]
        rows.append([v.name, mag] + dmg)
    return headers, rows


def build_weapon_damage_table(vocs):
    headers = ["voc", "meleeMult", "distMult", "wandMult",
               "melee max", "dist max", "wand max"]
    rows = []
    for v in vocs:
        mel = v.formula.get("meleeDamage", 1.0)
        dist = v.formula.get("distDamage", 1.0)
        wand = v.formula.get("wandDamage", 1.0)
        m_dmg = weapon_max_damage(WEAPON_BENCH_LEVEL, WEAPON_BENCH_SKILL,
                                  WEAPON_BENCH_ATTACK, 1.0, mel)
        d_dmg = weapon_max_damage(WEAPON_BENCH_LEVEL, WEAPON_BENCH_SKILL,
                                  WEAPON_BENCH_ATTACK, 1.0, dist)
        w_dmg = WAND_MAX * wand
        rows.append([v.name, mel, dist, wand,
                     round(m_dmg, 1), round(d_dmg, 1), round(w_dmg, 1)])
    return headers, rows


def build_promotion_delta(vocs):
    by_id = {v.id: v for v in vocs}
    pairs = []
    for v in vocs:
        if v.fromvoc != v.id and v.fromvoc in by_id and by_id[v.fromvoc].id != v.id:
            base = by_id[v.fromvoc]
            if base.id != v.id and base.name != v.name:
                pairs.append((base, v))
    headers = ["base → promoted", "field", "base", "promoted", "delta"]
    rows = []
    fields = ["gainhp", "gainmana", "gaincap", "gainhpticks", "gainmanaticks",
              "manamultiplier", "soulmax", "gainsoulticks", "lessloss"]
    for base, prom in pairs:
        label = f"{base.name} → {prom.name}"
        any_change = False
        for f_ in fields:
            b_, p_ = getattr(base, f_), getattr(prom, f_)
            if b_ != p_:
                rows.append([label, f_, b_, p_, p_ - b_])
                any_change = True
        for k in sorted(set(base.formula) | set(prom.formula)):
            b_, p_ = base.formula.get(k, 1.0), prom.formula.get(k, 1.0)
            if b_ != p_:
                rows.append([label, f"formula.{k}", b_, p_, p_ - b_])
                any_change = True
        for k in sorted(set(base.skill) | set(prom.skill)):
            b_, p_ = base.skill.get(k, 1.0), prom.skill.get(k, 1.0)
            if b_ != p_:
                rows.append([label, f"skill.{k}", b_, p_, p_ - b_])
                any_change = True
        if not any_change:
            rows.append([label, "(no diffs)", "", "", ""])
    return headers, rows


# --- findings -------------------------------------------------------------

def build_findings(vocs):
    findings = []

    flat = [v.name for v in vocs
            if all(abs(x - 1.0) < 1e-9 for x in v.formula.values())]
    if flat:
        findings.append(
            f"**Dead `<formula>` config:** every formula multiplier is 1.0 for "
            f"{', '.join(flat)}. Vocation has *no* damage/defense differentiation "
            f"in combat — magDamage, meleeDamage, distDamage, wandDamage, defense, "
            f"magDefense, armor, magHealingDamage are all the engine default. "
            f"All inter-vocation damage gaps come from skill multipliers and "
            f"mana pool / magic-level cost, never from the formula block.")

    by_id = {v.id: v for v in vocs}
    for v in vocs:
        if v.fromvoc != v.id and v.fromvoc in by_id and by_id[v.fromvoc].name != v.name:
            base = by_id[v.fromvoc]
            same_skill = all(
                abs(base.skill.get(k, 1.0) - v.skill.get(k, 1.0)) < 1e-9
                for k in set(base.skill) | set(v.skill))
            same_formula = all(
                abs(base.formula.get(k, 1.0) - v.formula.get(k, 1.0)) < 1e-9
                for k in set(base.formula) | set(v.formula))
            if same_skill and same_formula:
                findings.append(
                    f"**Promotion gives no combat boost:** {base.name} → {v.name} "
                    f"has identical skill and formula multipliers. Differences are "
                    f"only quality-of-life: soul cap/regen, skill-loss reduction, "
                    f"and mana/HP regen ticks.")

    for v in vocs:
        if v.manamultiplier >= 3.0:
            ml30 = cumulative_magic_mana(30, v.manamultiplier)
            findings.append(
                f"**Magic-level grind effectively impossible:** {v.name} has "
                f"manamultiplier={v.manamultiplier}. Reaching ML 30 costs "
                f"{fmt(ml30)} mana — at this vocation's regen "
                f"({regen_per_sec(v.gainmanaamount, v.gainmanaticks):.3f} mp/s), "
                f"that is "
                f"{secs_to_human(ml30 / regen_per_sec(v.gainmanaamount, v.gainmanaticks))} "
                f"of pure regen, ignoring spending. ML 60 is "
                f"{fmt(cumulative_magic_mana(60, v.manamultiplier))} mana.")

    sorc = next((v for v in vocs if v.name == "Sorcerer"), None)
    if sorc and abs(sorc.skill.get("club", 1.0) - 2.0) < 1e-9:
        findings.append(
            "**Sorcerer/Druid melee skill multipliers are dead config:** "
            "Sorcerer's club/sword/axe multipliers are 2.0 (the *easiest* in the "
            "table), but Sorcerer playstyle uses wand/spells, so this multiplier "
            "is rarely exercised. The same applies to distance=2.0 for "
            "Sorcerer/Druid. If a melee Sorcerer ever did exist, they'd train "
            "club nearly twice as fast as a Knight (skill mult 1.1) — likely "
            "unintentional.")

    sw = []
    for v in vocs:
        for k, m in v.skill.items():
            if k == "experience":
                continue
            if m < 1.05 or m > 2.0:
                sw.append((v.name, k, m))
    if sw:
        findings.append(
            "**Skill multipliers outside canonical 1.1–2.0 band:** "
            + ", ".join(f"{n}:{k}={m}" for n, k, m in sw)
            + " — review whether intentional.")

    knight = next((v for v in vocs if v.name == "Knight"), None)
    paladin = next((v for v in vocs if v.name == "Paladin"), None)
    if knight and paladin and knight.skill["distance"] == 1.4 and paladin.skill["distance"] == 1.1:
        findings.append(
            "**Knight trains distance faster than Paladin** (1.4 vs 1.1). "
            "This contradicts class identity — Paladin is the canonical "
            "ranged class. This appears to be a column transposition: "
            "Knight's *melee* multipliers are 1.1 and Paladin's are 1.2, "
            "but a Knight reaches dist 100 in fewer tries than a Paladin "
            "reaches sword 100. Verify intent.")

    base_pairs = [("Sorcerer", "Druid"), ("Druid", "Druid")]
    sorc_v = next((v for v in vocs if v.name == "Sorcerer"), None)
    druid_v = next((v for v in vocs if v.name == "Druid"), None)
    if sorc_v and druid_v:
        if (sorc_v.gainhp == druid_v.gainhp and sorc_v.gainmana == druid_v.gainmana
                and sorc_v.gaincap == druid_v.gaincap):
            findings.append(
                "**Sorcerer and Druid are mechanically identical** in HP/MP/Cap "
                "gains and regen. Differentiation is only via spell list "
                "(spells.xml gates Heal Friend / Mass Healing / paralyze rune to "
                "Druid, and Energy Wave / Ultimate Explosion to Sorcerer). The "
                "vocation table itself does not separate them.")

    return findings


# --- main -----------------------------------------------------------------

def write_csvs(tables: dict[str, tuple[list, list]]):
    CSV_OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, (headers, rows) in tables.items():
        path = CSV_OUT_DIR / f"{name}.csv"
        with path.open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(headers)
            for r in rows:
                w.writerow(r)
        print(f"wrote {path.relative_to(REPO_ROOT)}", file=sys.stderr)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--csv", action="store_true",
                    help=f"also write CSVs to {CSV_OUT_DIR.relative_to(REPO_ROOT)}/")
    ap.add_argument("--vocations", default=str(VOCATIONS_XML),
                    help="path to vocations.xml")
    args = ap.parse_args(argv)

    vocs = parse_vocations(Path(args.vocations))

    sections = [
        ("raw_stats", "Raw vocation stats", build_raw_table(vocs)),
        ("skill_mult", "Skill multipliers", build_skill_mult_table(vocs)),
        ("survivability", f"Survivability — HP & cap at levels {LEVEL_BENCHMARKS}",
         build_survivability_table(vocs)),
        ("mana_economy", f"Mana economy — pool & time-to-full at levels {LEVEL_BENCHMARKS}",
         build_mana_table(vocs)),
        ("magic_cost", f"Magic-level cost — cumulative mana to reach ML {MAGIC_BENCHMARKS}",
         build_magic_cost_table(vocs)),
        ("skill_cost", f"Primary-skill cost — cumulative tries to reach skill {SKILL_BENCHMARKS}",
         build_skill_cost_table(vocs)),
        ("spell_damage", f"Spell max damage at (level, magic level) {SPELL_BENCHMARKS}, factor=1.0",
         build_spell_damage_table(vocs)),
        ("weapon_damage", f"Weapon max damage at level {WEAPON_BENCH_LEVEL}, "
                          f"skill {WEAPON_BENCH_SKILL}, attack {WEAPON_BENCH_ATTACK}, "
                          f"attack-mode (wand uses min={WAND_MIN}, max={WAND_MAX})",
         build_weapon_damage_table(vocs)),
        ("promotion_delta", "Promotion deltas (base vs promoted)",
         build_promotion_delta(vocs)),
    ]

    print("# Vocation balance analysis")
    print()
    print(f"Generated from `{VOCATIONS_XML.relative_to(REPO_ROOT)}` "
          f"({len(vocs)} vocations).\n")
    print("Engine formulas reproduced from "
          "`sources/iologindata.cpp:1629-1653`, "
          "`sources/vocation.cpp:500-548`, "
          "`sources/combat.cpp:76-82`, "
          "`sources/player.cpp:5220-5230`, "
          "`sources/weapons.cpp:152-158,627-649,885-922`.\n")

    for _key, title, (headers, rows) in sections:
        print(f"## {title}\n")
        print(render_table(headers, rows))
        print()

    print("## Findings\n")
    for f_ in build_findings(vocs):
        print(f"- {f_}\n")

    if args.csv:
        write_csvs({k: (h, r) for k, _t, (h, r) in sections})


if __name__ == "__main__":
    main()
