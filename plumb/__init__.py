"""Plumb: local-first BI build QC and confidence engine."""

import warnings

__version__ = "0.1.0"

# snowflake-connector-python vendors requests, whose import-time version
# probe warns when the system urllib3/charset_normalizer are newer than it
# expects. The pins are CVE-driven (requirements.lock) and the connector
# works fine; the warning just makes every live invocation look broken.
# Filter exactly that message and nothing else.
warnings.filterwarnings(
    "ignore",
    message=r"urllib3 .* or chardet .* doesn't match a supported version!",
)
