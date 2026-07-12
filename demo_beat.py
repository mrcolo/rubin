"""Back-compat shim: the real module lives in rubin/demo_beat.py."""
import sys
from rubin import demo_beat as _mod
sys.modules[__name__] = _mod
