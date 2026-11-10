import sys
from types import SimpleNamespace

import pytest

from app import main


class FakeTursoConnection:
    def __init__(self):
        self.row_factory = None
        self.statements = []
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def execute(self, sql):
        self.statements.append(sql)
        return self

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def test_turso_backend_uses_remote_driver_and_closes_connection(monkeypatch):
    fake = FakeTursoConnection()
    calls = []

    def fake_connect(url, auth_token):
        calls.append((url, auth_token))
        return fake

    fake_module = SimpleNamespace(connect=fake_connect, Row=object)
    monkeypatch.setitem(sys.modules, "turso_serverless", fake_module)
    monkeypatch.setenv("OUTCOME_DB_BACKEND", "turso")
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://example.turso.io")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "test-token")

    with main.connect() as db:
        assert db is fake
        assert db.row_factory is object
        assert db.statements == ["PRAGMA foreign_keys = ON"]

    assert calls == [("libsql://example.turso.io", "test-token")]
    assert fake.committed is True
    assert fake.rolled_back is False
    assert fake.closed is True


def test_turso_backend_rolls_back_on_error_and_closes(monkeypatch):
    fake = FakeTursoConnection()
    monkeypatch.setitem(
        sys.modules,
        "turso_serverless",
        SimpleNamespace(connect=lambda *args, **kwargs: fake, Row=object),
    )
    monkeypatch.setenv("OUTCOME_DB_BACKEND", "turso")
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://example.turso.io")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "test-token")

    with pytest.raises(ValueError):
        with main.connect():
            raise ValueError("test failure")

    assert fake.rolled_back is True
    assert fake.committed is False
    assert fake.closed is True


def test_turso_backend_requires_both_credentials(monkeypatch):
    monkeypatch.setenv("OUTCOME_DB_BACKEND", "turso")
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://example.turso.io")
    monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="TURSO_DATABASE_URL and TURSO_AUTH_TOKEN"):
        with main.connect():
            pass


def test_unknown_database_backend_fails_closed(monkeypatch):
    monkeypatch.setenv("OUTCOME_DB_BACKEND", "unknown")

    with pytest.raises(RuntimeError, match="Unsupported OUTCOME_DB_BACKEND"):
        with main.connect():
            pass
