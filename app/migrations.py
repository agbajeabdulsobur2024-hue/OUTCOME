"""Small, race-safe SQLite/libSQL schema migration helpers.

Only identifiers are interpolated into PRAGMA/ALTER statements; callers must pass
trusted column definitions (the SQL type/default clause) from application code.
"""

import re


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DUPLICATE_COLUMN = re.compile(
    r"""duplicate column name\s*:\s*["']?([A-Za-z_][A-Za-z0-9_]*)["']?\s*$""",
    re.IGNORECASE,
)


def _identifier(value: str, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"Invalid {label} identifier: {value!r}")
    return value


def _column_names(db, table: str) -> set[str]:
    # PRAGMA does not accept a bound parameter for the table name. Validate it
    # above and quote it as an SQLite/libSQL identifier.
    rows = db.execute(f'PRAGMA table_info("{table}")').fetchall()
    names = set()
    for row in rows:
        try:
            name = row["name"]
        except (TypeError, KeyError, IndexError):
            name = row[1]
        names.add(str(name).casefold())
    return names


def add_column_if_missing(db, table: str, column: str, definition: str) -> bool:
    """Add a column only when absent; return True if this call added it.

    Concurrent application starts can both observe a missing column. If the
    other process wins the ALTER TABLE race, swallow only the exact duplicate
    column error and only after re-reading the schema confirms the column now
    exists. All unrelated database errors propagate unchanged.

    The definition is a trusted SQL fragment supplied by application code,
    e.g. "INTEGER NOT NULL DEFAULT 0".
    """
    table = _identifier(table, "table")
    column = _identifier(column, "column")
    if not isinstance(definition, str) or not definition.strip():
        raise ValueError("Column definition must be a non-empty SQL fragment")

    if column.casefold() in _column_names(db, table):
        return False

    try:
        db.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}')
        return True
    except Exception as exc:
        match = _DUPLICATE_COLUMN.search(str(exc))
        if not match or match.group(1).casefold() != column.casefold():
            raise
        if column.casefold() in _column_names(db, table):
            return False
        raise
