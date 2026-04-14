"""Tests for auth management, Device Flow API, and authenticated publishing."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from stepwise.cli import EXIT_SUCCESS, EXIT_USAGE_ERROR, main
from stepwise.registry_client import (
    AUTH_FILE,
    RegistryError,
    clear_auth,
    initiate_device_flow,
    load_auth,
    poll_device_flow,
    publish_flow,
    save_auth,
    update_flow,
    verify_auth,
)


# ── Auth file management ──────────────────────────────────────────


class TestAuthFileManagement:
    def test_save_and_load_auth(self, tmp_path, monkeypatch):
        monkeypatch.setattr("stepwise.registry_client.AUTH_FILE", tmp_path / "auth.json")
        monkeypatch.setattr("stepwise.registry_client.CONFIG_DIR", tmp_path)

        save_auth("tok_abc123", "alice", "https://stepwise.run")
        auth = load_auth()
        assert auth is not None
        assert auth["auth_token"] == "tok_abc123"
        assert auth["github_username"] == "alice"
        assert auth["registry_url"] == "https://stepwise.run"

    def test_auth_file_permissions(self, tmp_path, monkeypatch):
        auth_file = tmp_path / "auth.json"
        monkeypatch.setattr("stepwise.registry_client.AUTH_FILE", auth_file)
        monkeypatch.setattr("stepwise.registry_client.CONFIG_DIR", tmp_path)

        save_auth("tok_abc123", "alice", "https://stepwise.run")
        mode = auth_file.stat().st_mode & 0o777
        assert mode == 0o600

    def test_load_auth_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("stepwise.registry_client.AUTH_FILE", tmp_path / "auth.json")
        assert load_auth() is None

    def test_clear_auth(self, tmp_path, monkeypatch):
        auth_file = tmp_path / "auth.json"
        monkeypatch.setattr("stepwise.registry_client.AUTH_FILE", auth_file)
        monkeypatch.setattr("stepwise.registry_client.CONFIG_DIR", tmp_path)

        save_auth("tok_abc123", "alice", "https://stepwise.run")
        assert auth_file.exists()
        clear_auth()
        assert not auth_file.exists()

    def test_clear_auth_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("stepwise.registry_client.AUTH_FILE", tmp_path / "auth.json")
        # Should not raise
        clear_auth()

    def test_load_auth_corrupt_json(self, tmp_path, monkeypatch):
        auth_file = tmp_path / "auth.json"
        auth_file.write_text("not json{{{")
        monkeypatch.setattr("stepwise.registry_client.AUTH_FILE", auth_file)
        assert load_auth() is None


# ── Device Flow API ───────────────────────────────────────────────


def _mock_client(mock_response):
    """Create a mock httpx client context manager."""
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    return mock_client


class TestDeviceFlowAPI:
    def test_initiate_device_flow(self, monkeypatch):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {
            "device_code": "dc_123",
            "user_code": "ABCD-1234",
            "verification_uri": "https://github.com/login/device",
            "interval": 5,
            "expires_in": 900,
        }

        client = _mock_client(mock_response)
        client.post.return_value = mock_response
        monkeypatch.setattr("stepwise.registry_client._client", lambda: client)

        result = initiate_device_flow("https://stepwise.run")
        assert result["device_code"] == "dc_123"
        assert result["user_code"] == "ABCD-1234"
        client.post.assert_called_once_with("https://stepwise.run/api/auth/device")

    def test_poll_device_flow_success(self, monkeypatch):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {
            "auth_token": "tok_session_abc",
            "github_username": "alice",
        }

        client = _mock_client(mock_response)
        client.post.return_value = mock_response
        monkeypatch.setattr("stepwise.registry_client._client", lambda: client)

        result = poll_device_flow("dc_123", "https://stepwise.run")
        assert result["auth_token"] == "tok_session_abc"
        assert result["github_username"] == "alice"

    def test_poll_device_flow_pending(self, monkeypatch):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {"error": "authorization_pending"}

        client = _mock_client(mock_response)
        client.post.return_value = mock_response
        monkeypatch.setattr("stepwise.registry_client._client", lambda: client)

        result = poll_device_flow("dc_123", "https://stepwise.run")
        assert result["error"] == "authorization_pending"

    def test_verify_auth_success(self, monkeypatch):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {"github_username": "alice"}

        client = _mock_client(mock_response)
        client.get.return_value = mock_response
        monkeypatch.setattr("stepwise.registry_client._client", lambda: client)

        result = verify_auth("tok_abc", "https://stepwise.run")
        assert result["github_username"] == "alice"
        # Verify Bearer header was sent
        client.get.assert_called_once_with(
            "https://stepwise.run/api/auth/verify",
            headers={"Authorization": "Bearer tok_abc"},
        )

    def test_verify_auth_expired(self, monkeypatch):
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"

        client = _mock_client(mock_response)
        client.get.return_value = mock_response
        monkeypatch.setattr("stepwise.registry_client._client", lambda: client)

        with pytest.raises(RegistryError, match="Auth verification failed"):
            verify_auth("tok_expired", "https://stepwise.run")


# ── Publish with auth ─────────────────────────────────────────────


class TestPublishWithAuth:
    def test_publish_sends_auth_header(self, tmp_path, monkeypatch):
        monkeypatch.setattr("stepwise.registry_client.TOKENS_FILE", tmp_path / "tokens.json")
        monkeypatch.setattr("stepwise.registry_client.CONFIG_DIR", tmp_path)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {
            "slug": "my-flow",
            "name": "my-flow",
            "update_token": "stw_tok_abc",
        }

        client = _mock_client(mock_response)
        client.post.return_value = mock_response
        monkeypatch.setattr("stepwise.registry_client._client", lambda: client)

        publish_flow("name: my-flow\nsteps:\n  a:\n    run: echo\n", auth_token="tok_session")
        # Verify the auth header was included
        call_kwargs = client.post.call_args
        assert call_kwargs.kwargs.get("headers", {}).get("Authorization") == "Bearer tok_session"

    def test_publish_no_auth_header_when_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr("stepwise.registry_client.TOKENS_FILE", tmp_path / "tokens.json")
        monkeypatch.setattr("stepwise.registry_client.CONFIG_DIR", tmp_path)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {
            "slug": "my-flow",
            "name": "my-flow",
        }

        client = _mock_client(mock_response)
        client.post.return_value = mock_response
        monkeypatch.setattr("stepwise.registry_client._client", lambda: client)

        publish_flow("name: my-flow\nsteps:\n  a:\n    run: echo\n")
        call_kwargs = client.post.call_args
        headers = call_kwargs.kwargs.get("headers", {})
        assert "Authorization" not in headers


# ── Update with auth ──────────────────────────────────────────────


class TestUpdateWithAuth:
    def test_update_uses_per_flow_token_first(self, tmp_path, monkeypatch):
        """Per-flow token takes priority over session auth_token."""
        from stepwise.registry_client import save_token

        monkeypatch.setattr("stepwise.registry_client.TOKENS_FILE", tmp_path / "tokens.json")
        monkeypatch.setattr("stepwise.registry_client.CONFIG_DIR", tmp_path)

        save_token("my-flow", "stw_per_flow_tok")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {"slug": "my-flow", "version": 2}

        client = _mock_client(mock_response)
        client.put.return_value = mock_response
        monkeypatch.setattr("stepwise.registry_client._client", lambda: client)

        update_flow("my-flow", "name: my-flow\n", auth_token="tok_session_fallback")
        # Should use per-flow token, not session token
        call_kwargs = client.put.call_args
        assert "Bearer stw_per_flow_tok" in str(call_kwargs)

    def test_update_falls_back_to_auth_token(self, tmp_path, monkeypatch):
        """Uses auth_token when no per-flow token exists."""
        monkeypatch.setattr("stepwise.registry_client.TOKENS_FILE", tmp_path / "tokens.json")
        monkeypatch.setattr("stepwise.registry_client.CONFIG_DIR", tmp_path)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {"slug": "my-flow", "version": 2}

        client = _mock_client(mock_response)
        client.put.return_value = mock_response
        monkeypatch.setattr("stepwise.registry_client._client", lambda: client)

        update_flow("my-flow", "name: my-flow\n", auth_token="tok_session")
        call_kwargs = client.put.call_args
        assert "Bearer tok_session" in str(call_kwargs)

    def test_update_fails_when_no_tokens(self, tmp_path, monkeypatch):
        monkeypatch.setattr("stepwise.registry_client.TOKENS_FILE", tmp_path / "tokens.json")
        monkeypatch.setattr("stepwise.registry_client.CONFIG_DIR", tmp_path)

        with pytest.raises(RegistryError, match="No update token"):
            update_flow("my-flow", "name: my-flow\n")


# ── cmd_login ─────────────────────────────────────────────────────


class TestCmdLogin:
    def test_login_already_authenticated(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "stepwise.registry_client.load_auth",
            lambda: {"auth_token": "tok_valid", "github_username": "alice", "registry_url": "https://stepwise.run"},
        )
        monkeypatch.setattr(
            "stepwise.registry_client.verify_auth",
            lambda token, **kw: {"github_username": "alice"},
        )

        rc = main(["login"])
        assert rc == EXIT_SUCCESS
        err = capsys.readouterr().err
        assert "Already logged in as @alice" in err

    def test_login_full_device_flow(self, monkeypatch, capsys):
        # No existing auth
        monkeypatch.setattr("stepwise.registry_client.load_auth", lambda: None)

        # Mock initiate
        monkeypatch.setattr(
            "stepwise.registry_client.initiate_device_flow",
            lambda **kw: {
                "device_code": "dc_123",
                "user_code": "ABCD-1234",
                "verification_uri": "https://github.com/login/device",
                "interval": 0,  # don't actually sleep
                "expires_in": 900,
            },
        )

        # Mock poll: pending, pending, success
        poll_calls = [0]

        def mock_poll(device_code, **kw):
            poll_calls[0] += 1
            if poll_calls[0] < 3:
                return {"error": "authorization_pending"}
            return {"auth_token": "tok_new", "github_username": "bob"}

        monkeypatch.setattr("stepwise.registry_client.poll_device_flow", mock_poll)

        # Mock save_auth
        saved = {}

        def mock_save(token, username, url):
            saved["token"] = token
            saved["username"] = username

        monkeypatch.setattr("stepwise.registry_client.save_auth", mock_save)

        # Mock time.sleep to not actually sleep
        monkeypatch.setattr("time.sleep", lambda s: None)

        rc = main(["login"])
        assert rc == EXIT_SUCCESS
        assert saved["token"] == "tok_new"
        assert saved["username"] == "bob"
        err = capsys.readouterr().err
        assert "Logged in as @bob" in err

    def test_login_expired(self, monkeypatch, capsys):
        monkeypatch.setattr("stepwise.registry_client.load_auth", lambda: None)
        monkeypatch.setattr(
            "stepwise.registry_client.initiate_device_flow",
            lambda **kw: {
                "device_code": "dc_123",
                "user_code": "XXXX",
                "verification_uri": "https://github.com/login/device",
                "interval": 0,
                "expires_in": 900,
            },
        )
        monkeypatch.setattr(
            "stepwise.registry_client.poll_device_flow",
            lambda dc, **kw: {"error": "expired_token"},
        )
        monkeypatch.setattr("time.sleep", lambda s: None)

        rc = main(["login"])
        assert rc == EXIT_USAGE_ERROR
        err = capsys.readouterr().err
        assert "expired token" in err

    def test_login_denied(self, monkeypatch, capsys):
        monkeypatch.setattr("stepwise.registry_client.load_auth", lambda: None)
        monkeypatch.setattr(
            "stepwise.registry_client.initiate_device_flow",
            lambda **kw: {
                "device_code": "dc_123",
                "user_code": "XXXX",
                "verification_uri": "https://github.com/login/device",
                "interval": 0,
                "expires_in": 900,
            },
        )
        monkeypatch.setattr(
            "stepwise.registry_client.poll_device_flow",
            lambda dc, **kw: {"error": "access_denied"},
        )
        monkeypatch.setattr("time.sleep", lambda s: None)

        rc = main(["login"])
        assert rc == EXIT_USAGE_ERROR
        err = capsys.readouterr().err
        assert "access denied" in err


# ── cmd_logout ────────────────────────────────────────────────────


class TestCmdLogout:
    def test_logout_when_logged_in(self, tmp_path, monkeypatch, capsys):
        auth_file = tmp_path / "auth.json"
        monkeypatch.setattr("stepwise.registry_client.AUTH_FILE", auth_file)
        monkeypatch.setattr("stepwise.registry_client.CONFIG_DIR", tmp_path)

        save_auth("tok_abc", "alice", "https://stepwise.run")
        assert auth_file.exists()

        rc = main(["logout"])
        assert rc == EXIT_SUCCESS
        assert not auth_file.exists()
        err = capsys.readouterr().err
        assert "Logged out" in err

    def test_logout_when_not_logged_in(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("stepwise.registry_client.AUTH_FILE", tmp_path / "auth.json")

        rc = main(["logout"])
        assert rc == EXIT_SUCCESS
        err = capsys.readouterr().err
        assert "Not logged in" in err


# ── cmd_share auth ────────────────────────────────────────────────


class TestCmdShareAuth:
    def _write_flow(self, tmp_path):
        """Write a minimal valid flow file and init a project."""
        main(["--project-dir", str(tmp_path), "init", "--no-skill"])
        flows_dir = tmp_path / "flows"
        flows_dir.mkdir(exist_ok=True)
        flow_file = flows_dir / "test.flow.yaml"
        flow_file.write_text(
            "name: test-flow\nauthor: test\nsteps:\n  a:\n    run: echo done\n    outputs: [result]\n"
        )
        return flow_file

    def test_share_requires_login(self, tmp_path, monkeypatch, capsys):
        self._write_flow(tmp_path)
        monkeypatch.setattr("stepwise.registry_client.AUTH_FILE", tmp_path / "noauth.json")

        rc = main(["--project-dir", str(tmp_path), "share", "test"])
        assert rc == EXIT_USAGE_ERROR
        err = capsys.readouterr().err
        assert "stepwise login" in err

    def test_share_sends_auth_token(self, tmp_path, monkeypatch, capsys):
        self._write_flow(tmp_path)

        # Set up auth
        auth_file = tmp_path / "auth.json"
        monkeypatch.setattr("stepwise.registry_client.AUTH_FILE", auth_file)
        monkeypatch.setattr("stepwise.registry_client.CONFIG_DIR", tmp_path)
        save_auth("tok_session", "alice", "https://stepwise.run")

        # Mock publish_flow at the registry_client level (imported inside cmd_share)
        published = {}

        def mock_publish(yaml_content, author=None, files=None, auth_token=None):
            published["auth_token"] = auth_token
            return {"slug": "test-flow", "name": "test-flow", "author": "alice", "url": "https://stepwise.run/flows/test-flow"}

        monkeypatch.setattr("stepwise.registry_client.publish_flow", mock_publish)

        rc = main(["--project-dir", str(tmp_path), "share", "test"])
        assert rc == EXIT_SUCCESS
        assert published["auth_token"] == "tok_session"

    def test_share_401_suggests_relogin(self, tmp_path, monkeypatch, capsys):
        self._write_flow(tmp_path)

        # Set up auth
        auth_file = tmp_path / "auth.json"
        monkeypatch.setattr("stepwise.registry_client.AUTH_FILE", auth_file)
        monkeypatch.setattr("stepwise.registry_client.CONFIG_DIR", tmp_path)
        save_auth("tok_expired", "alice", "https://stepwise.run")

        # Mock publish_flow to raise 401
        def mock_publish(yaml_content, author=None, files=None, auth_token=None):
            raise RegistryError("Unauthorized", 401)

        monkeypatch.setattr("stepwise.registry_client.publish_flow", mock_publish)

        rc = main(["--project-dir", str(tmp_path), "share", "test"])
        assert rc == EXIT_USAGE_ERROR
        err = capsys.readouterr().err
        assert "stepwise login" in err
