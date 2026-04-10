"""Convert data/monster/*.xml into site/data/monsters.json for the
frontend's autocomplete, info card, and search.

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
    "file": "demon.xml"
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
    if flags_el is not None:
        for f in flags_el.findall("flag"):
            if _boolattr(f, "hostile"):
                hostile = True
            if _boolattr(f, "summonable"):
                summonable = True
    entry["hostile"] = hostile
    entry["summonable"] = summonable
    entry["file"] = os.path.basename(path)
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
