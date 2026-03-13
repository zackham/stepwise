"""Tests for CLI LLM client (acpx exec fallback)."""

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from stepwise.cli_llm_client import CliLLMClient, detect_cli_backend


# ── NDJSON fixtures ──────────────────────────────────────────────────

def _make_ndjson(*events: dict) -> str:
    return "\n".join(json.dumps(e) for e in events) + "\n"


def _agent_message_chunk(text: str) -> dict:
    return {
        "params": {
            "update": {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": text},
            }
        }
    }


def _usage_update(amount: float) -> dict:
    return {
        "params": {
            "update": {
                "sessionUpdate": "usage_update",
                "cost": {"amount": amount, "currency": "usd"},
            }
        }
    }


def _make_completed_process(ndjson_stdout: str, returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["acpx"],
        returncode=returncode,
        stdout=ndjson_stdout,
        stderr="",
    )


# ── detect_cli_backend ──────────────────────────────────────────────

class TestDetectCliBackend:
    def test_found(self):
        with patch("shutil.which", return_value="/usr/bin/acpx"):
            result = detect_cli_backend()
        assert result == ("/usr/bin/acpx", "claude")

    def test_not_found(self):
        with patch("shutil.which", return_value=None):
            result = detect_cli_backend()
        assert result is None

    def test_custom_agent(self):
        with patch("shutil.which", return_value="/usr/bin/acpx"), \
             patch.dict("os.environ", {"STEPWISE_DEFAULT_AGENT": "gemini"}):
            result = detect_cli_backend()
        assert result == ("/usr/bin/acpx", "gemini")

    def test_custom_acpx_path(self):
        with patch("shutil.which", return_value="/custom/acpx") as mock_which, \
             patch.dict("os.environ", {"ACPX_PATH": "/custom/acpx"}):
            result = detect_cli_backend()
        mock_which.assert_called_with("/custom/acpx")
        assert result == ("/custom/acpx", "claude")


# ── CliLLMClient ─────────────────────────────────────────────────────

class TestCliLLMClient:
    def test_chat_completion_parses_json_response(self):
        """Agent returns JSON in code fences — verify content is extracted."""
        ndjson = _make_ndjson(
            _agent_message_chunk("```json\n"),
            _agent_message_chunk('{"answer": "4"}'),
            _agent_message_chunk("\n```"),
            _usage_update(0.003),
        )

        client = CliLLMClient(acpx_path="/usr/bin/acpx", agent="claude")
        with patch("subprocess.run", return_value=_make_completed_process(ndjson)):
            response = client.chat_completion(
                model="ignored",
                messages=[{"role": "user", "content": "What is 2+2?"}],
            )

        assert response.content is not None
        assert '{"answer": "4"}' in response.content
        assert response.model == "cli:claude"
        assert response.tool_calls is None

    def test_chat_completion_extracts_cost(self):
        """Verify cost_usd is extracted from usage_update events."""
        ndjson = _make_ndjson(
            _agent_message_chunk("hello"),
            _usage_update(0.001),
            _usage_update(0.005),  # last one wins
        )

        client = CliLLMClient()
        with patch("subprocess.run", return_value=_make_completed_process(ndjson)):
            response = client.chat_completion(
                model="x", messages=[{"role": "user", "content": "hi"}],
            )

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

        ndjson = _make_ndjson(_agent_message_chunk('{"summary":"ok","score":"9"}'))
        written_prompt = None

        original_run = subprocess.run

        def capture_run(*args, **kwargs):
            nonlocal written_prompt
            cmd = args[0] if args else kwargs.get("args", [])
            # Find the prompt file path (last arg after -f)
            for i, arg in enumerate(cmd):
                if arg == "-f" and i + 1 < len(cmd):
                    with open(cmd[i + 1]) as f:
                        written_prompt = f.read()
            return _make_completed_process(ndjson)

        client = CliLLMClient()
        with patch("subprocess.run", side_effect=capture_run):
            client.chat_completion(
                model="x",
                messages=[{"role": "user", "content": "Analyze this."}],
                tools=tools,
            )

        assert written_prompt is not None
        assert '"summary"' in written_prompt
        assert '"score"' in written_prompt
        assert "valid JSON object" in written_prompt

    def test_chat_completion_handles_timeout(self):
        client = CliLLMClient()
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("acpx", 600)):
            with pytest.raises(RuntimeError, match="timed out"):
                client.chat_completion(
                    model="x", messages=[{"role": "user", "content": "hi"}],
                )

    def test_chat_completion_handles_missing_acpx(self):
        client = CliLLMClient()
        with patch("subprocess.run", side_effect=FileNotFoundError("acpx")):
            with pytest.raises(RuntimeError, match="acpx not found"):
                client.chat_completion(
                    model="x", messages=[{"role": "user", "content": "hi"}],
                )

    def test_env_claudecode_removed(self):
        """Verify CLAUDECODE is removed from the subprocess environment."""
        ndjson = _make_ndjson(_agent_message_chunk("hi"))
        captured_env = None

        def capture_run(*args, **kwargs):
            nonlocal captured_env
            captured_env = kwargs.get("env")
            return _make_completed_process(ndjson)

        client = CliLLMClient()
        with patch("subprocess.run", side_effect=capture_run), \
             patch.dict("os.environ", {"CLAUDECODE": "something"}):
            client.chat_completion(
                model="x", messages=[{"role": "user", "content": "hi"}],
            )

        assert captured_env is not None
        assert "CLAUDECODE" not in captured_env

    def test_ndjson_on_stdout(self):
        """Verify we parse stdout for NDJSON content (acpx exec outputs JSON-RPC to stdout)."""
        ndjson = _make_ndjson(_agent_message_chunk("from stdout"))

        # Put NDJSON in stdout, nothing useful in stderr
        result = subprocess.CompletedProcess(
            args=["acpx"], returncode=0,
            stdout=ndjson,
            stderr="",
        )

        client = CliLLMClient()
        with patch("subprocess.run", return_value=result):
            response = client.chat_completion(
                model="x", messages=[{"role": "user", "content": "hi"}],
            )

        assert response.content == "from stdout"

    def test_nonzero_exit_with_text_returns_partial(self):
        """Non-zero exit but text extracted → return partial response."""
        ndjson = _make_ndjson(_agent_message_chunk("partial answer"))
        result = _make_completed_process(ndjson, returncode=1)

        client = CliLLMClient()
        with patch("subprocess.run", return_value=result):
            response = client.chat_completion(
                model="x", messages=[{"role": "user", "content": "hi"}],
            )

        assert response.content == "partial answer"

    def test_nonzero_exit_no_text_raises(self):
        """Non-zero exit with no extractable text → RuntimeError."""
        result = _make_completed_process("", returncode=1)

        client = CliLLMClient()
        with patch("subprocess.run", return_value=result):
            with pytest.raises(RuntimeError, match="failed"):
                client.chat_completion(
                    model="x", messages=[{"role": "user", "content": "hi"}],
                )

    def test_latency_tracked(self):
        ndjson = _make_ndjson(_agent_message_chunk("hi"))
        client = CliLLMClient()
        with patch("subprocess.run", return_value=_make_completed_process(ndjson)):
            response = client.chat_completion(
                model="x", messages=[{"role": "user", "content": "hi"}],
            )
        assert response.latency_ms >= 0

    def test_tmpdir_cleaned_up(self):
        """Temp directory is removed after call completes."""
        ndjson = _make_ndjson(_agent_message_chunk("hi"))
        created_dirs = []

        original_mkdtemp = __import__("tempfile").mkdtemp

        def tracking_mkdtemp(**kwargs):
            d = original_mkdtemp(**kwargs)
            created_dirs.append(d)
            return d

        client = CliLLMClient()
        with patch("subprocess.run", return_value=_make_completed_process(ndjson)), \
             patch("tempfile.mkdtemp", side_effect=tracking_mkdtemp):
            client.chat_completion(
                model="x", messages=[{"role": "user", "content": "hi"}],
            )

        assert len(created_dirs) == 1
        import os
        assert not os.path.exists(created_dirs[0])

    def test_tmpdir_cleaned_up_on_error(self):
        """Temp directory is removed even when subprocess raises."""
        created_dirs = []

        original_mkdtemp = __import__("tempfile").mkdtemp

        def tracking_mkdtemp(**kwargs):
            d = original_mkdtemp(**kwargs)
            created_dirs.append(d)
            return d

        client = CliLLMClient()
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("x", 600)), \
             patch("tempfile.mkdtemp", side_effect=tracking_mkdtemp):
            with pytest.raises(RuntimeError):
                client.chat_completion(
                    model="x", messages=[{"role": "user", "content": "hi"}],
                )

        assert len(created_dirs) == 1
        import os
        assert not os.path.exists(created_dirs[0])


# ── Integration with LLMExecutor._parse_output ──────────────────────

class TestCliLLMWithLLMExecutor:
    """Verify CLI responses are parsed correctly by LLMExecutor._parse_output."""

    def test_json_in_code_fence_parsed_by_tier2(self):
        """Agent returns JSON in code fences — LLMExecutor._parse_output handles it."""
        from stepwise.executors import LLMExecutor
        from stepwise.llm_client import LLMResponse

        # Simulate what CliLLMClient returns when agent wraps JSON in code fences
        response = LLMResponse(
            content='```json\n{"summary": "all good", "score": "0.9"}\n```',
            tool_calls=None,
            model="cli:claude",
        )

        client = CliLLMClient()  # not used for parsing, just needed for constructor
        executor = LLMExecutor(client=client, model="x", prompt="test")
        executor._output_fields = ["summary", "score"]

        artifact, method = executor._parse_output(response, ["summary", "score"])
        assert artifact == {"summary": "all good", "score": "0.9"}
        assert method == "json_content"

    def test_plain_json_parsed_by_tier2(self):
        """Agent returns plain JSON without fences."""
        from stepwise.executors import LLMExecutor
        from stepwise.llm_client import LLMResponse

        response = LLMResponse(
            content='{"answer": "42"}',
            tool_calls=None,
            model="cli:claude",
        )

        client = CliLLMClient()
        executor = LLMExecutor(client=client, model="x", prompt="test")

        artifact, method = executor._parse_output(response, ["answer"])
        assert artifact == {"answer": "42"}
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
        """With no OpenRouter key but acpx available, LLM executor is registered."""
        from stepwise.config import StepwiseConfig
        from stepwise.registry_factory import create_default_registry

        config = StepwiseConfig(openrouter_api_key=None)

        with patch("stepwise.cli_llm_client.detect_cli_backend", return_value=("/usr/bin/acpx", "claude")):
            registry = create_default_registry(config)

        assert "llm" in registry._factories
        assert registry.llm_backend == "cli"

    def test_no_llm_when_neither_available(self):
        """With no OpenRouter key and no acpx, LLM executor is not registered."""
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
