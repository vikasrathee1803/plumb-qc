"""Test isolation: point the web report and history store at a temp dir so
the suite never writes to a user's real ~/.plumb history. Set before the web
app module is imported."""

import os
import tempfile

os.environ.setdefault(
    "PLUMB_WEB_REPORTS_DIR", tempfile.mkdtemp(prefix="plumb-test-web-")
)
# A fixed API token so the web tests can authenticate against the local API.
os.environ.setdefault("PLUMB_API_TOKEN", "test-token")
# Isolate the audit trail from a real ~/.plumb/audit.jsonl during tests.
os.environ.setdefault(
    "PLUMB_AUDIT_FILE", os.path.join(tempfile.mkdtemp(prefix="plumb-test-audit-"), "audit.jsonl")
)
