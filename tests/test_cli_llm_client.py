"""Tests for CLI LLM client (native ACP fallback)."""

import json
from concurrent.futures import Future
from unittest.mock import MagicMock, patch

import pytest

from stepwise.cli_llm_client import CliLLMClient, detect_cli_backend


# ── detect_cli_backend ──────────────────────────────────────────────

class TestDetectCliBackend:
    def test_found_default_agent(self):
        """When npx is on PATH and agent registry has claude, returns ('claude',)."""
        with patch("stepwise.cli_llm_client.shutil.which", return_value="/usr/bin/npx"):
            result = detect_cli_backend()
        assert result == ("claude",)

    def test_not_found(self):
        """When no agent command is on PATH, returns None."""
        with patch("stepwise.cli_llm_client.shutil.which", return_value=None):
            result = detect_cli_backend()
        assert result is None

    def test_custom_agent_via_env(self):
        """STEPWISE_DEFAULT_AGENT env var selects the agent."""
        with patch("stepwise.cli_llm_client.shutil.which", return_value="/usr/bin/npx"), \
             patch.dict("os.environ", {"STEPWISE_DEFAULT_AGENT": "codex"}):
            result = detect_cli_backend()
        assert result == ("codex",)

    def test_fallback_to_other_agents(self):
        """When default agent command isn't found but another is, returns that one."""
        from stepwise.agent_registry import AgentConfig

        def mock_which(cmd):
            return "/usr/bin/aloop" if cmd == "aloop" else None

        mock_agent = AgentConfig(name="bad", command=["bad-cmd"])

        with patch("stepwise.cli_llm_client.shutil.which", side_effect=mock_which), \
             patch("stepwise.agent_registry.get_agent", return_value=mock_agent):
            result = detect_cli_backend()
        assert result == ("aloop",)


# ── CliLLMClient ─────────────────────────────────────────────────────

class TestCliLLMClient:
    """Test CliLLMClient using mocked ACP transport."""

    def _make_mock_transport_and_client(self, text_chunks, cost=None):
        """Create mock transport/client that delivers text chunks via notification handler."""
        captured_handlers = {}

        class MockTransport:
            def __init__(self, *a, **kw):
                pass

            def start(self):
                pass

            def on_notification(self, method, handler):
                captured_handlers[method] = handler

            def send_request(self, method, params=None):
                future = Future()
                if method == "session/prompt":
                    handler = captured_handlers.get("session/update")
                    if handler:
                        for chunk in text_chunks:
                            handler({
                                "update": {
                                    "sessionUpdate": "agent_message_chunk",
                                    "content": {"type": "text", "text": chunk},
                                }
                            })
                        if cost is not None:
                            handler({
                                "update": {
                                    "sessionUpdate": "usage_update",
                                    "cost": {"amount": cost, "currency": "usd"},
                                }
                            })
                    future.set_result({"stopReason": "end_turn"})
                else:
                    future.set_result({})
                return future

            def close(self):
                pass

        mock_client = MagicMock()
        mock_client.initialize.return_value = {"capabilities": {}}
        mock_client.new_session.return_value = "test-session-123"

        return MockTransport, mock_client

    def _run_chat(self, text_chunks, cost=None, messages=None, tools=None):
        """Run a chat_completion with mocked ACP transport."""
        from stepwise.agent_registry import ResolvedAgentConfig

        MockTransport, mock_client = self._make_mock_transport_and_client(text_chunks, cost)

        mock_resolved = ResolvedAgentConfig(
            name="claude",
            command=["echo", "mock"],
            env_vars={},
        )

        mock_proc = MagicMock()
        mock_proc.terminate.return_value = None
        mock_proc.wait.return_value = 0

        client = CliLLMClient(agent="claude")
        with patch("stepwise.agent_registry.resolve_config", return_value=mock_resolved), \
             patch("subprocess.Popen", return_value=mock_proc), \
             patch("stepwise.acp_transport.JsonRpcTransport", side_effect=MockTransport), \
             patch("stepwise.acp_client.ACPClient", return_value=mock_client):
            return client.chat_completion(
                model="ignored",
                messages=messages or [{"role": "user", "content": "test"}],
                tools=tools,
            )

    def test_chat_completion_extracts_text(self):
        """Text chunks from ACP are concatenated into response content."""
        response = self._run_chat(["Hello ", "world"])
        assert response.content == "Hello world"
        assert response.model == "cli:claude"
        assert response.tool_calls is None

    def test_chat_completion_extracts_cost(self):
        """Cost from usage_update is extracted."""
        response = self._run_chat(["hi"], cost=0.005)
        assert response.cost_usd == 0.005

    def test_chat_completion_bakes_output_fields_into_prompt(self):
        """When tools with step_output schema are provided, JSON instructions are appended."""
        tools = [{
            "type": "function",
            "function": {
                "name": "step_output",
                "description": "Provide output.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string"},
                        "score": {"type": "string"},
                    },
                    "required": ["summary", "score"],
                },
            },
        }]

        # Just verify it doesn't crash — the prompt building is unit-tested below
        response = self._run_chat(
            ['{"summary":"ok","score":"9"}'],
            tools=tools,
        )
        assert response.content is not None

    def test_chat_completion_no_text_raises(self):
        """When ACP returns no text, raises RuntimeError."""
        with pytest.raises(RuntimeError, match="no text"):
            self._run_chat([])

    def test_latency_tracked(self):
        """Response includes latency_ms >= 0."""
        response = self._run_chat(["hi"])
        assert response.latency_ms >= 0

    def test_tmpdir_cleaned_up(self):
        """Temp directory is removed after call completes."""
        import os
        import tempfile

        created_dirs = []
        original_mkdtemp = tempfile.mkdtemp

        def tracking_mkdtemp(**kwargs):
            d = original_mkdtemp(**kwargs)
            created_dirs.append(d)
            return d

        with patch("stepwise.cli_llm_client.tempfile.mkdtemp", side_effect=tracking_mkdtemp):
            self._run_chat(["hi"])

        assert len(created_dirs) == 1
        assert not os.path.exists(created_dirs[0])


class TestBuildPrompt:
    def test_combines_messages(self):
        client = CliLLMClient()
        prompt = client._build_prompt([
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "What is 2+2?"},
        ])
        assert "You are helpful." in prompt
        assert "What is 2+2?" in prompt

    def test_adds_json_instructions_with_tools(self):
        client = CliLLMClient()
        tools = [{
            "type": "function",
            "function": {
                "name": "step_output",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string"},
                        "score": {"type": "string"},
                    },
                },
            },
        }]
        prompt = client._build_prompt(
            [{"role": "user", "content": "Analyze this."}],
            tools=tools,
        )
        assert '"summary"' in prompt
        assert '"score"' in prompt
        assert "valid JSON object" in prompt


class TestExtractOutputFields:
    def test_extracts_from_step_output(self):
        tools = [{
            "type": "function",
            "function": {
                "name": "step_output",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "answer": {"type": "string"},
                    },
                },
            },
        }]
        assert CliLLMClient._extract_output_fields(tools) == ["answer"]

    def test_returns_empty_for_no_match(self):
        assert CliLLMClient._extract_output_fields([]) == []


# ── Integration with LLMExecutor._parse_output ──────────────────────

class TestCliLLMWithLLMExecutor:
    """Verify CLI responses are parsed correctly by LLMExecutor._parse_output."""

    def test_json_in_code_fence_parsed_by_tier2(self):
        """Agent returns JSON in code fences — LLMExecutor._parse_output handles it."""
        from stepwise.executors import LLMExecutor
        from stepwise.llm_client import LLMResponse

        response = LLMResponse(
            content='```json\n{"summary": "all good", "score": "0.9"}\n```',
            tool_calls=None,
            model="cli:claude",
        )

        client = CliLLMClient()
        executor = LLMExecutor(client=client, model="x", prompt="test")
        executor._output_fields = ["summary", "score"]

        artifact, method = executor._parse_output(response, ["summary", "score"])
        assert artifact == {"summary": "all good", "score": "0.9"}
        assert method == "json_content"

    def test_single_field_fallback(self):
        """When agent returns prose and there's one output field, single-field shortcut works."""
        from stepwise.executors import LLMExecutor
        from stepwise.llm_client import LLMResponse

        response = LLMResponse(
            content="The answer is four.",
            tool_calls=None,
            model="cli:claude",
        )

        client = CliLLMClient()
        executor = LLMExecutor(client=client, model="x", prompt="test")

        artifact, method = executor._parse_output(response, ["answer"])
        assert artifact == {"answer": "The answer is four."}
        assert method == "single_field"


# ── Fallback registration ────────────────────────────────────────────

class TestFallbackRegistration:
    def test_cli_fallback_registered_when_no_openrouter_key(self):
        """With no OpenRouter key but agent available, LLM executor is registered."""
        from stepwise.config import StepwiseConfig
        from stepwise.registry_factory import create_default_registry

        config = StepwiseConfig(openrouter_api_key=None)

        with patch("stepwise.cli_llm_client.detect_cli_backend", return_value=("claude",)):
            registry = create_default_registry(config)

        assert "llm" in registry._factories
        assert registry.llm_backend == "cli"

    def test_no_llm_when_neither_available(self):
        """With no OpenRouter key and no agent, LLM executor is not registered."""
        from stepwise.config import StepwiseConfig
        from stepwise.registry_factory import create_default_registry

        config = StepwiseConfig(openrouter_api_key=None)

        with patch("stepwise.cli_llm_client.detect_cli_backend", return_value=None):
            registry = create_default_registry(config)

        assert "llm" not in registry._factories
        assert registry.llm_backend is None

    def test_openrouter_preferred_over_cli(self):
        """When OpenRouter key is set, it takes priority over CLI fallback."""
        from stepwise.config import StepwiseConfig
        from stepwise.registry_factory import create_default_registry

        config = StepwiseConfig(openrouter_api_key="sk-test-key")

        registry = create_default_registry(config)

        assert "llm" in registry._factories
        assert registry.llm_backend == "openrouter"
