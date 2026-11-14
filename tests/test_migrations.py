import sqlite3

import pytest

from app.migrations import add_column_if_missing


def test_add_column_if_missing_adds_column_and_is_idempotent():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE commitments (id TEXT PRIMARY KEY)")

    assert add_column_if_missing(db, "commitments", "workspace_id", "TEXT") is True
    assert add_column_if_missing(db, "commitments", "workspace_id", "TEXT") is False

    columns = {row[1] for row in db.execute('PRAGMA table_info("commitments")')}
    assert columns == {"id", "workspace_id"}


def test_duplicate_column_race_is_ignored_only_after_schema_recheck():
    class Cursor:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

    class RacingConnection:
        def __init__(self):
            self.column_exists = False

        def execute(self, sql):
            if sql.startswith("PRAGMA table_info"):
                rows = [(0, "workspace_id", "TEXT", 0, None, 0)] if self.column_exists else []
                return Cursor(rows)
            if sql.startswith("ALTER TABLE"):
                # Simulate another process winning after our initial schema read.
                self.column_exists = True
                raise sqlite3.OperationalError("duplicate column name: workspace_id")
            raise AssertionError(f"Unexpected SQL: {sql}")

    assert add_column_if_missing(
        RacingConnection(), "commitments", "workspace_id", "TEXT"
    ) is False


def test_unrelated_alter_error_is_not_swallowed():
    class Cursor:
        def fetchall(self):
            return []

    class BrokenConnection:
        def execute(self, sql):
            if sql.startswith("PRAGMA table_info"):
                return Cursor()
            raise sqlite3.OperationalError("disk I/O error")

    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        add_column_if_missing(BrokenConnection(), "commitments", "workspace_id", "TEXT")


@pytest.mark.parametrize("table,column", [("bad-name", "field"), ("commitments", "field; DROP TABLE commitments")])
def test_identifiers_are_validated_before_sql_is_built(table, column):
    db = sqlite3.connect(":memory:")
    with pytest.raises(ValueError, match="Invalid"):
        add_column_if_missing(db, table, column, "TEXT")
