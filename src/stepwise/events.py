"""Event type constants for Stepwise."""

# Step lifecycle
STEP_STARTED = "step.started"
STEP_COMPLETED = "step.completed"
STEP_FAILED = "step.failed"
STEP_SUSPENDED = "step.suspended"
STEP_DELEGATED = "step.delegated"

# Job lifecycle
JOB_STARTED = "job.started"
JOB_COMPLETED = "job.completed"
JOB_FAILED = "job.failed"
JOB_PAUSED = "job.paused"
JOB_RESUMED = "job.resumed"

# Engine actions
EXIT_RESOLVED = "exit.resolved"
WATCH_FULFILLED = "watch.fulfilled"
HUMAN_RERUN = "human.rerun"
LOOP_ITERATION = "loop.iteration"
LOOP_MAX_REACHED = "loop.max_reached"
CONTEXT_INJECTED = "context.injected"

# M4: Async executor events
STEP_STARTED_ASYNC = "step.started_async"
STEP_LIMIT_EXCEEDED = "step.limit_exceeded"
STEP_CANCELLED = "step.cancelled"
