"""Discover Logic Pro factory patches from the on-disk index.

The Library's 'Search Sounds' loads patches by name; this module answers
"what names exist?" — including which synth engine (Alchemy, Retro Synth,
ES2, Sculpture...) each patch instantiates, read from its #Root.cst.
"""

import os

PATCH_ROOTS = [
    "/Applications/Logic Pro X.app/Contents/Resources/Patches/Instrument",
    "/Applications/Logic Pro.app/Contents/Resources/Patches/Instrument",
    os.path.expanduser("~/Music/Audio Music Apps/Patches/Instrument"),
]

_index = None  # [(name, category, patch_dir)]


def _build_index():
    global _index
    if _index is not None:
        return _index
    _index = []
    for root in PATCH_ROOTS:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, _ in os.walk(root):
            for d in list(dirnames):
                if d.endswith(".patch"):
                    dirnames.remove(d)  # don't descend into bundles
                    full = os.path.join(dirpath, d)
                    name = d[:-6]
                    category = os.path.relpath(dirpath, root)
                    _index.append((name, "" if category == "." else category, full))
    return _index


def _plugin_of(patch_dir, needles):
    """Check the patch's #Root.cst for any of the plugin-name needles."""
    cst = os.path.join(patch_dir, "#Root.cst")
    try:
        with open(cst, "rb") as f:
            blob = f.read()
    except OSError:
        return False
    return any(n.encode() in blob for n in needles)


def find_patches(query=None, plugin=None, category=None, limit=25):
    """Search factory patches. All filters are case-insensitive substrings.

    `plugin` greps each candidate's #Root.cst (e.g. 'Alchemy'), so combine it
    with query/category filters when possible to keep it fast.
    """
    q = (query or "").lower()
    cat = (category or "").lower()
    hits = []
    for name, patch_cat, path in _build_index():
        if q and q not in name.lower():
            continue
        if cat and cat not in patch_cat.lower():
            continue
        if plugin and not _plugin_of(path, [plugin]):
            continue
        hits.append({"name": name, "category": patch_cat})
        if len(hits) >= max(1, int(limit)):
            break
    return hits
