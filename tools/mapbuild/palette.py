"""Tibia minimap color palette.

Tibia's client stores each ground item's minimap color as an index into a
216-color web-safe cube. The canonical formula (from otclient's
src/client/minimap.cpp::Minimap::getMinimapColor) is:

    r = (n // 36) * 51
    g = ((n // 6) % 6) * 51
    b = (n %  6) * 51

Authoritative source is items.otb::ITEM_ATTR_MINIMAPCOLOR, loaded by
otb_items.load_items. This module only provides color conversion and a
small fallback LUT for items that lack a minimap_color attribute.
"""


def palette_index_to_rgb(n):
    """Convert a palette index 0..215 to an (r, g, b) triple."""
    if n is None or n <= 0 or n >= 216:
        return None
    r = (n // 36) * 51
    g = ((n // 6) % 6) * 51
    b = (n % 6) * 51
    return (r, g, b)


# Fallback for ground items that have no minimap_color in items.otb.
# Values are RGB triples chosen to match Tibia 7.72 conventions. Item
# IDs here only act as a safety net; primary colors come from items.otb.
FALLBACK_GROUND_RGB = {
    101: (102, 102, 102),   # stone
    294: (0, 0, 0),         # pitfall
}


VOID_RGBA = (0, 0, 0, 0)        # fully empty tile
DEFAULT_RGBA = (80, 80, 80, 255)  # unknown ground


def color_for_tile(ground_id, items_data, has_blocking_stack):
    """Return an (r, g, b, a) tuple for a single map tile.

    Parameters
    ----------
    ground_id : int or None
        Server id of the tile's ground item, or None if the tile has no ground.
    items_data : dict
        Output of otb_items.load_items.
    has_blocking_stack : bool
        True if a non-ground, block-solid item is stacked on the tile.
    """
    if ground_id is None:
        return VOID_RGBA

    rgb = None
    info = items_data.get(ground_id)
    if info is not None and info["minimap_color"] is not None:
        rgb = palette_index_to_rgb(info["minimap_color"])
    if rgb is None:
        rgb = FALLBACK_GROUND_RGB.get(ground_id)
    if rgb is None:
        return DEFAULT_RGBA

    if has_blocking_stack:
        # Darken to 60% so walls/mountains stand out against their ground.
        rgb = (int(rgb[0] * 0.6), int(rgb[1] * 0.6), int(rgb[2] * 0.6))

    return (rgb[0], rgb[1], rgb[2], 255)


if __name__ == "__main__":
    # Quick smoke test.
    for n in (0, 24, 40, 129, 192, 207, 210, 215):
        print(f"palette[{n:3d}] = {palette_index_to_rgb(n)}")
