"""Expected loot value (gp) per monster kill.

Parses each monster XML's <loot> tree, computes E[gp] per kill using vendor
sell prices. Uses a curated vendor table (see ingest.VENDOR_SELL). The loot
chance attribute in OTX monster XML is given as `chance / 100000` (i.e. 100000
= 100%, 10000 = 10%). `countmax` on gold coins denotes an upper bound; the
drop count is uniform[1, countmax].
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

from ingest import VENDOR_SELL


def _loot_expected_gp(elem: ET.Element) -> float:
    gp = 0.0
    for item in elem.findall("item"):
        try:
            iid = int(item.get("id", "0") or 0)
            chance_raw = int(item.get("chance", "0") or 0)
            count_max = int(item.get("countmax", "1") or 1)
        except ValueError:
            continue
        prob = min(1.0, chance_raw / 100_000.0)
        avg_count = (1 + count_max) / 2.0
        sell = VENDOR_SELL.get(iid, 0)
        gp += prob * avg_count * sell
        # Recurse into <inside> containers (bags).
        inside = item.find("inside")
        if inside is not None:
            gp += prob * _loot_expected_gp(inside)
    return gp


def expected_gp_per_monster(monster_xml_path: Path) -> float:
    try:
        root = ET.parse(str(monster_xml_path)).getroot()
    except ET.ParseError:
        return 0.0
    loot = root.find("loot")
    if loot is None:
        return 0.0
    return _loot_expected_gp(loot)


def build_loot_table(monster_dir: Path) -> dict[str, float]:
    out: dict[str, float] = {}
    for p in monster_dir.glob("*.xml"):
        try:
            root = ET.parse(str(p)).getroot()
        except ET.ParseError:
            continue
        name = (root.get("name") or "").strip().lower()
        if not name:
            continue
        out[name] = expected_gp_per_monster(p)
    return out


if __name__ == "__main__":
    from ingest import REPO
    tbl = build_loot_table(REPO / "data" / "monster")
    (REPO / "tools" / "hunting_analysis" / "artifacts" / "ingest" / "loot_gp.json").write_text(
        json.dumps(tbl, indent=2)
    )
    for n in sorted(tbl, key=lambda k: tbl[k], reverse=True)[:20]:
        print(f"  {n:25s} E[gp/kill] = {tbl[n]:7.1f}")
