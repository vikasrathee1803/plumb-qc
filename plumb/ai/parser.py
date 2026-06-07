"""Tolerant JSON extraction for AI responses.

The assist prompts ask for raw JSON, but models sometimes wrap it in code
fences or add prose. This strips fences and extracts the first balanced
JSON object. A parse failure returns None so the caller degrades
gracefully: the deterministic check still shows its status, the AI field
stays null.
"""

from __future__ import annotations

import json
from typing import Any


def extract_json(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = _strip_fences(cleaned)
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    obj = _first_json_object(cleaned)
    if obj is None:
        return None
    try:
        parsed = json.loads(obj)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _strip_fences(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _first_json_object(text: str) -> str | None:
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None
