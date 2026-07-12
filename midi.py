"""Back-compat shim: the real module lives in rubin/midi.py."""
import sys
from rubin import midi as _mod
sys.modules[__name__] = _mod
