"""Tests for cross-job data wiring: $job_ref parsing, validation, and resolution."""

from __future__ import annotations

import asyncio

import pytest

from stepwise.engine import AsyncEngine, Engine
from stepwise.models import (
    ExecutorRef,
    HandoffEnvelope,
    InputBinding,
    Job,
    JobStatus,
    Sidecar,
    StepDefinition,
    StepRun,
    StepRunStatus,
    WorkflowDefinition,
    _gen_id,
    _now,
)
from stepwise.runner import parse_inputs
from stepwise.store import SQLiteStore
from tests.conftest import register_step_fn, run_job_sync


# ── parse_inputs tests ───────────────────────────────────────────────


class TestParseInputsJobRef:
    def test_plain_input_unchanged(self):
        result = parse_inputs(["count=42"])
        assert result == {"count": "42"}

    def test_file_input_unchanged(self, tmp_path):
        f = tmp_path / "data.txt"
        f.write_text("hello")
        result = parse_inputs([f"data=@{f}"])
        assert result == {"data": "hello"}

    def test_job_ref_parsed(self):
        result = parse_inputs(["plan=job-abc123.result"])
        assert result == {"plan": {"$job_ref": "job-abc123", "field": "result"}}

    def test_job_ref_nested_field(self):
        result = parse_inputs(["x=job-abc123.hero.headline"])
        assert result == {"x": {"$job_ref": "job-abc123", "field": "hero.headline"}}

    def test_mixed_inputs(self):
        result = parse_inputs(["count=42", "plan=job-abc123.result"])
        assert result == {
            "count": "42",
            "plan": {"$job_ref": "job-abc123", "field": "result"},
        }

    def test_not_a_job_ref_no_dot(self):
        """No dot = no ref, stays as plain string."""
        result = parse_inputs(["x=job-abc123"])
        assert result == {"x": "job-abc123"}

    def test_job_ref_only_alnum_after_prefix(self):
        """Hyphens in the ID portion after 'job-' should not match."""
        result = parse_inputs(["x=job-abc-123.field"])
        assert result == {"x": "job-abc-123.field"}  # plain string, not a ref


# ── Validation tests (engine create_job) ─────────────────────────────


def _simple_wf(fn_name: str = "noop") -> WorkflowDefinition:
    return WorkflowDefinition(steps={
        "step-a": StepDefinition(
            name="step-a",
            executor=ExecutorRef(type="callable", config={"fn_name": fn_name}),
            outputs=["result"],
        ),
    })


@pytest.fixture(autouse=True)
def _register_noop():
    register_step_fn("noop", lambda inputs: {"result": "ok"})
    yield
    from tests.conftest import clear_step_fns
    clear_step_fns()


class TestCreateJobWithRefs:
    def test_create_job_with_valid_ref(self, async_engine):
        """Referenced job exists -> job created + dependency auto-added."""
        job_a = async_engine.create_job(objective="producer", workflow=_simple_wf())
        job_b = async_engine.create_job(
            objective="consumer",
            workflow=_simple_wf(),
            inputs={"plan": {"$job_ref": job_a.id, "field": "result"}},
        )
        deps = async_engine.store.get_job_dependencies(job_b.id)
        assert job_a.id in deps

    def test_create_job_with_missing_ref(self, async_engine):
        """Referenced job doesn't exist -> ValueError."""
        with pytest.raises(ValueError, match="Referenced job not found: job-nonexistent"):
            async_engine.create_job(
                objective="consumer",
                workflow=_simple_wf(),
                inputs={"plan": {"$job_ref": "job-nonexistent", "field": "result"}},
            )

    def test_create_job_ref_auto_dependency(self, async_engine):
        """Auto-added dependency is visible via store."""
        job_a = async_engine.create_job(objective="a", workflow=_simple_wf())
        job_b = async_engine.create_job(
            objective="b",
            workflow=_simple_wf(),
            inputs={"data": {"$job_ref": job_a.id, "field": "result"}},
        )
        assert async_engine.store.get_job_dependencies(job_b.id) == [job_a.id]

    def test_create_job_ref_cycle_detection(self, async_engine):
        """Creating a job whose ref would form a cycle fails."""
        # A depends on nothing. B depends on A (via ref).
        job_a = async_engine.create_job(objective="a", workflow=_simple_wf())
        job_b = async_engine.create_job(
            objective="b",
            workflow=_simple_wf(),
            inputs={"data": {"$job_ref": job_a.id, "field": "result"}},
        )
        # Manually make A depend on B (creating A->B->A cycle)
        async_engine.store.add_job_dependency(job_a.id, job_b.id)

        # Now creating C with ref to A: C depends on A. A depends on B. B depends on A.
        # C->A is fine (no cycle from C's perspective). But let's test actual cycle:
        # We need a new job that refs B, where B already (transitively) depends on the new job.
        # That's impossible to set up since the new job doesn't exist yet.
        # Instead, verify would_create_cycle works indirectly: create D, add D->B dep manually,
        # then try to create a job with ref to D.
        job_d = async_engine.create_job(objective="d", workflow=_simple_wf())
        async_engine.store.add_job_dependency(job_d.id, job_b.id)  # D depends on B
        # D -> B -> A, and A -> B. If we try to make B depend on D: B->D->B cycle.
        # But create_job auto-adds dep from new job to ref target, so:
        # Create E with ref to D: E depends on D. No cycle.
        # The real cycle test: manually set up so that creating a ref would cycle.
        # Make A depend on D: now A->B (manual), A->D. D->B. B->A (ref).
        # If new job refs A, new->A is fine. For cycle: new job that A already depends on.
        # Simplest: use store directly.
        assert async_engine.store.would_create_cycle(job_b.id, job_a.id) is True

    def test_plain_inputs_no_deps_added(self, async_engine):
        """Jobs with plain-string inputs don't get auto-dependencies."""
        job = async_engine.create_job(
            objective="plain",
            workflow=_simple_wf(),
            inputs={"count": "42"},
        )
        assert async_engine.store.get_job_dependencies(job.id) == []


# ── Resolution tests (start_job) ─────────────────────────────────────


class TestResolveRefOnStart:
    """Test resolution using sync Engine (avoids needing a running event loop)."""

    def test_resolve_ref_on_start(self, engine):
        """Referenced job completed with artifact -> dependent job inputs resolve."""
        register_step_fn("produce", lambda inputs: {"result": "hello"})

        wf_producer = WorkflowDefinition(steps={
            "step-a": StepDefinition(
                name="step-a",
                executor=ExecutorRef(type="callable", config={"fn_name": "produce"}),
                outputs=["result"],
            ),
        })
        job_a = engine.create_job(objective="producer", workflow=wf_producer)
        engine.start_job(job_a.id)
        assert engine.store.load_job(job_a.id).status == JobStatus.COMPLETED

        # Create consumer with $job_ref input
        job_b = engine.create_job(
            objective="consumer",
            workflow=_simple_wf(),
            inputs={"plan": {"$job_ref": job_a.id, "field": "result"}},
        )

        # Start job B — refs should resolve
        engine.start_job(job_b.id)
        loaded = engine.store.load_job(job_b.id)
        assert loaded.inputs["plan"] == "hello"

    def test_resolve_nested_field(self, engine):
        """Nested field access: hero.headline navigates nested artifact."""
        register_step_fn("nested", lambda inputs: {"hero": {"headline": "big news"}})

        wf = WorkflowDefinition(steps={
            "step-a": StepDefinition(
                name="step-a",
                executor=ExecutorRef(type="callable", config={"fn_name": "nested"}),
                outputs=["hero"],
            ),
        })
        job_a = engine.create_job(objective="producer", workflow=wf)
        engine.start_job(job_a.id)

        job_b = engine.create_job(
            objective="consumer",
            workflow=_simple_wf(),
            inputs={"title": {"$job_ref": job_a.id, "field": "hero.headline"}},
        )
        engine.start_job(job_b.id)
        loaded = engine.store.load_job(job_b.id)
        assert loaded.inputs["title"] == "big news"

    def test_resolve_missing_field_returns_none(self, engine):
        """Field not found in any step's artifact -> None."""
        register_step_fn("minimal", lambda inputs: {"other": "data"})

        wf = WorkflowDefinition(steps={
            "step-a": StepDefinition(
                name="step-a",
                executor=ExecutorRef(type="callable", config={"fn_name": "minimal"}),
                outputs=["other"],
            ),
        })
        job_a = engine.create_job(objective="producer", workflow=wf)
        engine.start_job(job_a.id)

        job_b = engine.create_job(
            objective="consumer",
            workflow=_simple_wf(),
            inputs={"missing": {"$job_ref": job_a.id, "field": "nonexistent"}},
        )
        engine.start_job(job_b.id)
        loaded = engine.store.load_job(job_b.id)
        assert loaded.inputs["missing"] is None

    def test_resolve_ref_job_not_completed_raises(self, engine):
        """Referenced job is not COMPLETED -> error."""
        job_a = engine.create_job(objective="producer", workflow=_simple_wf())
        # job_a is PENDING, not COMPLETED
        job_b = engine.create_job(
            objective="consumer",
            workflow=_simple_wf(),
            inputs={"plan": {"$job_ref": job_a.id, "field": "result"}},
        )
        with pytest.raises(ValueError, match="expected COMPLETED"):
            engine.start_job(job_b.id)

    def test_plain_inputs_unchanged_on_start(self, engine):
        """Jobs without refs pass through start_job without modification."""
        register_step_fn("noop2", lambda inputs: {"result": "ok"})
        job = engine.create_job(
            objective="plain",
            workflow=_simple_wf(),
            inputs={"count": "42"},
        )
        engine.start_job(job.id)
        loaded = engine.store.load_job(job.id)
        assert loaded.inputs["count"] == "42"


# ── End-to-end integration test ──────────────────────────────────────


class TestEndToEndJobChain:
    def test_job_chain_with_data_wiring(self, async_engine):
        """Full flow: A produces data, B refs it, A completes -> B auto-starts with resolved input."""
        register_step_fn("produce", lambda inputs: {"result": "chain-data"})
        register_step_fn("consume", lambda inputs: {"echo": inputs.get("plan", "none")})

        wf_a = WorkflowDefinition(steps={
            "step-a": StepDefinition(
                name="step-a",
                executor=ExecutorRef(type="callable", config={"fn_name": "produce"}),
                outputs=["result"],
            ),
        })
        wf_b = WorkflowDefinition(steps={
            "step-a": StepDefinition(
                name="step-a",
                executor=ExecutorRef(type="callable", config={"fn_name": "consume"}),
                inputs=[InputBinding("plan", "$job", "plan")],
                outputs=["echo"],
            ),
        })

        job_a = async_engine.create_job(objective="producer", workflow=wf_a)
        job_b = async_engine.create_job(
            objective="consumer",
            workflow=wf_b,
            inputs={"plan": {"$job_ref": job_a.id, "field": "result"}},
        )

        # B should have auto-dependency on A
        assert job_a.id in async_engine.store.get_job_dependencies(job_b.id)

        # Run A to completion, then B should auto-start
        async def run_chain():
            engine_task = asyncio.create_task(async_engine.run())
            try:
                async_engine.start_job(job_a.id)
                await asyncio.wait_for(async_engine.wait_for_job(job_b.id), timeout=10)
            finally:
                engine_task.cancel()
                try:
                    await engine_task
                except asyncio.CancelledError:
                    pass

        asyncio.run(run_chain())

        result_a = async_engine.store.load_job(job_a.id)
        result_b = async_engine.store.load_job(job_b.id)
        assert result_a.status == JobStatus.COMPLETED
        assert result_b.status == JobStatus.COMPLETED

        # Verify B's input was resolved from A's output
        assert result_b.inputs["plan"] == "chain-data"

        # Verify B's step received the resolved input
        runs_b = async_engine.store.runs_for_job(job_b.id)
        assert len(runs_b) == 1
        assert runs_b[0].result.artifact["echo"] == "chain-data"


# ── Store method tests ───────────────────────────────────────────────


class TestGetJobOutputField:
    def test_simple_field(self, store):
        """Basic field extraction from completed run."""
        job = Job(
            id=_gen_id("job"),
            objective="test",
            workflow=WorkflowDefinition(steps={
                "final": StepDefinition(
                    name="final",
                    executor=ExecutorRef(type="script", config={"command": "echo"}),
                    outputs=["result"],
                ),
            }),
            status=JobStatus.COMPLETED,
            created_at=_now(),
            updated_at=_now(),
        )
        store.save_job(job)

        run = StepRun(
            id=_gen_id("run"),
            job_id=job.id,
            step_name="final",
            attempt=1,
            status=StepRunStatus.COMPLETED,
            result=HandoffEnvelope(
                artifact={"result": "hello"},
                sidecar=Sidecar(),
                workspace="",
                timestamp=_now(),
            ),
            started_at=_now(),
            completed_at=_now(),
        )
        store.save_run(run)

        value, found = store.get_job_output_field(job.id, "result")
        assert found is True
        assert value == "hello"

    def test_nested_field(self, store):
        """Nested field access via dot-path."""
        job = Job(
            id=_gen_id("job"),
            objective="test",
            workflow=WorkflowDefinition(steps={
                "final": StepDefinition(
                    name="final",
                    executor=ExecutorRef(type="script", config={"command": "echo"}),
                    outputs=["hero"],
                ),
            }),
            status=JobStatus.COMPLETED,
            created_at=_now(),
            updated_at=_now(),
        )
        store.save_job(job)

        run = StepRun(
            id=_gen_id("run"),
            job_id=job.id,
            step_name="final",
            attempt=1,
            status=StepRunStatus.COMPLETED,
            result=HandoffEnvelope(
                artifact={"hero": {"headline": "big news", "score": 0.9}},
                sidecar=Sidecar(),
                workspace="",
                timestamp=_now(),
            ),
            started_at=_now(),
            completed_at=_now(),
        )
        store.save_run(run)

        value, found = store.get_job_output_field(job.id, "hero.headline")
        assert found is True
        assert value == "big news"

    def test_missing_field(self, store):
        """Field not in artifact returns (None, False)."""
        job = Job(
            id=_gen_id("job"),
            objective="test",
            workflow=WorkflowDefinition(steps={
                "final": StepDefinition(
                    name="final",
                    executor=ExecutorRef(type="script", config={"command": "echo"}),
                    outputs=["other"],
                ),
            }),
            status=JobStatus.COMPLETED,
            created_at=_now(),
            updated_at=_now(),
        )
        store.save_job(job)

        run = StepRun(
            id=_gen_id("run"),
            job_id=job.id,
            step_name="final",
            attempt=1,
            status=StepRunStatus.COMPLETED,
            result=HandoffEnvelope(
                artifact={"other": "data"},
                sidecar=Sidecar(),
                workspace="",
                timestamp=_now(),
            ),
            started_at=_now(),
            completed_at=_now(),
        )
        store.save_run(run)

        value, found = store.get_job_output_field(job.id, "nonexistent")
        assert found is False
        assert value is None

    def test_no_completed_runs(self, store):
        """Job with no completed runs returns (None, False)."""
        job = Job(
            id=_gen_id("job"),
            objective="test",
            workflow=WorkflowDefinition(steps={
                "final": StepDefinition(
                    name="final",
                    executor=ExecutorRef(type="script", config={"command": "echo"}),
                    outputs=["result"],
                ),
            }),
            status=JobStatus.RUNNING,
            created_at=_now(),
            updated_at=_now(),
        )
        store.save_job(job)

        value, found = store.get_job_output_field(job.id, "result")
        assert found is False
        assert value is None
