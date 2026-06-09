"""Run Plumb's self-check from a source checkout (no install required).

    python scripts/selfcheck.py

Reports each dependency, module import, and engine/web check as PASS or FAIL
and exits non-zero if anything is wrong. Equivalent to `plumb doctor` once
installed, or `check.bat` inside the portable build. See plumb/diagnostics.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plumb.diagnostics import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
