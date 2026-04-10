"""Render Tibia OTBM floors to PNG tile pyramids for Leaflet.

One streaming parse of world.otbm, building per-floor arrays of (x, y, rgba)
triples. After parsing, each floor is materialized as an RGBA bytearray,
saved as a full-size PNG, then sliced into a Leaflet tile pyramid.

Coordinate system
-----------------
All tiles outside a fixed Tibia-main bounding box are dropped. This box was
determined empirically from world.otbm (see tools/mapbuild/README or plan).
All floors share the same origin so Leaflet pan/zoom state is stable when
the user switches floors.

Outputs
-------
    site/data/world_meta.json   — origin, size, zoom levels, per-floor counts
    site/tiles/z{z}/full.png    — intermediate native-resolution PNG per floor
    site/tiles/z{z}/{zoom}/{tx}_{ty}.png
                                 — 256×256 tile pyramid (zooms 0..max_native)
"""

import array
import json
import os
import sys
import time
import zipfile

from PIL import Image

from otb_items import load_items
from otbm_parse import walk_tiles
from palette import color_for_tile


# Tibia 7.72 main-world bounding box (inclusive-exclusive).
# Anything outside is dropped (tutorial / rookgaard / test areas).
WORLD_MIN_X = 31000
WORLD_MAX_X = 33700  # exclusive
WORLD_MIN_Y = 31200
WORLD_MAX_Y = 33300  # exclusive

TILE_SIZE = 256
MAX_NATIVE_ZOOM = 4   # zoom levels: 0, 1, 2, 3, 4 (4 = 1:1 native)


def pack_rgba(r, g, b, a):
    return (r << 24) | (g << 16) | (b << 8) | a


def unpack_rgba(v):
    return ((v >> 24) & 0xFF, (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)


def parse_map_to_floors(otbm_bytes, items_data):
    """Parse the whole OTBM once, returning 16 per-floor lists of
    (lx, ly, rgba_u32) triples stored as an array.array('I')."""
    W = WORLD_MAX_X - WORLD_MIN_X
    H = WORLD_MAX_Y - WORLD_MIN_Y

    floors = [array.array("I") for _ in range(16)]
    total = 0
    dropped = 0
    start = time.time()

    def _progress(n):
        print(f"  parsed {n:>9d} tiles ({time.time() - start:.1f}s)")

    for t in walk_tiles(otbm_bytes, items_data, progress=_progress):
        if not (WORLD_MIN_X <= t.x < WORLD_MAX_X and WORLD_MIN_Y <= t.y < WORLD_MAX_Y):
            dropped += 1
            continue
        if not (0 <= t.z < 16):
            dropped += 1
            continue
        rgba = color_for_tile(t.ground_id, items_data, t.has_blocking)
        if rgba[3] == 0:
            continue  # void / no color
        lx = t.x - WORLD_MIN_X
        ly = t.y - WORLD_MIN_Y
        floors[t.z].extend((lx, ly, pack_rgba(*rgba)))
        total += 1

    print(f"  stored {total} pixels, dropped {dropped} out-of-bounds tiles")
    return floors, W, H


def materialize_floor_image(tiles, W, H):
    """Build a PIL Image from a per-floor list of (lx, ly, rgba_u32)."""
    buf = bytearray(W * H * 4)
    it = iter(tiles)
    for lx in it:
        ly = next(it)
        rgba = next(it)
        idx = (ly * W + lx) * 4
        buf[idx]     = (rgba >> 24) & 0xFF
        buf[idx + 1] = (rgba >> 16) & 0xFF
        buf[idx + 2] = (rgba >> 8) & 0xFF
        buf[idx + 3] = rgba & 0xFF
    return Image.frombytes("RGBA", (W, H), bytes(buf))


def slice_floor_tiles(img, out_dir):
    """Write a Leaflet tile pyramid from a full-size floor PNG.

    Zoom levels: 0..MAX_NATIVE_ZOOM. At MAX_NATIVE_ZOOM we use the native
    resolution. Each level below is downsampled 2× with NEAREST to keep
    the Tibia minimap look. Only tiles with at least one opaque pixel are
    saved.
    """
    W, H = img.size
    written = 0
    skipped = 0
    for zoom in range(MAX_NATIVE_ZOOM + 1):
        scale = 1 << (MAX_NATIVE_ZOOM - zoom)
        zw = max(1, W // scale)
        zh = max(1, H // scale)
        if zoom == MAX_NATIVE_ZOOM:
            zoom_img = img
        else:
            zoom_img = img.resize((zw, zh), Image.NEAREST)

        zoom_dir = os.path.join(out_dir, str(zoom))
        os.makedirs(zoom_dir, exist_ok=True)
        tiles_x = (zw + TILE_SIZE - 1) // TILE_SIZE
        tiles_y = (zh + TILE_SIZE - 1) // TILE_SIZE
        for ty in range(tiles_y):
            for tx in range(tiles_x):
                box = (
                    tx * TILE_SIZE,
                    ty * TILE_SIZE,
                    min((tx + 1) * TILE_SIZE, zw),
                    min((ty + 1) * TILE_SIZE, zh),
                )
                crop = zoom_img.crop(box)
                if crop.size != (TILE_SIZE, TILE_SIZE):
                    pad = Image.new("RGBA", (TILE_SIZE, TILE_SIZE), (0, 0, 0, 0))
                    pad.paste(crop, (0, 0))
                    crop = pad
                # Skip fully transparent tiles to keep the file count down.
                bbox = crop.getbbox()
                if bbox is None:
                    skipped += 1
                    continue
                crop.save(os.path.join(zoom_dir, f"{tx}_{ty}.png"), optimize=True)
                written += 1
    return written, skipped


def build(otbm_zip_path, items_otb_path, out_site_dir):
    data_dir = os.path.join(out_site_dir, "data")
    tiles_dir = os.path.join(out_site_dir, "tiles")
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(tiles_dir, exist_ok=True)

    print("Loading items.otb...")
    items_data = load_items(items_otb_path)
    print(f"  {len(items_data)} items")

    print(f"Reading {otbm_zip_path}...")
    if otbm_zip_path.endswith(".zip"):
        with zipfile.ZipFile(otbm_zip_path) as zf:
            names = [n for n in zf.namelist() if n.endswith(".otbm")]
            otbm_bytes = zf.read(names[0])
    else:
        with open(otbm_zip_path, "rb") as f:
            otbm_bytes = f.read()
    print(f"  {len(otbm_bytes) / 1024 / 1024:.1f} MB")

    print("Parsing OTBM into per-floor buffers...")
    t0 = time.time()
    floors, W, H = parse_map_to_floors(otbm_bytes, items_data)
    print(f"  parse done in {time.time() - t0:.1f}s, image size {W}x{H}")

    meta = {
        "origin": {"x": WORLD_MIN_X, "y": WORLD_MIN_Y},
        "size": {"w": W, "h": H},
        "tile_size": TILE_SIZE,
        "max_native_zoom": MAX_NATIVE_ZOOM,
        "zoom_levels": list(range(MAX_NATIVE_ZOOM + 1)),
        "floors": {},
    }

    for z in range(16):
        count = len(floors[z]) // 3
        print(f"Floor z={z:2d}: {count} pixels")
        floor_dir = os.path.join(tiles_dir, f"z{z}")
        os.makedirs(floor_dir, exist_ok=True)
        if count == 0:
            meta["floors"][str(z)] = {"count": 0}
            continue
        img = materialize_floor_image(floors[z], W, H)
        full_path = os.path.join(floor_dir, "full.png")
        img.save(full_path, optimize=True)
        written, skipped = slice_floor_tiles(img, floor_dir)
        meta["floors"][str(z)] = {"count": count, "tiles": written}
        print(f"  wrote {written} tiles, skipped {skipped} empty, full.png={os.path.getsize(full_path)//1024}KB")
        # Free the floor buffer early to cap peak RAM.
        floors[z] = None
        img.close()

    meta_path = os.path.join(data_dir, "world_meta.json")
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)
    print(f"Wrote {meta_path}")


if __name__ == "__main__":
    root = "/home/user/Voidmark"
    build(
        otbm_zip_path=os.path.join(root, "data/world/world.zip"),
        items_otb_path=os.path.join(root, "data/items/items.otb"),
        out_site_dir=os.path.join(root, "site"),
    )
