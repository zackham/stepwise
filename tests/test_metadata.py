"""Tests for E1: Job Metadata + Event Foundation."""

import json

import pytest

from stepwise.hooks import build_event_envelope, fire_hook
from stepwise.models import (
    Event,
    ExecutorRef,
    InputBinding,
    Job,
    JobStatus,
    StepDefinition,
    WorkflowDefinition,
    _now,
    validate_job_metadata,
)
from tests.conftest import register_step_fn, run_job_sync


# ── Validation Tests ─────────────────────────────────────────────────


class TestMetadataValidation:
    def test_valid_metadata_accepted(self):
        """Well-formed metadata with known sys keys passes validation."""
        meta = {"sys": {"origin": "cli", "session_id": "abc"}, "app": {"foo": "bar"}}
        validate_job_metadata(meta)  # should not raise

    def test_unknown_sys_key_rejected(self):
        """Unknown keys in sys block raise ValueError."""
        meta = {"sys": {"unknown_key": "x"}, "app": {}}
        with pytest.raises(ValueError, match="unknown_key"):
            validate_job_metadata(meta)

    def test_sys_key_wrong_type_rejected(self):
        """sys.depth must be int, not string."""
        meta = {"sys": {"depth": "notint"}, "app": {}}
        with pytest.raises(ValueError, match="depth"):
            validate_job_metadata(meta)

    def test_metadata_size_limit_enforced(self):
        """Metadata > 8KB is rejected."""
        meta = {"sys": {}, "app": {"big": "x" * 9000}}
        with pytest.raises(ValueError, match="8192"):
            validate_job_metadata(meta)

    def test_app_block_accepts_arbitrary_json(self):
        """app block accepts nested structures without validation."""
        meta = {"sys": {}, "app": {"nested": {"list": [1, 2, 3], "deep": {"a": True}}}}
        validate_job_metadata(meta)  # should not raise

    def test_missing_sys_app_keys_defaulted(self):
        """Missing sys/app keys are auto-filled with {}."""
        meta = {}
        validate_job_metadata(meta)
        assert meta == {"sys": {}, "app": {}}

    def test_not_a_dict_raises(self):
        """Non-dict metadata raises ValueError."""
        with pytest.raises(ValueError, match="must be a dict"):
            validate_job_metadata("not a dict")

    def test_all_valid_sys_keys_accepted(self):
        """All documented sys keys pass validation with correct types."""
        meta = {
            "sys": {
                "origin": "cli",
                "session_id": "sess-1",
                "parent_job_id": "job-123",
                "root_job_id": "job-root",
                "depth": 3,
                "notify_url": "https://example.com",
                "created_by": "server",
            },
            "app": {},
        }
        validate_job_metadata(meta)  # should not raise


# ── Auto-Population Tests ────────────────────────────────────────────


def _simple_wf():
    """Helper: single-step callable workflow."""
    return WorkflowDefinition(
        steps={
            "s": StepDefinition(
                name="s",
                executor=ExecutorRef(type="callable", config={"fn_name": "noop"}),
                outputs=["out"],
            ),
        },
    )


class TestMetadataAutoPopulation:
    def test_depth_zero_for_top_level(self, async_engine):
        """Top-level job gets sys.depth = 0."""
        job = async_engine.create_job(
            "test", _simple_wf(),
            metadata={"sys": {"origin": "cli"}, "app": {}},
        )
        assert job.metadata["sys"]["depth"] == 0

    def test_root_job_id_self_for_top_level(self, async_engine):
        """Top-level job gets sys.root_job_id = own job.id."""
        job = async_engine.create_job("test", _simple_wf())
        assert job.metadata["sys"]["root_job_id"] == job.id

    def test_depth_increments_for_sub_job(self, async_engine):
        """Sub-job depth = parent depth + 1."""
        parent = async_engine.create_job("parent", _simple_wf())
        child = async_engine.create_job(
            "child", _simple_wf(),
            metadata={"sys": {"parent_job_id": parent.id}, "app": {}},
        )
        assert child.metadata["sys"]["depth"] == 1

    def test_root_job_id_inherited(self, async_engine):
        """Sub-job inherits root_job_id from parent."""
        parent = async_engine.create_job("parent", _simple_wf())
        child = async_engine.create_job(
            "child", _simple_wf(),
            metadata={"sys": {"parent_job_id": parent.id}, "app": {}},
        )
        assert child.metadata["sys"]["root_job_id"] == parent.id

    def test_depth_exceeds_10_rejected(self, async_engine):
        """Depth > 10 raises ValueError for loop prevention."""
        parent = async_engine.create_job("parent", _simple_wf())
        parent.metadata["sys"]["depth"] = 10
        async_engine.store.save_job(parent)
        with pytest.raises(ValueError, match="depth"):
            async_engine.create_job(
                "child", _simple_wf(),
                metadata={"sys": {"parent_job_id": parent.id}, "app": {}},
            )

    def test_default_metadata_when_none(self, async_engine):
        """create_job with no metadata sets default."""
        job = async_engine.create_job("test", _simple_wf())
        assert job.metadata["sys"]["depth"] == 0
        assert job.metadata["sys"]["root_job_id"] == job.id
        assert job.metadata["app"] == {}


# ── Immutability Test ────────────────────────────────────────────────


class TestMetadataImmutability:
    def test_metadata_unchanged_after_lifecycle(self, async_engine):
        """Metadata is not modified by job lifecycle operations."""
        register_step_fn("noop", lambda inputs: {"out": 1})
        original_meta = {
            "sys": {"origin": "cli", "session_id": "sess-1"},
            "app": {"tag": "test"},
        }
        job = async_engine.create_job("test", _simple_wf(), metadata=original_meta)
        result = run_job_sync(async_engine, job.id)
        reloaded = async_engine.store.load_job(job.id)
        assert reloaded.metadata["sys"]["origin"] == "cli"
        assert reloaded.metadata["sys"]["session_id"] == "sess-1"
        assert reloaded.metadata["app"] == {"tag": "test"}


# ── Store Round-Trip + Filter Tests ──────────────────────────────────


class TestMetadataStoreFilters:
    def test_metadata_round_trip(self, store):
        """Metadata survives save/load cycle."""
        wf = _simple_wf()
        meta = {"sys": {"origin": "api"}, "app": {"project": "test"}}
        job = Job(
            id="job-rt",
            objective="roundtrip",
            workflow=wf,
            metadata=meta,
        )
        store.save_job(job)
        loaded = store.load_job("job-rt")
        assert loaded.metadata == meta

    def test_all_jobs_meta_filter(self, store):
        """meta_filters returns only jobs matching the filter."""
        wf = _simple_wf()
        j1 = Job(id="job-1", objective="a", workflow=wf, metadata={"sys": {"origin": "cli"}, "app": {}})
        j2 = Job(id="job-2", objective="b", workflow=wf, metadata={"sys": {"origin": "api"}, "app": {}})
        store.save_job(j1)
        store.save_job(j2)
        result = store.all_jobs(meta_filters={"sys.origin": "cli"})
        assert len(result) == 1
        assert result[0].id == "job-1"

    def test_all_jobs_meta_filter_no_match(self, store):
        """meta_filters with no matches returns empty list."""
        wf = _simple_wf()
        j1 = Job(id="job-1", objective="a", workflow=wf, metadata={"sys": {"origin": "cli"}, "app": {}})
        store.save_job(j1)
        result = store.all_jobs(meta_filters={"sys.origin": "webhook"})
        assert result == []

    def test_all_jobs_app_meta_filter(self, store):
        """Filtering by app metadata works."""
        wf = _simple_wf()
        j1 = Job(id="job-1", objective="a", workflow=wf, metadata={"sys": {}, "app": {"project": "alpha"}})
        j2 = Job(id="job-2", objective="b", workflow=wf, metadata={"sys": {}, "app": {"project": "beta"}})
        store.save_job(j1)
        store.save_job(j2)
        result = store.all_jobs(meta_filters={"app.project": "beta"})
        assert len(result) == 1
        assert result[0].id == "job-2"


# ── CLI Parsing Tests ────────────────────────────────────────────────


class TestParseMetaFlags:
    def test_dot_notation_parsing(self):
        from stepwise.cli import parse_meta_flags

        result = parse_meta_flags(["sys.origin=cli", "app.project=stepwise"])
        assert result == {"sys": {"origin": "cli"}, "app": {"project": "stepwise"}}

    def test_nested_keys(self):
        from stepwise.cli import parse_meta_flags

        result = parse_meta_flags(["sys.session_id=abc-123"])
        assert result == {"sys": {"session_id": "abc-123"}, "app": {}}

    def test_value_with_equals_sign(self):
        from stepwise.cli import parse_meta_flags

        result = parse_meta_flags(["app.query=a=b"])
        assert result == {"sys": {}, "app": {"query": "a=b"}}

    def test_empty_list(self):
        from stepwise.cli import parse_meta_flags

        result = parse_meta_flags([])
        assert result == {"sys": {}, "app": {}}

    def test_invalid_top_level_key_rejected(self):
        from stepwise.cli import parse_meta_flags

        with pytest.raises(SystemExit):
            parse_meta_flags(["invalid.key=val"])


# ── Event Envelope Tests ─────────────────────────────────────────────


class TestEventEnvelope:
    def test_envelope_has_all_required_fields(self):
        """build_event_envelope returns all spec-required fields."""
        envelope = build_event_envelope(
            "step.completed",
            {"step": "s1", "run_id": "run-1"},
            "job-1", 42,
            {"sys": {}, "app": {}},
            "2026-03-21T00:00:00Z",
        )
        assert envelope["event"] == "step.completed"
        assert envelope["job_id"] == "job-1"
        assert envelope["event_id"] == 42
        assert isinstance(envelope["event_id"], int)
        assert envelope["metadata"] == {"sys": {}, "app": {}}
        assert envelope["data"]["step"] == "s1"
        assert envelope["timestamp"] == "2026-03-21T00:00:00Z"

    def test_step_promoted_to_top_level(self):
        """step field from data is promoted to top-level."""
        envelope = build_event_envelope(
            "step.suspended", {"step": "approve"},
            "job-1", 1, {"sys": {}, "app": {}}, "t",
        )
        assert envelope["step"] == "approve"
        assert envelope["data"]["step"] == "approve"  # also in data

    def test_no_step_in_data(self):
        """Envelope without step in data doesn't have top-level step."""
        envelope = build_event_envelope(
            "job.completed", {},
            "job-1", 1, {"sys": {}, "app": {}}, "t",
        )
        assert "step" not in envelope

    def test_event_id_monotonic(self, store):
        """Sequential save_event calls produce increasing rowids."""
        e1 = Event(id="evt-1", job_id="job-1", timestamp=_now(), type="step.started", data={})
        e2 = Event(id="evt-2", job_id="job-1", timestamp=_now(), type="step.completed", data={})
        # Need a job for the foreign key
        wf = _simple_wf()
        j = Job(id="job-1", objective="test", workflow=wf)
        store.save_job(j)
        r1 = store.save_event(e1)
        r2 = store.save_event(e2)
        assert isinstance(r1, int)
        assert isinstance(r2, int)
        assert r2 > r1


# ── Hook Payload Tests ───────────────────────────────────────────────


class TestHookEventEnvelope:
    def test_hook_receives_event_file(self, tmp_path):
        """Hook subprocess receives STEPWISE_EVENT_FILE env var pointing to temp JSON."""
        dot_dir = tmp_path / ".stepwise"
        dot_dir.mkdir()
        hooks_dir = dot_dir / "hooks"
        hooks_dir.mkdir()
        output_file = tmp_path / "envelope.json"
        hook = hooks_dir / "on-suspend"
        hook.write_text(f'#!/bin/sh\ncp "$STEPWISE_EVENT_FILE" {output_file}\n')
        hook.chmod(0o755)
        envelope = {
            "event": "step.suspended", "job_id": "job-1",
            "event_id": 42, "metadata": {"sys": {}, "app": {}},
            "data": {}, "timestamp": "t",
        }
        fire_hook("suspend", {"event": "step.suspended"}, dot_dir, envelope=envelope)
        assert output_file.exists()
        written = json.loads(output_file.read_text())
        assert written["event_id"] == 42
        assert "metadata" in written

    def test_hook_env_vars_set(self, tmp_path):
        """STEPWISE_JOB_ID, STEPWISE_EVENT, STEPWISE_SESSION_ID env vars are set."""
        dot_dir = tmp_path / ".stepwise"
        dot_dir.mkdir()
        hooks_dir = dot_dir / "hooks"
        hooks_dir.mkdir()
        output_file = tmp_path / "env_vars.txt"
        hook = hooks_dir / "on-suspend"
        hook.write_text(
            f'#!/bin/sh\necho "$STEPWISE_JOB_ID|$STEPWISE_EVENT|$STEPWISE_SESSION_ID" > {output_file}\n'
        )
        hook.chmod(0o755)
        envelope = {
            "event": "step.suspended", "job_id": "job-abc", "event_id": 1,
            "metadata": {"sys": {"session_id": "sess-xyz"}, "app": {}},
            "data": {}, "timestamp": "t",
        }
        fire_hook("suspend", {}, dot_dir, envelope=envelope)
        content = output_file.read_text().strip()
        assert content == "job-abc|step.suspended|sess-xyz"

    def test_hook_stdin_backward_compat(self, tmp_path):
        """Stdin still receives the old-format payload alongside new env vars."""
        dot_dir = tmp_path / ".stepwise"
        dot_dir.mkdir()
        hooks_dir = dot_dir / "hooks"
        hooks_dir.mkdir()
        output_file = tmp_path / "stdin_payload.json"
        hook = hooks_dir / "on-suspend"
        hook.write_text(f'#!/bin/sh\ncat > {output_file}\n')
        hook.chmod(0o755)
        payload = {"event": "step.suspended", "hook": "suspend", "job_id": "job-1", "step": "review"}
        fire_hook("suspend", payload, dot_dir, envelope={"event": "step.suspended", "job_id": "job-1", "event_id": 1, "metadata": {"sys": {}, "app": {}}, "data": {}, "timestamp": "t"})
        written = json.loads(output_file.read_text())
        assert written["event"] == "step.suspended"
        assert written["hook"] == "suspend"

    def test_temp_file_cleaned_up_after_hook(self, tmp_path):
        """Temp event file is deleted after hook completes."""
        dot_dir = tmp_path / ".stepwise"
        dot_dir.mkdir()
        (dot_dir / "hooks").mkdir()
        tmp_dir = dot_dir / "tmp"
        hook = dot_dir / "hooks" / "on-complete"
        hook.write_text("#!/bin/sh\ntrue\n")
        hook.chmod(0o755)
        envelope = {
            "event": "job.completed", "job_id": "job-1", "event_id": 1,
            "metadata": {"sys": {}, "app": {}}, "data": {}, "timestamp": "t",
        }
        fire_hook("complete", {}, dot_dir, envelope=envelope)
        if tmp_dir.exists():
            assert len(list(tmp_dir.iterdir())) == 0

    def test_temp_file_cleaned_up_on_hook_failure(self, tmp_path):
        """Temp event file is deleted even when hook script fails."""
        dot_dir = tmp_path / ".stepwise"
        dot_dir.mkdir()
        (dot_dir / "hooks").mkdir()
        (dot_dir / "logs").mkdir()
        tmp_dir = dot_dir / "tmp"
        hook = dot_dir / "hooks" / "on-fail"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        envelope = {
            "event": "job.failed", "job_id": "job-1", "event_id": 1,
            "metadata": {"sys": {}, "app": {}}, "data": {}, "timestamp": "t",
        }
        fire_hook("fail", {}, dot_dir, envelope=envelope)
        if tmp_dir.exists():
            assert len(list(tmp_dir.iterdir())) == 0

    def test_fire_hook_without_envelope(self, tmp_path):
        """fire_hook works without envelope (backward compat)."""
        dot_dir = tmp_path / ".stepwise"
        dot_dir.mkdir()
        hooks_dir = dot_dir / "hooks"
        hooks_dir.mkdir()
        output_file = tmp_path / "payload.json"
        hook = hooks_dir / "on-complete"
        hook.write_text(f'#!/bin/sh\ncat > {output_file}\n')
        hook.chmod(0o755)
        payload = {"event": "job.completed", "job_id": "job-1"}
        fire_hook("complete", payload, dot_dir)
        written = json.loads(output_file.read_text())
        assert written["event"] == "job.completed"
