"""Convert data/monster/*.xml into site/data/monsters.json for the
frontend's autocomplete, info card, search, and hunting calculator.

Output shape (keyed by lower-case monster name):
{
  "demon": {
    "name": "demon",
    "description": "a demon",
    "race": "fire",
    "hp": 8200,
    "exp": 6000,
    "speed": 240,
    "look": {"type": 35, "corpse": 2916},
    "hostile": true,
    "file": "demon.xml",
    "armor": 40,
    "defense": 65,
    "attacks": [
      {"name": "melee", "interval": 2000, "chance": 100, "skill": 120, "attack": 80},
      {"name": "fire", "interval": 1000, "chance": 34, "range": 7, ...}
    ],
    "healing": [{"interval": 1000, "chance": 15, "min": 90, "max": 150}],
    "elements": {"fire": 100, "earth": 100, "energy": 100},
    "immunities": ["lifedrain", "paralyze", "invisible"],
    "summons": {"max": 1, "list": [{"name": "fire elemental", ...}]},
    "runonhealth": 0
  },
  ...
}
"""

import glob
import json
import os
import sys
import xml.etree.ElementTree as ET


def _intattr(elem, key, default=None):
    try:
        return int(elem.get(key, default))
    except (TypeError, ValueError):
        return default


def _boolattr(elem, key, default=False):
    v = (elem.get(key, str(default)) or "").strip().lower()
    return v in ("1", "true", "yes")


def _parse_attacks(root):
    attacks_el = root.find("attacks")
    if attacks_el is None:
        return []
    result = []
    for atk in attacks_el.findall("attack"):
        entry = {"name": atk.get("name", "melee")}
        entry["interval"] = _intattr(atk, "interval", 2000)
        if entry["name"] == "melee":
            entry["chance"] = 100
            entry["skill"] = _intattr(atk, "skill", 0)
            entry["attack"] = _intattr(atk, "attack", 0)
            poison = _intattr(atk, "poison")
            if poison is not None:
                entry["poison"] = poison
        else:
            entry["chance"] = _intattr(atk, "chance", 100)
            for key in ("min", "max"):
                v = _intattr(atk, key)
                if v is not None:
                    entry[key] = v
        for key in ("range", "radius", "length", "spread", "target"):
            v = _intattr(atk, key)
            if v is not None:
                entry[key] = v
        result.append(entry)
    return result


def _parse_defenses(root):
    defs_el = root.find("defenses")
    if defs_el is None:
        return 0, 0, []
    armor = _intattr(defs_el, "armor", 0)
    defense = _intattr(defs_el, "defense", 0)
    healing = []
    for d in defs_el.findall("defense"):
        if d.get("name") != "healing":
            continue
        healing.append({
            "interval": _intattr(d, "interval", 1000),
            "chance": _intattr(d, "chance", 0),
            "min": _intattr(d, "min", 0),
            "max": _intattr(d, "max", 0),
        })
    return armor, defense, healing


def _parse_elements(root):
    elems_el = root.find("elements")
    if elems_el is None:
        return {}
    result = {}
    for el in elems_el.findall("element"):
        for attr_name, attr_val in el.attrib.items():
            if attr_name.endswith("Percent"):
                element_name = attr_name[: -len("Percent")]
                try:
                    result[element_name] = int(attr_val)
                except ValueError:
                    pass
    return result


def _parse_immunities(root):
    imm_el = root.find("immunities")
    if imm_el is None:
        return []
    result = []
    for el in imm_el.findall("immunity"):
        for attr_name, attr_val in el.attrib.items():
            if attr_val in ("1", "true", "yes"):
                result.append(attr_name)
    return result


def _parse_summons(root):
    summ_el = root.find("summons")
    if summ_el is None:
        return None
    max_summons = _intattr(summ_el, "maxSummons", 0)
    entries = []
    for s in summ_el.findall("summon"):
        name = (s.get("name") or "").strip()
        if not name:
            continue
        entries.append({
            "name": name,
            "interval": _intattr(s, "interval", 1000),
            "chance": _intattr(s, "chance", 0),
            "max": _intattr(s, "max", 1),
        })
    if not entries:
        return None
    return {"max": max_summons, "list": entries}


def parse_monster_file(path):
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return None
    if root.tag != "monster":
        return None

    name = (root.get("name") or "").strip()
    if not name:
        return None

    entry = {
        "name": name,
        "description": root.get("nameDescription", ""),
        "race": root.get("race", ""),
        "exp": _intattr(root, "experience", 0),
        "speed": _intattr(root, "speed", 0),
    }
    hp_el = root.find("health")
    if hp_el is not None:
        entry["hp"] = _intattr(hp_el, "max", 0)
    look_el = root.find("look")
    if look_el is not None:
        entry["look"] = {
            "type": _intattr(look_el, "type", 0),
            "corpse": _intattr(look_el, "corpse", 0),
        }
    flags_el = root.find("flags")
    hostile = False
    summonable = False
    runonhealth = 0
    if flags_el is not None:
        for f in flags_el.findall("flag"):
            if _boolattr(f, "hostile"):
                hostile = True
            if _boolattr(f, "summonable"):
                summonable = True
            roh = _intattr(f, "runonhealth")
            if roh is not None:
                runonhealth = roh
    entry["hostile"] = hostile
    entry["summonable"] = summonable
    entry["file"] = os.path.basename(path)

    entry["attacks"] = _parse_attacks(root)
    armor, defense, healing = _parse_defenses(root)
    entry["armor"] = armor
    entry["defense"] = defense
    if healing:
        entry["healing"] = healing
    elements = _parse_elements(root)
    if elements:
        entry["elements"] = elements
    immunities = _parse_immunities(root)
    if immunities:
        entry["immunities"] = immunities
    summons = _parse_summons(root)
    if summons:
        entry["summons"] = summons
    if runonhealth > 0:
        entry["runonhealth"] = runonhealth

    return entry


def extract(monster_dir, out_path):
    entries = {}
    patterns = [
        os.path.join(monster_dir, "*.xml"),
        os.path.join(monster_dir, "bosses", "*.xml"),
    ]
    files = []
    for p in patterns:
        files.extend(glob.glob(p))

    for path in sorted(files):
        e = parse_monster_file(path)
        if e is None:
            continue
        key = e["name"].strip().lower()
        entries[key] = e

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(entries, f, separators=(",", ":"))

    print(f"Wrote {out_path}")
    print(f"  monsters: {len(entries)}")
    print(f"  file size: {os.path.getsize(out_path) / 1024:.1f} KB")


if __name__ == "__main__":
    root = "/home/user/Voidmark"
    extract(
        monster_dir=os.path.join(root, "data/monster"),
        out_path=os.path.join(root, "site/data/monsters.json"),
    )
