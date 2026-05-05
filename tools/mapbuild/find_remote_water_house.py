"""Find the most remote house with walking access to a water tile.

Definitions
-----------
- "Remote": Chebyshev distance from house entry (entryx, entryy) to the
  nearest of the 9 main-continent towns. Floor (z) is ignored — towns
  span z=1..11 and we want surface remoteness.
- "Water access": a walkable tile reachable within WATER_STEP_CAP 4-steps
  from any of the house's owned tiles is 4-adjacent to a water ground tile.

Water ground tiles are detected via items.otb minimap_color == 40 and
group == ITEM_GROUP_GROUND, with a static fallback set drawn from
data/items/items.xml ranges 4608-4625, 4632-4643.

Run: python3 tools/mapbuild/find_remote_water_house.py
"""

import os
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from collections import deque

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, THIS_DIR)

from otb_items import load_items, ITEM_GROUP_GROUND  # noqa: E402
from otbm_parse import walk_tiles  # noqa: E402


REPO_ROOT = os.path.abspath(os.path.join(THIS_DIR, "..", ".."))
ITEMS_OTB = os.path.join(REPO_ROOT, "data", "items", "items.otb")
WORLD_ZIP = os.path.join(REPO_ROOT, "data", "world", "world.zip")
HOUSE_XML = os.path.join(REPO_ROOT, "data", "world", "world-house.xml")

# (name, x, y, z) — temple/teleport-stone coords from data/movements/scripts/town/*.lua
TOWN_CENTERS = [
    ("Thais",       32369, 32241, 7),
    ("Carlin",      32360, 31782, 7),
    ("Venore",      32957, 32076, 7),
    ("Ab'Dendriel", 32732, 31634, 7),
    ("Kazordoon",   32649, 31925, 11),
    ("Edron",       33217, 31814, 8),
    ("Darashia",    33213, 32454, 1),
    ("Ankrahmun",   33195, 32853, 8),
    ("Port Hope",   32595, 32745, 7),
]

WATER_STEP_CAP = 10

# Canonical water-tile IDs from data/items/items.xml (shallow water).
STATIC_WATER_IDS = set(range(4608, 4626)) | set(range(4632, 4644))


def load_houses(path):
    tree = ET.parse(path)
    out = {}
    for h in tree.getroot().findall("house"):
        hid = int(h.get("houseid"))
        out[hid] = {
            "id": hid,
            "name": h.get("name", ""),
            "entryx": int(h.get("entryx")),
            "entryy": int(h.get("entryy")),
            "entryz": int(h.get("entryz")),
            "townid": int(h.get("townid", 0)),
            "size": int(h.get("size", 0)),
            "guildhall": h.get("guildhall", "false").lower() == "true",
        }
    return out


def detect_water_ids(items_data):
    by_color = {
        sid for sid, info in items_data.items()
        if info.get("minimap_color") == 40
        and info.get("group") == ITEM_GROUP_GROUND
    }
    only_static = sorted(STATIC_WATER_IDS - by_color)
    only_color = sorted(by_color - STATIC_WATER_IDS)
    print(f"  water IDs by minimap_color==40: {len(by_color)}")
    print(f"  water IDs in canonical static set: {len(STATIC_WATER_IDS)}")
    if only_static:
        print(f"  in static but not by-color (rare; using static): {only_static[:10]}{'...' if len(only_static) > 10 else ''}")
    if only_color:
        print(f"  by-color but not in static set: {only_color[:10]}{'...' if len(only_color) > 10 else ''}")
    return by_color | STATIC_WATER_IDS


def chebyshev(ax, ay, bx, by):
    return max(abs(ax - bx), abs(ay - by))


def nearest_town(x, y):
    best = None
    for (name, tx, ty, _tz) in TOWN_CENTERS:
        d = chebyshev(x, y, tx, ty)
        if best is None or d < best[0]:
            best = (d, name)
    return best


def geo_hint(x, y, z):
    """Coarse geographic label for a coord. Best-effort labels; not a definitive
    region map — purely a sanity cue when reading the ranking."""
    if y < 31600:
        return "far north (Carlin/Folda area)"
    if y < 31800 and x < 32500:
        return "north of Carlin"
    if x < 32100:
        return "western edge / Fibula"
    if x > 33400:
        return "eastern islands (Cormaya/Goroma cluster)"
    if y > 32900:
        return "deep south (Ankrahmun desert / jungle)"
    if y > 32600 and x < 32700:
        return "southwest jungle (Tiquanda)"
    if y > 32400 and x > 33100:
        return "southeast desert (Darama)"
    if 32500 < x < 33000 and 31900 < y < 32200:
        return "central mainland (Venore swamps area)"
    return "mainland interior"


def main():
    t0 = time.time()
    print("Loading items.otb ...")
    items_data = load_items(ITEMS_OTB)
    print(f"  {len(items_data)} items in {time.time()-t0:.1f}s")

    print("Detecting water tile IDs ...")
    water_ids = detect_water_ids(items_data)
    print(f"  {len(water_ids)} water IDs (union of by-color and canonical)")

    print("Loading houses ...")
    houses = load_houses(HOUSE_XML)
    print(f"  {len(houses)} houses")

    print(f"Reading OTBM from {WORLD_ZIP} ...")
    with zipfile.ZipFile(WORLD_ZIP) as zf:
        otbm_name = next(n for n in zf.namelist() if n.endswith(".otbm"))
        data = zf.read(otbm_name)
    print(f"  {len(data)/1024/1024:.1f} MB")

    print("Walking OTBM tiles ...")
    walkable = set()         # (x,y,z) tiles a player can stand on
    water_at = set()         # (x,y,z) tiles whose ground is water
    house_tiles = {}         # house_id -> list[(x,y,z)]
    tcount = 0
    t1 = time.time()
    for t in walk_tiles(data, items_data):
        tcount += 1
        pos = (t.x, t.y, t.z)
        is_water = t.ground_id in water_ids
        if is_water:
            water_at.add(pos)
        # House tiles are always walkable from the owner's perspective.
        if t.house_id is not None:
            house_tiles.setdefault(t.house_id, []).append(pos)
            walkable.add(pos)
        elif not t.has_blocking and not t.block_pathfind and not is_water:
            # Water itself isn't walkable (block_pathfind), but check anyway.
            walkable.add(pos)
    print(f"  {tcount} tiles parsed in {time.time()-t1:.1f}s")
    print(f"  {len(walkable)} walkable, {len(water_at)} water, "
          f"{len(house_tiles)} houses with tiles in OTBM")

    # Multi-source BFS: seed = walkable tiles that are 4-adjacent to water.
    print(f"Running multi-source BFS (cap={WATER_STEP_CAP}) ...")
    t2 = time.time()
    NEIGH = ((1, 0), (-1, 0), (0, 1), (0, -1))
    dist = {}
    frontier = deque()
    for (wx, wy, wz) in water_at:
        for dx, dy in NEIGH:
            nb = (wx + dx, wy + dy, wz)
            if nb in walkable and nb not in dist:
                dist[nb] = 0
                frontier.append(nb)
    while frontier:
        cur = frontier.popleft()
        d = dist[cur]
        if d >= WATER_STEP_CAP:
            continue
        cx, cy, cz = cur
        for dx, dy in NEIGH:
            nb = (cx + dx, cy + dy, cz)
            if nb in walkable and nb not in dist:
                dist[nb] = d + 1
                frontier.append(nb)
    print(f"  {len(dist)} tiles within {WATER_STEP_CAP} steps of water "
          f"({time.time()-t2:.1f}s)")

    # For each house, find the minimum step distance to water across its tiles.
    print("Scoring houses ...")
    qualifying = []
    for hid, info in houses.items():
        tiles = house_tiles.get(hid, [])
        if not tiles:
            continue
        best = None
        best_at = None
        for tile in tiles:
            d = dist.get(tile)
            if d is None:
                continue
            if best is None or d < best:
                best = d
                best_at = tile
        if best is None:
            continue
        # Find an actual adjacent water tile near best_at for reporting.
        water_tile = None
        if best_at is not None:
            # walk back: find nearest water 4-neighbor of any tile at dist 0
            # within reach. Simplest: scan house's own tiles' neighbors first.
            for t in tiles:
                tx, ty, tz = t
                for dx, dy in NEIGH:
                    nb = (tx + dx, ty + dy, tz)
                    if nb in water_at:
                        water_tile = nb
                        break
                if water_tile:
                    break
            if water_tile is None:
                # fall back: any water tile near best_at within best+1 ring
                bx, by, bz = best_at
                for r in range(0, best + 2):
                    found = False
                    for dx in range(-r, r + 1):
                        for dy in range(-r, r + 1):
                            cand = (bx + dx, by + dy, bz)
                            if cand in water_at:
                                water_tile = cand
                                found = True
                                break
                        if found:
                            break
                    if found:
                        break
        td, tname = nearest_town(info["entryx"], info["entryy"])
        qualifying.append({
            "house": info,
            "town_dist": td,
            "town_name": tname,
            "water_steps": best,
            "water_tile": water_tile,
        })

    qualifying.sort(key=lambda r: (-r["town_dist"], r["water_steps"], r["house"]["id"]))

    print()
    print(f"=== Top 15 most remote houses with water within {WATER_STEP_CAP} steps ===")
    print(f"{'rank':>4} {'town_d':>7} {'town':<13} {'steps':>5} "
          f"{'size':>4} {'entry':<22} {'name':<40}")
    for i, row in enumerate(qualifying[:15], 1):
        h = row["house"]
        entry = f"({h['entryx']},{h['entryy']},{h['entryz']})"
        print(f"{i:>4} {row['town_dist']:>7} {row['town_name']:<13} "
              f"{row['water_steps']:>5} {h['size']:>4} {entry:<22} "
              f"{h['name']:<40}")

    if qualifying:
        winner = qualifying[0]
        h = winner["house"]
        wt = winner["water_tile"]
        print()
        print("=== Winner ===")
        print(f"  House:        '{h['name']}' (id={h['id']}, townid={h['townid']}, "
              f"size={h['size']}{', guildhall' if h['guildhall'] else ''})")
        print(f"  Entry:        ({h['entryx']}, {h['entryy']}, {h['entryz']})")
        print(f"  Nearest town: {winner['town_name']} at "
              f"{winner['town_dist']} tiles (Chebyshev)")
        print(f"  Water:        {winner['water_steps']} step(s) from "
              f"the closest house tile"
              + (f"; nearest water tile at {wt}" if wt else ""))
        print(f"  Region hint:  {geo_hint(h['entryx'], h['entryy'], h['entryz'])}")
        print(f"  Map URL:      site/index.html#z={h['entryz']}&x={h['entryx']}"
              f"&y={h['entryy']}&zoom=4")

    print()
    print(f"Total runtime: {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
