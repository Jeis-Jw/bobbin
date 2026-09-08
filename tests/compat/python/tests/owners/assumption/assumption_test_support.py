from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


_HELPERS = Path(__file__).with_name("helpers.py")
_SPEC = importlib.util.spec_from_file_location("context_assumption_test_helpers", _HELPERS)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)

for _name in dir(_MODULE):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_MODULE, _name)
