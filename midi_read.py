"""Back-compat shim: the real module lives in rubin/midi_read.py."""
import sys
from rubin import midi_read as _mod
sys.modules[__name__] = _mod
