"""Back-compat shim: the real module lives in rubin/logic_ctl.py."""
import sys
from rubin import logic_ctl as _mod
sys.modules[__name__] = _mod
