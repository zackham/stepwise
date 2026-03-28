"""Tests for /api/jobs endpoint validation."""

import os
import pytest
from starlette.testclient import TestClient

import stepwise.server as srv
from stepwise.server import app


@pytest.fixture
def client(tmp_path):
    old_env = os.environ.copy()
    os.environ["STEPWISE_PROJECT_DIR"] = str(tmp_path)
    os.environ["STEPWISE_DB"] = ":memory:"
    os.environ["STEPWISE_TEMPLATES"] = str(tmp_path / "_templates")
    os.environ["STEPWISE_JOBS_DIR"] = str(tmp_path / "_jobs")

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c

    os.environ.clear()
    os.environ.update(old_env)


class TestListJobsStatusValidation:
    def test_invalid_status_returns_400(self, client):
        resp = client.get("/api/jobs", params={"status": "suspended"})
        assert resp.status_code == 400
        body = resp.json()
        assert "Invalid status" in body["detail"]
        assert "suspended" in body["detail"]

    def test_valid_status_returns_200(self, client):
        resp = client.get("/api/jobs", params={"status": "running"})
        assert resp.status_code == 200
        assert resp.json() == []

    def test_no_status_returns_200(self, client):
        resp = client.get("/api/jobs")
        assert resp.status_code == 200
