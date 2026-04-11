"""Tests for engine integration with ACPBackend.

Verifies that the engine correctly:
- Creates AgentExecutor with ACPBackend (via registry_factory)
- Uses session_id (not claude_uuid) in SessionState
- Injects session_id as _fork_from_session_id for forks
- Passes _session_uuid for named session continuation
- Does not inject _backend_type into session context
"""

from __future__ import annotations

import pytest

from stepwise.agent import AgentExecutor, MockAgentBackend
from stepwise.engine import AsyncEngine, SessionState
from stepwise.executors import ExecutorRegistry
from stepwise.models import (
    ExecutorRef,
    StepDefinition,
    WorkflowDefinition,
)
from stepwise.store import SQLiteStore


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def mock_backend():
    return MockAgentBackend()


@pytest.fixture
def engine(mock_backend):
    store = SQLiteStore(":memory:")
    reg = ExecutorRegistry()
    reg.register("agent", lambda cfg: AgentExecutor(
        backend=mock_backend,
        prompt=cfg.get("prompt", ""),
        output_mode=cfg.get("output_mode", "effect"),
        output_path=cfg.get("output_path"),
        _user_set_output_mode=("output_mode" in cfg),
        **{k: v for k, v in cfg.items()
           if k not in ("prompt", "output_mode", "output_path")},
    ))
    return AsyncEngine(store=store, registry=reg)


# ── SessionState uses session_id ──────────────────────────────────────


class TestSessionStateField:
    def test_session_state_has_session_id(self):
        """SessionState uses session_id, not claude_uuid."""
        state = SessionState(name="test")
        assert hasattr(state, "session_id")
        assert not hasattr(state, "claude_uuid")
        assert state.session_id is None

    def test_session_state_no_backend_type(self):
        """SessionState no longer has backend_type field."""
        state = SessionState(name="test")
        assert not hasattr(state, "backend_type")


# ── No _backend_type in session context ───────────────────────────────


class TestNoBackendType:
    def test_new_session_no_backend_type(self, engine):
        """New session context does not include _backend_type."""
        wf = WorkflowDefinition(steps={
            "plan": StepDefinition(
                name="plan", outputs=["plan"],
                executor=ExecutorRef("agent", {"prompt": "Plan"}),
                session="planning",
            ),
        })
        job = engine.create_job("test", wf)
        engine._ensure_session_registry(job)

        run, exec_ref, inputs, ctx = engine._prepare_step_run(job, "plan")
        assert "_backend_type" not in exec_ref.config

    def test_continued_session_no_backend_type(self, engine):
        """Continued session context does not include _backend_type."""
        wf = WorkflowDefinition(steps={
            "plan": StepDefinition(
                name="plan", outputs=["plan"],
                executor=ExecutorRef("agent", {"prompt": "Plan"}),
                session="planning",
            ),
            "implement": StepDefinition(
                name="implement", outputs=["code"],
                executor=ExecutorRef("agent", {"prompt": "Impl"}),
                session="planning",
                after=["plan"],
            ),
        })
        job = engine.create_job("test", wf)
        engine._ensure_session_registry(job)

        registry = engine._session_registries[job.id]
        registry["planning"].created = True
        registry["planning"].session_id = "sid-123"

        run, exec_ref, inputs, ctx = engine._prepare_step_run(job, "implement")
        assert "_backend_type" not in exec_ref.config
        assert exec_ref.config.get("_session_uuid") == "sid-123"

    def test_fork_session_no_backend_type(self, engine):
        """Fork session context does not include _backend_type."""
        wf = WorkflowDefinition(steps={
            "plan": StepDefinition(
                name="plan", outputs=["plan"],
                executor=ExecutorRef("agent", {"prompt": "Plan"}),
                session="planning",
            ),
            "review": StepDefinition(
                name="review", outputs=["issues"],
                executor=ExecutorRef("agent", {"prompt": "Review"}),
                session="critic",
                fork_from="plan",
                after=["plan"],
            ),
        })
        job = engine.create_job("test", wf)
        engine._ensure_session_registry(job)

        registry = engine._session_registries[job.id]
        registry["planning"].created = True
        registry["planning"].session_id = "parent-sid"

        run, exec_ref, inputs, ctx = engine._prepare_step_run(job, "review")
        assert "_backend_type" not in exec_ref.config


# ── Fork injects session_id ───────────────────────────────────────────


class TestForkSessionId:
    def test_fork_uses_parent_session_id(self, engine):
        """Fork injects parent session_id as _fork_from_session_id."""
        wf = WorkflowDefinition(steps={
            "plan": StepDefinition(
                name="plan", outputs=["plan"],
                executor=ExecutorRef("agent", {"prompt": "Plan"}),
                session="planning",
            ),
            "review": StepDefinition(
                name="review", outputs=["issues"],
                executor=ExecutorRef("agent", {"prompt": "Review"}),
                session="critic",
                fork_from="plan",
                after=["plan"],
            ),
        })
        job = engine.create_job("test", wf)
        engine._ensure_session_registry(job)

        registry = engine._session_registries[job.id]
        registry["planning"].created = True
        registry["planning"].session_id = "parent-sid-456"

        run, exec_ref, inputs, ctx = engine._prepare_step_run(job, "review")
        # Falls back to live session_id since no snapshot exists
        assert exec_ref.config.get("_fork_from_session_id") == "parent-sid-456"


# ── Named session continuation ────────────────────────────────────────


class TestSessionContinuation:
    def test_continuation_uses_session_id(self, engine):
        """Named session continuation passes session_id as _session_uuid."""
        wf = WorkflowDefinition(steps={
            "plan": StepDefinition(
                name="plan", outputs=["plan"],
                executor=ExecutorRef("agent", {"prompt": "Plan"}),
                session="planning",
            ),
            "implement": StepDefinition(
                name="implement", outputs=["code"],
                executor=ExecutorRef("agent", {"prompt": "Impl"}),
                session="planning",
                after=["plan"],
            ),
        })
        job = engine.create_job("test", wf)
        engine._ensure_session_registry(job)

        registry = engine._session_registries[job.id]
        registry["planning"].created = True
        registry["planning"].session_id = "continue-sid-789"

        run, exec_ref, inputs, ctx = engine._prepare_step_run(job, "implement")
        assert exec_ref.config.get("_session_uuid") == "continue-sid-789"
        assert exec_ref.config.get("_session_name") == "planning"


# ── Backend selection always returns primary ──────────────────────────


class TestBackendSelection:
    def test_select_backend_always_primary(self, mock_backend):
        """_select_backend always returns the primary backend."""
        executor = AgentExecutor(backend=mock_backend, prompt="test")
        assert executor._select_backend({}) is mock_backend
        assert executor._select_backend({"_backend_type": "any_value"}) is mock_backend
        assert executor._select_backend({"_session_name": "fork"}) is mock_backend
