"""Test isolation: point the web report and history store at a temp dir so
the suite never writes to a user's real ~/.plumb history. Set before the web
app module is imported."""

import os
import tempfile

os.environ.setdefault(
    "PLUMB_WEB_REPORTS_DIR", tempfile.mkdtemp(prefix="plumb-test-web-")
)
