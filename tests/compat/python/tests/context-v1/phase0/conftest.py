from __future__ import annotations

import pathlib
import sys


PHASE0 = pathlib.Path(__file__).resolve().parent
if str(PHASE0) not in sys.path:
    sys.path.insert(0, str(PHASE0))
