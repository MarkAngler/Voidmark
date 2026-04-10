# Voidmark — Tibia Map Explorer

A static web frontend that lets you explore the Voidmark Tibia 7.72 world
map, search for creatures, and view spawn density statistics.

## Quick start

1. Build the map tiles and JSON data (one-time, regenerate whenever
   `data/world/world.otbm`, `data/world/world-spawn.xml`, or
   `data/monster/*.xml` change):

   ```bash
   pip install pillow
   python3 tools/mapbuild/build_all.py
   ```

2. Serve the site locally:

   ```bash
   python3 -m http.server 8000 --directory site
   ```

3. Open <http://localhost:8000/>.

## What you get

* **Full-map pan/zoom** across all 16 Tibia floors (z=0..15), using
  Leaflet with a simple CRS. The surface floor (z=7) is the default view.
* **Creature search** — type any monster name (case-insensitive, substring
  match). Selecting a creature highlights every spawn location on the map.
* **Density panel** — total spawns plus a per-floor histogram. The active
  floor is highlighted in orange.
* **Heatmap mode** — toggles a Leaflet.heat overlay of spawn density.
* **Shareable URL** — the view (floor, map centre, zoom, selected
  creature) is encoded in `#hash`, so you can copy-paste a link.

## Generated files

The build pipeline writes:

* `site/data/world_meta.json` — origin, image size, zoom pyramid metadata,
  per-floor pixel counts.
* `site/data/spawns.json` — 9057 spawn blocks, indexed by monster name,
  with pre-computed per-floor totals.
* `site/data/monsters.json` — 148 monster records (name, race, HP,
  experience, speed, look type, hostile flag).
* `site/tiles/z{0..15}/{0..4}/{x}_{y}.png` — 256×256 Leaflet tile pyramid,
  one layer per floor.
* `site/tiles/z{0..15}/full.png` — intermediate full-resolution floor
  images.

`site/tiles/` is in `.gitignore` because it is regenerable and large.

## Rendering details

Tibia's minimap uses a 216-color web-safe palette. Ground tile colors are
read directly from `data/items/items.otb` (`ITEM_ATTR_MINIMAPCOLOR`, server
attribute `0x21`). Non-ground items with `FLAG_BLOCK_SOLID` stacked on a
ground tile darken the ground color to 60% so walls, mountains, and
similar obstacles remain visible.

See the `tools/mapbuild/` Python modules for the OTBM parser, palette
conversion, renderer, and tile slicer.
