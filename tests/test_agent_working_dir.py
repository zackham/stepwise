"""Tests for agent working_dir defaulting to flow source directory."""

from stepwise.executors import ExecutionContext
from stepwise.agent import MockAgentBackend, AgentStatus


# ── Helpers ────────────────────────────────────────────────────────────


def _make_context(
    workspace_path: str = "/tmp/job-workspace",
    flow_source_dir: str | None = None,
) -> ExecutionContext:
    return ExecutionContext(
        job_id="job-test123",
        step_name="step-a",
        attempt=1,
        workspace_path=workspace_path,
        idempotency="auto",
        flow_source_dir=flow_source_dir,
    )


# ══════════════════════════════════════════════════════════════════════
# Feature 2: Agent working_dir defaults to flow source directory
# ══════════════════════════════════════════════════════════════════════


class TestAgentWorkingDirDefault:
    """Test that agent steps default working_dir to flow source dir."""

    def test_defaults_to_flow_source_dir(self):
        """When flow_source_dir is set and no explicit working_dir, use flow_source_dir."""
        backend = MockAgentBackend()
        backend.set_auto_complete(result={"ok": True})

        ctx = _make_context(flow_source_dir="/home/user/project/flows")
        process = backend.spawn("test prompt", config={}, context=ctx)

        assert process.working_dir == "/home/user/project/flows"

    def test_explicit_working_dir_takes_precedence(self):
        """When config has explicit working_dir, it should override flow_source_dir."""
        backend = MockAgentBackend()
        backend.set_auto_complete(result={"ok": True})

        ctx = _make_context(flow_source_dir="/home/user/project/flows")
        process = backend.spawn(
            "test prompt",
            config={"working_dir": "/custom/dir"},
            context=ctx,
        )

        assert process.working_dir == "/custom/dir"

    def test_falls_back_to_workspace_when_no_flow_source_dir(self):
        """When flow_source_dir is None, fall back to workspace_path."""
        backend = MockAgentBackend()
        backend.set_auto_complete(result={"ok": True})

        ctx = _make_context(flow_source_dir=None)
        process = backend.spawn("test prompt", config={}, context=ctx)

        assert process.working_dir == "/tmp/job-workspace"

    def test_execution_context_has_flow_source_dir_field(self):
        """ExecutionContext should have the flow_source_dir field."""
        ctx = _make_context(flow_source_dir="/some/dir")
        assert ctx.flow_source_dir == "/some/dir"

        ctx2 = _make_context()
        assert ctx2.flow_source_dir is None

    def test_flow_source_dir_populated_from_workflow(self):
        """WorkflowDefinition.source_dir should populate ExecutionContext.flow_source_dir."""
        from stepwise.models import WorkflowDefinition
        wf = WorkflowDefinition(source_dir="/home/user/flows")
        assert wf.source_dir == "/home/user/flows"
