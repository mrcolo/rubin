"""Back-compat shim: the real server lives in rubin/server.py.

Keeps `python3 server.py` and existing MCP registrations working.
"""
import sys
from rubin import server as _mod
sys.modules[__name__] = _mod

if __name__ == "__main__":
    _mod.main()
