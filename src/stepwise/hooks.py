"""Project hooks: fire-and-forget shell scripts triggered by engine events.

Hook scripts live in `.stepwise/hooks/` and are named `on-{event}`.
They receive a JSON payload on stdin with event details.

Events:
  - suspend: A step has been suspended (awaiting human input)
  - step-complete: A step has completed successfully
  - complete: A job has completed successfully
  - fail: A job or step has failed
"""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

HOOKS_DIR_NAME = "hooks"
LOGS_DIR_NAME = "logs"
HOOK_LOG_FILE = "hooks.log"

# Map engine event types to hook event names
EVENT_MAP = {
    "step.suspended": "suspend",
    "step.completed": "step-complete",
    "job.completed": "complete",
    "job.failed": "fail",
    "step.failed": "fail",
}


def fire_hook(
    event_name: str,
    payload: dict,
    project_dir: Path,
) -> bool:
    """Fire a project hook script if it exists.

    Args:
        event_name: Hook event name (suspend, complete, fail).
        payload: JSON-serializable dict piped to stdin.
        project_dir: The .stepwise/ directory.

    Returns:
        True if hook was found and launched, False if no hook exists.
    """
    hooks_dir = project_dir / HOOKS_DIR_NAME
    script = hooks_dir / f"on-{event_name}"

    if not script.exists():
        return False

    if not script.is_file():
        logger.warning("Hook %s exists but is not a file", script)
        return False

    payload_json = json.dumps(payload, default=str)

    try:
        # Fire-and-forget: don't block the engine
        proc = subprocess.Popen(
            [str(script)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(project_dir.parent),  # project root
        )
        # Send payload and close stdin, but don't wait
        # Use communicate with a short timeout to avoid blocking
        try:
            stdout, stderr = proc.communicate(
                input=payload_json.encode(), timeout=30
            )
            if proc.returncode != 0:
                _log_hook_failure(
                    project_dir, event_name, proc.returncode,
                    stderr.decode(errors="replace"),
                )
            return True
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            _log_hook_failure(
                project_dir, event_name, -1, "Hook timed out after 30s"
            )
            return True

    except OSError as e:
        _log_hook_failure(project_dir, event_name, -1, str(e))
        return False


def fire_hook_for_event(
    event_type: str,
    event_data: dict,
    job_id: str,
    project_dir: Path | None,
) -> bool:
    """Map an engine event type to a hook and fire it.

    Args:
        event_type: Engine event constant (e.g. "step.suspended").
        event_data: The event's data dict from the engine.
        job_id: The job ID associated with the event.
        project_dir: The .stepwise/ directory. If None, no-op.

    Returns:
        True if a hook was fired.
    """
    if project_dir is None:
        return False

    hook_name = EVENT_MAP.get(event_type)
    if hook_name is None:
        return False

    payload = {
        "event": event_type,
        "hook": hook_name,
        "job_id": job_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **event_data,
    }

    # Add fulfill_command for suspend events
    if hook_name == "suspend" and "run_id" in event_data:
        payload["fulfill_command"] = (
            f"stepwise fulfill {event_data['run_id']} '<json>'"
        )

    return fire_hook(hook_name, payload, project_dir)


def fire_notify_webhook(
    event_type: str,
    event_data: dict,
    job_id: str,
    notify_url: str,
    notify_context: dict | None = None,
) -> None:
    """Fire-and-forget HTTP POST to a webhook URL with event data.

    Args:
        event_type: Engine event constant (e.g. "job.completed").
        event_data: The event's data dict from the engine.
        job_id: The job ID associated with the event.
        notify_url: HTTP(S) URL to POST to.
        notify_context: Optional context dict merged into payload.
    """
    import threading

    payload = {
        "event": event_type,
        "job_id": job_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **event_data,
    }
    if notify_context:
        payload["context"] = notify_context

    def _send() -> None:
        try:
            import httpx
            httpx.post(notify_url, json=payload, timeout=10)
        except Exception:
            logger.warning("Webhook notification failed for %s → %s", event_type, notify_url, exc_info=True)

    threading.Thread(target=_send, daemon=True).start()


def _log_hook_failure(
    project_dir: Path,
    event_name: str,
    exit_code: int,
    stderr: str,
) -> None:
    """Append hook failure to .stepwise/logs/hooks.log."""
    logs_dir = project_dir / LOGS_DIR_NAME
    logs_dir.mkdir(exist_ok=True)
    log_file = logs_dir / HOOK_LOG_FILE

    timestamp = datetime.now(timezone.utc).isoformat()
    entry = (
        f"[{timestamp}] on-{event_name} exit={exit_code}\n"
        f"  stderr: {stderr.strip()}\n"
    )

    try:
        with open(log_file, "a") as f:
            f.write(entry)
    except OSError:
        logger.warning("Could not write hook failure log to %s", log_file)


# ── Hook scaffolding for `stepwise init` ──────────────────────────────


HOOK_TEMPLATES = {
    "on-suspend": """\
#!/bin/sh
# Stepwise hook: fired when a step is suspended (awaiting human input).
# Receives JSON payload on stdin with: event, hook, job_id, step, run_id,
# watch_mode, fulfill_command, timestamp.
#
# Examples:
#   # Send a Slack notification
#   payload=$(cat)
#   step=$(echo "$payload" | jq -r '.step')
#   cmd=$(echo "$payload" | jq -r '.fulfill_command')
#   curl -s -X POST "$SLACK_WEBHOOK" -d "{\\"text\\":\\"Step '$step' needs input: $cmd\\"}"
#
#   # Write to a file for polling
#   echo "$payload" >> .stepwise/pending-reviews.jsonl

# Uncomment to enable:
# cat  # read stdin payload
""",
    "on-step-complete": """\
#!/bin/sh
# Stepwise hook: fired when a step completes successfully.
# Receives JSON payload on stdin with: event, hook, job_id, step, run_id, timestamp.
#
# Examples:
#   # Log step completions
#   payload=$(cat)
#   step=$(echo "$payload" | jq -r '.step')
#   echo "Step $step completed at $(date)" >> /tmp/stepwise-steps.log

# Uncomment to enable:
# cat  # read stdin payload
""",
    "on-complete": """\
#!/bin/sh
# Stepwise hook: fired when a job completes successfully.
# Receives JSON payload on stdin with: event, hook, job_id, timestamp.
#
# Examples:
#   # Log completion
#   payload=$(cat)
#   job_id=$(echo "$payload" | jq -r '.job_id')
#   echo "Job $job_id completed at $(date)" >> /tmp/stepwise-completions.log

# Uncomment to enable:
# cat  # read stdin payload
""",
    "on-fail": """\
#!/bin/sh
# Stepwise hook: fired when a job or step fails.
# Receives JSON payload on stdin with: event, hook, job_id, step (if step failure),
# error (if available), timestamp.
#
# Examples:
#   # Alert on failure
#   payload=$(cat)
#   echo "$payload" | jq .

# Uncomment to enable:
# cat  # read stdin payload
""",
}


def scaffold_hooks(project_dir: Path) -> list[Path]:
    """Create example hook scripts in .stepwise/hooks/.

    Returns list of created paths.
    """
    hooks_dir = project_dir / HOOKS_DIR_NAME
    hooks_dir.mkdir(exist_ok=True)

    created = []
    for name, content in HOOK_TEMPLATES.items():
        path = hooks_dir / name
        if not path.exists():
            path.write_text(content)
            path.chmod(0o755)
            created.append(path)

    return created
