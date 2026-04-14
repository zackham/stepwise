#!/usr/bin/env python3
"""Manual end-to-end test for the ACP containment bridge.

Prerequisites:
  - KVM + cloud-hypervisor + virtiofsd installed — run `stepwise doctor --containment`
  - Rootfs built with the bridge embedded: `stepwise build-rootfs`
  - vmmd daemon running: `sudo stepwise vmmd start --detach`

Run:
  ~/.local/share/uv/tools/stepwise-run/bin/python tests/manual_e2e_bridge.py

Sibling to `manual_e2e_containment.py` (which tests the guest-agent
spawn path used by aloop). This one tests the in-VM ACP bridge path
used by claude/codex. It is NOT a pytest test — needs real VM resources
and ~15 seconds to run.

Boots a cloud-hypervisor VM via CloudHypervisorBackend, then exercises
both the guest-agent (port 9999) and the acp-bridge (port 9998) over
vsock without spawning a real ACP adapter. Validates the bridge
plumbing shipped in commit 3f74a0c.

Sanity items (from the roadmap E2E test plan):
 1. VM boot + bridge ping
 2. Workspace write via bridge → visible on host through virtiofs
 3. Workspace read via bridge (host-absolute path, translated)
 4. Workspace read via bridge (relative path, resolved against /mnt/workspace)
 5. Terminal echo roundtrip

Boundary items:
 6. VM identity — uname + /etc/os-release reflect Alpine guest, not host
 7. Bridge-side read of host-only /home/zack/.ssh/id_rsa → denied
 8. Bridge-side write to /tmp/stepwise-escape-marker → does NOT appear on host
 9. Bridge-side read of /home/zack/.claude/.credentials.json → denied
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from stepwise.containment.acp_bridge import translate_path
from stepwise.containment.backend import ContainmentConfig
from stepwise.containment.cloud_hypervisor import CloudHypervisorBackend


def tp(bridge, params):
    """Mimic the host-side _register_client_handlers path-translation step."""
    if "path" in params:
        params = dict(params)
        params["path"] = translate_path(params["path"], bridge.host_workdir)
    return params

HOST = os.uname().nodename


def section(label: str):
    print(f"\n== {label} ==")


def ok(msg: str):
    print(f"  PASS  {msg}")


def fail(msg: str):
    print(f"  FAIL  {msg}")


def main() -> int:
    failures = 0
    workspace = tempfile.mkdtemp(prefix="stepwise-e2e-")
    Path(workspace, "seed.txt").write_text("host-seed-bytes\n")

    backend = CloudHypervisorBackend()
    print(f"host workdir: {workspace}")
    print(f"host hostname: {HOST}")

    try:
        config = ContainmentConfig(
            mode="cloud-hypervisor",
            tools=["read", "write", "terminal"],
            allowed_paths=[workspace],
            working_dir=workspace,
        )
        section("boot VM")
        ctx = backend.get_spawn_context(config)
        ok("VMSpawnContext obtained")

        section("bridge: ping")
        bridge = ctx.open_bridge(host_workdir=workspace)
        if bridge.ping():
            ok("bridge ping → pong")
        else:
            fail("bridge ping returned False")
            failures += 1

        section("introspect VM filesystem")
        create = bridge.call("terminal/create", {"command": "mount | grep -E 'virtiofs|workspace' ; ls -la /mnt/workspace 2>&1"})
        tid = create["terminalId"]
        wait = bridge.call("terminal/wait_for_exit", {"terminalId": tid, "timeoutMs": 5000})
        fs_info = (wait.get("output") or "").strip()
        print("  VM fs probe:")
        for line in fs_info.splitlines():
            print(f"    | {line}")
        bridge.call("terminal/release", {"terminalId": tid})

        section("bridge: workspace write + host virtiofs verify")
        write_params = tp(bridge, {
            "path": os.path.join(workspace, "from-bridge.txt"),
            "content": "written-through-bridge\n",
        })
        print(f"  translated write path: {write_params['path']}")
        bridge.call("fs/write_text_file", write_params)
        host_view = Path(workspace, "from-bridge.txt")
        if host_view.exists() and host_view.read_text() == "written-through-bridge\n":
            ok("host sees bridge write via virtiofs")
        else:
            fail(f"host virtiofs view broken: exists={host_view.exists()}")
            failures += 1

        section("bridge: workspace read (absolute host path, translated)")
        r = bridge.call("fs/read_text_file", tp(bridge, {
            "path": os.path.join(workspace, "seed.txt"),
        }))
        if r.get("content") == "host-seed-bytes\n":
            ok("bridge read host-seeded file through virtiofs")
        else:
            fail(f"read mismatch: {r!r}")
            failures += 1

        section("bridge: relative-path read (cwd=/mnt/workspace)")
        r = bridge.call("fs/read_text_file", {"path": "seed.txt"})
        if r.get("content") == "host-seed-bytes\n":
            ok("bridge read via relative path")
        else:
            fail(f"relative read mismatch: {r!r}")
            failures += 1

        section("bridge: terminal echo roundtrip")
        create = bridge.call("terminal/create", {"command": "echo stepwise-contained"})
        tid = create["terminalId"]
        wait = bridge.call("terminal/wait_for_exit", {"terminalId": tid, "timeoutMs": 5000})
        out = bridge.call("terminal/output", {"terminalId": tid})
        combined = (wait.get("output") or "") + (out.get("output") or "")
        if wait.get("exitCode") == 0 and "stepwise-contained" in combined:
            ok(f"terminal echo returned {combined.strip()!r}")
        else:
            fail(f"terminal echo failed: exitCode={wait.get('exitCode')} out={combined!r}")
            failures += 1
        bridge.call("terminal/release", {"terminalId": tid})

        section("BOUNDARY: VM identity — Alpine rootfs, distinct from host OS")
        create = bridge.call("terminal/create", {
            "command": "uname -a; echo ---; cat /etc/os-release 2>&1 | head -3",
        })
        tid = create["terminalId"]
        wait = bridge.call("terminal/wait_for_exit", {"terminalId": tid, "timeoutMs": 5000})
        ident = (wait.get("output") or "").strip()
        print("  VM identity:")
        for line in ident.splitlines():
            print(f"    | {line}")
        # The rootfs is Alpine per `stepwise build-rootfs`. If we see Alpine
        # and the host ISN'T Alpine (check /etc/os-release ID), the guest is
        # demonstrably its own filesystem.
        host_os = ""
        try:
            for line in Path("/etc/os-release").read_text().splitlines():
                if line.startswith("ID="):
                    host_os = line.split("=", 1)[1].strip().strip('"').lower()
                    break
        except OSError:
            pass
        if "alpine" in ident.lower() and host_os != "alpine":
            ok(f"VM is Alpine, host is {host_os or 'unknown'} — distinct rootfs")
        elif "alpine" in ident.lower():
            # Host is also Alpine (unlikely for dev machines, but honest).
            ok("VM is Alpine (host is also Alpine — weaker signal)")
        else:
            fail(f"VM identity unclear: {ident!r}")
            failures += 1
        bridge.call("terminal/release", {"terminalId": tid})

        host_home = os.path.expanduser("~")
        ssh_key = os.path.join(host_home, ".ssh", "id_rsa")
        section(f"BOUNDARY: read host-only file ({ssh_key})")
        # NB: outside host_workdir so translate_path leaves it untouched — the
        # bridge sees the literal host path inside the VM, which should not
        # exist in a fresh Alpine rootfs.
        try:
            bridge.call("fs/read_text_file", tp(bridge, {"path": ssh_key}))
            fail("bridge successfully read host SSH key — CONTAINMENT BYPASS")
            failures += 1
        except Exception as exc:
            ok(f"bridge refused host SSH key path: {exc}")

        section("BOUNDARY: write to /tmp/stepwise-escape-marker (outside workspace)")
        escape_path = "/tmp/stepwise-escape-marker"
        try:
            Path(escape_path).unlink()
        except FileNotFoundError:
            pass
        bridge.call("fs/write_text_file", tp(bridge, {
            "path": escape_path,
            "content": "if-you-see-this-on-host-containment-is-broken\n",
        }))
        if Path(escape_path).exists():
            content = Path(escape_path).read_text()
            fail(f"host /tmp/stepwise-escape-marker exists: {content!r}")
            failures += 1
        else:
            ok("host /tmp/stepwise-escape-marker does NOT exist (VM-local write)")

        claude_creds = os.path.join(host_home, ".claude", ".credentials.json")
        section(f"BOUNDARY: read {claude_creds} (host secret)")
        try:
            bridge.call("fs/read_text_file", tp(bridge, {"path": claude_creds}))
            fail("bridge successfully read host claude credentials — BYPASS")
            failures += 1
        except Exception as exc:
            ok(f"bridge refused host credentials path: {exc}")

        bridge.close()

    finally:
        section("cleanup")
        try:
            backend.release_all()
            ok("backend.release_all() OK")
        except Exception as exc:
            fail(f"cleanup error: {exc}")
            failures += 1

    print()
    print("=" * 40)
    if failures:
        print(f"RESULT: {failures} FAILURE(S)")
    else:
        print("RESULT: ALL CHECKS PASSED")
    print("=" * 40)
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
