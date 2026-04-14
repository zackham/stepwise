"""Guest agent: runs inside the microVM, bridges vsock to ACP processes.

This script is embedded in the guest rootfs and started by init.sh.
It listens on vsock port 9999, accepts connections, and for each:
  1. Reads a JSON config line: {"command": [...], "env": {...}, "cwd": "..."}
  2. Spawns the process with stdin/stdout bridged to the vsock stream
  3. Sends back {"pid": N} as acknowledgement
  4. Forwards data bidirectionally until the process exits

This is the minimal agent needed to run ACP commands inside the VM.
"""

GUEST_AGENT_SCRIPT = r'''#!/usr/bin/env python3
"""Stepwise guest agent — vsock-to-stdio bridge for ACP processes."""

import json
import os
import select
import signal
import socket
import subprocess
import sys
import threading

LISTEN_PORT = 9999
BUF_SIZE = 65536


def handle_connection(conn: socket.socket, addr: tuple) -> None:
    """Handle a single vsock connection: spawn process, bridge stdio."""
    try:
        # Read config line
        config_line = b""
        while b"\n" not in config_line:
            chunk = conn.recv(4096)
            if not chunk:
                conn.close()
                return
            config_line += chunk

        # Split: first line is config, rest is initial stdin data
        first_line, _, extra = config_line.partition(b"\n")

        # Handle ping
        try:
            data = json.loads(first_line)
        except json.JSONDecodeError:
            conn.close()
            return

        if "ping" in data:
            conn.sendall(b'{"pong": true}\n')
            conn.close()
            return

        command = data["command"]
        env = {**os.environ, **data.get("env", {})}
        cwd = data.get("cwd", "/mnt/workspace")

        # Ensure cwd exists
        os.makedirs(cwd, exist_ok=True)

        # Spawn the process
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # Merge stderr into stdout
            env=env,
            cwd=cwd,
            start_new_session=True,
        )

        # Send ACK with PID
        ack = json.dumps({"pid": proc.pid}).encode() + b"\n"
        conn.sendall(ack)

        # Send any extra data that came after the config line
        if extra:
            proc.stdin.write(extra)
            proc.stdin.flush()

        # Bridge: vsock <-> process stdio
        # Thread 1: vsock -> process stdin
        def vsock_to_stdin():
            try:
                while True:
                    data = conn.recv(BUF_SIZE)
                    if not data:
                        break
                    if data == b"__KILL__\n":
                        try:
                            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                        except (ProcessLookupError, PermissionError):
                            pass
                        break
                    proc.stdin.write(data)
                    proc.stdin.flush()
            except (OSError, BrokenPipeError):
                pass
            finally:
                try:
                    proc.stdin.close()
                except Exception:
                    pass

        # Thread 2: process stdout -> vsock
        def stdout_to_vsock():
            try:
                while True:
                    data = proc.stdout.read1(BUF_SIZE) if hasattr(proc.stdout, 'read1') else proc.stdout.read(BUF_SIZE)
                    if not data:
                        break
                    conn.sendall(data)
            except (OSError, BrokenPipeError):
                pass
            finally:
                try:
                    conn.shutdown(socket.SHUT_WR)
                except (OSError, socket.error):
                    pass

        t1 = threading.Thread(target=vsock_to_stdin, daemon=True)
        t2 = threading.Thread(target=stdout_to_vsock, daemon=True)
        t1.start()
        t2.start()

        # Wait for process to finish
        proc.wait()

        # Give stdout thread time to flush
        t2.join(timeout=5)
        t1.join(timeout=2)

    except Exception as e:
        try:
            err = json.dumps({"error": str(e)}).encode() + b"\n"
            conn.sendall(err)
        except Exception:
            pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def main():
    # Set up signal handling
    signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))
    signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))

    # Listen on vsock
    sock = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((socket.VMADDR_CID_ANY, LISTEN_PORT))
    sock.listen(16)

    print(f"Guest agent listening on vsock port {LISTEN_PORT}", file=sys.stderr)

    while True:
        try:
            conn, addr = sock.accept()
            t = threading.Thread(
                target=handle_connection, args=(conn, addr), daemon=True,
            )
            t.start()
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Accept error: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
'''

# Init script that runs as PID 1 in the guest
GUEST_INIT_SCRIPT = r'''#!/bin/sh
export PATH=/usr/bin:/bin:/usr/sbin:/sbin

# Mount essential filesystems
mount -t proc proc /proc
mount -t sysfs sys /sys
mount -t devtmpfs dev /dev 2>/dev/null || true
mkdir -p /dev/pts /dev/shm
mount -t devpts devpts /dev/pts 2>/dev/null || true
mount -t tmpfs tmpfs /dev/shm 2>/dev/null || true
mount -t tmpfs tmpfs /tmp 2>/dev/null || true

# Mount virtiofs shares. The primary share is always "workspace"; the
# host may also mount per-agent credential dirs like claude_home and
# codex_home. Bind them into the corresponding dotfile paths in /root
# so the adapters find their OAuth creds without having to re-read
# the cmdline.
if grep -q virtiofs /proc/filesystems 2>/dev/null; then
    mkdir -p /mnt/workspace
    mount -t virtiofs workspace /mnt/workspace 2>/dev/null
    if [ -d /root ] && mount -t virtiofs claude_home /root/.claude 2>/dev/null; then
        :  # claude creds + projects mounted
    fi
    if mount -t virtiofs codex_home /root/.codex 2>/dev/null; then
        :  # codex creds mounted
    fi
fi

# Restore /root/.claude.json from the most recent backup inside
# /root/.claude/backups/. Claude Code writes this flat-file config
# at HOME root (not inside ~/.claude), so virtiofs can't project it
# directly. /root/.claude.json in the rootfs is a symlink to
# /tmp/.claude.json (writable tmpfs). Without this file claude-code
# child process spawned by claude-agent-acp session/new exits with
# "Query closed before response received".
if [ -d /root/.claude/backups ]; then
    backup=$(ls -1t /root/.claude/backups/.claude.json.backup.* 2>/dev/null | head -1)
    if [ -n "$backup" ] && [ -f "$backup" ]; then
        cp "$backup" /tmp/.claude.json
    fi
fi

# ── Network config from kernel cmdline ────────────────────────────
# vmmd.py puts ch_ip=10.X.Y.2/24 ch_gw=10.X.Y.1 ch_dns=1.1.1.1 on
# the cmdline when it sets up a tap + MASQUERADE. The guest parses
# those and configures eth0 statically. If any token is missing or
# there's no eth0, we skip silently — aloop is the only agent that
# NEEDS network today (kimi via OpenRouter), and claude/codex will
# need it once they run in-VM. Without this stanza the VM has no
# route and API calls fail with ENETUNREACH.
if [ -e /sys/class/net/eth0 ]; then
    CMDLINE=$(cat /proc/cmdline)
    CH_IP=$(echo "$CMDLINE" | sed -n 's/.*\bch_ip=\([^ ]*\).*/\1/p')
    CH_GW=$(echo "$CMDLINE" | sed -n 's/.*\bch_gw=\([^ ]*\).*/\1/p')
    CH_DNS=$(echo "$CMDLINE" | sed -n 's/.*\bch_dns=\([^ ]*\).*/\1/p')
    if [ -n "$CH_IP" ] && [ -n "$CH_GW" ]; then
        ip link set eth0 up
        ip addr add "$CH_IP" dev eth0
        ip route add default via "$CH_GW"
        if [ -n "$CH_DNS" ]; then
            echo "nameserver $CH_DNS" > /etc/resolv.conf
        fi
    fi
fi

# Start the ACP containment bridge in the background (vsock port 9998).
# Host-side claude/codex adapters proxy fs/terminal requests to this
# process so operations execute inside the VM sandbox.
if [ -f /opt/stepwise/acp-bridge.py ]; then
    python3 /opt/stepwise/acp-bridge.py &
fi

# Start the guest agent (vsock port 9999) in the foreground as PID 1's main.
exec python3 /opt/stepwise/guest-agent.py
'''
