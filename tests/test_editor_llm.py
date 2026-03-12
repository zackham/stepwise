"""Tests for M13 Editor LLM chat endpoint."""

import json
import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from starlette.testclient import TestClient

import stepwise.server as srv
from stepwise.server import app


@pytest.fixture
def project_dir(tmp_path):
    flows_dir = tmp_path / "flows"
    flows_dir.mkdir()
    return tmp_path


@pytest.fixture
def client(project_dir):
    old_env = os.environ.copy()
    os.environ["STEPWISE_PROJECT_DIR"] = str(project_dir)
    os.environ["STEPWISE_DB"] = ":memory:"
    os.environ["STEPWISE_TEMPLATES"] = str(project_dir / "_templates")
    os.environ["STEPWISE_JOBS_DIR"] = str(project_dir / "_jobs")

    with TestClient(app, raise_server_exceptions=False) as c:
        assert srv._project_dir == project_dir.resolve()
        yield c

    os.environ.clear()
    os.environ.update(old_env)


class TestEditorChat:
    def test_chat_returns_stream(self, client):
        """Chat endpoint returns streaming NDJSON."""
        # Mock the chat_stream to yield test chunks
        def mock_stream(*args, **kwargs):
            yield {"type": "text", "content": "Here is a "}
            yield {"type": "text", "content": "flow:"}
            yield {"type": "yaml", "content": "name: test\nsteps:\n  s1:\n    run: echo hi\n    outputs: [out]", "apply_id": "yaml-0"}
            yield {"type": "done", "model": "test-model", "cost_usd": 0.001}

        with patch("stepwise.server.chat_stream", mock_stream, create=True):
            with patch("stepwise.editor_llm.chat_stream", mock_stream):
                resp = client.post(
                    "/api/editor/chat",
                    json={"message": "Create a simple flow"},
                )
                assert resp.status_code == 200

                # Parse NDJSON lines
                lines = [line for line in resp.text.strip().split("\n") if line]
                chunks = [json.loads(line) for line in lines]

                assert len(chunks) == 4
                assert chunks[0]["type"] == "text"
                assert chunks[0]["content"] == "Here is a "
                assert chunks[1]["type"] == "text"
                assert chunks[2]["type"] == "yaml"
                assert "test" in chunks[2]["content"]
                assert chunks[3]["type"] == "done"

    def test_chat_with_current_yaml(self, client):
        """Chat endpoint accepts current_yaml context."""
        def mock_stream(*args, **kwargs):
            yield {"type": "text", "content": "Modified."}
            yield {"type": "done", "model": "test", "cost_usd": None}

        with patch("stepwise.editor_llm.chat_stream", mock_stream):
            resp = client.post(
                "/api/editor/chat",
                json={
                    "message": "Add a step",
                    "current_yaml": "name: test\nsteps:\n  s1:\n    run: echo hi\n    outputs: [out]",
                    "selected_step": "s1",
                },
            )
            assert resp.status_code == 200

    def test_chat_no_api_key(self, client):
        """Chat returns error when no API key configured."""
        from stepwise.editor_llm import chat_stream
        from stepwise.config import StepwiseConfig

        with patch("stepwise.editor_llm.load_config", return_value=StepwiseConfig()):
            chunks = list(chat_stream("test"))
            assert len(chunks) == 1
            assert chunks[0]["type"] == "error"
            assert "API key" in chunks[0]["content"]


class TestBuildMessages:
    def test_basic_message(self):
        from stepwise.editor_llm import _build_messages
        msgs = _build_messages("Hello", [], None, None)
        assert msgs[0]["role"] == "system"
        assert msgs[-1]["role"] == "user"
        assert msgs[-1]["content"] == "Hello"

    def test_with_yaml_context(self):
        from stepwise.editor_llm import _build_messages
        yaml = "name: test\nsteps:\n  s1:\n    run: echo\n    outputs: [x]"
        msgs = _build_messages("Modify it", [], yaml, None)
        # Should have system prompt, yaml context, user message
        assert len(msgs) == 3
        assert "Current flow YAML" in msgs[1]["content"]

    def test_with_selected_step(self):
        from stepwise.editor_llm import _build_messages
        yaml = "name: test\nsteps:\n  s1:\n    run: echo\n    outputs: [x]"
        msgs = _build_messages("Fix it", [], yaml, "s1")
        assert "s1" in msgs[1]["content"]

    def test_with_history(self):
        from stepwise.editor_llm import _build_messages
        history = [
            {"role": "user", "content": "Create a flow"},
            {"role": "assistant", "content": "Here's a flow..."},
        ]
        msgs = _build_messages("Now modify it", history, None, None)
        assert len(msgs) == 4  # system + 2 history + user
        assert msgs[1]["role"] == "user"
        assert msgs[2]["role"] == "assistant"
