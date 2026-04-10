# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Voidmark is a Tibia 7.72 OTX game server (C++) paired with a static web-based map explorer. The repo has three decoupled subsystems that share the `data/` directory:

1. **C++ game server** (`sources/`) — OTX 2.X.S.5 FORGOTTEN engine
2. **Python build pipeline** (`tools/mapbuild/`) — parses OTBM map + game data into tiles/JSON
3. **Static web frontend** (`site/`) — Leaflet-based map explorer with creature search

## Build commands

### Map explorer (Python)

```bash
pip install pillow                              # only external dependency
python3 tools/mapbuild/build_all.py             # full build: tiles + JSON (~30s)
python3 tools/mapbuild/build_all.py --skip-tiles  # rebuild JSON only (fast)
python3 -m http.server 8000 --directory site    # serve at localhost:8000
```

Individual pipeline stages can be run standalone (each has `if __name__ == "__main__"` with defaults):
```bash
python3 tools/mapbuild/otb_items.py             # parse items.otb, print diagnostics
python3 tools/mapbuild/otbm_parse.py            # parse OTBM, print per-floor tile counts
python3 tools/mapbuild/extract_spawns.py        # rebuild spawns.json only
python3 tools/mapbuild/extract_monsters.py      # rebuild monsters.json only
```

### C++ game server

```bash
cd sources
autoreconf -ivf && ./configure && make -j$(nproc)
# or use the wrapper scripts:
./sources/compile.sh    # interactive: installs deps (Debian/Ubuntu/Fedora), configures, builds
./sources/build.sh      # quick rebuild with ccache
```

Dependencies: libxml2 ≥ 2.6.5, zlib, OpenSSL, Boost (thread, regex, system, filesystem), Lua 5.1 or LuaJIT, pthread. Default DB backend is SQLite; MySQL/PostgreSQL optional via `./configure --enable-mysql`.

## Architecture

### Data flow

```
data/world/world.zip (OTBM)  ─┐
data/items/items.otb           ├─→ tools/mapbuild/ ──→ site/data/*.json + site/tiles/**/*.png
data/monster/*.xml             │     (Python)
data/world/world-spawn.xml   ─┘
                                                              ↓
                                                    site/app.js (Leaflet UI)
```

The C++ server reads from `data/` independently at runtime. The web frontend is purely static.

### OTBM binary format

The OTBM uses escape-framed node trees (0xFE=start, 0xFF=end, 0xFD=escape). The canonical reference is `sources/fileloader.cpp` and `sources/iomap.cpp`. Key constants live in `sources/iomap.h` and `sources/itemloader.h`.

`otbm_reader.py` implements the streaming parser. `otbm_parse.py` walks the tree yielding `TileRecord(x, y, z, ground_id, has_blocking)` for every tile. `otb_items.py` extracts minimap colors (`ITEM_ATTR_MINIMAPCOLOR = 0x21`) from `items.otb`.

### Map rendering

The renderer clips to a fixed Tibia-main bounding box (31000..33700 × 31200..33300), renders each of 16 floors into an RGBA image, then slices into a 5-level tile pyramid (zoom 0..4, 256px tiles). All floors share the same pixel origin so Leaflet preserves pan/zoom when switching floors. Empty tiles are skipped (Leaflet falls back to a transparent 1px placeholder).

### Frontend

Vanilla JS + Leaflet 1.9 + leaflet.heat (CDN). State management is a plain object. Key mechanics:
- 16 pre-created `L.tileLayer` instances, one per floor; only the active one is attached
- `spawns.json` ships a pre-built `byMonster` inverted index and `stats` histogram — zero client-side filtering
- URL hash (`#z=7&m=demon&x=...&y=...&zoom=3`) persists view state

### Game server (C++)

`sources/` contains ~160 .cpp/.h files. The entry point is `otserv.cpp`. Key subsystems:
- `iomap.cpp` / `fileloader.cpp` — OTBM map loader
- `game.cpp` — game loop, creature/player actions
- `item.cpp` / `items.cpp` — item type system and serialization
- `spawn.cpp` — creature spawn management (reads `world-spawn.xml`)
- Game data lives in `data/`: Lua scripts (`data/lib/`, `data/actions/`, `data/movements/`, `data/spells/`), XML configs (`data/xml/`, `data/monster/`, `data/npc/`)
- Database schema in `schemas/mysql.sql` and `schemas/otxserver.s3db` (SQLite)
- Server config in `config.lua`

## Generated files

`site/tiles/` is in `.gitignore` — regenerate with `build_all.py`. `site/data/*.json` is committed (small, avoids requiring build step for frontend dev).

## Code standards

See `.Claude/rules/Prod-code.md` for the full production code ruleset. Key points:
- Conventional Commits format: `<type>[optional scope]: <description>`
- Do not reference AI tools in commit messages
- Match existing conventions; minimize changes
- No dead code, no speculative abstractions, no TODO comments in production paths

## No test infrastructure

There is no test suite or linter configuration. Verification is manual: run `build_all.py`, check printed diagnostics (tile counts, JSON sizes, cross-reference orphans), and smoke-test the served site.
