"""Cluster nearby same-floor spawns into hunting areas for the
frontend's hunting calculator.

Output shape (site/data/hunting_areas.json):
{
  "areas": [
    {
      "id": 0,
      "cx": 32354, "cy": 31646, "cz": 1,
      "radius": 12,
      "creatures": {"valkyrie": 4, "amazon": 6},
      "spawn_ids": [12, 13, 14],
      "respawn_avg": 60
    },
    ...
  ]
}
"""

import json
import os
import sys


_CELL_SIZE = 40  # Grid cell size in tiles


def _cluster_spawns(spawns):
    """Group spawns into fixed-size grid cells per floor.

    Uses a grid-based approach (CELL_SIZE x CELL_SIZE tiles) to produce
    predictable, non-overlapping hunting areas without chain-linking
    artifacts from single-linkage clustering.

    Returns a list of hunting area dicts.
    """
    # Bucket spawns by (floor, grid_x, grid_y)
    cells = {}
    for sp in spawns:
        if not sp["c"]:
            continue
        gx = sp["cx"] // _CELL_SIZE
        gy = sp["cy"] // _CELL_SIZE
        key = (sp["cz"], gx, gy)
        cells.setdefault(key, []).append(sp)

    areas = []
    area_id = 0

    for (cz, gx, gy), members in sorted(cells.items()):
        creatures = {}
        spawn_ids = []
        total_respawn_weighted = 0
        total_creatures = 0
        xs, ys = [], []

        for sp in members:
            spawn_ids.append(sp["id"])
            xs.append(sp["cx"])
            ys.append(sp["cy"])
            for c in sp["c"]:
                name = c["n"]
                creatures[name] = creatures.get(name, 0) + 1
                total_respawn_weighted += c["t"]
                total_creatures += 1

        cx = round(sum(xs) / len(xs))
        cy = round(sum(ys) / len(ys))
        radius = max(
            max(abs(x - cx) for x in xs),
            max(abs(y - cy) for y in ys),
        ) if len(xs) > 1 else members[0]["r"]

        respawn_avg = (
            round(total_respawn_weighted / total_creatures)
            if total_creatures > 0
            else 60
        )

        areas.append({
            "id": area_id,
            "cx": cx,
            "cy": cy,
            "cz": cz,
            "radius": radius,
            "creatures": creatures,
            "spawn_ids": spawn_ids,
            "respawn_avg": respawn_avg,
        })
        area_id += 1

    return areas


def extract(spawns_json_path, out_path):
    with open(spawns_json_path) as f:
        spawns_data = json.load(f)

    areas = _cluster_spawns(spawns_data["spawns"])

    out = {"areas": areas}

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(out, f, separators=(",", ":"))

    # Diagnostics
    sizes = [sum(a["creatures"].values()) for a in areas]
    print(f"Wrote {out_path}")
    print(f"  hunting areas: {len(areas)}")
    if sizes:
        print(f"  creatures per area: avg={sum(sizes)/len(sizes):.1f}, "
              f"max={max(sizes)}, min={min(sizes)}")
    floors = {}
    for a in areas:
        floors[a["cz"]] = floors.get(a["cz"], 0) + 1
    print(f"  areas by floor: {dict(sorted(floors.items()))}")
    print(f"  file size: {os.path.getsize(out_path) / 1024:.1f} KB")


if __name__ == "__main__":
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    extract(
        spawns_json_path=os.path.join(root, "site/data/spawns.json"),
        out_path=os.path.join(root, "site/data/hunting_areas.json"),
    )
