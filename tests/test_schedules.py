"""Tests for the Stepwise Schedules feature.

Covers: poll_eval, models, store, scheduler service.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from stepwise.models import (
    Schedule,
    ScheduleStatus,
    ScheduleTick,
    ScheduleType,
    OverlapPolicy,
    RecoveryPolicy,
    TickOutcome,
    _gen_id,
    _now,
)
from stepwise.poll_eval import PollResult, evaluate_poll_command_sync
from stepwise.store import SQLiteStore


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def store():
    """Fresh in-memory SQLite store."""
    return SQLiteStore(":memory:")


@pytest.fixture
def sample_cron_schedule():
    return Schedule(
        id="sched-cron1",
        name="daily-report",
        type=ScheduleType.CRON,
        flow_path="research/deep",
        cron_expr="0 9 * * *",
        job_inputs={"topic": "ai"},
        timezone="UTC",
    )


@pytest.fixture
def sample_poll_schedule():
    return Schedule(
        id="sched-poll1",
        name="gh-watcher",
        type=ScheduleType.POLL,
        flow_path="fix-issue",
        cron_expr="*/5 * * * *",
        poll_command='echo \'{"issue": 42}\'',
        cooldown_seconds=300,
        overlap_policy=OverlapPolicy.ALLOW,
        job_inputs={"repo": "org/name"},
        timezone="UTC",
    )


# ── PollEval Tests ────────────────────────────────────────────────────────


class TestPollEvalSync:
    """Tests for evaluate_poll_command_sync."""

    def test_exit_zero_json_dict_is_ready(self, tmp_path):
        result = evaluate_poll_command_sync(
            command='echo \'{"ready": true, "count": 5}\'',
            cwd=str(tmp_path),
        )
        assert result.ready is True
        assert result.output == {"ready": True, "count": 5}
        assert result.error is None
        assert result.duration_ms >= 0

    def test_exit_zero_empty_stdout_is_not_ready(self, tmp_path):
        result = evaluate_poll_command_sync(command="echo -n ''", cwd=str(tmp_path))
        assert result.ready is False
        assert result.output is None
        assert result.error is None

    def test_exit_zero_non_json_is_not_ready(self, tmp_path):
        result = evaluate_poll_command_sync(command="echo 'not json'", cwd=str(tmp_path))
        assert result.ready is False
        assert result.output is None

    def test_exit_zero_json_array_is_not_ready(self, tmp_path):
        result = evaluate_poll_command_sync(command='echo \'[1, 2, 3]\'', cwd=str(tmp_path))
        assert result.ready is False

    def test_exit_zero_json_scalar_is_not_ready(self, tmp_path):
        result = evaluate_poll_command_sync(command="echo '42'", cwd=str(tmp_path))
        assert result.ready is False

    def test_nonzero_exit_is_error(self, tmp_path):
        result = evaluate_poll_command_sync(
            command="echo 'something went wrong' >&2; exit 1",
            cwd=str(tmp_path),
        )
        assert result.ready is False
        assert result.error is not None
        assert "something went wrong" in result.error

    def test_timeout(self, tmp_path):
        result = evaluate_poll_command_sync(
            command="sleep 10",
            cwd=str(tmp_path),
            timeout_seconds=1,
        )
        assert result.ready is False
        assert result.error is not None
        assert "timeout" in result.error.lower()

    def test_env_vars_passed(self, tmp_path):
        result = evaluate_poll_command_sync(
            command='echo "{\\\"val\\\": \\\"$MY_VAR\\\"}"',
            cwd=str(tmp_path),
            env={"MY_VAR": "hello"},
        )
        assert result.ready is True
        assert result.output == {"val": "hello"}

    def test_cwd_respected(self, tmp_path):
        marker = tmp_path / "marker.txt"
        marker.write_text("found")
        result = evaluate_poll_command_sync(
            command='test -f marker.txt && echo \'{"found": true}\'',
            cwd=str(tmp_path),
        )
        assert result.ready is True

    def test_stderr_captured_on_error(self, tmp_path):
        result = evaluate_poll_command_sync(
            command="echo 'err detail' >&2; exit 1",
            cwd=str(tmp_path),
        )
        assert "err detail" in result.error

    def test_error_message_truncated(self, tmp_path):
        long_err = "x" * 2000
        result = evaluate_poll_command_sync(
            command=f"echo '{long_err}' >&2; exit 1",
            cwd=str(tmp_path),
        )
        assert len(result.error) <= 1000


# ── Model Tests ───────────────────────────────────────────────────────────


class TestScheduleModel:

    def test_to_dict_roundtrip(self, sample_cron_schedule):
        d = sample_cron_schedule.to_dict()
        restored = Schedule.from_dict(d)
        assert restored.id == sample_cron_schedule.id
        assert restored.name == sample_cron_schedule.name
        assert restored.type == ScheduleType.CRON
        assert restored.job_inputs == {"topic": "ai"}

    def test_poll_schedule_roundtrip(self, sample_poll_schedule):
        d = sample_poll_schedule.to_dict()
        restored = Schedule.from_dict(d)
        assert restored.type == ScheduleType.POLL
        assert restored.poll_command == 'echo \'{"issue": 42}\''
        assert restored.cooldown_seconds == 300
        assert restored.overlap_policy == OverlapPolicy.ALLOW

    def test_defaults(self):
        s = Schedule(id="s", name="n", type=ScheduleType.CRON, flow_path="f", cron_expr="* * * * *")
        assert s.overlap_policy == OverlapPolicy.SKIP
        assert s.recovery_policy == RecoveryPolicy.SKIP
        assert s.status == ScheduleStatus.ACTIVE
        assert s.max_consecutive_errors == 10
        assert s.poll_timeout_seconds == 30


class TestTickModel:

    def test_to_dict_roundtrip(self):
        tick = ScheduleTick(
            id="t1",
            schedule_id="s1",
            scheduled_for=datetime(2026, 4, 11, 9, 0, tzinfo=timezone.utc),
            evaluated_at=datetime(2026, 4, 11, 9, 0, 2, tzinfo=timezone.utc),
            outcome=TickOutcome.FIRED,
            poll_output={"issue": 42},
            job_id="job-abc",
            duration_ms=150,
        )
        d = tick.to_dict()
        restored = ScheduleTick.from_dict(d)
        assert restored.outcome == TickOutcome.FIRED
        assert restored.poll_output == {"issue": 42}
        assert restored.duration_ms == 150


# ── Store Tests ───────────────────────────────────────────────────────────


class TestScheduleStore:

    def test_save_and_get(self, store, sample_cron_schedule):
        store.save_schedule(sample_cron_schedule)
        got = store.get_schedule("sched-cron1")
        assert got is not None
        assert got.name == "daily-report"
        assert got.type == ScheduleType.CRON
        assert got.job_inputs == {"topic": "ai"}

    def test_get_by_name(self, store, sample_cron_schedule):
        store.save_schedule(sample_cron_schedule)
        got = store.get_schedule_by_name("daily-report")
        assert got.id == "sched-cron1"

    def test_get_nonexistent(self, store):
        assert store.get_schedule("nope") is None
        assert store.get_schedule_by_name("nope") is None

    def test_list_all(self, store, sample_cron_schedule, sample_poll_schedule):
        store.save_schedule(sample_cron_schedule)
        store.save_schedule(sample_poll_schedule)
        assert len(store.list_schedules()) == 2

    def test_list_filter_status(self, store, sample_cron_schedule, sample_poll_schedule):
        sample_poll_schedule.status = ScheduleStatus.PAUSED
        store.save_schedule(sample_cron_schedule)
        store.save_schedule(sample_poll_schedule)
        assert len(store.list_schedules(status="active")) == 1
        assert len(store.list_schedules(status="paused")) == 1

    def test_list_filter_type(self, store, sample_cron_schedule, sample_poll_schedule):
        store.save_schedule(sample_cron_schedule)
        store.save_schedule(sample_poll_schedule)
        assert len(store.list_schedules(schedule_type="cron")) == 1
        assert len(store.list_schedules(schedule_type="poll")) == 1

    def test_update(self, store, sample_cron_schedule):
        store.save_schedule(sample_cron_schedule)
        now = _now()
        store.update_schedule("sched-cron1", status="paused", paused_at=now)
        got = store.get_schedule("sched-cron1")
        assert got.status == ScheduleStatus.PAUSED
        assert got.paused_at is not None

    def test_update_auto_bumps_updated_at(self, store, sample_cron_schedule):
        store.save_schedule(sample_cron_schedule)
        old_updated = store.get_schedule("sched-cron1").updated_at
        store.update_schedule("sched-cron1", cron_expr="0 10 * * *")
        new_updated = store.get_schedule("sched-cron1").updated_at
        assert new_updated >= old_updated

    def test_delete(self, store, sample_cron_schedule):
        store.save_schedule(sample_cron_schedule)
        store.delete_schedule("sched-cron1")
        assert store.get_schedule("sched-cron1") is None

    def test_delete_cascades_ticks(self, store, sample_cron_schedule):
        store.save_schedule(sample_cron_schedule)
        store.save_tick(ScheduleTick(
            id="t1", schedule_id="sched-cron1",
            scheduled_for=_now(), evaluated_at=_now(),
            outcome=TickOutcome.FIRED,
        ))
        assert len(store.list_ticks("sched-cron1")) == 1
        store.delete_schedule("sched-cron1")
        assert len(store.list_ticks("sched-cron1")) == 0

    def test_save_schedule_upsert(self, store, sample_cron_schedule):
        """save_schedule is an upsert — same ID replaces the existing row."""
        store.save_schedule(sample_cron_schedule)
        updated = Schedule(
            id="sched-cron1", name="daily-report-v2",
            type=ScheduleType.CRON, flow_path="other", cron_expr="0 10 * * *",
        )
        store.save_schedule(updated)
        got = store.get_schedule("sched-cron1")
        assert got.name == "daily-report-v2"
        assert got.cron_expr == "0 10 * * *"


class TestTickStore:

    def test_save_and_list(self, store, sample_cron_schedule):
        store.save_schedule(sample_cron_schedule)
        for i in range(5):
            store.save_tick(ScheduleTick(
                id=f"t{i}", schedule_id="sched-cron1",
                scheduled_for=_now(), evaluated_at=_now(),
                outcome=TickOutcome.FIRED if i == 0 else TickOutcome.SKIPPED,
                duration_ms=10 + i,
            ))
        ticks = store.list_ticks("sched-cron1")
        assert len(ticks) == 5

    def test_list_with_outcome_filter(self, store, sample_cron_schedule):
        store.save_schedule(sample_cron_schedule)
        store.save_tick(ScheduleTick(
            id="t1", schedule_id="sched-cron1",
            scheduled_for=_now(), evaluated_at=_now(),
            outcome=TickOutcome.FIRED,
        ))
        store.save_tick(ScheduleTick(
            id="t2", schedule_id="sched-cron1",
            scheduled_for=_now(), evaluated_at=_now(),
            outcome=TickOutcome.SKIPPED,
        ))
        assert len(store.list_ticks("sched-cron1", outcome="fired")) == 1

    def test_list_with_limit(self, store, sample_cron_schedule):
        store.save_schedule(sample_cron_schedule)
        for i in range(10):
            store.save_tick(ScheduleTick(
                id=f"t{i}", schedule_id="sched-cron1",
                scheduled_for=_now(), evaluated_at=_now(),
                outcome=TickOutcome.SKIPPED,
            ))
        assert len(store.list_ticks("sched-cron1", limit=3)) == 3

    def test_last_fired_tick(self, store, sample_cron_schedule):
        store.save_schedule(sample_cron_schedule)
        store.save_tick(ScheduleTick(
            id="t1", schedule_id="sched-cron1",
            scheduled_for=_now(), evaluated_at=_now(),
            outcome=TickOutcome.SKIPPED,
        ))
        store.save_tick(ScheduleTick(
            id="t2", schedule_id="sched-cron1",
            scheduled_for=_now(), evaluated_at=_now(),
            outcome=TickOutcome.FIRED,
            poll_output={"n": 42},
        ))
        last = store.last_fired_tick("sched-cron1")
        assert last is not None
        assert last.poll_output == {"n": 42}

    def test_last_fired_tick_none(self, store, sample_cron_schedule):
        store.save_schedule(sample_cron_schedule)
        assert store.last_fired_tick("sched-cron1") is None

    def test_tick_stats(self, store, sample_cron_schedule):
        store.save_schedule(sample_cron_schedule)
        for i in range(10):
            store.save_tick(ScheduleTick(
                id=f"t{i}", schedule_id="sched-cron1",
                scheduled_for=_now(), evaluated_at=_now(),
                outcome=TickOutcome.FIRED if i < 3 else TickOutcome.SKIPPED,
                duration_ms=100,
            ))
        stats = store.tick_stats("sched-cron1")
        assert stats["total_ticks"] == 10
        assert stats["total_fires"] == 3
        assert abs(stats["fire_rate"] - 0.3) < 0.01
        assert stats["avg_check_duration_ms"] == 100

    def test_consecutive_errors(self, store, sample_cron_schedule):
        store.save_schedule(sample_cron_schedule)
        # OK, OK, ERR, ERR, ERR
        outcomes = [TickOutcome.SKIPPED, TickOutcome.SKIPPED,
                    TickOutcome.ERROR, TickOutcome.ERROR, TickOutcome.ERROR]
        for i, out in enumerate(outcomes):
            store.save_tick(ScheduleTick(
                id=f"t{i}", schedule_id="sched-cron1",
                scheduled_for=_now() + timedelta(seconds=i),
                evaluated_at=_now() + timedelta(seconds=i),
                outcome=out,
            ))
        assert store.consecutive_errors("sched-cron1") == 3

    def test_consecutive_errors_broken_by_success(self, store, sample_cron_schedule):
        store.save_schedule(sample_cron_schedule)
        # ERR, ERR, OK, ERR
        outcomes = [TickOutcome.ERROR, TickOutcome.ERROR,
                    TickOutcome.SKIPPED, TickOutcome.ERROR]
        for i, out in enumerate(outcomes):
            store.save_tick(ScheduleTick(
                id=f"t{i}", schedule_id="sched-cron1",
                scheduled_for=_now() + timedelta(seconds=i),
                evaluated_at=_now() + timedelta(seconds=i),
                outcome=out,
            ))
        assert store.consecutive_errors("sched-cron1") == 1  # only the last one

    def test_prune_ticks(self, store, sample_cron_schedule):
        store.save_schedule(sample_cron_schedule)
        old = _now() - timedelta(days=60)
        recent = _now()
        # Old skipped tick (should be pruned)
        store.save_tick(ScheduleTick(
            id="old1", schedule_id="sched-cron1",
            scheduled_for=old, evaluated_at=old,
            outcome=TickOutcome.SKIPPED,
        ))
        # Old fired tick (should be kept)
        store.save_tick(ScheduleTick(
            id="old2", schedule_id="sched-cron1",
            scheduled_for=old, evaluated_at=old,
            outcome=TickOutcome.FIRED,
        ))
        # Recent skipped tick (should be kept)
        store.save_tick(ScheduleTick(
            id="new1", schedule_id="sched-cron1",
            scheduled_for=recent, evaluated_at=recent,
            outcome=TickOutcome.SKIPPED,
        ))
        pruned = store.prune_ticks("sched-cron1", keep_days=30)
        assert pruned == 1
        remaining = store.list_ticks("sched-cron1")
        assert len(remaining) == 2


class TestScheduleQueue:

    def test_enqueue_dequeue(self, store, sample_cron_schedule):
        store.save_schedule(sample_cron_schedule)
        # Disable FK for testing without real jobs
        store._conn.execute("PRAGMA foreign_keys=OFF")
        store.enqueue_schedule_job("sched-cron1", "job-a")
        store.enqueue_schedule_job("sched-cron1", "job-b")
        assert store.schedule_queue_depth("sched-cron1") == 2
        popped = store.dequeue_schedule_job("sched-cron1")
        assert popped == "job-a"  # FIFO
        assert store.schedule_queue_depth("sched-cron1") == 1

    def test_dequeue_empty(self, store, sample_cron_schedule):
        store.save_schedule(sample_cron_schedule)
        assert store.dequeue_schedule_job("sched-cron1") is None


# ── Scheduler Service Tests ──────────────────────────────────────────────


class TestSchedulerService:

    def test_compute_next(self):
        from stepwise.scheduler import SchedulerService
        store = SQLiteStore(":memory:")
        svc = SchedulerService(store=store, project_dir="/tmp")
        sched = Schedule(
            id="s1", name="test", type=ScheduleType.CRON,
            flow_path="test", cron_expr="*/5 * * * *", timezone="UTC",
        )
        next_fire = svc._compute_next(sched)
        assert next_fire > _now()
        # Should be within 5 minutes
        assert next_fire < _now() + timedelta(minutes=6)

    def test_build_poll_env_no_cursor(self):
        from stepwise.scheduler import SchedulerService
        store = SQLiteStore(":memory:")
        svc = SchedulerService(store=store, project_dir="/tmp/project")
        sched = Schedule(
            id="s1", name="test-poll", type=ScheduleType.POLL,
            flow_path="test", cron_expr="*/5 * * * *",
            poll_command="echo test",
            job_inputs={"repo": "org/name", "count": 5},
        )
        store.save_schedule(sched)
        env = svc._build_poll_env(sched)
        assert env["STEPWISE_SCHEDULE_ID"] == "s1"
        assert env["STEPWISE_SCHEDULE_NAME"] == "test-poll"
        assert env["STEPWISE_PROJECT_DIR"] == "/tmp/project"
        assert env["STEPWISE_POLL_CURSOR"] == ""
        assert env["STEPWISE_INPUT_REPO"] == "org/name"
        assert env["STEPWISE_INPUT_COUNT"] == "5"

    def test_build_poll_env_with_cursor(self):
        from stepwise.scheduler import SchedulerService
        store = SQLiteStore(":memory:")
        svc = SchedulerService(store=store, project_dir="/tmp")
        sched = Schedule(
            id="s1", name="test", type=ScheduleType.POLL,
            flow_path="test", cron_expr="*/5 * * * *", poll_command="echo test",
        )
        store.save_schedule(sched)
        # Add a fired tick with output
        store.save_tick(ScheduleTick(
            id="t1", schedule_id="s1",
            scheduled_for=_now(), evaluated_at=_now(),
            outcome=TickOutcome.FIRED,
            poll_output={"last_id": 42},
        ))
        env = svc._build_poll_env(sched)
        cursor = json.loads(env["STEPWISE_POLL_CURSOR"])
        assert cursor == {"last_id": 42}

    def test_render_job_name_default(self):
        from stepwise.scheduler import SchedulerService
        store = SQLiteStore(":memory:")
        svc = SchedulerService(store=store, project_dir="/tmp")
        sched = Schedule(
            id="s1", name="my-sched", type=ScheduleType.CRON,
            flow_path="test", cron_expr="* * * * *",
        )
        assert svc._render_job_name(sched, None) == "sched: my-sched"

    def test_render_job_name_template(self):
        from stepwise.scheduler import SchedulerService
        store = SQLiteStore(":memory:")
        svc = SchedulerService(store=store, project_dir="/tmp")
        sched = Schedule(
            id="s1", name="gh", type=ScheduleType.POLL,
            flow_path="test", cron_expr="* * * * *",
            poll_command="echo test",
            job_name_template="fix-issue-{number}",
        )
        assert svc._render_job_name(sched, {"number": 42}) == "fix-issue-42"

    def test_render_job_name_template_missing_key(self):
        from stepwise.scheduler import SchedulerService
        store = SQLiteStore(":memory:")
        svc = SchedulerService(store=store, project_dir="/tmp")
        sched = Schedule(
            id="s1", name="gh", type=ScheduleType.POLL,
            flow_path="test", cron_expr="* * * * *",
            poll_command="echo test",
            job_name_template="fix-{missing}",
        )
        # Falls back to default
        assert svc._render_job_name(sched, {"number": 42}) == "sched: gh"

    def test_record_tick(self):
        from stepwise.scheduler import SchedulerService
        store = SQLiteStore(":memory:")
        svc = SchedulerService(store=store, project_dir="/tmp")
        sched = Schedule(
            id="s1", name="test", type=ScheduleType.CRON,
            flow_path="test", cron_expr="* * * * *",
        )
        store.save_schedule(sched)
        svc._record_tick(sched, _now(), TickOutcome.SKIPPED, reason="test skip")
        ticks = store.list_ticks("s1")
        assert len(ticks) == 1
        assert ticks[0].outcome == TickOutcome.SKIPPED
        assert ticks[0].reason == "test skip"

    def test_check_auto_pause(self):
        from stepwise.scheduler import SchedulerService, _ScheduleState
        store = SQLiteStore(":memory:")
        svc = SchedulerService(store=store, project_dir="/tmp")
        sched = Schedule(
            id="s1", name="test", type=ScheduleType.POLL,
            flow_path="test", cron_expr="* * * * *",
            poll_command="exit 1", max_consecutive_errors=3,
        )
        store.save_schedule(sched)
        state = _ScheduleState(schedule=sched, next_fire=_now(), consecutive_errors=3)
        svc._states["s1"] = state
        svc._check_auto_pause(sched, state)
        # Should be paused
        got = store.get_schedule("s1")
        assert got.status == ScheduleStatus.PAUSED
        # Should be removed from active states
        assert "s1" not in svc._states

    def test_earliest_due(self):
        from stepwise.scheduler import SchedulerService, _ScheduleState
        store = SQLiteStore(":memory:")
        svc = SchedulerService(store=store, project_dir="/tmp")
        now = _now()
        svc._states = {
            "a": _ScheduleState(
                schedule=MagicMock(), next_fire=now + timedelta(minutes=5)
            ),
            "b": _ScheduleState(
                schedule=MagicMock(), next_fire=now + timedelta(minutes=2)
            ),
        }
        earliest = svc._earliest_due()
        assert earliest == now + timedelta(minutes=2)

    def test_earliest_due_empty(self):
        from stepwise.scheduler import SchedulerService
        store = SQLiteStore(":memory:")
        svc = SchedulerService(store=store, project_dir="/tmp")
        assert svc._earliest_due() is None

    def test_reload_schedule_active(self):
        from stepwise.scheduler import SchedulerService
        store = SQLiteStore(":memory:")
        svc = SchedulerService(store=store, project_dir="/tmp")
        sched = Schedule(
            id="s1", name="test", type=ScheduleType.CRON,
            flow_path="test", cron_expr="*/5 * * * *", timezone="UTC",
        )
        store.save_schedule(sched)
        svc.reload_schedule("s1")
        assert "s1" in svc._states

    def test_reload_schedule_paused_removes(self):
        from stepwise.scheduler import SchedulerService
        store = SQLiteStore(":memory:")
        svc = SchedulerService(store=store, project_dir="/tmp")
        sched = Schedule(
            id="s1", name="test", type=ScheduleType.CRON,
            flow_path="test", cron_expr="*/5 * * * *",
            status=ScheduleStatus.PAUSED, timezone="UTC",
        )
        store.save_schedule(sched)
        svc._states["s1"] = MagicMock()  # pretend it was active
        svc.reload_schedule("s1")
        assert "s1" not in svc._states

    def test_reload_schedule_deleted_removes(self):
        from stepwise.scheduler import SchedulerService
        store = SQLiteStore(":memory:")
        svc = SchedulerService(store=store, project_dir="/tmp")
        svc._states["s1"] = MagicMock()
        svc.reload_schedule("s1")  # doesn't exist in store
        assert "s1" not in svc._states


# ── Cron Description Tests ────────────────────────────────────────────────


class TestCronDescription:

    def test_basic_expressions(self):
        from cron_descriptor import get_description
        assert "09:00 AM" in get_description("0 9 * * *")
        assert "5 minutes" in get_description("*/5 * * * *")
        assert "Monday" in get_description("0 9 * * MON")

    def test_complex_expression(self):
        from cron_descriptor import get_description
        desc = get_description("*/15 9-17 * * MON-FRI")
        assert "15 minutes" in desc
        assert "Monday" in desc or "Friday" in desc


# ── Integration / Async Tests ─────────────────────────────────────────────


class TestPollEvalAsync:
    """Test the async evaluate_poll_command."""

    def test_async_ready(self, tmp_path):
        from stepwise.poll_eval import evaluate_poll_command
        result = asyncio.get_event_loop().run_until_complete(
            evaluate_poll_command(
                command='echo \'{"status": "ok"}\'',
                cwd=str(tmp_path),
            )
        )
        assert result.ready is True
        assert result.output == {"status": "ok"}

    def test_async_not_ready(self, tmp_path):
        from stepwise.poll_eval import evaluate_poll_command
        result = asyncio.get_event_loop().run_until_complete(
            evaluate_poll_command(command="echo ''", cwd=str(tmp_path))
        )
        assert result.ready is False

    def test_async_error(self, tmp_path):
        from stepwise.poll_eval import evaluate_poll_command
        result = asyncio.get_event_loop().run_until_complete(
            evaluate_poll_command(command="exit 1", cwd=str(tmp_path))
        )
        assert result.ready is False
        assert result.error is not None

    def test_async_timeout(self, tmp_path):
        from stepwise.poll_eval import evaluate_poll_command
        result = asyncio.get_event_loop().run_until_complete(
            evaluate_poll_command(
                command="sleep 10",
                cwd=str(tmp_path),
                timeout_seconds=1,
            )
        )
        assert result.ready is False
        assert "timeout" in result.error.lower()

    def test_async_env_and_cursor(self, tmp_path):
        from stepwise.poll_eval import evaluate_poll_command
        # Script reads STEPWISE_POLL_CURSOR
        script = tmp_path / "check.sh"
        script.write_text('#!/bin/sh\necho "{\\\"cursor\\\": \\\"$STEPWISE_POLL_CURSOR\\\"}"')
        script.chmod(0o755)
        result = asyncio.get_event_loop().run_until_complete(
            evaluate_poll_command(
                command=str(script),
                cwd=str(tmp_path),
                env={"STEPWISE_POLL_CURSOR": "prev_value"},
            )
        )
        assert result.ready is True
        assert result.output["cursor"] == "prev_value"
