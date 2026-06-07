# ADR-0004: Local state locations

Date: 2026-06-07. Status: accepted.

The spec names the connection profile and the rules pin but not where
they live. Decision: all local Plumb state is under ~/.plumb/

- ~/.plumb/connection.yml      connection profile (never in a repo)
- ~/.plumb/rules.pin           pinned ruleset version, plain text
- ~/.plumb/keys/               suggested home for key-pair files

Secrets (key passphrase, OAuth token) come from the OS keychain under
service name "plumb", or from the environment variables
PLUMB_PRIVATE_KEY_PASSPHRASE and PLUMB_OAUTH_TOKEN. They are never read
from any YAML file.

Reversibility: cheap. Paths are constants in plumb/config/loader.py and
plumb/connect/snowflake.py.
