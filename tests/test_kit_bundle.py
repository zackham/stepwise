"""Tests for kit bundle collect/unpack, registry client kit functions, and CLI kit commands."""

import json
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from stepwise.bundle import (
    BundleError,
    collect_kit_bundle,
    unpack_kit_bundle,
)
from stepwise.cli import EXIT_SUCCESS, EXIT_USAGE_ERROR, main
from stepwise.registry_client import (
    RegistryError,
    fetch_kit,
    fetch_kit_flow,
    publish_kit,
    update_kit,
)


SIMPLE_KIT_YAML = "name: test-kit\ndescription: A test kit\nauthor: alice\ntags: [test]\n"

SIMPLE_FLOW_A = """\
name: flow-a
author: test
steps:
  hello:
    run: 'echo "{\\"msg\\": \\"hi\\"}"'
    outputs: [msg]
"""

SIMPLE_FLOW_B = """\
name: flow-b
author: test
steps:
  world:
    run: 'echo "{\\"msg\\": \\"world\\"}"'
    outputs: [msg]
"""


def _make_kit_dir(tmp_path: Path, name: str = "test-kit") -> Path:
    """Create a minimal kit directory with KIT.yaml and two flows."""
    kit_dir = tmp_path / name
    kit_dir.mkdir()
    (kit_dir / "KIT.yaml").write_text(SIMPLE_KIT_YAML)
    flow_a = kit_dir / "flow-a"
    flow_a.mkdir()
    (flow_a / "FLOW.yaml").write_text(SIMPLE_FLOW_A)
    flow_b = kit_dir / "flow-b"
    flow_b.mkdir()
    (flow_b / "FLOW.yaml").write_text(SIMPLE_FLOW_B)
    return kit_dir


# ── collect_kit_bundle tests ─────────────────────────────────────────


class TestCollectKitBundle:
    def test_collects_kit_yaml_and_flows(self, tmp_path):
        kit_dir = _make_kit_dir(tmp_path)
        kit_yaml, bundled = collect_kit_bundle(kit_dir)

        assert "test-kit" in kit_yaml
        assert len(bundled) == 2
        names = {b["name"] for b in bundled}
        assert names == {"flow-a", "flow-b"}
        for b in bundled:
            assert "yaml" in b
            assert "steps" in b["yaml"]

    def test_no_flows_raises(self, tmp_path):
        kit_dir = tmp_path / "empty-kit"
        kit_dir.mkdir()
        (kit_dir / "KIT.yaml").write_text(SIMPLE_KIT_YAML)

        with pytest.raises(BundleError, match="no bundled flows"):
            collect_kit_bundle(kit_dir)

    def test_collects_colocated_files(self, tmp_path):
        kit_dir = _make_kit_dir(tmp_path)
        (kit_dir / "flow-a" / "helper.py").write_text("x = 1")

        kit_yaml, bundled = collect_kit_bundle(kit_dir)
        flow_a = next(b for b in bundled if b["name"] == "flow-a")
        assert flow_a["files"] is not None
        assert "helper.py" in flow_a["files"]

    def test_skips_non_flow_subdirs(self, tmp_path):
        kit_dir = _make_kit_dir(tmp_path)
        # Add a subdir without FLOW.yaml
        (kit_dir / "docs").mkdir()
        (kit_dir / "docs" / "readme.md").write_text("# docs")

        kit_yaml, bundled = collect_kit_bundle(kit_dir)
        names = {b["name"] for b in bundled}
        assert "docs" not in names
        assert len(bundled) == 2

    def test_no_kit_yaml_raises(self, tmp_path):
        kit_dir = tmp_path / "no-kit"
        kit_dir.mkdir()
        (kit_dir / "flow-a").mkdir()
        (kit_dir / "flow-a" / "FLOW.yaml").write_text(SIMPLE_FLOW_A)

        with pytest.raises(BundleError, match="No KIT.yaml"):
            collect_kit_bundle(kit_dir)


# ── unpack_kit_bundle tests ──────────────────────────────────────────


class TestUnpackKitBundle:
    def test_creates_structure(self, tmp_path):
        target = tmp_path / "installed-kit"
        bundled = [
            {"name": "flow-a", "yaml": SIMPLE_FLOW_A},
            {"name": "flow-b", "yaml": SIMPLE_FLOW_B},
        ]
        unpack_kit_bundle(target, SIMPLE_KIT_YAML, bundled)

        assert (target / "KIT.yaml").exists()
        assert (target / "KIT.yaml").read_text() == SIMPLE_KIT_YAML
        assert (target / "flow-a" / "FLOW.yaml").exists()
        assert (target / "flow-b" / "FLOW.yaml").exists()

    def test_writes_origin_json(self, tmp_path):
        target = tmp_path / "kit-with-origin"
        bundled = [{"name": "flow-a", "yaml": SIMPLE_FLOW_A}]
        origin = {"registry": "https://stepwise.run", "slug": "test-kit", "type": "kit"}

        unpack_kit_bundle(target, SIMPLE_KIT_YAML, bundled, origin=origin)

        origin_path = target / ".origin.json"
        assert origin_path.exists()
        data = json.loads(origin_path.read_text())
        assert data["type"] == "kit"
        assert data["slug"] == "test-kit"

    def test_unpacks_colocated_files(self, tmp_path):
        target = tmp_path / "kit-files"
        bundled = [
            {"name": "flow-a", "yaml": SIMPLE_FLOW_A, "files": {"helper.py": "x = 1"}},
        ]
        unpack_kit_bundle(target, SIMPLE_KIT_YAML, bundled)

        assert (target / "flow-a" / "helper.py").read_text() == "x = 1"

    def test_handles_yaml_content_key(self, tmp_path):
        """Server API returns yaml_content key, not yaml."""
        target = tmp_path / "kit-server-format"
        bundled = [
            {"name": "flow-a", "yaml_content": SIMPLE_FLOW_A},
        ]
        unpack_kit_bundle(target, SIMPLE_KIT_YAML, bundled)

        assert (target / "flow-a" / "FLOW.yaml").exists()
        assert "flow-a" in (target / "flow-a" / "FLOW.yaml").read_text()


# ── Registry client kit functions ────────────────────────────────────


def _mock_client(monkeypatch, response):
    """Set up mock httpx client for registry_client."""
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = response
    mock_client.get.return_value = response
    mock_client.put.return_value = response
    monkeypatch.setattr("stepwise.registry_client._client", lambda: mock_client)
    return mock_client


def _json_response(data, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = {"content-type": "application/json"}
    resp.json.return_value = data
    resp.text = json.dumps(data)
    return resp


class TestPublishKitClient:
    def test_publish_kit_success(self, monkeypatch, tmp_path):
        monkeypatch.setattr("stepwise.registry_client.TOKENS_FILE", tmp_path / "tokens.json")
        monkeypatch.setattr("stepwise.registry_client.CONFIG_DIR", tmp_path)

        response = _json_response({
            "slug": "test-kit",
            "name": "test-kit",
            "update_token": "stw_tok_kit123",
            "flow_count": 2,
        })
        _mock_client(monkeypatch, response)

        result = publish_kit(
            SIMPLE_KIT_YAML,
            [{"name": "flow-a", "yaml": SIMPLE_FLOW_A}],
            auth_token="tok_test",
        )
        assert result["slug"] == "test-kit"
        assert result["update_token"] == "stw_tok_kit123"

    def test_publish_kit_conflict(self, monkeypatch):
        response = MagicMock()
        response.status_code = 409
        response.text = "already exists"
        _mock_client(monkeypatch, response)

        with pytest.raises(RegistryError, match="already exists"):
            publish_kit(SIMPLE_KIT_YAML, [{"name": "flow-a", "yaml": SIMPLE_FLOW_A}])


class TestFetchKitClient:
    def test_fetch_kit_success(self, monkeypatch):
        data = {
            "slug": "test-kit",
            "name": "test-kit",
            "author": "alice",
            "bundled_flows": [
                {"name": "flow-a", "yaml": SIMPLE_FLOW_A},
                {"name": "flow-b", "yaml": SIMPLE_FLOW_B},
            ],
            "kit_yaml": SIMPLE_KIT_YAML,
            "downloads": 42,
        }
        _mock_client(monkeypatch, _json_response(data))

        result = fetch_kit("test-kit")
        assert result["slug"] == "test-kit"
        assert len(result["bundled_flows"]) == 2

    def test_fetch_kit_not_found(self, monkeypatch):
        response = MagicMock()
        response.status_code = 404
        response.text = "Not found"
        _mock_client(monkeypatch, response)

        with pytest.raises(RegistryError, match="not found"):
            fetch_kit("nonexistent")

    def test_fetch_kit_flow_success(self, monkeypatch):
        data = {
            "name": "flow-a",
            "slug": "flow-a",
            "yaml": SIMPLE_FLOW_A,
        }
        _mock_client(monkeypatch, _json_response(data))

        result = fetch_kit_flow("test-kit", "flow-a")
        assert result["name"] == "flow-a"

    def test_fetch_kit_flow_not_found(self, monkeypatch):
        response = MagicMock()
        response.status_code = 404
        response.text = "Not found"
        _mock_client(monkeypatch, response)

        with pytest.raises(RegistryError, match="not found"):
            fetch_kit_flow("test-kit", "nonexistent")


class TestUpdateKitClient:
    def test_update_kit_success(self, monkeypatch, tmp_path):
        monkeypatch.setattr("stepwise.registry_client.TOKENS_FILE", tmp_path / "tokens.json")
        monkeypatch.setattr("stepwise.registry_client.CONFIG_DIR", tmp_path)

        # Save a token first
        from stepwise.registry_client import save_token
        save_token("test-kit", "stw_tok_kit123")

        response = _json_response({"slug": "test-kit", "version": "2.0"})
        _mock_client(monkeypatch, response)

        result = update_kit("test-kit", SIMPLE_KIT_YAML, [{"name": "flow-a", "yaml": SIMPLE_FLOW_A}])
        assert result["slug"] == "test-kit"

    def test_update_kit_no_token(self, monkeypatch, tmp_path):
        monkeypatch.setattr("stepwise.registry_client.TOKENS_FILE", tmp_path / "tokens.json")
        monkeypatch.setattr("stepwise.registry_client.CONFIG_DIR", tmp_path)

        with pytest.raises(RegistryError, match="No update token"):
            update_kit("test-kit", SIMPLE_KIT_YAML, [])


# ── CLI: stepwise share (kit) ───────────────────────────────────────


class TestShareKit:
    def _setup_auth(self, tmp_path, monkeypatch):
        auth_file = tmp_path / "auth.json"
        auth_file.write_text(json.dumps({
            "auth_token": "tok_test",
            "github_username": "test",
            "registry_url": "https://stepwise.run",
        }))
        monkeypatch.setattr("stepwise.registry_client.AUTH_FILE", auth_file)

    def test_share_kit_directory(self, tmp_path, capsys, monkeypatch):
        """stepwise share with a kit directory calls publish_kit."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("stepwise.registry_client.TOKENS_FILE", tmp_path / "tokens.json")
        monkeypatch.setattr("stepwise.registry_client.CONFIG_DIR", tmp_path)
        self._setup_auth(tmp_path, monkeypatch)

        # Create kit in flows/ (a discovery dir)
        flows_dir = tmp_path / "flows"
        flows_dir.mkdir()
        _make_kit_dir(flows_dir, "test-kit")

        response = _json_response({
            "slug": "test-kit",
            "name": "test-kit",
            "update_token": "stw_tok_kit",
            "flow_count": 2,
        })
        mock_client = _mock_client(monkeypatch, response)

        # Auto-confirm prompt
        from stepwise.io import PlainAdapter
        monkeypatch.setattr(
            "stepwise.cli.create_adapter",
            lambda **kw: PlainAdapter(output=sys.stderr, input_stream=StringIO("y\n")),
        )

        rc = main(["share", "test-kit"])
        assert rc == EXIT_SUCCESS

        # Verify POST was made to /api/kits
        call_args = mock_client.post.call_args
        url = call_args[0][0]
        assert "/api/kits" in url
        payload = call_args[1]["json"]
        assert "kit_yaml" in payload
        assert "bundled_flows" in payload
        assert len(payload["bundled_flows"]) == 2

    def test_share_detects_kit_vs_flow(self, tmp_path, capsys, monkeypatch):
        """stepwise share with a regular flow uses publish_flow, not publish_kit."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("stepwise.registry_client.TOKENS_FILE", tmp_path / "tokens.json")
        monkeypatch.setattr("stepwise.registry_client.CONFIG_DIR", tmp_path)
        self._setup_auth(tmp_path, monkeypatch)

        # Create a regular flow (no KIT.yaml)
        flow_dir = tmp_path / "my-flow"
        flow_dir.mkdir()
        (flow_dir / "FLOW.yaml").write_text(SIMPLE_FLOW_A)

        response = _json_response({
            "slug": "my-flow",
            "name": "flow-a",
            "update_token": "stw_tok_flow",
        })
        mock_client = _mock_client(monkeypatch, response)

        rc = main(["share", str(flow_dir)])
        assert rc == EXIT_SUCCESS

        # Verify POST was made to /api/flows, not /api/kits
        call_args = mock_client.post.call_args
        url = call_args[0][0]
        assert "/api/flows" in url
        assert "/api/kits" not in url


# ── CLI: stepwise get (kit) ──────────────────────────────────────────


class TestGetKit:
    def test_get_kit_creates_structure(self, tmp_path, capsys, monkeypatch):
        """stepwise get for a kit installs KIT.yaml + flow subdirectories."""
        monkeypatch.chdir(tmp_path)

        kit_data = {
            "slug": "test-kit",
            "name": "test-kit",
            "author": "alice",
            "type": "kit",
            "version": "1.0",
            "downloads": 42,
            "kit_yaml": SIMPLE_KIT_YAML,
            "bundled_flows": [
                {"name": "flow-a", "yaml": SIMPLE_FLOW_A},
                {"name": "flow-b", "yaml": SIMPLE_FLOW_B},
            ],
            "include": [],
        }

        # fetch_flow returns 404, then fetch_kit returns kit data
        def mock_fetch_flow(slug, **kw):
            raise RegistryError(f"Flow '{slug}' not found in registry", 404)

        monkeypatch.setattr("stepwise.registry_client.fetch_flow", mock_fetch_flow)
        monkeypatch.setattr("stepwise.registry_client.fetch_kit", lambda slug, **kw: kit_data)

        rc = main(["get", "test-kit"])
        assert rc == EXIT_SUCCESS

        target_dir = tmp_path / ".stepwise" / "registry" / "@alice" / "test-kit"
        assert (target_dir / "KIT.yaml").exists()
        assert (target_dir / "flow-a" / "FLOW.yaml").exists()
        assert (target_dir / "flow-b" / "FLOW.yaml").exists()

        # Verify .origin.json
        origin = json.loads((target_dir / ".origin.json").read_text())
        assert origin["type"] == "kit"
        assert origin["slug"] == "test-kit"

        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "Downloaded" in combined
        assert "2 flows" in combined

    def test_get_kit_fallback_from_flow_404(self, tmp_path, capsys, monkeypatch):
        """When fetch_flow returns 404, cmd_get falls back to fetch_kit."""
        monkeypatch.chdir(tmp_path)

        kit_data = {
            "slug": "my-kit",
            "name": "my-kit",
            "author": "bob",
            "downloads": 10,
            "kit_yaml": SIMPLE_KIT_YAML,
            "bundled_flows": [{"name": "flow-a", "yaml": SIMPLE_FLOW_A}],
            "include": [],
        }

        fetch_kit_called = []

        def mock_fetch_flow(slug, **kw):
            raise RegistryError(f"Flow '{slug}' not found", 404)

        def mock_fetch_kit(slug, **kw):
            fetch_kit_called.append(slug)
            return kit_data

        monkeypatch.setattr("stepwise.registry_client.fetch_flow", mock_fetch_flow)
        monkeypatch.setattr("stepwise.registry_client.fetch_kit", mock_fetch_kit)

        rc = main(["get", "my-kit"])
        assert rc == EXIT_SUCCESS
        assert "my-kit" in fetch_kit_called

    def test_get_flow_still_works(self, tmp_path, capsys, monkeypatch):
        """When fetch_flow succeeds, kit fallback is not triggered."""
        monkeypatch.chdir(tmp_path)

        flow_data = {
            "name": "my-flow",
            "slug": "my-flow",
            "author": "alice",
            "yaml": SIMPLE_FLOW_A,
            "steps": 1,
            "downloads": 5,
        }

        fetch_kit_called = []

        monkeypatch.setattr("stepwise.registry_client.fetch_flow", lambda slug, **kw: flow_data)
        monkeypatch.setattr(
            "stepwise.registry_client.fetch_kit",
            lambda slug, **kw: fetch_kit_called.append(slug) or {},
        )

        rc = main(["get", "my-flow"])
        assert rc == EXIT_SUCCESS
        assert len(fetch_kit_called) == 0

        # Verify it's installed as a flow, not a kit
        target_dir = tmp_path / ".stepwise" / "registry" / "@alice" / "my-flow"
        assert (target_dir / "FLOW.yaml").exists()
        assert not (target_dir / "KIT.yaml").exists()


# ── CLI: stepwise search (type column) ───────────────────────────────


class TestSearchKit:
    def test_search_shows_type_column(self, capsys, monkeypatch):
        """stepwise search includes TYPE column and shows kit type."""
        result = {
            "flows": [
                {"slug": "my-flow", "type": "flow", "author": "alice", "steps": 3, "downloads": 10},
            ],
            "kits": [
                {"slug": "my-kit", "author": "bob", "flow_count": 5, "downloads": 20},
            ],
            "total": 2,
        }
        monkeypatch.setattr("stepwise.registry_client.search_flows", lambda **kw: result)

        rc = main(["search", "test"])
        assert rc == EXIT_SUCCESS

        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "TYPE" in combined
        assert "flow" in combined
        assert "kit" in combined
        assert "my-flow" in combined
        assert "my-kit" in combined

    def test_search_no_results(self, capsys, monkeypatch):
        """Search with no flows or kits shows 'No results found'."""
        monkeypatch.setattr("stepwise.registry_client.search_flows", lambda **kw: {"flows": [], "kits": [], "total": 0})

        rc = main(["search", "nonexistent"])
        assert rc == EXIT_SUCCESS

        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "No results found" in combined

    def test_search_backward_compat_no_kits_key(self, capsys, monkeypatch):
        """Search works even if server doesn't return 'kits' key (backward compat)."""
        result = {
            "flows": [
                {"slug": "old-flow", "author": "alice", "steps": 2, "downloads": 5},
            ],
            "total": 1,
        }
        monkeypatch.setattr("stepwise.registry_client.search_flows", lambda **kw: result)

        rc = main(["search", "old"])
        assert rc == EXIT_SUCCESS

        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "old-flow" in combined
