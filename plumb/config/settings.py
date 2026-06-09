"""Write and read local connection settings for the setup page.

Connection metadata (account, user, role, warehouse, key path, server, site)
is written to the local YAML files the engine reads. Real secrets (key
passphrase, OAuth token, Tableau token or app secret) go to the OS keychain
via keyring, never to a file and never returned in an API response. Password
auth is refused by the models.
"""

from __future__ import annotations

from typing import Any

import keyring
import keyring.errors
import yaml

from plumb.config.loader import CONNECTION_FILE, TABLEAU_FILE
from plumb.config.models import ConnectionProfile, TableauConnection

KEYRING_SERVICE = "plumb"


def set_secret(entry: str, value: str) -> None:
    keyring.set_password(KEYRING_SERVICE, entry, value)


def delete_secret(entry: str) -> None:
    try:
        keyring.delete_password(KEYRING_SERVICE, entry)
    except (keyring.errors.PasswordDeleteError, keyring.errors.KeyringError):
        pass


def has_secret(entry: str) -> bool:
    try:
        return keyring.get_password(KEYRING_SERVICE, entry) is not None
    except keyring.errors.KeyringError:
        return False


def get_secret(entry: str) -> str | None:
    """Read a secret value. Server-side only (for a live connection test); a
    secret value is never returned in an API response."""
    try:
        return keyring.get_password(KEYRING_SERVICE, entry)
    except keyring.errors.KeyringError:
        return None


def passphrase_entry(account: str, user: str) -> str:
    return f"private_key_passphrase:{account}:{user}"


def oauth_entry(account: str, user: str) -> str:
    return f"oauth_token:{account}:{user}"


def tableau_pat_entry(server: str, name: str) -> str:
    return f"tableau_pat:{server}:{name}"


def tableau_app_entry(server: str, secret_id: str) -> str:
    return f"tableau_secret:{server}:{secret_id}"


def write_snowflake(
    data: dict[str, Any], *, passphrase: str | None, oauth_token: str | None
) -> ConnectionProfile:
    """Validate (refuses passwords), write connection.yml, and store secrets in
    the keychain under the engine's conventions. A passphrase of "" clears it;
    None leaves it untouched."""
    profile = ConnectionProfile.model_validate(data)
    CONNECTION_FILE.parent.mkdir(parents=True, exist_ok=True)
    out = {k: v for k, v in profile.model_dump().items() if v is not None}
    CONNECTION_FILE.write_text(yaml.safe_dump(out, sort_keys=False), encoding="utf-8")
    if profile.authenticator == "snowflake_jwt" and passphrase is not None:
        entry = passphrase_entry(profile.account, profile.user)
        set_secret(entry, passphrase) if passphrase else delete_secret(entry)
    if profile.authenticator == "oauth" and oauth_token:
        set_secret(oauth_entry(profile.account, profile.user), oauth_token)
    return profile


def write_tableau(data: dict[str, Any], *, secret: str | None) -> TableauConnection:
    """Validate, write tableau.yml, store the token/app secret in the keychain."""
    conn = TableauConnection.model_validate(data)
    TABLEAU_FILE.parent.mkdir(parents=True, exist_ok=True)
    out = {k: v for k, v in conn.model_dump().items() if v is not None and v != ""}
    TABLEAU_FILE.write_text(yaml.safe_dump(out, sort_keys=False), encoding="utf-8")
    if secret:
        if conn.auth == "pat" and conn.pat_name:
            set_secret(tableau_pat_entry(conn.server, conn.pat_name), secret)
        elif conn.auth == "connected_app" and conn.secret_id:
            set_secret(tableau_app_entry(conn.server, conn.secret_id), secret)
    return conn
