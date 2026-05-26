"""Database integrity guardrails."""

from __future__ import annotations

import json

import pytest

from stepwise.cli import EXIT_JOB_FAILED, EXIT_SUCCESS, main
from stepwise.project import init_project
from stepwise.store import (
    DatabaseIntegrityError,
    SQLiteStore,
    check_database_integrity,
)


SIMPLE_FLOW = """\
name: simple
author: test
steps:
  hello:
    run: 'echo "{\\"msg\\": \\"hi\\"}"'
    outputs: [msg]
"""


def _corrupt(path) -> None:
    path.write_bytes(b"this is not a sqlite database")


def test_sqlite_store_refuses_corrupt_existing_db(tmp_path):
    db_path = tmp_path / "stepwise.db"
    _corrupt(db_path)

    with pytest.raises(DatabaseIntegrityError) as exc:
        SQLiteStore(str(db_path))

    assert str(db_path) in str(exc.value)
    assert "Refusing to open the store" in str(exc.value)
    assert not (tmp_path / "stepwise.db-wal").exists()
    assert not (tmp_path / "stepwise.db-shm").exists()


def test_thread_safe_store_refuses_corrupt_existing_db(tmp_path):
    from stepwise.server import ThreadSafeStore

    db_path = tmp_path / "stepwise.db"
    _corrupt(db_path)

    with pytest.raises(DatabaseIntegrityError):
        ThreadSafeStore(str(db_path))


def test_integrity_check_accepts_empty_project_db(tmp_path):
    db_path = tmp_path / "stepwise.db"

    assert check_database_integrity(db_path) == ["ok"]

    store = SQLiteStore(str(db_path))
    try:
        assert store.all_jobs() == []
    finally:
        store.close()


def test_cli_reports_corrupt_db_without_traceback(tmp_path, capsys, monkeypatch):
    init_project(tmp_path)
    _corrupt(tmp_path / ".stepwise" / "stepwise.db")
    monkeypatch.chdir(tmp_path)

    rc = main(["jobs"])

    captured = capsys.readouterr()
    assert rc == EXIT_JOB_FAILED
    assert "integrity check failed" in captured.err
    assert "Traceback" not in captured.err


def test_run_wait_returns_json_error_for_corrupt_db(tmp_path, capsys, monkeypatch):
    init_project(tmp_path)
    flow = tmp_path / "simple.flow.yaml"
    flow.write_text(SIMPLE_FLOW)
    _corrupt(tmp_path / ".stepwise" / "stepwise.db")
    monkeypatch.chdir(tmp_path)

    rc = main(["run", str(flow), "--wait"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == EXIT_JOB_FAILED
    assert payload["status"] == "error"
    assert payload["exit_code"] == EXIT_JOB_FAILED
    assert "integrity check failed" in payload["error"]
    assert "Traceback" not in captured.err


def test_doctor_fails_on_corrupt_db(tmp_path, capsys, monkeypatch):
    init_project(tmp_path)
    _corrupt(tmp_path / ".stepwise" / "stepwise.db")
    monkeypatch.chdir(tmp_path)

    rc = main(["doctor"])

    captured = capsys.readouterr()
    assert rc == EXIT_JOB_FAILED
    assert "Database integrity" in captured.err
    assert "All checks passed" not in captured.err


def test_doctor_passes_on_empty_project(tmp_path, capsys, monkeypatch):
    init_project(tmp_path)
    monkeypatch.chdir(tmp_path)

    rc = main(["doctor"])

    captured = capsys.readouterr()
    assert rc == EXIT_SUCCESS
    assert "Database integrity" in captured.err
