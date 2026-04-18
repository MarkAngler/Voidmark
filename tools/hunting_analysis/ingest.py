"""Data ingestion layer for the hunting analysis pipeline.

Parses and normalizes:
  - monsters:       data/monster/*.xml       via existing extract_monsters.parse_monster_file
  - spells/runes:   data/spells/spells.xml   + data/spells/scripts/*/
  - weapons:        data/items/items.xml     (sword/axe/club attacks + element damage)
  - NPC prices:     data/npc/scripts/*.lua   (buy prices for runes)
  - spawns:         site/data/spawns.json    (already parsed by mapbuild pipeline)

Formulas and item IDs are sourced from the server repo directly; see REPORT.md.
"""

from __future__ import annotations

import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools" / "mapbuild"))
from extract_monsters import parse_monster_file  # noqa: E402


# ----- Weapon ingest -----

@dataclass
class Weapon:
    item_id: int
    name: str
    attack: int
    defense: int
    extra_def: int = 0
    element_damage: int = 0
    element_type: str = ""
    weapon_type: str = ""


WEAPON_ATTR_PATTERN = re.compile(r'key="([^"]+)"\s+value="([^"]+)"')


def parse_weapons(items_xml: Path) -> dict[int, Weapon]:
    """Scan items.xml for melee weapons that a knight would use."""
    tree = ET.parse(items_xml)
    out: dict[int, Weapon] = {}
    for item in tree.getroot().findall("item"):
        item_id = item.get("id")
        if item_id is None:
            continue
        try:
            iid = int(item_id)
        except ValueError:
            continue
        name = (item.get("name") or "").strip()
        if not name:
            continue
        attrs: dict[str, str] = {}
        for attr in item.findall("attribute"):
            k = attr.get("key")
            v = attr.get("value")
            if k and v is not None:
                attrs[k] = v
        wt = attrs.get("weaponType", "").lower()
        if wt not in ("sword", "axe", "club"):
            continue
        attack = int(attrs.get("attack", "0") or 0)
        if attack <= 0:
            continue
        elem = 0
        etype = ""
        for k, v in attrs.items():
            if k.startswith("element") and k != "elementType":
                try:
                    elem = int(v)
                    etype = k[len("element"):].lower()
                except ValueError:
                    pass
        out[iid] = Weapon(
            item_id=iid,
            name=name,
            attack=attack,
            defense=int(attrs.get("defense", "0") or 0),
            extra_def=int(attrs.get("extradef", "0") or 0),
            element_damage=elem,
            element_type=etype,
            weapon_type=wt,
        )
    return out


# ----- Spell / rune ingest -----

@dataclass
class Rune:
    item_id: int
    words: str
    maglv: int
    charges: int
    script: str
    needtarget: bool = False
    aggressive: bool = True
    # Parsed from script:
    formula_type: str = ""     # "levelmagic" | "healing_callback" | ""
    mina: float = 0.0
    minb: float = 0.0
    maxa: float = 0.0
    maxb: float = 0.0
    damage_type: str = ""      # "physical", "fire", "energy", "poison", "healing"
    area: list[list[int]] = field(default_factory=list)
    heal_base_mult: tuple[float, float] = (0.0, 0.0)  # (min_mult, max_mult) on B=2L+3M
    heal_min_floor: int = 0


COMBAT_TYPE_MAP = {
    "COMBAT_PHYSICALDAMAGE": "physical",
    "COMBAT_FIREDAMAGE": "fire",
    "COMBAT_ENERGYDAMAGE": "energy",
    "COMBAT_EARTHDAMAGE": "poison",
    "COMBAT_ICEDAMAGE": "ice",
    "COMBAT_HOLYDAMAGE": "holy",
    "COMBAT_DEATHDAMAGE": "death",
    "COMBAT_LIFEDRAIN": "lifedrain",
    "COMBAT_MANADRAIN": "manadrain",
    "COMBAT_HEALING": "healing",
}

LEVELMAGIC_RE = re.compile(
    r"setCombatFormula\s*\(\s*\w+\s*,\s*COMBAT_FORMULA_LEVELMAGIC\s*,"
    r"\s*(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)\s*\)"
)
COMBAT_TYPE_RE = re.compile(r"COMBAT_PARAM_TYPE\s*,\s*(COMBAT_[A-Z]+)")
AREA_ROWS_RE = re.compile(r"\{\s*(\d+(?:\s*,\s*\d+)+)\s*\}")


def parse_rune_script(path: Path) -> dict[str, Any]:
    """Extract level/magic formula, area grid, and damage type from a rune Lua script.

    Mirrors the server's spell runtime: only the fields we need for the model.
    """
    text = path.read_text()
    out: dict[str, Any] = {
        "formula_type": "",
        "mina": 0.0, "minb": 0.0, "maxa": 0.0, "maxb": 0.0,
        "damage_type": "",
        "area": [],
        "heal_base_mult": (0.0, 0.0),
        "heal_min_floor": 0,
    }
    m = LEVELMAGIC_RE.search(text)
    if m:
        out["formula_type"] = "levelmagic"
        out["mina"], out["minb"], out["maxa"], out["maxb"] = (float(g) for g in m.groups())
    # UH-style callback
    if "onGetFormulaValues" in text:
        out["formula_type"] = "healing_callback"
        mmin = re.search(r"min\s*=\s*\(level\s*\*\s*2\s*\+\s*maglevel\s*\*\s*3\)\s*\*\s*([0-9.]+)", text)
        mmax = re.search(r"max\s*=\s*\(level\s*\*\s*2\s*\+\s*maglevel\s*\*\s*3\)\s*\*\s*([0-9.]+)", text)
        floor = re.search(r"if\s+min\s*<\s*(\d+)\s+then", text)
        if mmin and mmax:
            out["heal_base_mult"] = (float(mmin.group(1)), float(mmax.group(1)))
        if floor:
            out["heal_min_floor"] = int(floor.group(1))
    t = COMBAT_TYPE_RE.search(text)
    if t:
        out["damage_type"] = COMBAT_TYPE_MAP.get(t.group(1), "")
    # Extract all inner rows of digits (anywhere in the script). The outer
    # createCombatArea({...}) wrapper can't be matched by a single non-greedy
    # regex because nested braces don't play with `.+?`; instead we grab every
    # `{d,d,...}` row we see.
    area_rows: list[list[int]] = []
    for row_match in AREA_ROWS_RE.finditer(text):
        cells = [int(x.strip()) for x in row_match.group(1).split(",") if x.strip()]
        if cells:
            area_rows.append(cells)
    out["area"] = area_rows
    return out


def parse_runes(spells_xml: Path, scripts_dir: Path) -> dict[str, Rune]:
    tree = ET.parse(spells_xml)
    runes: dict[str, Rune] = {}
    for r in tree.getroot().findall("rune"):
        name = r.get("name", "").strip()
        iid = int(r.get("id", "0") or 0)
        script = r.get("script", "")
        if not script:
            continue
        entry = Rune(
            item_id=iid,
            words=name,
            maglv=int(r.get("maglv", "0") or 0),
            charges=int(r.get("charges", "1") or 1),
            script=script,
            needtarget=r.get("needtarget", "0") in ("1", "true"),
            aggressive=r.get("aggressive", "1") != "0",
        )
        script_path = scripts_dir / script
        if script_path.exists():
            info = parse_rune_script(script_path)
            for k, v in info.items():
                setattr(entry, k, v)
        runes[name] = entry
    return runes


# ----- NPC price ingest -----

RUNE_ID_NAMES = {
    2273: "ultimate healing rune",
    2311: "heavy magic missile rune",
    2313: "explosion rune",
    2304: "great fireball rune",
    2302: "fireball rune",
    2305: "firebomb rune",
    2268: "sudden death rune",
    2265: "intense healing rune",
    2287: "light magic missile rune",
    2293: "magic wall rune",
}

# Pattern: shopModule:addBuyableItem({'keyword'}, 2273, 175, 'ultimate healing rune')
BUY_RE = re.compile(
    r"addBuyableItem(?:Container)?\s*\(\s*\{[^}]*\}\s*,\s*(\d+)[^,]*,\s*(\d+)",
    re.IGNORECASE,
)
BUY_RE2 = re.compile(
    r"shopModule:addBuyableItem\([^,]+,\s*(\d+)[^,]*,\s*(\d+)",
    re.IGNORECASE,
)


def parse_npc_prices(npc_dir: Path) -> dict[int, int]:
    """Return a map item_id -> lowest buy price found across all NPCs."""
    prices: dict[int, int] = {}
    for p in npc_dir.glob("scripts/*.lua"):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for regex in (BUY_RE, BUY_RE2):
            for m in regex.finditer(text):
                try:
                    iid = int(m.group(1))
                    price = int(m.group(2))
                except ValueError:
                    continue
                if iid not in RUNE_ID_NAMES:
                    continue
                if iid not in prices or price < prices[iid]:
                    prices[iid] = price
    # XML-based NPCs: look for <item id=... buy=.../>
    xml_price_re = re.compile(r'<item\s+[^>]*id="(\d+)"[^>]*buy="(\d+)"', re.IGNORECASE)
    for p in npc_dir.glob("*.xml"):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in xml_price_re.finditer(text):
            iid = int(m.group(1))
            price = int(m.group(2))
            if iid in RUNE_ID_NAMES and (iid not in prices or price < prices[iid]):
                prices[iid] = price
    return prices


# ----- Monster loot -> gp/kill -----

# Common vendor sell prices (from merchant.xml / npc scripts). Small, curated,
# sufficient for the creatures we hunt. Items not listed are ignored (0 gp).
VENDOR_SELL = {
    2148: 1,       # gold coin (chance 100 in nearly every loot)
    2152: 100,     # platinum coin
    2392: 1000,    # fire sword
    2393: 6000,    # giant sword
    2395: 2000,    # ice rapier
    2396: 5000,    # fire axe
    2400: 6000,    # two handed sword
    2402: 95,      # short sword
    2404: 35,      # sabre
    2406: 90,      # sword
    2407: 25,      # axe
    2408: 95,      # hatchet
    2409: 900,     # serpent sword
    2413: 30,      # longsword
    2414: 65,      # broadsword
    2415: 35,      # battle axe
    2417: 150,     # battle hammer
    2423: 40,      # club
    2425: 50,      # morning star
    2429: 60,      # double axe
    2430: 2000,    # knight axe
    2432: 220,     # fire axe
    2433: 500,     # dragon hammer
    2445: 25,      # studded shield
    2450: 70,      # crossbow
    2456: 100,     # bow
    2461: 10,      # leather helmet
    2462: 70,      # soldier helmet
    2463: 180,     # chain helmet
    2464: 100,     # iron helmet
    2465: 250,     # brass helmet
    2471: 7500,    # golden helmet
    2475: 900,     # warrior helmet
    2477: 165,     # legion helmet
    2478: 500,     # steel helmet
    2480: 5000,    # crusader helmet
    2483: 500,     # viking helmet
    2486: 250,     # strange helmet
    2487: 600,     # royal helmet
    2488: 430,     # crown helmet
    2491: 250,     # war helmet (dark helmet)
    2492: 900,     # crown legs... varies
    2501: 2000,    # crown armor
    2503: 5000,    # magic plate armor
    2510: 300,     # plate shield
    2516: 5000,    # dragon shield
    2520: 1500,    # demon shield
    2523: 12000,   # mastermind shield
    2525: 40000,   # medusa shield
    2528: 800,     # tower shield
    2529: 80,      # black shield
    2530: 43,      # copper shield
    2534: 200,     # brass shield
    2535: 1500,    # bone shield
    2537: 200,     # ornamented shield
    2539: 95,      # rose shield
    2542: 150,     # dwarven shield
    2544: 2,       # arrow
    2548: 3,       # poison arrow
    2550: 18,      # scythe
    2551: 8,       # spear
    2557: 30,      # scale armor
    2560: 95,      # chain armor
    2464: 100,     # iron helmet (dup ok)
    2465: 250,     # brass helmet (dup ok)
    2463: 180,     # chain helmet (dup ok)
    7401: 1500,    # dragonbone staff roughly
    2643: 40,      # leather boots
    2645: 30000,   # boots of haste
    2646: 2500,    # crown boots
    2647: 50,      # patched boots
    2648: 400,     # steel boots
    2649: 2000,    # golden boots
    2650: 30,      # sandals
    2656: 150,     # blue robe (varies)
    2657: 1500,    # magician's robe
    2658: 500,     # mystic's robe
    2660: 150,     # scale legs
    2661: 500,     # phoenix shield (dup ok)
    2464: 100,     # iron helmet
    2465: 250,     # brass helmet
    2470: 15000,   # golden armor
    2472: 8000,    # magic plate armor
    2473: 80,      # chain armor
    2476: 450,     # knight armor
    2477: 165,     # legion helmet
    2483: 500,     # viking helmet
    2487: 600,     # royal helmet
    2488: 400,     # crown helmet
    2491: 250,     # dark helmet
    2486: 250,     # strange helmet
    2487: 600,     # royal helmet
    2488: 400,     # crown helmet
    2492: 15000,   # golden legs
    2493: 95,      # plate legs
    2494: 600,     # knight legs
    2495: 1500,    # crown legs
    2496: 5000,    # magic plate legs (if any)
    2497: 8000,    # crown legs approx
    2501: 2000,    # crown armor
    2503: 5000,    # magic plate armor approx
    2504: 900,     # crown armor approx (varies)
    2463: 180,
    2465: 250,
    2467: 65,      # leather armor
    2660: 150,     # scale legs
    2648: 400,     # steel boots
    2514: 15000,   # mastermind shield approx
    2515: 15000,   # demon shield approx (varies)
    7451: 9000,    # spike sword
    7382: 1500,    # crimson sword (not applicable)
    7404: 15000,   # assassin dagger... etc
    2165: 150,     # stealth ring
    2167: 500,     # energy ring
    2168: 100,     # life ring
    2169: 150,     # time ring
    2170: 250,     # gold ring
    2171: 5000,    # platinum amulet
    2172: 50,      # bronze amulet
    2173: 1500,    # amulet of loss (drop only)
    2175: 300,     # spellbook
    2181: 15000,   # wand of inferno
    2182: 15000,   # wand of cosmic energy
    2183: 6000,    # wand of decay
    2189: 500,     # wand of dragonbreath
    2195: 30000,   # boots of haste (dup)
    2197: 30000,   # stone skin amulet (rare)
    2199: 4000,    # garlic necklace
    2200: 5000,    # protection amulet
    2205: 8000,    # crystal ring
    2214: 100,     # ring of healing
    2215: 100,     # might ring (varies)
    7408: 12000,   # serpent sword approx (varies)
    7455: 8000,    # noble axe
    7419: 15000,   # dreaded cleaver
    7387: 8000,    # bloody edge
    2158: 300,     # small sapphire
    2159: 300,     # small emerald
    2160: 5000,    # crystal coin (gold ingot) approx
    2145: 250,     # small diamond
    2146: 500,     # small ruby
    2147: 500,     # small amethyst
    7590: 50,      # green mushroom
    6300: 5,       # death ring... misc
}


# ----- Spawn / hunting area ingest -----

def load_spawns(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def load_hunting_areas(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    d = json.loads(path.read_text())
    return d.get("areas", []) if isinstance(d, dict) else d


# ----- Orchestration -----

def ingest_all(repo_root: Path, out_dir: Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    monsters_path = out_dir / "monsters.json"
    if (repo_root / "site" / "data" / "monsters.json").exists():
        # Reuse pre-built monster index
        monsters_raw = json.loads((repo_root / "site" / "data" / "monsters.json").read_text())
    else:
        monsters_raw = {}
        for p in sorted((repo_root / "data" / "monster").glob("*.xml")):
            e = parse_monster_file(str(p))
            if e is not None:
                monsters_raw[e["name"].lower()] = e
    monsters_path.write_text(json.dumps(monsters_raw))

    weapons = parse_weapons(repo_root / "data" / "items" / "items.xml")
    (out_dir / "weapons.json").write_text(
        json.dumps({str(k): asdict(v) for k, v in weapons.items()})
    )

    runes = parse_runes(
        repo_root / "data" / "spells" / "spells.xml",
        repo_root / "data" / "spells" / "scripts",
    )
    (out_dir / "runes.json").write_text(
        json.dumps({k: asdict(v) for k, v in runes.items()})
    )

    prices = parse_npc_prices(repo_root / "data" / "npc")
    # Fallback prices from the Rachel NPC (BP prices confirmed in data/npc/scripts/Rachel.lua):
    # BP UH 2500 gp / 20 = 125; BP HMM 1000 gp / 100 charges = 10 per charge (50 per rune of 5);
    # BP Explosion not sold there, fallback 250; BP GFB not sold, fallback 180.
    fallback = {2273: 175, 2311: 50, 2313: 250, 2304: 180, 2302: 125, 2265: 95}
    for iid, fp in fallback.items():
        prices.setdefault(iid, fp)
    (out_dir / "rune_prices.json").write_text(json.dumps(prices))

    spawns = load_spawns(repo_root / "site" / "data" / "spawns.json")
    (out_dir / "spawns.json").write_text(json.dumps(spawns))

    areas = load_hunting_areas(repo_root / "site" / "data" / "hunting_areas.json")
    (out_dir / "hunting_areas.json").write_text(json.dumps(areas))

    return {
        "monsters": len(monsters_raw),
        "weapons": len(weapons),
        "runes": len(runes),
        "rune_prices": len(prices),
        "spawn_entries": len(spawns.get("spawns", [])) if isinstance(spawns, dict) else 0,
        "hunting_areas": len(areas),
    }


if __name__ == "__main__":
    counts = ingest_all(REPO, REPO / "tools" / "hunting_analysis" / "artifacts" / "ingest")
    for k, v in counts.items():
        print(f"  {k}: {v}")
