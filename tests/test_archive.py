"""Tests for job archive and cleanup features."""

import pytest

from stepwise.models import (
    Job,
    JobStatus,
    WorkflowDefinition,
    StepDefinition,
    ExecutorRef,
    _now,
    _gen_id,
)
from stepwise.store import SQLiteStore


def _make_job(store: SQLiteStore, status: JobStatus = JobStatus.COMPLETED, name: str | None = None, group: str | None = None) -> Job:
    """Create and persist a test job with the given status."""
    job = Job(
        id=_gen_id(),
        objective="test",
        name=name,
        workflow=WorkflowDefinition(steps={
            "step-a": StepDefinition(
                name="step-a",
                executor=ExecutorRef(type="script", config={"command": "echo ok"}),
                outputs=["result"],
            ),
        }),
        status=status,
        inputs={},
        workspace_path="",
        created_at=_now(),
        updated_at=_now(),
        job_group=group,
    )
    store.save_job(job)
    return job


class TestArchiveStatus:
    """Test that ARCHIVED is a valid job status."""

    def test_archived_status_exists(self):
        assert JobStatus.ARCHIVED.value == "archived"

    def test_archived_round_trip(self, store):
        job = _make_job(store, JobStatus.ARCHIVED)
        loaded = store.load_job(job.id)
        assert loaded.status == JobStatus.ARCHIVED


class TestStoreArchive:
    """Test store.archive_job() and store.unarchive_job()."""

    def test_archive_job(self, store):
        job = _make_job(store, JobStatus.COMPLETED)
        store.archive_job(job.id)
        loaded = store.load_job(job.id)
        assert loaded.status == JobStatus.ARCHIVED

    def test_unarchive_job(self, store):
        job = _make_job(store, JobStatus.ARCHIVED)
        store.unarchive_job(job.id)
        loaded = store.load_job(job.id)
        assert loaded.status == JobStatus.COMPLETED

    def test_unarchive_with_custom_status(self, store):
        job = _make_job(store, JobStatus.ARCHIVED)
        store.unarchive_job(job.id, restore_status=JobStatus.FAILED)
        loaded = store.load_job(job.id)
        assert loaded.status == JobStatus.FAILED


class TestAllJobsArchiveFilter:
    """Test that all_jobs() excludes archived by default."""

    def test_excludes_archived_by_default(self, store):
        _make_job(store, JobStatus.COMPLETED, name="visible")
        _make_job(store, JobStatus.ARCHIVED, name="hidden")
        jobs = store.all_jobs()
        names = [j.name for j in jobs]
        assert "visible" in names
        assert "hidden" not in names

    def test_includes_archived_when_requested(self, store):
        _make_job(store, JobStatus.COMPLETED, name="visible")
        _make_job(store, JobStatus.ARCHIVED, name="hidden")
        jobs = store.all_jobs(include_archived=True)
        names = [j.name for j in jobs]
        assert "visible" in names
        assert "hidden" in names

    def test_status_filter_for_archived(self, store):
        _make_job(store, JobStatus.COMPLETED)
        _make_job(store, JobStatus.ARCHIVED)
        jobs = store.all_jobs(status=JobStatus.ARCHIVED)
        assert len(jobs) == 1
        assert jobs[0].status == JobStatus.ARCHIVED

    def test_status_filter_completed_excludes_archived(self, store):
        _make_job(store, JobStatus.COMPLETED)
        _make_job(store, JobStatus.ARCHIVED)
        jobs = store.all_jobs(status=JobStatus.COMPLETED)
        assert len(jobs) == 1
        assert jobs[0].status == JobStatus.COMPLETED


class TestDeleteJob:
    """Test that delete_job works for any status."""

    def test_delete_archived_job(self, store):
        job = _make_job(store, JobStatus.ARCHIVED)
        store.delete_job(job.id)
        with pytest.raises(KeyError):
            store.load_job(job.id)

    def test_delete_completed_job(self, store):
        job = _make_job(store, JobStatus.COMPLETED)
        store.delete_job(job.id)
        with pytest.raises(KeyError):
            store.load_job(job.id)

    def test_delete_failed_job(self, store):
        job = _make_job(store, JobStatus.FAILED)
        store.delete_job(job.id)
        with pytest.raises(KeyError):
            store.load_job(job.id)


class TestBulkArchiveByStatus:
    """Test bulk archive by status filter."""

    def test_archive_all_completed(self, store):
        j1 = _make_job(store, JobStatus.COMPLETED)
        j2 = _make_job(store, JobStatus.COMPLETED)
        _make_job(store, JobStatus.RUNNING)  # should not be archived

        completed = store.all_jobs(status=JobStatus.COMPLETED)
        for j in completed:
            store.archive_job(j.id)

        assert store.load_job(j1.id).status == JobStatus.ARCHIVED
        assert store.load_job(j2.id).status == JobStatus.ARCHIVED
        # Running job untouched
        running = store.all_jobs(status=JobStatus.RUNNING)
        assert len(running) == 1

    def test_archive_by_group(self, store):
        j1 = _make_job(store, JobStatus.COMPLETED, group="batch-1")
        j2 = _make_job(store, JobStatus.FAILED, group="batch-1")
        j3 = _make_job(store, JobStatus.COMPLETED, group="batch-2")

        TERMINAL = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
        all_jobs = store.all_jobs()
        group_jobs = [j for j in all_jobs if j.job_group == "batch-1" and j.status in TERMINAL]
        for j in group_jobs:
            store.archive_job(j.id)

        assert store.load_job(j1.id).status == JobStatus.ARCHIVED
        assert store.load_job(j2.id).status == JobStatus.ARCHIVED
        assert store.load_job(j3.id).status == JobStatus.COMPLETED  # different group


class TestBulkDeleteArchived:
    """Test bulk delete of archived jobs."""

    def test_delete_all_archived(self, store):
        _make_job(store, JobStatus.ARCHIVED)
        _make_job(store, JobStatus.ARCHIVED)
        _make_job(store, JobStatus.COMPLETED)

        archived = store.all_jobs(status=JobStatus.ARCHIVED)
        for j in archived:
            store.delete_job(j.id)

        remaining = store.all_jobs(include_archived=True)
        assert len(remaining) == 1
        assert remaining[0].status == JobStatus.COMPLETED
