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
        import logging
        logging.getLogger("stepwise.server_detect").warning(
            "Stale server.pid (PID %d dead). Previous server crashed — cleaning up.", pid
        )
        try:
            pid_file.unlink()
        except OSError:
            pass
        return None

    # Probe health endpoint
    if _probe_health(url):
        return url

    return None


def write_pidfile(
    project_dir: Path,
    port: int,
    *,
    pid: int | None = None,
    log_file: str | None = None,
) -> Path:
    """Write server.pid with current process info.

    Returns path to the pidfile.
    """
    from datetime import datetime, timezone

    pid_file = project_dir / "server.pid"
    data = {
        "pid": pid or os.getpid(),
        "port": port,
        "url": f"http://localhost:{port}",
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    if log_file:
        data["log_file"] = log_file
    pid_file.write_text(json.dumps(data))
    return pid_file


def read_pidfile(project_dir: Path) -> dict:
    """Read server.pid and return its contents as a dict.

    Returns {} if the file is missing, unreadable, or corrupt.
    """
    pid_file = project_dir / "server.pid"
    if not pid_file.exists():
        return {}
    try:
        return json.loads(pid_file.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


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


# ── Global server registry ────────────────────────────────────────────

GLOBAL_REGISTRY = Path.home() / ".config" / "stepwise" / "servers.json"


def register_server(project_path: str, pid: int, port: int, url: str) -> None:
    """Register a server in the global registry (atomic write)."""
    servers = _load_registry()
    servers[project_path] = {
        "project_path": project_path,
        "pid": pid,
        "port": port,
        "url": url,
        "started_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(),
    }
    _save_registry(servers)


def unregister_server(project_path: str) -> None:
    """Remove a server from the global registry."""
    servers = _load_registry()
    if project_path in servers:
        del servers[project_path]
        _save_registry(servers)


def list_active_servers() -> list[dict]:
    """Return all registered servers, pruning dead ones."""
    servers = _load_registry()
    alive = {}
    changed = False
    for key, entry in servers.items():
        pid = entry.get("pid")
        if pid and _pid_alive(pid):
            alive[key] = entry
        else:
            changed = True
    if changed:
        _save_registry(alive)
    return list(alive.values())


def detect_any_server() -> list[dict]:
    """Return all live servers across all projects (convenience alias)."""
    return list_active_servers()


def verify_server_identity(url: str, expected_project: Path) -> bool:
    """Check if the server at url belongs to the expected project.

    GETs /api/health and compares the project_path field.
    Returns True if it matches or if the server doesn't report a project path.
    """
    try:
        import urllib.request
        req = urllib.request.Request(f"{url}/api/health", method="GET")
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            if resp.status == 200:
                data = json.loads(resp.read())
                server_path = data.get("project_path")
                if server_path is None:
                    return True  # old server, no project_path field
                return str(Path(server_path).resolve()) == str(expected_project.resolve())
    except Exception:
        pass
    return False


def _load_registry() -> dict[str, dict]:
    """Load the global server registry."""
    if not GLOBAL_REGISTRY.exists():
        return {}
    try:
        return json.loads(GLOBAL_REGISTRY.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_registry(servers: dict[str, dict]) -> None:
    """Atomic write of the global server registry."""
    import tempfile
    GLOBAL_REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    # Write to temp file then rename for atomicity
    fd, tmp_path = tempfile.mkstemp(
        dir=str(GLOBAL_REGISTRY.parent), suffix=".tmp"
    )
    closed = False
    try:
        os.write(fd, json.dumps(servers, indent=2).encode())
        os.close(fd)
        closed = True
        os.rename(tmp_path, str(GLOBAL_REGISTRY))
    except Exception:
        if not closed:
            os.close(fd)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
