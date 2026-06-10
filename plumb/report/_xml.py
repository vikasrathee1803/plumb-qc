"""XML text sanitation shared by the JUnit writers.

ElementTree escapes markup (&, <, quotes) but passes control characters
straight through, and XML 1.0 forbids them entirely — one \\x0b in an
exception message renders a junit file that CI parsers reject as not
well-formed. Check evidence and estate error strings are raw exception
text from hostile inputs, so every attribute and text node built from
them goes through xml_safe. Illegal characters become U+FFFD so the spot
where something was removed stays visible.
"""

from __future__ import annotations

import re

# XML 1.0 Char excludes C0 controls other than tab/newline/carriage-return,
# and DEL. (Surrogates cannot appear in Python str from decoded text.)
_XML_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def xml_safe(text: str) -> str:
    """Replace characters that are illegal in XML 1.0 with U+FFFD."""
    return _XML_ILLEGAL.sub("�", text)
