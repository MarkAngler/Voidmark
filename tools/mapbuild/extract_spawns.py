"""Convert data/world/world-spawn.xml into a JSON structure tuned for the
frontend's creature search and density panel.

Output shape (site/data/spawns.json):
{
  "spawns": [
    {"id":0, "cx":31666, "cy":32824, "cz":0, "r":1,
     "c": [{"n":"hero", "dx":0, "dy":0, "dz":0, "t":60}]}
  ],
  "byMonster": {"hero": [0, 1, 2], ...},
  "stats": {"hero": {"total": 3, "byFloor": {"0": 3}}, ...},
  "npcCount": 123
}

NPCs are counted but not included in the spawn index (v1 is creature-only).
"""

import json
import os
import sys
import xml.etree.ElementTree as ET


def extract(spawn_xml_path, out_path):
    tree = ET.parse(spawn_xml_path)
    root = tree.getroot()

    spawns = []
    by_monster = {}
    stats = {}
    npc_count = 0

    for spawn in root.findall("spawn"):
        cx = int(spawn.get("centerx", 0))
        cy = int(spawn.get("centery", 0))
        cz = int(spawn.get("centerz", 0))
        radius = int(spawn.get("radius", 1))

        creatures = []
        for m in spawn.findall("monster"):
            name = (m.get("name") or "").strip().lower()
            if not name:
                continue
            creatures.append({
                "n": name,
                "dx": int(m.get("x", 0)),
                "dy": int(m.get("y", 0)),
                "dz": int(m.get("z", cz)),
                "t": int(m.get("spawntime", 60)),
            })
        for n in spawn.findall("npc"):
            npc_count += 1

        spawn_id = len(spawns)
        spawns.append({
            "id": spawn_id,
            "cx": cx,
            "cy": cy,
            "cz": cz,
            "r": radius,
            "c": creatures,
        })

        seen_names = set()
        for c in creatures:
            name = c["n"]
            if name not in by_monster:
                by_monster[name] = []
            if name not in seen_names:
                by_monster[name].append(spawn_id)
                seen_names.add(name)
            s = stats.setdefault(name, {"total": 0, "byFloor": {}})
            s["total"] += 1
            fz = str(cz)
            s["byFloor"][fz] = s["byFloor"].get(fz, 0) + 1

    out = {
        "spawns": spawns,
        "byMonster": by_monster,
        "stats": stats,
        "npcCount": npc_count,
    }

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(out, f, separators=(",", ":"))

    print(f"Wrote {out_path}")
    print(f"  spawns: {len(spawns)}")
    print(f"  distinct monsters: {len(by_monster)}")
    print(f"  total creatures: {sum(s['total'] for s in stats.values())}")
    print(f"  npc entries (not included): {npc_count}")
    size = os.path.getsize(out_path)
    print(f"  file size: {size / 1024:.1f} KB")


if __name__ == "__main__":
    root = "/home/user/Voidmark"
    extract(
        spawn_xml_path=os.path.join(root, "data/world/world-spawn.xml"),
        out_path=os.path.join(root, "site/data/spawns.json"),
    )
