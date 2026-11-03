#!/usr/bin/env python3
"""Delete an unpaired local branch while retaining recovery information."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libexec"))

from rwgk_wt.local_branch import main

raise SystemExit(main())
