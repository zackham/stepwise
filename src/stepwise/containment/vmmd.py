"""vmmd — VM Manager Daemon for stepwise containment.

Privileged daemon that manages cloud-hypervisor VM lifecycle.
Runs as root (via sudo), listens on a Unix socket for control
commands from the unprivileged stepwise process.

The data path (vsock for ACP stdio) goes directly from the user
process to the guest — vmmd only handles the control plane.

Usage:
  sudo stepwise vmmd start         # foreground
  sudo stepwise vmmd start --detach  # background
  stepwise vmmd stop
  stepwise vmmd status
"""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("stepwise.vmmd")

def _default_vmm_dir() -> Path:
    """Resolve VMM dir from SUDO_USER's home (not root's) when running under sudo."""
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        import pwd
        try:
            home = Path(pwd.getpwnam(sudo_user).pw_dir)
            return home / ".stepwise" / "vmm"
        except KeyError:
            pass
    return Path.home() / ".stepwise" / "vmm"
GUEST_AGENT_PORT = 9999
SOCKET_NAME = "vmmd.sock"
PID_NAME = "vmmd.pid"


# ── VM data types ─────────────────────────────────────────────────


@dataclass
class VirtiofsMount:
    tag: str
    host_path: str
    socket_path: str
    process: subprocess.Popen | None = None


@dataclass
class MicroVM:
    vm_id: str
    vm_dir: str
    vsock_socket: str
    api_socket: str
    cid: int
    ch_process: subprocess.Popen | None = None
    virtiofs_mounts: list[VirtiofsMount] = field(default_factory=list)
    config: dict = field(default_factory=dict)
    boot_time: float = 0.0

    def to_info(self) -> dict:
        return {
            "vm_id": self.vm_id,
            "cid": self.cid,
            "vsock_socket": self.vsock_socket,
            "config": self.config,
            "uptime_seconds": round(time.monotonic() - self.boot_time, 1),
        }


# ── CID allocation ───────────────────────────────────────────────

_next_cid = 3
_cid_lock = threading.Lock()


def _allocate_cid() -> int:
    global _next_cid
    with _cid_lock:
        cid = _next_cid
        _next_cid += 1
    return cid


# ── Config equality for VM reuse ─────────────────────────────────


def _vm_config_eq(a: dict, b: dict) -> bool:
    """Two configs share a VM if tools, paths, creds, and network match."""
    return (
        a.get("tools") == b.get("tools")
        and a.get("allowed_paths") == b.get("allowed_paths")
        and a.get("credentials") == b.get("credentials")
        and a.get("network") == b.get("network")
    )


# ── VM Manager Daemon ────────────────────────────────────────────


class VMManagerDaemon:
    """Manages cloud-hypervisor VM lifecycle via a Unix socket API."""

    def __init__(
        self,
        work_dir: Path | None = None,
        kernel_path: Path | None = None,
        rootfs_path: Path | None = None,
        virtiofsd_path: str = "/usr/lib/virtiofsd",
        socket_owner_uid: int | None = None,
    ):
        self.work_dir = Path(work_dir or _default_vmm_dir())
        self.kernel_path = Path(kernel_path or self.work_dir / "vmlinux-x86_64")
        self.rootfs_path = Path(rootfs_path or self.work_dir / "rootfs.ext4")
        self.virtiofsd_path = virtiofsd_path
        self.socket_owner_uid = socket_owner_uid
        self._vms: dict[str, MicroVM] = {}
        self._lock = threading.Lock()
        self._running = False
        self._start_time = time.monotonic()
        self._server_socket: socket.socket | None = None

    @property
    def socket_path(self) -> Path:
        return self.work_dir / SOCKET_NAME

    @property
    def pid_path(self) -> Path:
        return self.work_dir / PID_NAME

    # ── VM lifecycle ──────────────────────────────────────────

    def boot_vm(self, config: dict) -> dict:
        """Boot a new VM or reuse an existing one with matching config."""
        with self._lock:
            # Check for reusable VM
            for vm in self._vms.values():
                if _vm_config_eq(vm.config, config):
                    logger.info("Reusing VM %s for config", vm.vm_id)
                    return {
                        "vm_id": vm.vm_id,
                        "vsock_socket": vm.vsock_socket,
                        "cid": vm.cid,
                        "reused": True,
                    }

            # Boot new VM
            vm = self._create_vm(config)
            self._vms[vm.vm_id] = vm
            return {
                "vm_id": vm.vm_id,
                "vsock_socket": vm.vsock_socket,
                "cid": vm.cid,
                "reused": False,
            }

    def _create_vm(self, config: dict) -> MicroVM:
        """Create and boot a new microVM."""
        import uuid as uuid_mod

        vm_id = f"sw-{uuid_mod.uuid4().hex[:8]}"
        vm_dir = self.work_dir / vm_id
        vm_dir.mkdir(parents=True, exist_ok=True)

        api_socket = str(vm_dir / "api.sock")
        vsock_socket = str(vm_dir / "vsock.sock")
        serial_log = str(vm_dir / "serial.log")
        cid = _allocate_cid()

        logger.info("Booting VM %s (cid=%d, working_dir=%s)",
                     vm_id, cid, config.get("working_dir", "."))

        # Copy rootfs
        rootfs_copy = str(vm_dir / "rootfs.ext4")
        subprocess.run(
            ["cp", "--reflink=auto", str(self.rootfs_path), rootfs_copy],
            check=True, capture_output=True,
        )

        # Start virtiofsd for workspace
        virtiofs_mounts = []
        working_dir = config.get("working_dir")
        if working_dir and working_dir != ".":
            mount = self._start_virtiofsd(vm_dir, "workspace", working_dir)
            virtiofs_mounts.append(mount)

        # Build cloud-hypervisor CLI command
        ch_cmd = [
            "cloud-hypervisor",
            "--api-socket", f"path={api_socket}",
            "--kernel", str(self.kernel_path),
            "--cmdline", "console=ttyS0 root=/dev/vda ro quiet init=/init.sh",
            "--disk", f"path={rootfs_copy}",
            "--vsock", f"cid={cid},socket={vsock_socket}",
            "--memory", f"size={config.get('memory_mb', 512)}M,shared=on",
            "--cpus", f"boot={config.get('cpus', 2)}",
            "--serial", f"file={serial_log}",
            "--console", "off",
        ]
        for mount in virtiofs_mounts:
            ch_cmd.extend([
                "--fs",
                f"tag={mount.tag},socket={mount.socket_path},"
                f"num_queues=1,queue_size=1024",
            ])

        ch_proc = subprocess.Popen(
            ch_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )

        vm = MicroVM(
            vm_id=vm_id,
            vm_dir=str(vm_dir),
            vsock_socket=vsock_socket,
            api_socket=api_socket,
            cid=cid,
            ch_process=ch_proc,
            virtiofs_mounts=virtiofs_mounts,
            config=config,
            boot_time=time.monotonic(),
        )

        # Wait for vsock socket and make it accessible to unprivileged user
        self._wait_for_socket(vsock_socket, timeout=10.0)
        self._make_accessible(vsock_socket)
        self._make_accessible(api_socket)

        # Wait for guest agent
        self._wait_for_guest(vsock_socket, timeout=30.0)

        elapsed = time.monotonic() - vm.boot_time
        logger.info("VM %s booted and ready in %.1fs", vm_id, elapsed)
        return vm

    def _start_virtiofsd(
        self, vm_dir: Path, tag: str, host_path: str,
    ) -> VirtiofsMount:
        """Start a virtiofsd process for a directory share."""
        socket_path = str(vm_dir / f"virtiofs-{tag}.sock")

        proc = subprocess.Popen(
            [
                self.virtiofsd_path,
                f"--socket-path={socket_path}",
                f"--shared-dir={host_path}",
                "--cache=auto",
                "--sandbox=chroot",
                "--log-level=error",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )

        self._wait_for_socket(socket_path, timeout=5.0)
        self._make_accessible(socket_path)

        return VirtiofsMount(
            tag=tag, host_path=host_path,
            socket_path=socket_path, process=proc,
        )

    def _wait_for_socket(self, path: str, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if Path(path).exists():
                return
            time.sleep(0.1)
        raise RuntimeError(f"Socket not ready after {timeout}s: {path}")

    def _make_accessible(self, path: str) -> None:
        """Make a socket accessible to the unprivileged user."""
        try:
            os.chmod(path, 0o666)
        except OSError:
            pass
        # Also set ownership if we know the calling user
        if self.socket_owner_uid is not None:
            try:
                os.chown(path, self.socket_owner_uid, -1)
            except OSError:
                pass

    def _wait_for_guest(self, vsock_socket: str, timeout: float = 30.0) -> None:
        """Wait for guest agent to respond on vsock."""
        logger.info("Waiting for guest agent on %s (timeout=%ds)", vsock_socket, timeout)
        deadline = time.monotonic() + timeout
        last_err = None
        attempt = 0
        while time.monotonic() < deadline:
            attempt += 1
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.settimeout(3)
                s.connect(vsock_socket)
                s.sendall(b"CONNECT 9999\n")
                r = b""
                while b"\n" not in r:
                    chunk = s.recv(256)
                    if not chunk:
                        break
                    r += chunk
                if r.startswith(b"OK"):
                    s.sendall(b'{"ping": true}\n')
                    p = b""
                    while b"\n" not in p:
                        chunk = s.recv(256)
                        if not chunk:
                            break
                        p += chunk
                    if b"pong" in p:
                        s.close()
                        logger.info("Guest agent ready after %d attempts (%.1fs)",
                                    attempt, time.monotonic() - (deadline - timeout))
                        return
                s.close()
            except Exception as e:
                last_err = e
                if attempt <= 3 or attempt % 10 == 0:
                    logger.debug("Guest probe attempt %d: %s", attempt, e)
            time.sleep(0.5)
        raise RuntimeError(f"Guest agent not ready after {timeout}s (attempts={attempt}): {last_err}")

    def destroy_vm(self, vm_id: str) -> dict:
        """Destroy a specific VM."""
        with self._lock:
            vm = self._vms.pop(vm_id, None)
        if not vm:
            return {"destroyed": False, "error": f"VM {vm_id} not found"}

        self._cleanup_vm(vm)
        return {"destroyed": True, "vm_id": vm_id}

    def destroy_all(self) -> dict:
        """Destroy all VMs."""
        with self._lock:
            vms = list(self._vms.values())
            self._vms.clear()

        for vm in vms:
            try:
                self._cleanup_vm(vm)
            except Exception:
                logger.debug("Cleanup error for VM %s", vm.vm_id, exc_info=True)

        return {"destroyed_count": len(vms)}

    def _cleanup_vm(self, vm: MicroVM) -> None:
        """Shut down and clean up a VM."""
        logger.info("Destroying VM %s", vm.vm_id)

        # Kill cloud-hypervisor
        if vm.ch_process:
            try:
                pgid = os.getpgid(vm.ch_process.pid)
                os.killpg(pgid, signal.SIGTERM)
                vm.ch_process.wait(timeout=5)
            except Exception:
                try:
                    os.killpg(os.getpgid(vm.ch_process.pid), signal.SIGKILL)
                except Exception:
                    pass

        # Kill virtiofsd processes
        for mount in vm.virtiofs_mounts:
            if mount.process:
                try:
                    mount.process.terminate()
                    mount.process.wait(timeout=3)
                except Exception:
                    try:
                        mount.process.kill()
                    except Exception:
                        pass

        # Reap the per-VM workspace dir (rootfs copy + sockets + serial log).
        # Without this every booted VM leaks ~the size of the rootfs (≈360MB
        # on a non-reflink FS) and a handful of stale Unix sockets. Best-
        # effort: a missing dir or a permissions issue shouldn't break
        # destroy_vm.
        import shutil
        try:
            shutil.rmtree(vm.vm_dir, ignore_errors=True)
        except Exception:
            logger.debug("Failed to remove vm_dir %s", vm.vm_dir, exc_info=True)

    def list_vms(self) -> dict:
        with self._lock:
            return {"vms": [vm.to_info() for vm in self._vms.values()]}

    def clean_orphans(self) -> dict:
        """Reap stale `sw-*` workspace dirs not associated with a live VM.

        Crashed cloud-hypervisor processes, killed test runs, and pre-fix
        teardowns (which never rmtree'd) all leave per-VM directories on
        disk. Each holds a ~360 MB rootfs copy on filesystems without
        reflink. This method enumerates the work_dir, drops anything that
        looks like a VM workspace and is not currently tracked, and
        returns counts + freed-bytes for the operator log.
        """
        import shutil
        with self._lock:
            live_ids = set(self._vms.keys())

        try:
            entries = list(self.work_dir.iterdir())
        except OSError:
            return {"removed": [], "kept_live": sorted(live_ids), "freed_bytes": 0}

        removed: list[str] = []
        freed = 0
        for entry in entries:
            if not entry.is_dir() or not entry.name.startswith("sw-"):
                continue
            if entry.name in live_ids:
                continue
            pre = 0
            try:
                for path in entry.rglob("*"):
                    if path.is_file():
                        try:
                            pre += path.stat().st_size
                        except OSError:
                            pass
            except OSError:
                continue
            shutil.rmtree(entry, ignore_errors=True)
            # Only credit removal + bytes when the dir is actually gone.
            # rmtree(ignore_errors=True) would otherwise silently lie.
            if not entry.exists():
                removed.append(entry.name)
                freed += pre
            else:
                logger.debug("clean_orphans: %s still present after rmtree", entry)

        return {
            "removed": removed,
            "kept_live": sorted(live_ids),
            "freed_bytes": freed,
        }

    def ping(self) -> dict:
        return {
            "pong": True,
            "pid": os.getpid(),
            "uptime_seconds": round(time.monotonic() - self._start_time, 1),
            "vm_count": len(self._vms),
        }

    def status(self) -> dict:
        return {
            "pid": os.getpid(),
            "uptime_seconds": round(time.monotonic() - self._start_time, 1),
            "vm_count": len(self._vms),
            "kernel_path": str(self.kernel_path),
            "rootfs_path": str(self.rootfs_path),
            "work_dir": str(self.work_dir),
            "vms": [vm.to_info() for vm in self._vms.values()],
        }

    # ── Socket server ─────────────────────────────────────────

    def serve_forever(self) -> None:
        """Start the daemon socket server."""
        self.work_dir.mkdir(parents=True, exist_ok=True)

        sock_path = self.socket_path
        if sock_path.exists():
            sock_path.unlink()

        self._server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server_socket.bind(str(sock_path))
        self._server_socket.listen(16)
        self._make_accessible(str(sock_path))

        # Write PID file
        self.pid_path.write_text(str(os.getpid()))

        self._running = True
        logger.info("vmmd listening on %s (pid=%d)", sock_path, os.getpid())

        # Handle SIGTERM gracefully
        def _shutdown(signum, frame):
            logger.info("Received signal %d, shutting down...", signum)
            self._running = False
            # Close server socket to unblock accept()
            try:
                self._server_socket.close()
            except Exception:
                pass

        signal.signal(signal.SIGTERM, _shutdown)
        signal.signal(signal.SIGINT, _shutdown)

        try:
            while self._running:
                try:
                    conn, _ = self._server_socket.accept()
                    t = threading.Thread(
                        target=self._handle_client, args=(conn,), daemon=True,
                    )
                    t.start()
                except OSError:
                    if self._running:
                        raise
                    break
        finally:
            self._shutdown_cleanup()

    def _handle_client(self, conn: socket.socket) -> None:
        """Handle a single client connection."""
        try:
            rfile = conn.makefile("r", encoding="utf-8")
            wfile = conn.makefile("w", encoding="utf-8")

            while True:
                line = rfile.readline()
                if not line:
                    break  # Client disconnected (EOF)
                line = line.strip()
                if not line:
                    continue

                try:
                    request = json.loads(line)
                except json.JSONDecodeError:
                    self._send_error(wfile, -1, -32700, "Parse error")
                    continue

                req_id = request.get("id", 0)
                method = request.get("method", "")
                params = request.get("params", {})

                try:
                    result = self._dispatch(method, params)
                    self._send_result(wfile, req_id, result)
                except Exception as e:
                    logger.error("Error handling %s: %s", method, e, exc_info=True)
                    self._send_error(wfile, req_id, -32000, str(e))

        except Exception:
            logger.debug("Client disconnected", exc_info=True)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _dispatch(self, method: str, params: dict) -> dict:
        """Dispatch a request to the appropriate handler."""
        handlers = {
            "boot": lambda p: self.boot_vm(p),
            "destroy": lambda p: self.destroy_vm(p.get("vm_id", "")),
            "destroy_all": lambda _: self.destroy_all(),
            "list": lambda _: self.list_vms(),
            "ping": lambda _: self.ping(),
            "status": lambda _: self.status(),
            "clean_orphans": lambda _: self.clean_orphans(),
        }
        handler = handlers.get(method)
        if not handler:
            raise ValueError(f"Unknown method: {method}")
        return handler(params)

    @staticmethod
    def _send_result(wfile, req_id: int, result: dict) -> None:
        wfile.write(json.dumps({"result": result, "id": req_id}) + "\n")
        wfile.flush()

    @staticmethod
    def _send_error(wfile, req_id: int, code: int, message: str) -> None:
        wfile.write(json.dumps({
            "error": {"code": code, "message": message}, "id": req_id,
        }) + "\n")
        wfile.flush()

    def _shutdown_cleanup(self) -> None:
        """Clean up on daemon shutdown."""
        logger.info("Shutting down — destroying %d VMs", len(self._vms))
        self.destroy_all()

        try:
            self.socket_path.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            self.pid_path.unlink(missing_ok=True)
        except Exception:
            pass
        logger.info("vmmd stopped")


# ── Entry point ───────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    """vmmd entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Stepwise VM Manager Daemon")
    parser.add_argument("--work-dir", type=Path, default=None)
    parser.add_argument("--kernel", type=Path, default=None)
    parser.add_argument("--rootfs", type=Path, default=None)
    parser.add_argument("--virtiofsd", default="/usr/lib/virtiofsd")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )

    # Resolve socket owner from SUDO_UID if available
    socket_owner_uid = None
    sudo_uid = os.environ.get("SUDO_UID")
    if sudo_uid:
        socket_owner_uid = int(sudo_uid)

    daemon = VMManagerDaemon(
        work_dir=args.work_dir,
        kernel_path=args.kernel,
        rootfs_path=args.rootfs,
        virtiofsd_path=args.virtiofsd,
        socket_owner_uid=socket_owner_uid,
    )

    try:
        daemon.serve_forever()
    except KeyboardInterrupt:
        pass

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
