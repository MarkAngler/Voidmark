"""Orchestrator for the Voidmark map-explorer build pipeline.

Runs every stage in order:
    1. Render OTBM floors + tile pyramid  (render_floors.build)
    2. Extract spawn data                   (extract_spawns.extract)
    3. Extract monster metadata             (extract_monsters.extract)

Usage:  python3 tools/mapbuild/build_all.py [--skip-tiles]
"""

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from render_floors import build as build_tiles  # noqa: E402
from extract_spawns import extract as extract_spawns  # noqa: E402
from extract_monsters import extract as extract_monsters  # noqa: E402
from extract_navdata import extract as extract_navdata  # noqa: E402


def main():
    root = os.path.abspath(os.path.join(HERE, "..", ".."))
    site = os.path.join(root, "site")
    skip_tiles = "--skip-tiles" in sys.argv

    t0 = time.time()
    print("=== 1. Render OTBM floors ===")
    if skip_tiles:
        print("  (skipped via --skip-tiles)")
    else:
        build_tiles(
            otbm_zip_path=os.path.join(root, "data/world/world.zip"),
            items_otb_path=os.path.join(root, "data/items/items.otb"),
            out_site_dir=site,
        )

    print("\n=== 2. Extract spawn data ===")
    extract_spawns(
        spawn_xml_path=os.path.join(root, "data/world/world-spawn.xml"),
        out_path=os.path.join(site, "data/spawns.json"),
    )

    print("\n=== 3. Extract monster metadata ===")
    extract_monsters(
        monster_dir=os.path.join(root, "data/monster"),
        out_path=os.path.join(site, "data/monsters.json"),
    )

    print("\n=== 4. Extract navigation data ===")
    extract_navdata(
        otbm_zip_path=os.path.join(root, "data/world/world.zip"),
        items_otb_path=os.path.join(root, "data/items/items.otb"),
        items_xml_path=os.path.join(root, "data/items/items.xml"),
        out_path=os.path.join(site, "data/navdata.json"),
    )

    print(f"\nAll stages complete in {time.time() - t0:.1f}s.")
    print(f"Site root: {site}")
    print("Serve locally with:  python3 -m http.server 8000 --directory", site)


if __name__ == "__main__":
    main()
