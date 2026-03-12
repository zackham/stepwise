"""Detect whether a Stepwise server is running for the current project.

Checks `.stepwise/server.pid` and probes the health endpoint.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def detect_server(project_dir: Path | None = None) -> str | None:
    """Check if a Stepwise server is running and reachable.

    Args:
        project_dir: The .stepwise/ directory. If None, tries to find it.

    Returns:
        Server URL (e.g., "http://localhost:8765") if server is running, None otherwise.
    """
    if project_dir is None:
        return None

    pid_file = project_dir / "server.pid"
    if not pid_file.exists():
        return None

    try:
        data = json.loads(pid_file.read_text())
        pid = data.get("pid")
        port = data.get("port", 8765)
        url = data.get("url", f"http://localhost:{port}")
    except (json.JSONDecodeError, KeyError):
        return None

    # Check if process is alive
    if pid and not _pid_alive(pid):
        # Stale pidfile — clean up
        try:
            pid_file.unlink()
        except OSError:
            pass
        return None

    # Probe health endpoint
    if _probe_health(url):
        return url

    return None


def write_pidfile(project_dir: Path, port: int) -> Path:
    """Write server.pid with current process info.

    Returns path to the pidfile.
    """
    pid_file = project_dir / "server.pid"
    data = {
        "pid": os.getpid(),
        "port": port,
        "url": f"http://localhost:{port}",
    }
    pid_file.write_text(json.dumps(data))
    return pid_file


def remove_pidfile(project_dir: Path) -> None:
    """Remove server.pid on clean shutdown."""
    pid_file = project_dir / "server.pid"
    try:
        pid_file.unlink(missing_ok=True)
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    """Check if a process with given PID is alive."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _probe_health(url: str, timeout: float = 2.0) -> bool:
    """Probe the server health endpoint."""
    try:
        import urllib.request
        req = urllib.request.Request(f"{url}/api/health", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                data = json.loads(resp.read())
                return data.get("status") == "ok"
    except Exception:
        pass
    return False
