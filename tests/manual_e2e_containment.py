#!/usr/bin/env python3
"""Manual end-to-end test for containment via vmmd.

Prerequisites:
  - vmmd daemon running: sudo stepwise vmmd start --detach
  - OR: vmmd auto-starts (requires sudo access)

Run: python3 tests/manual_e2e_containment.py
"""

import json
import os
import socket
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def main():
    print("=" * 60)
    print("Stepwise Containment: Manual E2E Test (via vmmd)")
    print("=" * 60)

    from stepwise.containment.vmmd_client import VMManagerClient, is_vmmd_running
    from stepwise.containment.backend import ContainmentConfig

    # 1. Check vmmd
    print("\n1. Checking vmmd...")
    if is_vmmd_running():
        print("   vmmd is running")
    else:
        print("   vmmd not running — will attempt auto-start (needs sudo)")

    client = VMManagerClient(auto_start=True)

    try:
        status = client.ping()
        print(f"   Connected to vmmd (PID {status['pid']}, {status['vm_count']} VMs)")
    except Exception as e:
        print(f"   FAIL: Cannot connect to vmmd: {e}")
        print("   Start vmmd first: sudo stepwise vmmd start --detach")
        return 1

    # 2. Set up workspace
    workspace = Path(tempfile.mkdtemp(prefix="stepwise-e2e-"))
    (workspace / "hello.txt").write_text("hello from host!")
    (workspace / "data.json").write_text(json.dumps({"items": [1, 2, 3]}))
    print(f"\n2. Workspace: {workspace}")

    # 3. Boot VM via vmmd
    print("\n3. Booting VM...")
    t0 = time.monotonic()
    try:
        result = client.boot(ContainmentConfig(
            mode="cloud-hypervisor",
            tools=["read", "write"],
            allowed_paths=[str(workspace)],
            working_dir=str(workspace),
        ))
    except Exception as e:
        print(f"   FAIL: {e}")
        return 1

    boot_time = time.monotonic() - t0
    vm_id = result["vm_id"]
    vsock_socket = result["vsock_socket"]
    reused = result.get("reused", False)
    print(f"   VM {vm_id} {'reused' if reused else 'booted'} in {boot_time:.2f}s")
    print(f"   vsock: {vsock_socket}")

    try:
        # 4. Run command through guest agent
        print("\n4. Running command in VM...")

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(30)
        sock.connect(vsock_socket)
        sock.sendall(b"CONNECT 9999\n")

        resp = b""
        while b"\n" not in resp:
            resp += sock.recv(256)

        if not resp.startswith(b"OK"):
            print(f"   FAIL: CONNECT failed: {resp!r}")
            return 1
        print(f"   CONNECT: {resp.strip().decode()}")

        # Send spawn command
        cmd = json.dumps({
            "command": ["sh", "-c",
                "echo HELLO && "
                "python3 --version && "
                "node --version && "
                "echo FILES: $(ls /mnt/workspace/) && "
                "cat /mnt/workspace/hello.txt && "
                "echo guest-wrote-this > /mnt/workspace/from-guest.txt && "
                "echo DONE"],
            "env": {},
            "cwd": "/mnt/workspace",
        }).encode() + b"\n"
        sock.sendall(cmd)

        # Wrap in file for line-buffered reading AFTER sending command
        sock.settimeout(None)
        rfile = sock.makefile("r", buffering=1, encoding="utf-8")

        # Read ACK
        ack_line = rfile.readline()
        try:
            ack = json.loads(ack_line)
            print(f"   ACK: pid={ack.get('pid', '?')}")
        except json.JSONDecodeError:
            print(f"   ACK (raw): {ack_line.strip()}")

        # Read output
        sock.settimeout(5)
        lines = []
        for _ in range(20):
            try:
                line = rfile.readline()
                if not line:
                    break
                lines.append(line.strip())
                if "DONE" in line:
                    break
            except Exception:
                break

        for line in lines:
            print(f"   {line}")

        sock.close()

        # 5. Verify virtiofs write
        print("\n5. Verifying virtiofs write...")
        guest_file = workspace / "from-guest.txt"
        if guest_file.exists():
            content = guest_file.read_text().strip()
            print(f"   ✓ Host reads guest file: '{content}'")
        else:
            print("   ✗ Guest file NOT visible on host")
            return 1

        # 6. Validate output
        print("\n6. Validating output...")
        output = "\n".join(lines)
        checks = {
            "HELLO": "HELLO" in output,
            "Python version": "Python 3" in output,
            "Node version": "v2" in output,
            "Workspace files": "hello.txt" in output,
            "File content": "hello from host" in output,
            "DONE": "DONE" in output,
        }
        all_pass = True
        for check, ok in checks.items():
            print(f"   {'✓' if ok else '✗'} {check}")
            if not ok:
                all_pass = False

        # 7. Test VM reuse
        print("\n7. Testing VM reuse...")
        result2 = client.boot(ContainmentConfig(
            mode="cloud-hypervisor",
            tools=["read", "write"],
            allowed_paths=[str(workspace)],
            working_dir=str(workspace),
        ))
        if result2["reused"]:
            print(f"   ✓ VM reused (same config)")
        else:
            print(f"   ✗ New VM created (expected reuse)")
            all_pass = False

        if all_pass:
            print("\n" + "=" * 60)
            print("ALL TESTS PASSED")
            print("=" * 60)
            return 0
        else:
            print("\nSOME TESTS FAILED")
            return 1

    finally:
        # Cleanup — destroy the VM
        print("\nCleanup...")
        try:
            client.destroy(vm_id)
            print(f"   VM {vm_id} destroyed")
        except Exception as e:
            print(f"   Cleanup error: {e}")
        client.close()


if __name__ == "__main__":
    sys.exit(main())
