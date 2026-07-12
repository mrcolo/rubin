"""Back-compat shim: the real module lives in rubin/transcribe.py."""
import sys
from rubin import transcribe as _mod
sys.modules[__name__] = _mod
