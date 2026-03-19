"""Tests for optional job naming."""

from stepwise.models import (
    ExecutorRef,
    Job,
    JobStatus,
    StepDefinition,
    WorkflowDefinition,
)
from stepwise.store import SQLiteStore


def _minimal_workflow():
    return WorkflowDefinition(steps={})


def _valid_workflow():
    """Workflow with one step — passes engine validation."""
    return WorkflowDefinition(steps={
        "noop": StepDefinition(
            name="noop",
            executor=ExecutorRef(type="callable", config={"fn_name": "noop"}),
            outputs=["result"],
        ),
    })


class TestJobNameModel:
    def test_to_dict_includes_name(self):
        job = Job(
            id="j1",
            objective="test",
            name="my-job",
            workflow=_minimal_workflow(),
        )
        d = job.to_dict()
        assert d["name"] == "my-job"

    def test_to_dict_name_none(self):
        job = Job(
            id="j1",
            objective="test",
            workflow=_minimal_workflow(),
        )
        d = job.to_dict()
        assert d["name"] is None

    def test_from_dict_round_trip(self):
        job = Job(
            id="j1",
            objective="test",
            name="round-trip",
            workflow=_minimal_workflow(),
        )
        d = job.to_dict()
        restored = Job.from_dict(d)
        assert restored.name == "round-trip"

    def test_from_dict_missing_name(self):
        d = {
            "id": "j1",
            "objective": "test",
            "workflow": {"steps": {}},
            "status": "pending",
        }
        job = Job.from_dict(d)
        assert job.name is None


class TestJobNameStore:
    def test_save_and_load_with_name(self):
        store = SQLiteStore(":memory:")
        job = Job(
            id="j1",
            objective="test",
            name="store-test",
            workflow=_minimal_workflow(),
        )
        store.save_job(job)
        loaded = store.load_job("j1")
        assert loaded.name == "store-test"

    def test_save_and_load_without_name(self):
        store = SQLiteStore(":memory:")
        job = Job(
            id="j2",
            objective="test",
            workflow=_minimal_workflow(),
        )
        store.save_job(job)
        loaded = store.load_job("j2")
        assert loaded.name is None

    def test_migration_adds_name_column(self):
        store = SQLiteStore(":memory:")
        cursor = store._conn.execute("PRAGMA table_info(jobs)")
        columns = {row[1] for row in cursor.fetchall()}
        assert "name" in columns


class TestJobNameEngine:
    def test_create_job_with_name(self, async_engine):
        wf = _valid_workflow()
        job = async_engine.create_job(
            objective="test",
            workflow=wf,
            name="engine-test",
        )
        assert job.name == "engine-test"

    def test_create_job_without_name(self, async_engine):
        wf = _valid_workflow()
        job = async_engine.create_job(
            objective="test",
            workflow=wf,
        )
        assert job.name is None

    def test_create_job_name_persisted(self, async_engine):
        wf = _valid_workflow()
        job = async_engine.create_job(
            objective="test",
            workflow=wf,
            name="persisted",
        )
        loaded = async_engine.store.load_job(job.id)
        assert loaded.name == "persisted"
