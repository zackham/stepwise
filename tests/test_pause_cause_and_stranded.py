"""Tests for escalation / pause-cause / stranded-run surfacing via the API.

The engine already emits rich event payloads when:
  - an exit rule resolves (`exit.resolved` with step, rule, action)
  - a job pauses due to escalate / loop-max / suspend (`job.paused` with
    step, rule, reason)

These tests verify that the API layer re-exposes that information on the
job and run payloads so the UI can render ESCALATED badges, STRANDED
states, and a pause-cause banner without replaying the event stream.

The API-surface tests manipulate the store directly (Job + StepRun + Event
rows) rather than driving an Engine through executor machinery, because
the TestClient's lifespan wires the production ExecutorRegistry which
doesn't know about test-only executors like `callable`. Unit tests for
the helper functions still exercise the real engine end-to-end.
"""

from __future__ import annotations

import os
import pytest
from starlette.testclient import TestClient

from stepwise.server import app, _latest_pause_cause, _exit_rules_by_step
from stepwise.engine import Engine
from stepwise.executors import ExecutorRegistry, ScriptExecutor
from stepwise.events import JOB_PAUSED, EXIT_RESOLVED
from stepwise.models import (
    Event,
    ExitRule,
    ExecutorRef,
    Job,
    JobConfig,
    JobStatus,
    StepDefinition,
    StepRun,
    StepRunStatus,
    WorkflowDefinition,
    _gen_id,
    _now,
)
from stepwise.store import SQLiteStore
from tests.conftest import CallableExecutor, register_step_fn


def _make_engine():
    store = SQLiteStore(":memory:")
    reg = ExecutorRegistry()
    reg.register("callable", lambda c: CallableExecutor(fn_name=c.get("fn_name", "default")))
    reg.register("script", lambda c: ScriptExecutor(command=c.get("command", "echo '{}'")))
    return Engine(store=store, registry=reg)


def _escalating_workflow() -> WorkflowDefinition:
    register_step_fn("crit", lambda i: {"severity": "critical"})
    return WorkflowDefinition(steps={
        "gate": StepDefinition(
            name="gate", outputs=["severity"],
            executor=ExecutorRef("callable", {"fn_name": "crit"}),
            exit_rules=[
                ExitRule("too_hot", "field_match", {
                    "field": "severity", "value": "critical",
                    "action": "escalate",
                }, priority=10),
            ],
        ),
    })


# ── Unit tests on the helpers (real engine) ───────────────────────────

class TestLatestPauseCause:
    def test_returns_none_for_never_paused_job(self):
        register_step_fn("ok", lambda i: {"value": 1})
        engine = _make_engine()
        w = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["value"],
                executor=ExecutorRef("callable", {"fn_name": "ok"}),
            ),
        })
        job = engine.create_job("no pause", w)
        engine.start_job(job.id)
        assert engine.get_job(job.id).status == JobStatus.COMPLETED
        assert _latest_pause_cause(engine, job.id) is None

    def test_captures_escalate_payload(self):
        engine = _make_engine()
        job = engine.create_job("escalator", _escalating_workflow())
        engine.start_job(job.id)
        assert engine.get_job(job.id).status == JobStatus.PAUSED

        cause = _latest_pause_cause(engine, job.id)
        assert cause is not None
        assert cause["reason"] == "escalated"
        assert cause["step"] == "gate"
        assert cause["rule"] == "too_hot"
        assert "at" in cause and cause["at"] is not None


class TestExitRulesByStep:
    def test_records_escalate_rule_by_step_name(self):
        engine = _make_engine()
        job = engine.create_job("escalator", _escalating_workflow())
        engine.start_job(job.id)

        rules = _exit_rules_by_step(engine, job.id)
        assert "gate" in rules
        assert rules["gate"]["rule"] == "too_hot"
        assert rules["gate"]["action"] == "escalate"
        assert rules["gate"]["at"] is not None

    def test_latest_resolution_wins_when_step_reran(self):
        engine = _make_engine()
        job = engine.create_job("escalator", _escalating_workflow())
        engine.start_job(job.id)
        engine._emit(job.id, EXIT_RESOLVED, {
            "step": "gate", "rule": "all_clear", "action": "advance",
        })
        rules = _exit_rules_by_step(engine, job.id)
        assert rules["gate"]["rule"] == "all_clear"
        assert rules["gate"]["action"] == "advance"


# ── API-surface tests (store-level setup) ────────────────────────────

@pytest.fixture
def client(tmp_path):
    old_env = os.environ.copy()
    os.environ["STEPWISE_PROJECT_DIR"] = str(tmp_path)
    os.environ["STEPWISE_DB"] = ":memory:"
    os.environ["STEPWISE_TEMPLATES"] = str(tmp_path / "_templates")
    os.environ["STEPWISE_JOBS_DIR"] = str(tmp_path / "_jobs")
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    os.environ.clear()
    os.environ.update(old_env)


def _seed_paused_escalated_job(store, *, with_stranded_sibling: bool = False) -> str:
    """Create a paused job with a completed+escalated `gate` step and an
    exit.resolved + job.paused event pair, as the engine would have left
    the store. Optionally add a RUNNING `sibling` step that was stranded
    by the pause.
    """
    steps: dict[str, StepDefinition] = {
        "gate": StepDefinition(
            name="gate", outputs=["severity"],
            executor=ExecutorRef("script", {"command": "echo '{}'"}),
            exit_rules=[
                ExitRule("too_hot", "field_match", {
                    "field": "severity", "value": "critical",
                    "action": "escalate",
                }, priority=10),
            ],
        ),
    }
    if with_stranded_sibling:
        steps["sibling"] = StepDefinition(
            name="sibling", outputs=["value"],
            executor=ExecutorRef("script", {"command": "echo '{}'"}),
        )

    job = Job(
        id=f"job-{_gen_id()}",
        objective="seeded escalation",
        workflow=WorkflowDefinition(steps=steps),
        status=JobStatus.PAUSED,
        inputs={},
    )
    store.save_job(job)

    gate_run = StepRun(
        id=f"run-{_gen_id()}",
        job_id=job.id,
        step_name="gate",
        attempt=1,
        status=StepRunStatus.COMPLETED,
        inputs={},
        started_at=_now(),
        completed_at=_now(),
    )
    store.save_run(gate_run)

    store.save_event(Event(
        id=f"evt-{_gen_id()}",
        job_id=job.id,
        timestamp=_now(),
        type=EXIT_RESOLVED,
        data={"step": "gate", "rule": "too_hot", "action": "escalate"},
    ))
    store.save_event(Event(
        id=f"evt-{_gen_id()}",
        job_id=job.id,
        timestamp=_now(),
        type=JOB_PAUSED,
        data={"reason": "escalated", "step": "gate", "rule": "too_hot"},
    ))

    if with_stranded_sibling:
        store.save_run(StepRun(
            id=f"run-{_gen_id()}",
            job_id=job.id,
            step_name="sibling",
            attempt=1,
            status=StepRunStatus.RUNNING,
            inputs={},
            started_at=_now(),
        ))

    return job.id


def _seed_running_job(store) -> str:
    """A plain running job with a single COMPLETED step — nothing escalated."""
    job = Job(
        id=f"job-{_gen_id()}",
        objective="plain running",
        workflow=WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["value"],
                executor=ExecutorRef("script", {"command": "echo '{}'"}),
            ),
        }),
        status=JobStatus.RUNNING,
        inputs={},
    )
    store.save_job(job)
    store.save_run(StepRun(
        id=f"run-{_gen_id()}",
        job_id=job.id,
        step_name="a",
        attempt=1,
        status=StepRunStatus.RUNNING,
        inputs={},
        started_at=_now(),
    ))
    return job.id


class TestJobEndpointSurfacesPauseCause:
    def test_running_job_has_no_pause_cause(self, client):
        from stepwise.server import _get_engine
        engine = _get_engine()
        job_id = _seed_running_job(engine.store)

        resp = client.get(f"/api/jobs/{job_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "running"
        assert "pause_cause" not in body or body["pause_cause"] is None

    def test_paused_job_surfaces_pause_cause(self, client):
        from stepwise.server import _get_engine
        engine = _get_engine()
        job_id = _seed_paused_escalated_job(engine.store)

        resp = client.get(f"/api/jobs/{job_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "paused"
        cause = body.get("pause_cause")
        assert cause is not None
        assert cause["reason"] == "escalated"
        assert cause["step"] == "gate"
        assert cause["rule"] == "too_hot"
        assert cause.get("at") is not None


class TestRunsEndpointDecorations:
    def test_escalated_step_carries_exit_rule(self, client):
        from stepwise.server import _get_engine
        engine = _get_engine()
        job_id = _seed_paused_escalated_job(engine.store)

        resp = client.get(f"/api/jobs/{job_id}/runs")
        assert resp.status_code == 200
        runs = resp.json()
        gate = next(r for r in runs if r["step_name"] == "gate")
        assert gate["exit_rule"] is not None
        assert gate["exit_rule"]["rule"] == "too_hot"
        assert gate["exit_rule"]["action"] == "escalate"

    def test_non_stranded_when_job_running(self, client):
        from stepwise.server import _get_engine
        engine = _get_engine()
        job_id = _seed_running_job(engine.store)

        resp = client.get(f"/api/jobs/{job_id}/runs")
        assert resp.status_code == 200
        for run in resp.json():
            assert run.get("is_stranded") is False

    def test_stranded_when_job_paused_and_run_still_running(self, client):
        """Regression target: the original gumball bug — an escalate-triggered
        pause leaves a sibling agent step stuck in RUNNING with no way to
        advance. The API must flag this so the UI renders STRANDED."""
        from stepwise.server import _get_engine
        engine = _get_engine()
        job_id = _seed_paused_escalated_job(engine.store, with_stranded_sibling=True)

        resp = client.get(f"/api/jobs/{job_id}/runs")
        assert resp.status_code == 200
        runs = resp.json()
        sib = next(r for r in runs if r["step_name"] == "sibling")
        assert sib["is_stranded"] is True
        assert sib["status"] == "running"
        gate = next(r for r in runs if r["step_name"] == "gate")
        assert gate["is_stranded"] is False  # completed, not stranded

    def test_run_without_exit_rule_reports_none(self, client):
        from stepwise.server import _get_engine
        engine = _get_engine()
        job_id = _seed_paused_escalated_job(engine.store, with_stranded_sibling=True)

        resp = client.get(f"/api/jobs/{job_id}/runs")
        assert resp.status_code == 200
        sib = next(r for r in resp.json() if r["step_name"] == "sibling")
        assert sib["exit_rule"] is None

    def test_tree_endpoint_also_enriches_runs(self, client):
        """Graph view consumes /tree — enrichment must apply there too."""
        from stepwise.server import _get_engine
        engine = _get_engine()
        job_id = _seed_paused_escalated_job(engine.store, with_stranded_sibling=True)

        resp = client.get(f"/api/jobs/{job_id}/tree")
        assert resp.status_code == 200
        tree = resp.json()
        assert tree["job"]["status"] == "paused"
        assert tree["job"]["pause_cause"]["step"] == "gate"

        runs = tree["runs"]
        gate = next(r for r in runs if r["step_name"] == "gate")
        sib = next(r for r in runs if r["step_name"] == "sibling")
        assert gate["exit_rule"]["action"] == "escalate"
        assert sib["is_stranded"] is True
