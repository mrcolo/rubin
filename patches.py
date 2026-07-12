"""Back-compat shim: the real module lives in rubin/patches.py."""
import sys
from rubin import patches as _mod
sys.modules[__name__] = _mod
