"""Generate navigation/pathfinding data from the OTBM map.

Output: site/data/navdata.json with walkability RLE and floor-change list.

Output shape:
{
  "origin": [31000, 31200],
  "size": [2700, 2100],
  "floors": {
    "7": {
      "rows": {
        "524": [[45, 120], [200, 80]],
        ...
      }
    }
  },
  "fc": [[worldX, worldY, z, bitmask], ...]
}

Bitmask encoding: down=1, north=2, east=4, south=8, west=16.
"""

import json
import os
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

from otb_items import load_items, FC_DOWN, FC_NORTH, FC_EAST, FC_SOUTH, FC_WEST
from otbm_parse import walk_tiles


# Map bounding box (must match render_floors.py constants)
ORIGIN_X = 31000
ORIGIN_Y = 31200
WIDTH = 2700
HEIGHT = 2100


_FC_VALUE_MAP = {
    "down": FC_DOWN,
    "north": FC_NORTH,
    "east": FC_EAST,
    "south": FC_SOUTH,
    "west": FC_WEST,
}


def _load_xml_floorchanges(xml_path):
    """Parse items.xml for floorchange attributes.

    Returns {server_id: bitmask} for all items with floorchange.
    Handles id ranges like '369-370' and multiple floorchange attributes.
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()
    result = {}

    for item_el in root.findall("item"):
        raw_id = item_el.get("id", "")
        fc = 0
        for attr in item_el.findall("attribute"):
            if attr.get("key") == "floorchange":
                val = attr.get("value", "").strip().lower()
                fc |= _FC_VALUE_MAP.get(val, 0)
        if fc == 0:
            continue

        m = re.match(r"^(\d+)-(\d+)$", raw_id)
        if m:
            for sid in range(int(m.group(1)), int(m.group(2)) + 1):
                result[sid] = result.get(sid, 0) | fc
        else:
            try:
                sid = int(raw_id)
                result[sid] = result.get(sid, 0) | fc
            except ValueError:
                pass

    return result


def _merge_xml_floorchanges(items_data, xml_fc):
    """Merge items.xml floor-change data into items_data (from OTB).

    For each item, the final floorchange is the union of OTB and XML flags.
    """
    for sid, fc in xml_fc.items():
        if sid in items_data:
            items_data[sid]["floorchange"] = items_data[sid].get("floorchange", 0) | fc
        else:
            # Item exists in XML but not OTB — create minimal entry so
            # walk_tiles can look it up.
            items_data[sid] = {
                "group": 0,
                "flags": 0,
                "stackable": False,
                "block_solid": False,
                "block_pathfind": False,
                "always_on_top": False,
                "floorchange": fc,
                "minimap_color": None,
                "client_id": None,
            }


def extract(otbm_zip_path, items_otb_path, items_xml_path, out_path):
    print("  Loading items.otb...")
    items_data = load_items(items_otb_path)
    print(f"    {len(items_data)} items")

    print("  Loading items.xml floor-change overrides...")
    xml_fc = _load_xml_floorchanges(items_xml_path)
    print(f"    {len(xml_fc)} items with floorchange in XML")
    _merge_xml_floorchanges(items_data, xml_fc)

    otb_fc_count = sum(1 for it in items_data.values() if it.get("floorchange", 0))
    print(f"    {otb_fc_count} items with floorchange after merge")

    print("  Parsing OTBM...")
    with zipfile.ZipFile(otbm_zip_path) as zf:
        names = [n for n in zf.namelist() if n.endswith(".otbm")]
        data = zf.read(names[0])

    # Per-floor walkability: floors[z][local_y] -> sorted list of (start_lx, length)
    floors = {}
    fc_list = []
    tile_count = 0
    walkable_count = 0
    fc_tile_count = 0

    def _progress(n):
        print(f"    parsed {n:>9d} tiles")

    for t in walk_tiles(data, items_data, progress=_progress):
        tile_count += 1
        lx = t.x - ORIGIN_X
        ly = t.y - ORIGIN_Y
        if not (0 <= lx < WIDTH and 0 <= ly < HEIGHT and 0 <= t.z < 16):
            continue

        walkable = not t.has_blocking and not t.block_pathfind
        if walkable:
            walkable_count += 1
            floor = floors.setdefault(t.z, {})
            row = floor.setdefault(ly, [])
            row.append(lx)

        if t.floor_change:
            fc_tile_count += 1
            fc_list.append([t.x, t.y, t.z, t.floor_change])

    # Convert per-floor per-row lists of x-coords into sorted RLE runs
    out_floors = {}
    for z, rows in sorted(floors.items()):
        out_rows = {}
        for ly, xs in sorted(rows.items()):
            xs.sort()
            runs = []
            start = xs[0]
            length = 1
            for i in range(1, len(xs)):
                if xs[i] == start + length:
                    length += 1
                else:
                    runs.append([start, length])
                    start = xs[i]
                    length = 1
            runs.append([start, length])
            out_rows[str(ly)] = runs
        out_floors[str(z)] = {"rows": out_rows}

    result = {
        "origin": [ORIGIN_X, ORIGIN_Y],
        "size": [WIDTH, HEIGHT],
        "floors": out_floors,
        "fc": fc_list,
    }

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(result, f, separators=(",", ":"))

    size = os.path.getsize(out_path)
    print(f"  Wrote {out_path}")
    print(f"    tiles parsed: {tile_count}")
    print(f"    walkable tiles: {walkable_count}")
    print(f"    floor-change tiles: {fc_tile_count}")
    print(f"    floors with data: {len(out_floors)}")
    print(f"    file size: {size / 1024:.1f} KB")


if __name__ == "__main__":
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    extract(
        otbm_zip_path=os.path.join(root, "data/world/world.zip"),
        items_otb_path=os.path.join(root, "data/items/items.otb"),
        items_xml_path=os.path.join(root, "data/items/items.xml"),
        out_path=os.path.join(root, "site/data/navdata.json"),
    )
