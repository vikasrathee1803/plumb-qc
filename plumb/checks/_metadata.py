"""INFORMATION_SCHEMA helpers shared by the metadata checks.

All access is read-only SELECT against each referenced database's
INFORMATION_SCHEMA, so the read-only guard sees only reads. Snowflake
stores unquoted identifiers in uppercase, so names are uppercased for
comparison. Unqualified references (no database) cannot be resolved
through a specific INFORMATION_SCHEMA and are reported as unverified
rather than guessed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from plumb.checks._sql import TableRef, extract_table_refs


@dataclass
class ColumnInfo:
    schema: str
    table: str
    column: str
    data_type: str


@dataclass
class ResolvedRefs:
    qualified: list[TableRef] = field(default_factory=list)
    unqualified: list[TableRef] = field(default_factory=list)

    def by_database(self) -> dict[str, list[TableRef]]:
        grouped: dict[str, list[TableRef]] = {}
        for ref in self.qualified:
            grouped.setdefault(ref.db_or_catalog(), []).append(ref)
        return grouped


def resolve_refs(sql: str) -> ResolvedRefs:
    resolved = ResolvedRefs()
    for ref in extract_table_refs(sql):
        if ref.is_qualified():
            resolved.qualified.append(ref)
        else:
            resolved.unqualified.append(ref)
    return resolved


def schema_name_pairs(refs: list[TableRef]) -> list[str]:
    return [f"{(r.db or '').upper()}.{r.name.upper()}" for r in refs]


def tables_query(database: str, refs: list[TableRef]) -> str:
    pairs = ", ".join(f"'{p}'" for p in schema_name_pairs(refs))
    return (
        f"SELECT TABLE_SCHEMA, TABLE_NAME, COALESCE(COMMENT, '') AS COMMENT\n"
        f"FROM {database.upper()}.INFORMATION_SCHEMA.TABLES\n"
        f"WHERE TABLE_SCHEMA || '.' || TABLE_NAME IN ({pairs})"
    )


def columns_query(database: str, refs: list[TableRef]) -> str:
    pairs = ", ".join(f"'{p}'" for p in schema_name_pairs(refs))
    return (
        f"SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE\n"
        f"FROM {database.upper()}.INFORMATION_SCHEMA.COLUMNS\n"
        f"WHERE TABLE_SCHEMA || '.' || TABLE_NAME IN ({pairs})"
    )


def type_family(data_type: str) -> str:
    t = data_type.upper()
    if any(k in t for k in ("CHAR", "TEXT", "STRING", "VARCHAR")):
        return "TEXT"
    if any(k in t for k in ("INT", "NUMBER", "DECIMAL", "NUMERIC", "FLOAT", "REAL", "DOUBLE")):
        return "NUMBER"
    if "TIMESTAMP" in t or t == "DATE" or "DATETIME" in t:
        return "DATETIME"
    if "BOOL" in t:
        return "BOOLEAN"
    if "VARIANT" in t or "OBJECT" in t or "ARRAY" in t:
        return "SEMISTRUCTURED"
    return t
