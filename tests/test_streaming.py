"""Tests for agent output streaming: NDJSON parsing, REST endpoint, objective, prompt rendering, workspace resolve."""

import json
import os
import tempfile

import pytest
from starlette.testclient import TestClient

from stepwise.agent import AgentExecutor, MockAgentBackend, AgentProcess, AgentStatus
from stepwise.executors import ExecutionContext, ExecutorResult
from stepwise.models import (
    ExecutorRef,
    HandoffEnvelope,
    InputBinding,
    JobStatus,
    Sidecar,
    StepDefinition,
    StepRun,
    StepRunStatus,
    WorkflowDefinition,
    _gen_id,
    _now,
)
from stepwise.server import _parse_ndjson_events, app
from stepwise.store import SQLiteStore


# ── Helpers ──────────────────────────────────────────────────────────


def _ndjson(*events: dict) -> str:
    """Build an NDJSON string from a list of dicts."""
    return "\n".join(json.dumps(e) for e in events)


def _make_acp_event(session_update: str, **fields) -> dict:
    """Build an ACP-style NDJSON event dict."""
    return {
        "params": {
            "update": {
                "sessionUpdate": session_update,
                **fields,
            },
        },
    }


def _ctx(step_name="test", attempt=1, workspace=None, objective=""):
    return ExecutionContext(
        job_id="job-test",
        step_name=step_name,
        attempt=attempt,
        workspace_path=workspace or tempfile.mkdtemp(),
        idempotency="idempotent",
        objective=objective,
    )


# ══════════════════════════════════════════════════════════════════════
# 1. _parse_ndjson_events
# ══════════════════════════════════════════════════════════════════════


class TestParseNdjsonEvents:
    """Tests for _parse_ndjson_events parsing ACP NDJSON lines."""

    def test_text_chunk(self):
        """agent_message_chunk with text content produces a text event."""
        raw = _ndjson(_make_acp_event(
            "agent_message_chunk",
            content={"type": "text", "text": "Hello world"},
        ))
        events = _parse_ndjson_events(raw)
        assert len(events) == 1
        assert events[0] == {"t": "text", "text": "Hello world"}

    def test_text_chunk_empty_text_skipped(self):
        """agent_message_chunk with empty text produces no event."""
        raw = _ndjson(_make_acp_event(
            "agent_message_chunk",
            content={"type": "text", "text": ""},
        ))
        events = _parse_ndjson_events(raw)
        assert len(events) == 0

    def test_text_chunk_non_text_type_skipped(self):
        """agent_message_chunk with non-text content type produces no event."""
        raw = _ndjson(_make_acp_event(
            "agent_message_chunk",
            content={"type": "image", "url": "http://example.com/img.png"},
        ))
        events = _parse_ndjson_events(raw)
        assert len(events) == 0

    def test_tool_call_produces_tool_start(self):
        """tool_call event produces a tool_start event."""
        raw = _ndjson(_make_acp_event(
            "tool_call",
            toolCallId="tc-123",
            title="Read file",
            kind="bash",
        ))
        events = _parse_ndjson_events(raw)
        assert len(events) == 1
        assert events[0] == {
            "t": "tool_start",
            "id": "tc-123",
            "title": "Read file",
            "kind": "bash",
        }

    def test_tool_call_update_completed_produces_tool_end(self):
        """tool_call_update with status=completed produces a tool_end event."""
        raw = _ndjson(_make_acp_event(
            "tool_call_update",
            toolCallId="tc-123",
            status="completed",
        ))
        events = _parse_ndjson_events(raw)
        assert len(events) == 1
        assert events[0] == {"t": "tool_end", "id": "tc-123"}

    def test_tool_call_update_completed_with_title_includes_output(self):
        """tool_call_update with status=completed and title includes output."""
        raw = _ndjson(_make_acp_event(
            "tool_call_update",
            toolCallId="tc-123",
            status="completed",
            title="Read src/main.py",
        ))
        events = _parse_ndjson_events(raw)
        assert len(events) == 1
        assert events[0] == {"t": "tool_end", "id": "tc-123", "output": "Read src/main.py"}

    def test_tool_call_update_failed_produces_tool_end_with_error(self):
        """tool_call_update with status=failed produces a tool_end event with error flag."""
        raw = _ndjson(_make_acp_event(
            "tool_call_update",
            toolCallId="tc-456",
            status="failed",
            title="Command failed",
        ))
        events = _parse_ndjson_events(raw)
        assert len(events) == 1
        assert events[0] == {"t": "tool_end", "id": "tc-456", "output": "Command failed", "error": True}

    def test_tool_call_update_running_skipped(self):
        """tool_call_update with status!=completed/failed produces no event."""
        raw = _ndjson(_make_acp_event(
            "tool_call_update",
            toolCallId="tc-123",
            status="running",
        ))
        events = _parse_ndjson_events(raw)
        assert len(events) == 0

    def test_usage_update(self):
        """usage_update event produces a usage event."""
        raw = _ndjson(_make_acp_event(
            "usage_update",
            used=1500,
            size=200000,
        ))
        events = _parse_ndjson_events(raw)
        assert len(events) == 1
        assert events[0] == {"t": "usage", "used": 1500, "size": 200000}

    def test_malformed_json_skipped(self):
        """Malformed JSON lines are silently skipped."""
        raw = "not valid json\n{also broken"
        events = _parse_ndjson_events(raw)
        assert len(events) == 0

    def test_empty_input(self):
        """Empty string produces no events."""
        events = _parse_ndjson_events("")
        assert len(events) == 0

    def test_whitespace_only_input(self):
        """Whitespace-only string produces no events."""
        events = _parse_ndjson_events("   \n\n  \n  ")
        assert len(events) == 0

    def test_mixed_events(self):
        """Multiple event types in a single NDJSON stream are all parsed."""
        raw = _ndjson(
            _make_acp_event("agent_message_chunk",
                            content={"type": "text", "text": "Starting..."}),
            _make_acp_event("tool_call",
                            toolCallId="tc-1", title="Bash", kind="bash"),
            _make_acp_event("tool_call_update",
                            toolCallId="tc-1", status="completed"),
            _make_acp_event("agent_message_chunk",
                            content={"type": "text", "text": "Done."}),
            _make_acp_event("usage_update", used=500, size=100000),
        )
        events = _parse_ndjson_events(raw)
        assert len(events) == 5
        assert events[0] == {"t": "text", "text": "Starting..."}
        assert events[1]["t"] == "tool_start"
        assert events[2]["t"] == "tool_end"
        assert events[3] == {"t": "text", "text": "Done."}
        assert events[4]["t"] == "usage"

    def test_mixed_with_malformed_lines(self):
        """Valid events are extracted even when interspersed with bad lines."""
        good = json.dumps(_make_acp_event(
            "agent_message_chunk",
            content={"type": "text", "text": "hello"},
        ))
        raw = f"garbage\n{good}\n{{broken}}\n"
        events = _parse_ndjson_events(raw)
        assert len(events) == 1
        assert events[0] == {"t": "text", "text": "hello"}

    def test_unknown_session_update_skipped(self):
        """Events with unknown sessionUpdate types produce no output."""
        raw = _ndjson(_make_acp_event("some_unknown_event", data="whatever"))
        events = _parse_ndjson_events(raw)
        assert len(events) == 0

    def test_tool_call_missing_optional_fields(self):
        """tool_call with missing optional fields uses empty string defaults."""
        raw = _ndjson(_make_acp_event("tool_call"))
        events = _parse_ndjson_events(raw)
        assert len(events) == 1
        assert events[0] == {
            "t": "tool_start",
            "id": "",
            "title": "",
            "kind": "",
        }

    def test_usage_update_defaults_to_zero(self):
        """usage_update with missing used/size defaults to 0."""
        raw = _ndjson(_make_acp_event("usage_update"))
        events = _parse_ndjson_events(raw)
        assert len(events) == 1
        assert events[0] == {"t": "usage", "used": 0, "size": 0}


# ══════════════════════════════════════════════════════════════════════
# 2. GET /api/runs/{run_id}/agent-output
# ══════════════════════════════════════════════════════════════════════


class TestGetAgentOutputEndpoint:
    """Tests for the agent-output REST endpoint via TestClient."""

    @pytest.fixture
    def client(self):
        """Create a TestClient with a minimal engine setup."""
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c

    @pytest.fixture(autouse=True)
    def _setup_engine(self):
        """Set up a minimal engine for the server module's global state."""
        import stepwise.server as srv
        from stepwise.engine import Engine
        from stepwise.executors import ExecutorRegistry, ScriptExecutor

        store = SQLiteStore(":memory:")
        reg = ExecutorRegistry()
        reg.register("script", lambda config: ScriptExecutor(
            command=config.get("command", "echo '{}'"),
        ))
        engine = Engine(store=store, registry=reg)
        srv._engine = engine
        yield
        store.close()
        srv._engine = None

    def _create_run_with_output(self, ndjson_content: str | None = None,
                                 output_path: str | None = None) -> str:
        """Create a completed StepRun in the engine store. Returns run_id."""
        import stepwise.server as srv
        engine = srv._engine

        # Create a job first
        wf = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["result"],
                executor=ExecutorRef("script", {"command": "echo '{}'"}),
            ),
        })
        job = engine.create_job("Test job", wf)

        # Build executor_state
        executor_state = {}
        if output_path is not None:
            executor_state["output_path"] = output_path

        run = StepRun(
            id=_gen_id("run"),
            job_id=job.id,
            step_name="a",
            attempt=1,
            status=StepRunStatus.COMPLETED,
            executor_state=executor_state if executor_state else None,
            started_at=_now(),
            completed_at=_now(),
        )
        engine.store.save_run(run)

        # Write the NDJSON file if content and path provided
        if ndjson_content is not None and output_path is not None:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, "w") as f:
                f.write(ndjson_content)

        return run.id

    def test_returns_events_for_run_with_output(self, client):
        """Returns parsed events when run has an NDJSON output file."""
        tmpdir = tempfile.mkdtemp()
        output_path = os.path.join(tmpdir, "output.jsonl")
        content = _ndjson(
            _make_acp_event("agent_message_chunk",
                            content={"type": "text", "text": "Hello"}),
            _make_acp_event("tool_call",
                            toolCallId="tc-1", title="Bash", kind="bash"),
        )
        run_id = self._create_run_with_output(content, output_path)

        resp = client.get(f"/api/runs/{run_id}/agent-output")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["events"]) == 2
        assert data["events"][0] == {"t": "text", "text": "Hello"}
        assert data["events"][1]["t"] == "tool_start"

    def test_returns_empty_events_without_output_path(self, client):
        """Returns empty events list when run has no output_path in executor_state."""
        run_id = self._create_run_with_output()

        resp = client.get(f"/api/runs/{run_id}/agent-output")
        assert resp.status_code == 200
        data = resp.json()
        assert data["events"] == []

    def test_returns_404_for_nonexistent_run(self, client):
        """Returns 404 for a run_id that doesn't exist."""
        resp = client.get("/api/runs/run-nonexistent/agent-output")
        assert resp.status_code == 404

    def test_returns_empty_events_when_file_missing(self, client):
        """Returns empty events when output_path points to a nonexistent file."""
        run_id = self._create_run_with_output(
            output_path="/tmp/definitely-does-not-exist-abc123.jsonl",
        )

        resp = client.get(f"/api/runs/{run_id}/agent-output")
        assert resp.status_code == 200
        data = resp.json()
        assert data["events"] == []

    def test_returns_empty_events_for_empty_file(self, client):
        """Returns empty events when the output file exists but is empty."""
        tmpdir = tempfile.mkdtemp()
        output_path = os.path.join(tmpdir, "empty.jsonl")
        run_id = self._create_run_with_output("", output_path)

        resp = client.get(f"/api/runs/{run_id}/agent-output")
        assert resp.status_code == 200
        data = resp.json()
        assert data["events"] == []


# ══════════════════════════════════════════════════════════════════════
# 2b. Script output REST endpoint
# ══════════════════════════════════════════════════════════════════════


class TestScriptOutputEndpoint:
    """Tests for GET /api/runs/{run_id}/script-output."""

    @pytest.fixture
    def client(self):
        """Create a TestClient with a minimal engine setup."""
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c

    @pytest.fixture(autouse=True)
    def _setup_engine(self):
        """Set up a minimal engine for the server module's global state."""
        import stepwise.server as srv
        from stepwise.engine import Engine
        from stepwise.executors import ExecutorRegistry, ScriptExecutor

        store = SQLiteStore(":memory:")
        reg = ExecutorRegistry()
        reg.register("script", lambda config: ScriptExecutor(
            command=config.get("command", "echo '{}'"),
        ))
        engine = Engine(store=store, registry=reg)
        srv._engine = engine
        yield
        store.close()
        srv._engine = None

    def _create_run(self, executor_state=None):
        """Create a completed StepRun in the engine store. Returns run_id."""
        import stepwise.server as srv
        engine = srv._engine

        wf = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["result"],
                executor=ExecutorRef("script", {"command": "echo '{}'"}),
            ),
        })
        job = engine.create_job("Test job", wf)
        run = StepRun(
            id=_gen_id("run"),
            job_id=job.id,
            step_name="a",
            attempt=1,
            status=StepRunStatus.COMPLETED,
            executor_state=executor_state,
            started_at=_now(),
            completed_at=_now(),
        )
        engine.store.save_run(run)
        return run.id

    def test_returns_file_content(self, client, tmp_path):
        """Write content to stdout/stderr files, verify endpoint returns it."""
        stdout_file = tmp_path / "test-1.stdout"
        stderr_file = tmp_path / "test-1.stderr"
        stdout_file.write_text("line 1\nline 2\n")
        stderr_file.write_text("warning\n")
        run_id = self._create_run(executor_state={
            "stdout_path": str(stdout_file),
            "stderr_path": str(stderr_file),
        })
        resp = client.get(f"/api/runs/{run_id}/script-output")
        assert resp.status_code == 200
        data = resp.json()
        assert data["stdout"] == "line 1\nline 2\n"
        assert data["stderr"] == "warning\n"
        assert data["stdout_offset"] > 0

    def test_offset_returns_only_new_content(self, client, tmp_path):
        """Request with offset -> only content after that byte position."""
        stdout_file = tmp_path / "test-1.stdout"
        stdout_file.write_bytes(b"AAABBB")
        run_id = self._create_run(executor_state={
            "stdout_path": str(stdout_file),
        })
        resp = client.get(f"/api/runs/{run_id}/script-output?stdout_offset=3")
        data = resp.json()
        assert data["stdout"] == "BBB"
        assert data["stdout_offset"] == 6

    def test_missing_files_returns_empty(self, client, tmp_path):
        run_id = self._create_run(executor_state={
            "stdout_path": str(tmp_path / "nonexistent.stdout"),
        })
        resp = client.get(f"/api/runs/{run_id}/script-output")
        assert resp.json()["stdout"] == ""

    def test_no_paths_returns_empty(self, client):
        run_id = self._create_run(executor_state={})
        resp = client.get(f"/api/runs/{run_id}/script-output")
        assert resp.json()["stdout"] == ""
        assert resp.json()["stderr"] == ""

    def test_404_for_unknown_run(self, client):
        resp = client.get("/api/runs/nonexistent/script-output")
        assert resp.status_code == 404


# ══════════════════════════════════════════════════════════════════════
# 3. ExecutionContext.objective
# ══════════════════════════════════════════════════════════════════════


class TestExecutionContextObjective:
    """Tests for the objective field on ExecutionContext."""

    def test_objective_defaults_to_empty_string(self):
        """objective defaults to empty string when not provided."""
        ctx = ExecutionContext(
            job_id="j1",
            step_name="s1",
            attempt=1,
            workspace_path="/tmp",
            idempotency="idempotent",
        )
        assert ctx.objective == ""

    def test_objective_is_set(self):
        """objective can be explicitly set."""
        ctx = ExecutionContext(
            job_id="j1",
            step_name="s1",
            attempt=1,
            workspace_path="/tmp",
            idempotency="idempotent",
            objective="Build the landing page",
        )
        assert ctx.objective == "Build the landing page"

    def test_engine_passes_job_objective_to_context(self):
        """Engine sets ExecutionContext.objective from job.objective."""
        from tests.conftest import CallableExecutor, register_step_fn
        from stepwise.engine import Engine
        from stepwise.executors import ExecutorRegistry

        captured = {}

        class CapturingExecutor:
            """Captures the ExecutionContext passed to start()."""
            def start(self, inputs, context):
                captured["objective"] = context.objective
                return ExecutorResult(
                    type="data",
                    envelope=HandoffEnvelope(
                        artifact={"result": "ok"},
                        sidecar=Sidecar(),
                        workspace=context.workspace_path,
                        timestamp=_now(),
                    ),
                )

            def check_status(self, state):
                from stepwise.executors import ExecutorStatus
                return ExecutorStatus(state="completed")

            def cancel(self, state):
                pass

        store = SQLiteStore(":memory:")
        reg = ExecutorRegistry()
        reg.register("capturing", lambda config: CapturingExecutor())
        engine = Engine(store=store, registry=reg)

        wf = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["result"],
                executor=ExecutorRef("capturing", {}),
            ),
        })
        job = engine.create_job("Build the dashboard", wf)
        engine.start_job(job.id)

        assert captured["objective"] == "Build the dashboard"
        store.close()


# ══════════════════════════════════════════════════════════════════════
# 4. AgentExecutor._render_prompt
# ══════════════════════════════════════════════════════════════════════


class TestAgentRenderPrompt:
    """Tests for AgentExecutor._render_prompt template substitution."""

    def test_objective_substitution(self):
        """$objective is substituted from context.objective."""
        backend = MockAgentBackend()
        executor = AgentExecutor(
            backend=backend,
            prompt="Your goal: $objective",
        )
        ctx = _ctx(objective="Fix the login bug")
        result = executor._render_prompt({}, ctx)
        assert result == "Your goal: Fix the login bug"

    def test_workspace_substitution(self):
        """$workspace is substituted from context.workspace_path."""
        backend = MockAgentBackend()
        executor = AgentExecutor(
            backend=backend,
            prompt="Work in: $workspace",
        )
        ctx = _ctx(workspace="/home/user/project")
        result = executor._render_prompt({}, ctx)
        assert result == "Work in: /home/user/project"

    def test_input_variables_alongside_context(self):
        """Input variables work alongside $objective and $workspace."""
        backend = MockAgentBackend()
        executor = AgentExecutor(
            backend=backend,
            prompt="Objective: $objective\nFile: $filename\nDir: $workspace",
        )
        ctx = _ctx(objective="Review code", workspace="/tmp/proj")
        result = executor._render_prompt({"filename": "main.py"}, ctx)
        assert "Objective: Review code" in result
        assert "File: main.py" in result
        assert "Dir: /tmp/proj" in result

    def test_missing_input_variables_left_as_is(self):
        """Missing input variables are left as-is via safe_substitute."""
        backend = MockAgentBackend()
        executor = AgentExecutor(
            backend=backend,
            prompt="Use $undefined_var here",
        )
        ctx = _ctx()
        result = executor._render_prompt({}, ctx)
        assert "$undefined_var" in result

    def test_inputs_override_context_defaults(self):
        """Explicit inputs for 'objective' or 'workspace' take precedence only if provided first (setdefault behavior)."""
        backend = MockAgentBackend()
        executor = AgentExecutor(
            backend=backend,
            prompt="$objective / $workspace",
        )
        # setdefault won't overwrite if input already has the key
        ctx = _ctx(objective="from-context", workspace="/ctx/path")
        result = executor._render_prompt(
            {"objective": "from-input", "workspace": "/input/path"}, ctx,
        )
        # Inputs are processed first, then setdefault doesn't overwrite
        assert "from-input" in result
        assert "/input/path" in result

    def test_injected_context_appended(self):
        """Injected context is appended after the template."""
        backend = MockAgentBackend()
        executor = AgentExecutor(
            backend=backend,
            prompt="Base prompt.",
        )
        ctx = _ctx()
        ctx.injected_context = ["Focus on API", "Skip tests"]
        result = executor._render_prompt({}, ctx)
        assert "Base prompt." in result
        assert "Additional context:" in result
        assert "Focus on API" in result
        assert "Skip tests" in result

    def test_no_injected_context(self):
        """Without injected context, no 'Additional context' section."""
        backend = MockAgentBackend()
        executor = AgentExecutor(
            backend=backend,
            prompt="Just a prompt.",
        )
        ctx = _ctx()
        result = executor._render_prompt({}, ctx)
        assert result == "Just a prompt."
        assert "Additional context" not in result

    def test_non_string_inputs_converted(self):
        """Non-string input values are converted to strings."""
        backend = MockAgentBackend()
        executor = AgentExecutor(
            backend=backend,
            prompt="Count: $count, Active: $active",
        )
        ctx = _ctx()
        result = executor._render_prompt({"count": 42, "active": True}, ctx)
        assert "Count: 42" in result
        assert "Active: True" in result


# ══════════════════════════════════════════════════════════════════════
# 5. Agent workspace .resolve()
# ══════════════════════════════════════════════════════════════════════


class TestAgentWorkspaceResolve:
    """Tests for workspace path .resolve() in ACPBackend.spawn."""

    def test_relative_path_resolved_to_absolute(self):
        """A relative workspace_path in config is converted to absolute via .resolve()."""
        # The resolve happens inside ACPBackend.spawn at:
        #   working_dir = str(Path(config.get("working_dir", context.workspace_path)).resolve())
        # We test the same logic directly since spawning the agent requires the binary.
        from pathlib import Path

        relative = "some/relative/path"
        resolved = str(Path(relative).resolve())
        assert os.path.isabs(resolved)
        assert resolved.endswith("some/relative/path")

    def test_absolute_path_stays_absolute(self):
        """An absolute workspace_path stays unchanged after .resolve()."""
        from pathlib import Path

        absolute = "/home/user/project"
        resolved = str(Path(absolute).resolve())
        assert resolved == absolute

    def test_dot_path_resolves_to_cwd(self):
        """A '.' workspace_path resolves to the current working directory."""
        from pathlib import Path

        resolved = str(Path(".").resolve())
        assert resolved == os.getcwd()

    def test_agent_executor_uses_resolved_workspace_in_start(self):
        """AgentExecutor.start() passes a resolved workspace to the backend."""
        backend = MockAgentBackend()
        backend.set_auto_complete({"status": "done"})
        executor = AgentExecutor(
            backend=backend,
            prompt="Do work",
        )
        tmpdir = tempfile.mkdtemp()
        ctx = _ctx(workspace=tmpdir)
        result = executor.start({}, ctx)

        # Blocking start() returns type="data"
        assert result.type == "data"
        assert result.executor_state is not None
        # working_dir should be set from the mock backend
        assert result.executor_state["working_dir"] == tmpdir
