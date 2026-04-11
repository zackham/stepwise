"""Scheduler service: evaluates schedules on their cron cadence, launches jobs.

Runs as a background asyncio task inside the stepwise server process.
Manages both cron (always-fire) and poll (conditional-fire) schedules.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from croniter import croniter

from stepwise.models import (
    OverlapPolicy,
    RecoveryPolicy,
    Schedule,
    ScheduleStatus,
    ScheduleTick,
    ScheduleType,
    TickOutcome,
    _gen_id,
    _now,
)
from stepwise.poll_eval import evaluate_poll_command

if TYPE_CHECKING:
    from stepwise.store import SQLiteStore

logger = logging.getLogger("stepwise.scheduler")


@dataclass
class _ScheduleState:
    """In-memory runtime state for an active schedule."""

    schedule: Schedule
    next_fire: datetime
    consecutive_errors: int = 0


class SchedulerService:
    """Evaluates schedules on their cron cadence, launches jobs when conditions are met.

    Single-server constraint: assumes it is the sole evaluator. Running two instances
    against the same DB will cause duplicate fires.
    """

    def __init__(self, store: SQLiteStore, project_dir: str) -> None:
        self.store = store
        self.project_dir = project_dir
        self._states: dict[str, _ScheduleState] = {}
        self._eval_locks: set[str] = set()  # schedule IDs currently being evaluated
        self._task: asyncio.Task | None = None
        # Set by server after engine is available
        self._create_and_start_job: None = None  # callable set externally

    async def start(self, create_and_start_job_fn) -> None:
        """Load schedules, handle recovery, begin tick loop."""
        self._create_and_start_job = create_and_start_job_fn
        schedules = self.store.list_schedules(status="active")
        for sched in schedules:
            try:
                self._states[sched.id] = _ScheduleState(
                    schedule=sched,
                    next_fire=self._compute_next(sched),
                    consecutive_errors=self.store.consecutive_errors(sched.id),
                )
            except Exception:
                logger.warning("Failed to compute next fire for schedule %s", sched.name, exc_info=True)
        logger.info("Scheduler started with %d active schedule(s)", len(self._states))
        await self._handle_recovery()
        self._task = asyncio.create_task(self._tick_loop(), name="scheduler-tick-loop")

    async def stop(self) -> None:
        """Stop the tick loop."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Scheduler stopped")

    def reload_schedule(self, schedule_id: str) -> None:
        """Reload a schedule from the store (after create/update/delete)."""
        sched = self.store.get_schedule(schedule_id)
        if sched is None or sched.status != ScheduleStatus.ACTIVE:
            self._states.pop(schedule_id, None)
            return
        try:
            self._states[schedule_id] = _ScheduleState(
                schedule=sched,
                next_fire=self._compute_next(sched),
                consecutive_errors=self.store.consecutive_errors(schedule_id),
            )
        except Exception:
            logger.warning("Failed to reload schedule %s", schedule_id, exc_info=True)

    # ── Tick Loop ─────────────────────────────────────────────────────────

    async def _tick_loop(self) -> None:
        """Main loop: sleep until next due schedule, evaluate, repeat."""
        last_prune = _now()
        while True:
            try:
                next_time = self._earliest_due()
                if next_time is None:
                    await asyncio.sleep(60)
                    continue
                sleep_for = max(0, (next_time - _now()).total_seconds())
                # Wake at least every 60s to pick up newly added schedules
                await asyncio.sleep(min(sleep_for, 60))
                await self._evaluate_due()

                # Daily tick pruning (runs once per 24h cycle)
                if (_now() - last_prune).total_seconds() > 86400:
                    await self.prune_old_ticks()
                    last_prune = _now()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("Scheduler tick loop error", exc_info=True)
                await asyncio.sleep(5)

    async def _evaluate_due(self) -> None:
        """Process all schedules past their next_fire time."""
        now = _now()
        for state in list(self._states.values()):
            if state.next_fire <= now:
                try:
                    await self._evaluate_one(state)
                except Exception:
                    logger.error(
                        "Error evaluating schedule %s", state.schedule.name, exc_info=True
                    )
                state.next_fire = self._compute_next(state.schedule)

    async def _evaluate_one(self, state: _ScheduleState) -> None:
        """Single tick evaluation with all gates."""
        sched = state.schedule
        scheduled_for = state.next_fire

        # Gate 1: eval overlap (previous poll still running for this schedule)
        if sched.id in self._eval_locks:
            self._record_tick(sched, scheduled_for, TickOutcome.SKIPPED, reason="previous evaluation still running")
            return

        # Gate 2: cooldown (poll type only)
        if sched.type == ScheduleType.POLL and sched.cooldown_seconds and sched.last_fired_at:
            cooldown_until = sched.last_fired_at + timedelta(seconds=sched.cooldown_seconds)
            if _now() < cooldown_until:
                self._record_tick(sched, scheduled_for, TickOutcome.COOLDOWN_SKIPPED)
                return

        # Gate 3: poll evaluation (poll type only)
        poll_output = None
        duration_ms = None
        if sched.type == ScheduleType.POLL:
            self._eval_locks.add(sched.id)
            try:
                result = await evaluate_poll_command(
                    command=sched.poll_command,
                    cwd=self.project_dir,
                    env=self._build_poll_env(sched),
                    timeout_seconds=sched.poll_timeout_seconds,
                )
            finally:
                self._eval_locks.discard(sched.id)

            duration_ms = result.duration_ms

            if result.error:
                self._record_tick(
                    sched, scheduled_for, TickOutcome.ERROR,
                    reason=result.error, duration_ms=duration_ms,
                )
                state.consecutive_errors += 1
                self._check_auto_pause(sched, state)
                return

            if not result.ready:
                self._record_tick(sched, scheduled_for, TickOutcome.SKIPPED, duration_ms=duration_ms)
                state.consecutive_errors = 0  # reset on successful eval (even if not ready)
                return

            poll_output = result.output
            state.consecutive_errors = 0

        # Gate 4: overlap check
        if sched.overlap_policy != OverlapPolicy.ALLOW:
            running_job_id = self._find_running_job(sched)
            if running_job_id:
                if sched.overlap_policy == OverlapPolicy.SKIP:
                    self._record_tick(
                        sched, scheduled_for, TickOutcome.OVERLAP_SKIPPED,
                        duration_ms=duration_ms,
                    )
                    return
                elif sched.overlap_policy == OverlapPolicy.QUEUE:
                    depth = self.store.schedule_queue_depth(sched.id)
                    if depth >= 5:  # max queue depth
                        self._record_tick(
                            sched, scheduled_for, TickOutcome.OVERLAP_SKIPPED,
                            reason="queue full", duration_ms=duration_ms,
                        )
                        return
                    # Fire but stage the job (handled in _fire)
                    await self._fire(sched, scheduled_for, poll_output, duration_ms, staged=True)
                    return

        # Fire!
        await self._fire(sched, scheduled_for, poll_output, duration_ms, staged=False)

    # ── Job Launch ────────────────────────────────────────────────────────

    async def _fire(
        self,
        sched: Schedule,
        scheduled_for: datetime,
        poll_output: dict | None,
        duration_ms: int | None,
        staged: bool = False,
    ) -> None:
        """Create job + record tick atomically."""
        tick_id = _gen_id("tick")
        now = _now()

        # Merge inputs: static job_inputs + poll output (poll wins on collision)
        inputs = {**sched.job_inputs}
        if poll_output:
            inputs.update(poll_output)

        # Render job name
        job_name = self._render_job_name(sched, poll_output)

        metadata = {
            "sys": {
                "schedule_id": sched.id,
                "schedule_name": sched.name,
                "tick_id": tick_id,
            }
        }

        try:
            job_id = await self._create_and_start_job(
                flow_path=sched.flow_path,
                inputs=inputs,
                name=job_name,
                metadata=metadata,
                staged=staged,
            )
        except Exception as e:
            logger.error("Failed to create job for schedule %s: %s", sched.name, e)
            self._record_tick(
                sched, scheduled_for, TickOutcome.ERROR,
                reason=f"job creation failed: {e}", duration_ms=duration_ms,
            )
            return

        # Record tick
        tick = ScheduleTick(
            id=tick_id,
            schedule_id=sched.id,
            scheduled_for=scheduled_for,
            evaluated_at=now,
            outcome=TickOutcome.FIRED,
            poll_output=poll_output,
            job_id=job_id,
            duration_ms=duration_ms,
        )
        self.store.save_tick(tick)

        # Update schedule state
        self.store.update_schedule(sched.id, last_fired_at=now)
        sched.last_fired_at = now

        # If staged (queue policy), register in queue
        if staged:
            self.store.enqueue_schedule_job(sched.id, job_id)

        logger.info(
            "Schedule %s fired → job %s%s",
            sched.name, job_id, " (queued)" if staged else "",
        )

    def _render_job_name(self, sched: Schedule, poll_output: dict | None) -> str:
        """Render job name from template or generate default."""
        if sched.job_name_template and poll_output:
            try:
                return sched.job_name_template.format(**poll_output)
            except (KeyError, ValueError):
                pass
        return f"sched: {sched.name}"

    # ── Queue Completion Hook ─────────────────────────────────────────────

    async def on_job_completed(self, job_id: str) -> None:
        """Called when any job completes. Checks if a queued schedule job should start."""
        # Check if this job was launched by a schedule with queue policy
        job = self.store.get_job(job_id) if hasattr(self.store, 'get_job') else None
        if not job:
            return
        meta = job.metadata if hasattr(job, 'metadata') and isinstance(job.metadata, dict) else {}
        sys_meta = meta.get("sys", {})
        schedule_id = sys_meta.get("schedule_id")
        if not schedule_id:
            return

        sched = self.store.get_schedule(schedule_id)
        if not sched or sched.overlap_policy != OverlapPolicy.QUEUE:
            return

        # Pop next queued job
        next_job_id = self.store.dequeue_schedule_job(schedule_id)
        if next_job_id:
            try:
                await self._create_and_start_job(
                    job_id=next_job_id,
                    start_only=True,
                )
            except Exception:
                logger.error("Failed to start queued job %s for schedule %s", next_job_id, sched.name, exc_info=True)

    # ── Recovery ──────────────────────────────────────────────────────────

    async def _handle_recovery(self) -> None:
        """On startup, handle missed ticks per recovery policy."""
        for state in list(self._states.values()):
            sched = state.schedule
            if sched.recovery_policy != RecoveryPolicy.CATCH_UP_ONCE:
                continue

            # Check if we missed any ticks
            last_tick = self.store.list_ticks(sched.id, limit=1)
            if not last_tick:
                continue  # never ran, not a recovery situation

            last_eval = last_tick[0].evaluated_at
            # If more than one interval has passed since last eval, we missed ticks
            try:
                cron = croniter(sched.cron_expr, last_eval)
                next_expected = cron.get_next(datetime)
                if next_expected.tzinfo is None:
                    next_expected = next_expected.replace(tzinfo=timezone.utc)
            except Exception:
                continue

            if next_expected < _now():
                logger.info("Recovery: catch_up_once for schedule %s (missed since %s)", sched.name, last_eval)
                # Evaluate once immediately
                state.next_fire = _now() - timedelta(seconds=1)

    # ── Helpers ───────────────────────────────────────────────────────────

    def _compute_next(self, sched: Schedule) -> datetime:
        """Compute next fire time from cron expression."""
        try:
            import zoneinfo
            tz = zoneinfo.ZoneInfo(sched.timezone)
        except Exception:
            tz = timezone.utc

        now_local = datetime.now(tz)
        cron = croniter(sched.cron_expr, now_local)
        next_dt = cron.get_next(datetime)
        # Convert to UTC for internal comparison
        if next_dt.tzinfo is None:
            next_dt = next_dt.replace(tzinfo=tz)
        return next_dt.astimezone(timezone.utc)

    def _earliest_due(self) -> datetime | None:
        """Find the earliest next_fire across all active schedules."""
        if not self._states:
            return None
        return min(s.next_fire for s in self._states.values())

    def _build_poll_env(self, sched: Schedule) -> dict[str, str]:
        """Build environment variables for a poll command."""
        env: dict[str, str] = {
            "STEPWISE_SCHEDULE_ID": sched.id,
            "STEPWISE_SCHEDULE_NAME": sched.name,
            "STEPWISE_PROJECT_DIR": self.project_dir,
        }
        # Cursor from last fired tick
        last_fired = self.store.last_fired_tick(sched.id)
        if last_fired and last_fired.poll_output:
            env["STEPWISE_POLL_CURSOR"] = json.dumps(last_fired.poll_output)
        else:
            env["STEPWISE_POLL_CURSOR"] = ""

        # Static job inputs as STEPWISE_INPUT_* vars
        for key, val in sched.job_inputs.items():
            env_key = f"STEPWISE_INPUT_{key.upper()}"
            env[env_key] = str(val) if not isinstance(val, str) else val

        return env

    def _find_running_job(self, sched: Schedule) -> str | None:
        """Check if the most recent job launched by this schedule is still running."""
        last_fired = self.store.last_fired_tick(sched.id)
        if not last_fired or not last_fired.job_id:
            return None
        job = self.store.get_job(last_fired.job_id) if hasattr(self.store, 'get_job') else None
        if job and job.status.value in ("running", "pending", "paused"):
            return job.id
        return None

    def _record_tick(
        self,
        sched: Schedule,
        scheduled_for: datetime,
        outcome: TickOutcome,
        reason: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        """Record a tick to the store."""
        tick = ScheduleTick(
            id=_gen_id("tick"),
            schedule_id=sched.id,
            scheduled_for=scheduled_for,
            evaluated_at=_now(),
            outcome=outcome,
            reason=reason,
            duration_ms=duration_ms,
        )
        self.store.save_tick(tick)

    def _check_auto_pause(self, sched: Schedule, state: _ScheduleState) -> None:
        """Auto-pause schedule after too many consecutive errors."""
        if state.consecutive_errors >= sched.max_consecutive_errors:
            logger.warning(
                "Auto-pausing schedule %s after %d consecutive errors",
                sched.name, state.consecutive_errors,
            )
            self.store.update_schedule(
                sched.id,
                status="paused",
                paused_at=_now(),
            )
            sched.status = ScheduleStatus.PAUSED
            self._states.pop(sched.id, None)

    # ── Tick Pruning ──────────────────────────────────────────────────────

    async def prune_old_ticks(self) -> int:
        """Prune old skip/cooldown ticks across all schedules. Returns count deleted."""
        total = 0
        for sched in self.store.list_schedules():
            total += self.store.prune_ticks(sched.id)
        if total:
            logger.info("Pruned %d old ticks", total)
        return total
