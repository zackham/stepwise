"""Tests for flow registry client and @author:name resolution."""

from __future__ import annotations

import json
import os
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from stepwise.registry_client import (
    CACHE_DIR,
    TOKENS_FILE,
    RegistryError,
    cache_flow,
    fetch_flow,
    fetch_flow_yaml,
    get_cached,
    get_registry_url,
    get_token,
    publish_flow,
    save_token,
    search_flows,
    update_flow,
)
from stepwise.yaml_loader import YAMLLoadError, load_workflow_yaml


# ── Registry URL config ─────────────────────────────────────────────


class TestRegistryURL:
    def test_default_url(self, monkeypatch):
        monkeypatch.delenv("STEPWISE_REGISTRY_URL", raising=False)
        assert get_registry_url() == "https://stepwise.run"

    def test_env_var_override(self, monkeypatch):
        monkeypatch.setenv("STEPWISE_REGISTRY_URL", "http://localhost:8341")
        assert get_registry_url() == "http://localhost:8341"

    def test_trailing_slash_stripped(self, monkeypatch):
        monkeypatch.setenv("STEPWISE_REGISTRY_URL", "http://localhost:8341/")
        assert get_registry_url() == "http://localhost:8341"


# ── Token management ────────────────────────────────────────────────


class TestTokens:
    def test_save_and_load(self, tmp_path, monkeypatch):
        tokens_file = tmp_path / "tokens.json"
        monkeypatch.setattr("stepwise.registry_client.TOKENS_FILE", tokens_file)
        monkeypatch.setattr("stepwise.registry_client.CONFIG_DIR", tmp_path)

        save_token("my-flow", "stw_tok_abc123")
        assert get_token("my-flow") == "stw_tok_abc123"
        assert get_token("nonexistent") is None

    def test_file_permissions(self, tmp_path, monkeypatch):
        tokens_file = tmp_path / "tokens.json"
        monkeypatch.setattr("stepwise.registry_client.TOKENS_FILE", tokens_file)
        monkeypatch.setattr("stepwise.registry_client.CONFIG_DIR", tmp_path)

        save_token("my-flow", "stw_tok_abc123")
        mode = tokens_file.stat().st_mode & 0o777
        assert mode == 0o600


# ── Disk cache ──────────────────────────────────────────────────────


class TestDiskCache:
    def test_cache_and_retrieve(self, tmp_path, monkeypatch):
        monkeypatch.setattr("stepwise.registry_client.CACHE_DIR", tmp_path)

        assert get_cached("my-flow") is None
        cache_flow("my-flow", "name: my-flow\nsteps:\n  a:\n    run: echo\n")
        cached = get_cached("my-flow")
        assert cached is not None
        assert "my-flow" in cached


# ── Fetch flow ──────────────────────────────────────────────────────


class TestFetchFlow:
    def test_fetch_flow_success(self, monkeypatch):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {
            "name": "test-flow",
            "slug": "test-flow",
            "author": "alice",
            "yaml": "name: test-flow\nsteps:\n  a:\n    run: echo\n    outputs: [r]\n",
            "steps": 1,
        }

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response

        monkeypatch.setattr("stepwise.registry_client._client", lambda: mock_client)
        monkeypatch.setattr("stepwise.registry_client.cache_flow", lambda *a: None)

        data = fetch_flow("test-flow")
        assert data["name"] == "test-flow"
        assert data["author"] == "alice"

    def test_fetch_flow_not_found(self, monkeypatch):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "Not found"

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response

        monkeypatch.setattr("stepwise.registry_client._client", lambda: mock_client)

        with pytest.raises(RegistryError, match="not found"):
            fetch_flow("nonexistent")


class TestFetchFlowYaml:
    def test_uses_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr("stepwise.registry_client.CACHE_DIR", tmp_path)

        yaml_content = "name: cached\nsteps:\n  a:\n    run: echo\n    outputs: [r]\n"
        cache_flow("my-flow", yaml_content)

        result = fetch_flow_yaml("my-flow", use_cache=True)
        assert result == yaml_content

    def test_fetches_when_no_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr("stepwise.registry_client.CACHE_DIR", tmp_path)

        yaml_content = "name: fetched\nsteps:\n  a:\n    run: echo\n    outputs: [r]\n"
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = yaml_content

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response

        monkeypatch.setattr("stepwise.registry_client._client", lambda: mock_client)

        result = fetch_flow_yaml("fetched-flow")
        assert result == yaml_content
        # Should be cached now
        assert get_cached("fetched-flow") == yaml_content


# ── Search ──────────────────────────────────────────────────────────


class TestSearch:
    def test_search_success(self, monkeypatch):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {
            "flows": [{"slug": "test-flow", "author": "alice"}],
            "total": 1,
        }

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response

        monkeypatch.setattr("stepwise.registry_client._client", lambda: mock_client)

        result = search_flows(query="test")
        assert len(result["flows"]) == 1


# ── Publish ─────────────────────────────────────────────────────────


class TestPublish:
    def test_publish_success(self, tmp_path, monkeypatch):
        monkeypatch.setattr("stepwise.registry_client.TOKENS_FILE", tmp_path / "tokens.json")
        monkeypatch.setattr("stepwise.registry_client.CONFIG_DIR", tmp_path)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {
            "slug": "new-flow",
            "name": "new-flow",
            "update_token": "stw_tok_abc",
        }

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response

        monkeypatch.setattr("stepwise.registry_client._client", lambda: mock_client)

        result = publish_flow("name: new\nsteps:\n  a:\n    run: echo\n", author="alice")
        assert result["slug"] == "new-flow"
        # Token should be saved
        assert get_token("new-flow") == "stw_tok_abc"

    def test_publish_conflict(self, monkeypatch):
        mock_response = MagicMock()
        mock_response.status_code = 409
        mock_response.text = "Already exists"

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response

        monkeypatch.setattr("stepwise.registry_client._client", lambda: mock_client)

        with pytest.raises(RegistryError, match="already exists"):
            publish_flow("name: dup\nsteps:\n  a:\n    run: echo\n")


# ── Update ──────────────────────────────────────────────────────────


class TestUpdate:
    def test_update_no_token(self, tmp_path, monkeypatch):
        monkeypatch.setattr("stepwise.registry_client.TOKENS_FILE", tmp_path / "tokens.json")

        with pytest.raises(RegistryError, match="No update token"):
            update_flow("some-flow", "name: some\nsteps:\n  a:\n    run: echo\n")


# ── Parse-time @author:name resolution ──────────────────────────────


QUICK_TASK_YAML = textwrap.dedent("""\
    name: quick-task
    author: stepwise
    steps:
      execute:
        run: 'echo "{\\"result\\": \\"done\\", \\"status\\": \\"ok\\"}"'
        outputs: [result, status]
""")


class TestRegistryRefResolution:
    """Test that @author:name refs are resolved at parse time."""

    def _mock_fetch(self, monkeypatch, slug_to_yaml: dict[str, str]):
        """Set up mock that resolves specific slugs to YAML content."""
        def mock_fetch_yaml(slug, *, use_cache=True):
            if slug in slug_to_yaml:
                return slug_to_yaml[slug]
            raise RegistryError(f"Flow '{slug}' not found", 404)

        monkeypatch.setattr(
            "stepwise.registry_client.fetch_flow_yaml",
            mock_fetch_yaml,
        )

    def test_for_each_with_registry_ref(self, monkeypatch):
        self._mock_fetch(monkeypatch, {"quick-task": QUICK_TASK_YAML})

        yaml_str = textwrap.dedent("""\
            name: test
            steps:
              source:
                run: 'echo "{\\"items\\": [1, 2, 3]}"'
                outputs: [items]
              process:
                for_each: source.items
                flow: "@stepwise:quick-task"
                outputs: [results]
        """)

        wf = load_workflow_yaml(yaml_str)
        step = wf.steps["process"]
        assert step.sub_flow is not None
        assert "execute" in step.sub_flow.steps



# ── Engine _resolve_flow_ref stays dead code ─────────────────────────


class TestEngineResolveFlowRef:
    """Verify the engine's _resolve_flow_ref raises for any ref type."""

    def _make_job(self):
        from stepwise.models import Job, JobStatus
        wf = load_workflow_yaml("name: dummy\nsteps:\n  a:\n    run: echo hi\n    outputs: [r]\n")
        return Job(id="j1", objective="test", workflow=wf, status=JobStatus.RUNNING)

    def test_registry_ref_raises(self, engine):
        job = self._make_job()
        with pytest.raises(ValueError, match="Registry references"):
            engine._resolve_flow_ref("@alice:my-flow", job)

    def test_file_ref_raises(self, engine):
        job = self._make_job()
        with pytest.raises(ValueError, match="Unexpected file ref"):
            engine._resolve_flow_ref("flows/sub.yaml", job)
