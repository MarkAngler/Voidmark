"""Parse Tibia items.otb into a Python dict.

Reference: sources/items.cpp::loadFromOtb (lines 150-385)
           sources/itemloader.h: itemgroup_t, itemflags_t, ITEM_ATTR_*

Per-item props layout (after the 1-byte node type = itemgroup_t):
    <u32 flags>
    Loop until props end:
        <u8 attr> <u16 datasize> <datasize bytes>

Attributes of interest:
    ITEM_ATTR_SERVERID      = 0x10  (u16)
    ITEM_ATTR_CLIENTID      = 0x11  (u16)
    ITEM_ATTR_MINIMAPCOLOR  = 0x21  (u16)
    ITEM_ATTR_NAME          = 0x12  (string, but rarely present in OTB)
    ITEM_ATTR_TOPORDER      = 0x2D  (u8)

Flags of interest:
    FLAG_BLOCK_SOLID      = 1<<0
    FLAG_BLOCK_PROJECTILE = 1<<1
    FLAG_BLOCK_PATHFIND   = 1<<2
    FLAG_STACKABLE        = 1<<7
    FLAG_ALWAYS_ON_TOP    = 1<<13
"""

import struct

from otbm_reader import iter_nodes, load_file_bytes


ITEM_GROUP_NONE = 0
ITEM_GROUP_GROUND = 1
ITEM_GROUP_SPLASH = 11
ITEM_GROUP_FLUID = 12

FLAG_BLOCK_SOLID = 1 << 0
FLAG_BLOCK_PROJECTILE = 1 << 1
FLAG_BLOCK_PATHFIND = 1 << 2
FLAG_STACKABLE = 1 << 7
FLAG_FLOORCHANGE_DOWN = 1 << 8
FLAG_FLOORCHANGE_NORTH = 1 << 9
FLAG_FLOORCHANGE_EAST = 1 << 10
FLAG_FLOORCHANGE_SOUTH = 1 << 11
FLAG_FLOORCHANGE_WEST = 1 << 12
FLAG_ALWAYS_ON_TOP = 1 << 13

FC_DOWN = 1
FC_NORTH = 2
FC_EAST = 4
FC_SOUTH = 8
FC_WEST = 16

ITEM_ATTR_SERVERID = 0x10
ITEM_ATTR_CLIENTID = 0x11
ITEM_ATTR_MINIMAPCOLOR = 0x21


def load_items(path):
    """Parse items.otb. Returns dict keyed by server id:
        {server_id: {"group", "flags", "stackable", "block_solid",
                     "always_on_top", "minimap_color" | None, "client_id" | None}}
    """
    data = load_file_bytes(path)
    items = {}

    in_root = False
    root_skipped = False
    for event, ntype, props in iter_nodes(data, start_pos=4):
        if event == "enter":
            if not in_root:
                in_root = True
                # Root node — skip. Its props are `u32 flags` then a
                # ROOT_ATTR_VERSION=0x01 with a VERSIONINFO struct; we don't
                # need any of it for rendering.
                continue

            # This is an item node. ntype is the itemgroup_t.
            group = ntype
            try:
                flags = props.u32()
            except ValueError:
                continue

            server_id = None
            client_id = None
            minimap_color = None
            while not props.eof():
                try:
                    attr = props.u8()
                    datasize = props.u16()
                except ValueError:
                    break
                if props.pos + datasize > props.size:
                    break
                chunk = props.raw(datasize)
                if attr == ITEM_ATTR_SERVERID and datasize >= 2:
                    server_id = struct.unpack_from("<H", chunk, 0)[0]
                elif attr == ITEM_ATTR_CLIENTID and datasize >= 2:
                    client_id = struct.unpack_from("<H", chunk, 0)[0]
                elif attr == ITEM_ATTR_MINIMAPCOLOR and datasize >= 2:
                    minimap_color = struct.unpack_from("<H", chunk, 0)[0]

            if server_id is not None:
                fc = 0
                if flags & FLAG_FLOORCHANGE_DOWN:
                    fc |= FC_DOWN
                if flags & FLAG_FLOORCHANGE_NORTH:
                    fc |= FC_NORTH
                if flags & FLAG_FLOORCHANGE_EAST:
                    fc |= FC_EAST
                if flags & FLAG_FLOORCHANGE_SOUTH:
                    fc |= FC_SOUTH
                if flags & FLAG_FLOORCHANGE_WEST:
                    fc |= FC_WEST
                items[server_id] = {
                    "group": group,
                    "flags": flags,
                    "stackable": bool(flags & FLAG_STACKABLE),
                    "block_solid": bool(flags & FLAG_BLOCK_SOLID),
                    "block_pathfind": bool(flags & FLAG_BLOCK_PATHFIND),
                    "always_on_top": bool(flags & FLAG_ALWAYS_ON_TOP),
                    "floorchange": fc,
                    "minimap_color": minimap_color,
                    "client_id": client_id,
                }
        # "leave" events are ignored; we don't recurse into items.
    return items


if __name__ == "__main__":
    import os
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "/home/user/Voidmark/data/items/items.otb"
    items = load_items(path)
    print(f"Loaded {len(items)} items from {path}")
    grounds = [i for i in items.values() if i["group"] == ITEM_GROUP_GROUND]
    grounds_with_color = [i for i in grounds if i["minimap_color"] is not None]
    print(f"  Ground items: {len(grounds)}")
    print(f"  Grounds with minimap color: {len(grounds_with_color)}")
    for sid in (101, 102, 103, 104, 105, 106, 231, 294, 406, 425, 459, 460, 469, 483, 491, 519, 597, 598, 670, 671, 4526, 4597, 4820):
        it = items.get(sid)
        if it:
            print(f"  id={sid:5d} group={it['group']} flags=0x{it['flags']:04x} "
                  f"block_solid={it['block_solid']} minimap_color={it['minimap_color']}")
        else:
            print(f"  id={sid:5d} NOT FOUND")
    fc_items = [(sid, it) for sid, it in items.items() if it["floorchange"]]
    print(f"  Items with OTB floor-change flags: {len(fc_items)}")
    for sid in (294, 369, 383, 410, 428, 1385, 1388, 1390, 1392, 1394, 3687):
        it = items.get(sid)
        if it:
            print(f"  id={sid:5d} floorchange={it['floorchange']:>2d} block_pathfind={it['block_pathfind']}")
        else:
            print(f"  id={sid:5d} NOT FOUND")
    # Stackable item count (needed for tile ground count-byte heuristic)
    stackable = sum(1 for i in items.values() if i["stackable"])
    print(f"  Stackable items: {stackable}")
