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
# Isolate the connection/tableau config files so the settings tests never touch
# a real ~/.plumb profile.
_cfg = tempfile.mkdtemp(prefix="plumb-test-cfg-")
os.environ.setdefault("PLUMB_CONNECTION_FILE", os.path.join(_cfg, "connection.yml"))
os.environ.setdefault("PLUMB_TABLEAU_FILE", os.path.join(_cfg, "tableau.yml"))

# Use an in-memory keychain so the suite never writes to the real OS keyring.
import keyring  # noqa: E402
from keyring.backend import KeyringBackend  # noqa: E402
from keyring.errors import PasswordDeleteError  # noqa: E402


class _MemoryKeyring(KeyringBackend):
    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self._store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self._store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self._store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if (service, username) in self._store:
            del self._store[(service, username)]
        else:
            raise PasswordDeleteError("not found")


keyring.set_keyring(_MemoryKeyring())
