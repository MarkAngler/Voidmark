"""Parse a Tibia OTBM map file and yield tile records.

Reference: sources/iomap.cpp (canonical tree walk)
           sources/item.cpp::CreateItem (tile-ground count-byte logic)
           sources/fileloader.* (escape-framed node stream)

Strategy
--------
We use otbm_reader.iter_nodes to stream enter/leave events for every OTBM
node, then walk a tiny state machine:
    root -> OTBM_MAP_DATA -> OTBM_TILE_AREA -> OTBM_TILE | OTBM_HOUSETILE
                                                   -> OTBM_ITEM (stacked)

For each tile we yield a `TileRecord(x, y, z, ground_id, has_blocking)`.
`has_blocking` is True when any stacked OTBM_ITEM child has FLAG_BLOCK_SOLID
set (per items.otb). Towns and waypoints from OTBM_TOWNS/OTBM_WAYPOINTS are
currently ignored (could be added later for the frontend).
"""

from collections import namedtuple

from otbm_reader import iter_nodes, PropReader
from otb_items import (
    ITEM_GROUP_GROUND,
    ITEM_GROUP_SPLASH,
    ITEM_GROUP_FLUID,
)


# OTBM node types (sources/iomap.h:55-75)
OTBM_ROOTV1 = 0
OTBM_ROOTV2 = 1
OTBM_MAP_DATA = 2
OTBM_ITEM_DEF = 3
OTBM_TILE_AREA = 4
OTBM_TILE = 5
OTBM_ITEM = 6
OTBM_TILE_SQUARE = 7
OTBM_TILE_REF = 8
OTBM_SPAWNS = 9
OTBM_SPAWN_AREA = 10
OTBM_MONSTER = 11
OTBM_TOWNS = 12
OTBM_TOWN = 13
OTBM_HOUSETILE = 14
OTBM_WAYPOINTS = 15
OTBM_WAYPOINT = 16

# OTBM attribute bytes (sources/iomap.h)
OTBM_ATTR_DESCRIPTION = 1
OTBM_ATTR_EXT_FILE = 2
OTBM_ATTR_TILE_FLAGS = 3
OTBM_ATTR_ACTION_ID = 4
OTBM_ATTR_UNIQUE_ID = 5
OTBM_ATTR_TEXT = 6
OTBM_ATTR_DESC = 7
OTBM_ATTR_TELE_DEST = 8
OTBM_ATTR_ITEM = 9
OTBM_ATTR_DEPOT_ID = 10
OTBM_ATTR_EXT_SPAWN_FILE = 11
OTBM_ATTR_RUNE_CHARGES = 12
OTBM_ATTR_EXT_HOUSE_FILE = 13
OTBM_ATTR_HOUSEDOORID = 14
OTBM_ATTR_COUNT = 15
OTBM_ATTR_DURATION = 16
OTBM_ATTR_DECAYING_STATE = 17
OTBM_ATTR_WRITTENDATE = 18
OTBM_ATTR_WRITTENBY = 19
OTBM_ATTR_SLEEPERGUID = 20
OTBM_ATTR_SLEEPSTART = 21
OTBM_ATTR_CHARGES = 22
OTBM_ATTR_CONTAINER_ITEMS = 23
OTBM_ATTR_NAME = 30
OTBM_ATTR_PLURALNAME = 31
OTBM_ATTR_ATTACK = 33
OTBM_ATTR_EXTRAATTACK = 34
OTBM_ATTR_DEFENSE = 35
OTBM_ATTR_EXTRADEFENSE = 36
OTBM_ATTR_ARMOR = 37
OTBM_ATTR_ATTACKSPEED = 38
OTBM_ATTR_HITCHANCE = 39
OTBM_ATTR_SHOOTRANGE = 40
OTBM_ATTR_ARTICLE = 41
OTBM_ATTR_SCRIPTPROTECTED = 42
OTBM_ATTR_DUALWIELD = 43


TileRecord = namedtuple("TileRecord", [
    "x", "y", "z", "ground_id", "has_blocking", "block_pathfind", "floor_change",
    "house_id",
])
TileRecord.__new__.__defaults__ = (None,)  # house_id defaults to None


def _has_count_byte(item_id, items_data):
    info = items_data.get(item_id)
    if info is None:
        return False
    return info["stackable"] or info["group"] in (ITEM_GROUP_SPLASH, ITEM_GROUP_FLUID)


def _is_block_solid(item_id, items_data):
    info = items_data.get(item_id)
    return info is not None and info["block_solid"]


def _is_block_pathfind(item_id, items_data):
    info = items_data.get(item_id)
    return info is not None and info.get("block_pathfind", False)


def _get_floor_change(item_id, items_data):
    info = items_data.get(item_id)
    if info is None:
        return 0
    return info.get("floorchange", 0)


def _read_tile_props(props, is_housetile, items_data):
    """Consume a tile node's props (already a PropReader). Returns
    (ground_id, (dx, dy), block_pathfind, floor_change, house_id).
    `house_id` is the OTBM_HOUSETILE owner id when applicable, else None.
    Recovers on attribute errors by stopping early.
    """
    # tile coord offsets (always present)
    try:
        _dx = props.u8()
        _dy = props.u8()
    except ValueError:
        return None, (None, None), False, 0, None
    house_id = None
    if is_housetile:
        try:
            house_id = props.u32()
        except ValueError:
            return None, (_dx, _dy), False, 0, None

    ground_id = None
    bp = False
    fc = 0
    while not props.eof():
        try:
            attr = props.u8()
        except ValueError:
            break
        if attr == OTBM_ATTR_TILE_FLAGS:
            try:
                props.u32()
            except ValueError:
                break
        elif attr == OTBM_ATTR_ITEM:
            try:
                item_id = props.u16()
            except ValueError:
                break
            if _has_count_byte(item_id, items_data):
                try:
                    props.u8()
                except ValueError:
                    break
            bp = bp or _is_block_pathfind(item_id, items_data)
            fc |= _get_floor_change(item_id, items_data)
            # First ATTR_ITEM in a tile is the ground; later ones are
            # stacked items (rare in stock maps but handled).
            if ground_id is None and items_data.get(item_id, {}).get("group") == ITEM_GROUP_GROUND:
                ground_id = item_id
            elif ground_id is None:
                # Save whatever we saw as the "ground" even if items.otb
                # marks it as a non-ground group (e.g. always-on-top water).
                ground_id = item_id
        else:
            # Unknown attr inside a tile — stop to avoid desyncing.
            break
    return ground_id, (_dx, _dy), bp, fc, house_id


def walk_tiles(data, items_data, progress=None):
    """Yield TileRecord for every tile in the OTBM buffer `data`.
    `items_data` is the dict from otb_items.load_items.
    `progress` is an optional callable(tiles_seen) invoked every ~100000 tiles.
    """
    # State
    base_x = base_y = base_z = 0
    in_map_data = False
    in_area = False
    pending_tile = None   # (x, y, z) of the tile currently open
    current_ground = None
    current_blocking = False
    current_block_pathfind = False
    current_floor_change = 0
    current_house_id = None

    tiles_seen = 0
    item_depth = 0        # depth of nested OTBM_ITEM nodes (for walls on tile)

    for event, ntype, props in iter_nodes(data, start_pos=4):
        if event == "enter":
            if ntype == OTBM_MAP_DATA:
                in_map_data = True
                continue
            if not in_map_data:
                continue
            if ntype == OTBM_TILE_AREA:
                try:
                    base_x = props.u16()
                    base_y = props.u16()
                    base_z = props.u8()
                    in_area = True
                except ValueError:
                    in_area = False
                continue
            if not in_area:
                continue
            if ntype in (OTBM_TILE, OTBM_HOUSETILE):
                ground, (dx, dy), bp, fc, hid = _read_tile_props(
                    props, ntype == OTBM_HOUSETILE, items_data
                )
                if dx is None:
                    continue
                pending_tile = (base_x + dx, base_y + dy, base_z)
                current_ground = ground
                current_blocking = False
                current_block_pathfind = bp
                current_floor_change = fc
                current_house_id = hid
                continue
            if ntype == OTBM_ITEM:
                # Stacked item on a tile (or nested container item).
                item_depth += 1
                if pending_tile is not None and item_depth == 1:
                    try:
                        item_id = props.u16()
                        if _has_count_byte(item_id, items_data):
                            props.u8()
                        if _is_block_solid(item_id, items_data):
                            current_blocking = True
                        if _is_block_pathfind(item_id, items_data):
                            current_block_pathfind = True
                        current_floor_change |= _get_floor_change(item_id, items_data)
                    except ValueError:
                        pass
                continue
        else:  # leave
            if ntype == OTBM_MAP_DATA:
                in_map_data = False
                continue
            if ntype == OTBM_TILE_AREA:
                in_area = False
                continue
            if ntype in (OTBM_TILE, OTBM_HOUSETILE):
                if pending_tile is not None:
                    x, y, z = pending_tile
                    yield TileRecord(
                        x, y, z, current_ground, current_blocking,
                        current_block_pathfind, current_floor_change,
                        current_house_id,
                    )
                    tiles_seen += 1
                    if progress is not None and (tiles_seen % 100000) == 0:
                        progress(tiles_seen)
                pending_tile = None
                current_ground = None
                current_blocking = False
                current_block_pathfind = False
                current_floor_change = 0
                current_house_id = None
                item_depth = 0
                continue
            if ntype == OTBM_ITEM:
                if item_depth > 0:
                    item_depth -= 1
                continue


if __name__ == "__main__":
    import sys
    import time
    import zipfile

    from otb_items import load_items

    otbm_path = sys.argv[1] if len(sys.argv) > 1 else "/home/user/Voidmark/data/world/world.zip"
    print(f"Loading items.otb...")
    items_data = load_items("/home/user/Voidmark/data/items/items.otb")
    print(f"  {len(items_data)} items loaded")

    if otbm_path.endswith(".zip"):
        with zipfile.ZipFile(otbm_path) as zf:
            names = [n for n in zf.namelist() if n.endswith(".otbm")]
            print(f"  extracting {names[0]} from zip")
            data = zf.read(names[0])
    else:
        with open(otbm_path, "rb") as f:
            data = f.read()
    print(f"  OTBM size: {len(data) / 1024 / 1024:.1f} MB")

    start = time.time()
    floor_counts = [0] * 16
    minx = [None] * 16
    maxx = [None] * 16
    miny = [None] * 16
    maxy = [None] * 16
    unknown_grounds = {}

    def _progress(n):
        print(f"  parsed {n:>9d} tiles  ({time.time() - start:.1f}s)")

    for t in walk_tiles(data, items_data, progress=_progress):
        if 0 <= t.z < 16:
            floor_counts[t.z] += 1
            if minx[t.z] is None or t.x < minx[t.z]:
                minx[t.z] = t.x
            if maxx[t.z] is None or t.x > maxx[t.z]:
                maxx[t.z] = t.x
            if miny[t.z] is None or t.y < miny[t.z]:
                miny[t.z] = t.y
            if maxy[t.z] is None or t.y > maxy[t.z]:
                maxy[t.z] = t.y

    total = sum(floor_counts)
    print(f"\nParsed {total} tiles in {time.time() - start:.1f}s")
    for z in range(16):
        if floor_counts[z] == 0:
            print(f"  z={z:2d}: (empty)")
        else:
            print(
                f"  z={z:2d}: {floor_counts[z]:>8d} tiles  "
                f"bbox=({minx[z]}..{maxx[z]}, {miny[z]}..{maxy[z]})"
            )
