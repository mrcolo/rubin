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


def _search(entries, query, category, limit, extra=None):
    """Shared name/category substring filter over (name, category, *rest)."""
    q = (query or "").lower()
    cat = (category or "").lower()
    hits = []
    for entry in entries:
        name, entry_cat = entry[0], entry[1]
        if q and q not in name.lower():
            continue
        if cat and cat not in entry_cat.lower():
            continue
        if extra and not extra(entry):
            continue
        hits.append({"name": name, "category": entry_cat})
        if len(hits) >= max(1, int(limit)):
            break
    return hits


def find_patches(query=None, plugin=None, category=None, limit=25):
    """Search factory patches. All filters are case-insensitive substrings.

    `plugin` greps each candidate's #Root.cst (e.g. 'Alchemy'), so combine it
    with query/category filters when possible to keep it fast.
    """
    extra = (lambda e: _plugin_of(e[2], [plugin])) if plugin else None
    return _search(_build_index(), query, category, limit, extra)


CST_ROOTS = [
    "/Library/Application Support/Logic/Channel Strip Settings",
    os.path.expanduser("~/Music/Audio Music Apps/Channel Strip Settings"),
]

_cst_index = None


def _build_cst_index():
    global _cst_index
    if _cst_index is not None:
        return _cst_index
    _cst_index = []
    for root in CST_ROOTS:
        if not os.path.isdir(root):
            continue
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                if f.endswith(".cst"):
                    category = os.path.relpath(dirpath, root)
                    _cst_index.append((f[:-4], "" if category == "." else category))
    return _cst_index


def find_channel_strips(query=None, category=None, limit=25):
    """Search factory channel-strip settings (.cst) — complete FX chains
    (EQ, compression, sends). Discovery only: load via the channel strip's
    Setting menu. `category` e.g. 'Track/04 Bass Guitar', 'Bus'.
    """
    return _search(_build_cst_index(), query, category, limit)


SURGE_ROOTS = [
    os.path.expanduser("~/Library/Application Support/Surge XT"),
    "/Library/Application Support/Surge XT",
]

_surge_index = None


def _build_surge_index():
    global _surge_index
    if _surge_index is not None:
        return _surge_index
    _surge_index = []
    for root in SURGE_ROOTS:
        for bank in ("patches_factory", "patches_3rdparty"):
            base = os.path.join(root, bank)
            if not os.path.isdir(base):
                continue
            for dirpath, _dirs, files in os.walk(base):
                for f in files:
                    if f.endswith(".fxp"):
                        category = os.path.relpath(dirpath, base)
                        _surge_index.append(
                            (f[:-4], "" if category == "." else category))
        if _surge_index:
            break  # first root that has content wins
    return _surge_index


def find_surge_presets(query=None, category=None, limit=25):
    """Search installed Surge XT presets (.fxp) by name/category substring.

    Discovery only — Surge presets load through Surge's own browser, not
    Logic's Library. Categories mirror Surge's bank layout (Basses, Leads,
    Pads, Keys...).
    """
    return _search(_build_surge_index(), query, category, limit)
