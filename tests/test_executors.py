"""Tests for ScriptExecutor, ExternalExecutor, MockLLMExecutor, and decorators.

Covers required test cases 15, 16, 24.
"""

import json
import os
import tempfile

import pytest

from stepwise.decorators import (
    FallbackDecorator,
    RetryDecorator,
    TimeoutDecorator,
)
from stepwise.executors import (
    ExecutionContext,
    Executor,
    ExecutorResult,
    ExecutorStatus,
    ExternalExecutor,
    MockLLMExecutor,
    ScriptExecutor,
    _is_simple_command,
)
from stepwise.models import (
    HandoffEnvelope,
    Sidecar,
    WatchSpec,
    _now,
)


def _ctx(step_name="test", attempt=1, workspace=None):
    return ExecutionContext(
        job_id="job-test",
        step_name=step_name,
        attempt=attempt,
        workspace_path=workspace or tempfile.mkdtemp(),
        idempotency="idempotent",
    )


# ── ScriptExecutor ────────────────────────────────────────────────────


class TestScriptExecutor:
    def test_success_json_output(self):
        ctx = _ctx()
        executor = ScriptExecutor(command='echo \'{"result": "hello"}\'')
        result = executor.start({}, ctx)
        assert result.type == "data"
        assert result.envelope.artifact["result"] == "hello"

    def test_success_non_json_output(self):
        ctx = _ctx()
        executor = ScriptExecutor(command="echo 'plain text'")
        result = executor.start({}, ctx)
        assert result.type == "data"
        assert result.envelope.artifact["stdout"] == "plain text"

    def test_failure_exit_code(self):
        ctx = _ctx()
        executor = ScriptExecutor(command="exit 1")
        result = executor.start({}, ctx)
        assert result.type == "data"
        assert result.executor_state["failed"] is True

    def test_inputs_written_to_file(self):
        ctx = _ctx()
        executor = ScriptExecutor(command="cat $JOB_ENGINE_INPUTS")
        inputs = {"key": "value", "num": 42}
        result = executor.start(inputs, ctx)
        assert result.type == "data"
        # The command reads the input file (JSON) and prints it to stdout.
        # ScriptExecutor parses valid JSON stdout directly into artifact.
        assert result.envelope.artifact["key"] == "value"
        assert result.envelope.artifact["num"] == 42

    def test_empty_stdout(self):
        ctx = _ctx()
        executor = ScriptExecutor(command="true")  # exit 0, no output
        result = executor.start({}, ctx)
        assert result.type == "data"
        assert result.envelope.artifact.get("stdout", "") == ""

    # ── Test 24: Poll watch protocol ──────────────────────────────────

    def test_watch_signaling(self):
        """If output JSON contains _watch key, step suspends."""
        ctx = _ctx()
        watch_output = json.dumps({
            "_watch": {
                "mode": "poll",
                "config": {"check_command": "echo done", "interval_seconds": 5},
                "fulfillment_outputs": ["status"],
            }
        })
        executor = ScriptExecutor(command=f"echo '{watch_output}'")
        result = executor.start({}, ctx)
        assert result.type == "watch"
        assert result.watch.mode == "poll"
        assert result.watch.config["interval_seconds"] == 5


# ── N20: Auto-detect command vs shell script ──────────────────────────


class TestIsSimpleCommand:
    """Unit tests for _is_simple_command detection helper."""

    # Commands that should be detected as simple (direct execution)
    def test_simple_bare_command(self):
        assert _is_simple_command("true") is True

    def test_simple_command_with_args(self):
        assert _is_simple_command("python script.py --flag value") is True

    def test_simple_python_command(self):
        assert _is_simple_command("python3 /abs/path/to/script.py --verbose") is True

    def test_simple_command_with_equals_arg(self):
        # = is not a shell metachar — used in --key=value style args
        assert _is_simple_command("mybin --output=file.txt") is True

    # Commands that must use the shell
    def test_pipe_requires_shell(self):
        assert _is_simple_command("echo hello | cat") is False

    def test_redirect_out_requires_shell(self):
        assert _is_simple_command("echo hello > /tmp/out.txt") is False

    def test_redirect_in_requires_shell(self):
        assert _is_simple_command("cat < /tmp/in.txt") is False

    def test_logical_and_requires_shell(self):
        assert _is_simple_command("true && echo yes") is False

    def test_logical_or_requires_shell(self):
        assert _is_simple_command("false || echo no") is False

    def test_semicolon_requires_shell(self):
        assert _is_simple_command("echo a; echo b") is False

    def test_dollar_var_requires_shell(self):
        assert _is_simple_command("echo $HOME") is False

    def test_command_substitution_requires_shell(self):
        assert _is_simple_command("echo $(date)") is False

    def test_backtick_requires_shell(self):
        assert _is_simple_command("echo `date`") is False

    def test_glob_star_requires_shell(self):
        assert _is_simple_command("ls *.py") is False

    def test_glob_question_requires_shell(self):
        assert _is_simple_command("ls file?.txt") is False

    def test_multiline_requires_shell(self):
        assert _is_simple_command("echo hello\necho world") is False

    def test_multiline_script_requires_shell(self):
        script = "#!/bin/bash\necho hello\necho world"
        assert _is_simple_command(script) is False


class TestScriptExecutorAutoDetect:
    """Integration tests: ScriptExecutor runs simple commands directly and
    shell scripts through the shell, with shell_mode recorded in executor_meta."""

    def test_simple_command_runs_directly(self):
        """A command with no metacharacters uses direct execution (shell_mode=direct)."""
        ctx = _ctx()
        # 'true' is a simple command — no metacharacters, single line
        executor = ScriptExecutor(command="true")
        result = executor.start({}, ctx)
        assert result.type == "data"
        assert result.envelope.executor_meta.get("shell_mode") == "direct"

    def test_shell_command_uses_shell(self):
        """A command with a pipe uses shell execution (shell_mode=shell)."""
        ctx = _ctx()
        executor = ScriptExecutor(command="echo hello | cat")
        result = executor.start({}, ctx)
        assert result.type == "data"
        assert result.envelope.executor_meta.get("shell_mode") == "shell"

    def test_simple_command_produces_correct_output(self):
        """Direct-mode execution produces the same output as shell mode."""
        ctx = _ctx()
        # 'printf' with a plain string — no metacharacters
        executor = ScriptExecutor(command="printf hello")
        result = executor.start({}, ctx)
        assert result.type == "data"
        assert result.envelope.artifact.get("stdout") == "hello"
        assert result.envelope.executor_meta.get("shell_mode") == "direct"

    def test_multiline_script_uses_shell(self):
        """A multiline run: block is always sent through the shell."""
        ctx = _ctx()
        script = "printf foo\nprintf bar"
        executor = ScriptExecutor(command=script)
        result = executor.start({}, ctx)
        assert result.type == "data"
        assert result.envelope.executor_meta.get("shell_mode") == "shell"

    def test_simple_command_failure_records_shell_mode(self):
        """shell_mode is recorded even when the command exits non-zero."""
        ctx = _ctx()
        # A non-existent but simple command — no metacharacters so direct mode
        executor = ScriptExecutor(command="false")
        result = executor.start({}, ctx)
        assert result.executor_state["failed"] is True
        assert result.envelope.executor_meta.get("shell_mode") == "direct"

    def test_env_var_in_command_uses_shell(self):
        """A command referencing $VAR must go through the shell."""
        ctx = _ctx()
        executor = ScriptExecutor(command="echo $HOME")
        result = executor.start({}, ctx)
        assert result.type == "data"
        assert result.envelope.executor_meta.get("shell_mode") == "shell"

    def test_redirect_uses_shell(self):
        """A command with output redirect uses shell mode."""
        ctx = _ctx()
        import tempfile
        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp.close()
        executor = ScriptExecutor(command=f"echo hi > {tmp.name}")
        result = executor.start({}, ctx)
        assert result.envelope.executor_meta.get("shell_mode") == "shell"


class TestScriptExecutorEnvNamespace:
    """Input env var namespacing and system-name protection."""

    def test_prefixed_env_var_set(self):
        executor = ScriptExecutor(command="printenv STEPWISE_INPUT_url")
        result = executor.start({"url": "https://example.com"}, _ctx())
        assert result.envelope.artifact.get("stdout") == "https://example.com"

    def test_bare_env_var_set_during_deprecation(self):
        executor = ScriptExecutor(command="printenv url")
        result = executor.start({"url": "https://example.com"}, _ctx())
        assert "https://example.com" in result.envelope.artifact.get("stdout", "")

    def test_system_path_not_overridden(self):
        executor = ScriptExecutor(command="which echo")
        result = executor.start({"PATH": "/nonexistent"}, _ctx())
        assert not (result.executor_state or {}).get("failed")

    def test_system_path_available_prefixed(self):
        executor = ScriptExecutor(command="printenv STEPWISE_INPUT_PATH")
        result = executor.start({"PATH": "/custom"}, _ctx())
        assert "/custom" in result.envelope.artifact.get("stdout", "")


# ── ExternalExecutor ─────────────────────────────────────────────────────


class TestExternalExecutor:
    def test_immediately_returns_watch(self):
        executor = ExternalExecutor(prompt="Approve?")
        ctx = _ctx()
        result = executor.start({}, ctx)
        assert result.type == "watch"
        assert result.watch.mode == "external"
        assert result.watch.config["prompt"] == "Approve?"

    def test_check_status_running(self):
        executor = ExternalExecutor(prompt="test")
        status = executor.check_status({})
        assert status.state == "running"


# ── Test 16: MockLLMExecutor ─────────────────────────────────────────


class TestMockLLMExecutor:
    def test_success_default(self):
        executor = MockLLMExecutor()
        ctx = _ctx(step_name="analyze")
        result = executor.start({"data": "test"}, ctx)
        assert result.type == "data"
        assert result.envelope is not None

    def test_with_preconfigured_responses(self):
        executor = MockLLMExecutor(responses={
            "analyze": {"plan": "do stuff", "confidence": 0.9},
        })
        ctx = _ctx(step_name="analyze")
        result = executor.start({}, ctx)
        assert result.envelope.artifact["plan"] == "do stuff"
        assert result.envelope.artifact["confidence"] == 0.9

    def test_failure_mode(self):
        executor = MockLLMExecutor(failure_rate=1.0)  # always fail
        ctx = _ctx()
        result = executor.start({}, ctx)
        assert result.executor_state.get("failed") is True

    def test_partial_mode(self):
        executor = MockLLMExecutor(partial_rate=1.0)  # always partial
        ctx = _ctx()
        result = executor.start({}, ctx)
        assert result.envelope.executor_meta.get("partial") is True

    def test_callable_response(self):
        executor = MockLLMExecutor(responses={
            "transform": lambda inputs: {"doubled": inputs.get("n", 0) * 2},
        })
        ctx = _ctx(step_name="transform")
        result = executor.start({"n": 5}, ctx)
        assert result.envelope.artifact["doubled"] == 10


# ── Test 15: Decorator composition ───────────────────────────────────


class TestDecoratorComposition:
    def test_timeout_retry_script(self):
        """timeout(retry(script)) — decorators compose."""
        # Inner: script that succeeds
        inner = ScriptExecutor(command='echo \'{"result": "ok"}\'')

        # Wrap with retry
        retried = RetryDecorator(inner, {"max_retries": 2})

        # Wrap with timeout
        timed = TimeoutDecorator(retried, {"minutes": 5})

        ctx = _ctx()
        result = timed.start({}, ctx)

        assert result.type == "data"
        assert result.envelope.artifact["result"] == "ok"
        # Check decorator metadata is present
        assert "timeout" in result.envelope.executor_meta
        assert result.envelope.executor_meta["timeout"]["triggered"] is False

    def test_retry_on_failure(self):
        """RetryDecorator retries failed executor."""
        call_count = {"n": 0}

        class FailOnceExecutor(Executor):
            def start(self, inputs, context):
                call_count["n"] += 1
                if call_count["n"] == 1:
                    return ExecutorResult(
                        type="data",
                        envelope=HandoffEnvelope(
                            artifact={},
                            sidecar=Sidecar(),
                            workspace="",
                            timestamp=_now(),
                            executor_meta={"failed": True},
                        ),
                        executor_state={"failed": True, "error": "first fail"},
                    )
                return ExecutorResult(
                    type="data",
                    envelope=HandoffEnvelope(
                        artifact={"result": "success"},
                        sidecar=Sidecar(),
                        workspace="",
                        timestamp=_now(),
                    ),
                )

            def check_status(self, state):
                return ExecutorStatus(state="completed")

            def cancel(self, state):
                pass

        retried = RetryDecorator(FailOnceExecutor(), {"max_retries": 2})
        ctx = _ctx()
        result = retried.start({}, ctx)

        assert result.type == "data"
        assert result.envelope.artifact["result"] == "success"
        assert call_count["n"] == 2
        assert result.envelope.executor_meta["retry"]["attempts"] == 2

    def test_retry_respects_non_retriable(self):
        """RetryDecorator doesn't retry non-retriable steps."""
        call_count = {"n": 0}

        class AlwaysFailExecutor(Executor):
            def start(self, inputs, context):
                call_count["n"] += 1
                return ExecutorResult(
                    type="data",
                    envelope=HandoffEnvelope(
                        artifact={},
                        sidecar=Sidecar(),
                        workspace="",
                        timestamp=_now(),
                        executor_meta={"failed": True},
                    ),
                    executor_state={"failed": True, "error": "fail"},
                )

            def check_status(self, state):
                return ExecutorStatus(state="failed")

            def cancel(self, state):
                pass

        retried = RetryDecorator(AlwaysFailExecutor(), {"max_retries": 3})
        ctx = _ctx()
        ctx.idempotency = "non_retriable"
        result = retried.start({}, ctx)

        # Should only be called once since it's non-retriable
        assert call_count["n"] == 1

    def test_fallback_decorator(self):
        class FailExecutor(Executor):
            def start(self, inputs, context):
                return ExecutorResult(
                    type="data",
                    envelope=HandoffEnvelope(
                        artifact={},
                        sidecar=Sidecar(),
                        workspace="",
                        timestamp=_now(),
                        executor_meta={"failed": True},
                    ),
                    executor_state={"failed": True, "error": "primary failed"},
                )

            def check_status(self, state):
                return ExecutorStatus(state="failed")

            def cancel(self, state):
                pass

        class SuccessExecutor(Executor):
            def start(self, inputs, context):
                return ExecutorResult(
                    type="data",
                    envelope=HandoffEnvelope(
                        artifact={"result": "from_fallback"},
                        sidecar=Sidecar(),
                        workspace="",
                        timestamp=_now(),
                    ),
                )

            def check_status(self, state):
                return ExecutorStatus(state="completed")

            def cancel(self, state):
                pass

        fb = FallbackDecorator(FailExecutor(), SuccessExecutor(), {})
        ctx = _ctx()
        result = fb.start({}, ctx)

        assert result.envelope.artifact["result"] == "from_fallback"
        assert result.envelope.executor_meta["fallback"]["primary_failed"] is True
