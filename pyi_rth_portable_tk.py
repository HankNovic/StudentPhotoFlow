from __future__ import annotations

import os
import sys


bundle_root = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
os.environ["TCL_LIBRARY"] = os.path.join(bundle_root, "_tcl_data")
os.environ["TK_LIBRARY"] = os.path.join(bundle_root, "_tk_data")
