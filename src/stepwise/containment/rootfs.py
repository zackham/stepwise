"""Rootfs builder for containment VMs.

Builds ext4 filesystem images suitable for cloud-hypervisor guests.
Derives requirements from the agent registry: if an agent command
starts with 'npx', the rootfs needs Node.js; if it starts with
'python' or 'aloop', it needs Python.

Build process:
  1. Create a Docker container from Alpine base
  2. Install Python, Node, and required packages
  3. Install the guest agent script
  4. Export as ext4 image

Usage:
  stepwise build-rootfs              # Build from agent registry
  stepwise build-rootfs --agent X    # Include specific agent deps
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from stepwise.containment.acp_bridge import ACP_BRIDGE_SCRIPT
from stepwise.containment.guest_agent import GUEST_AGENT_SCRIPT, GUEST_INIT_SCRIPT


def _find_local_aloop_wheel() -> Path | None:
    """Locate a local aloop wheel to bake into the rootfs.

    aloop 0.3+ isn't on public PyPI. Bio-zack builds wheels under
    ~/work/aloop/dist/ via its own pyproject. We prefer the highest
    version we find. Returns None if the tree isn't there, in which
    case callers fall through and the rootfs goes without aloop —
    the claude/codex agents still work, but aloop-under-containment
    fails at spawn with "aloop: not found".
    """
    for candidate_base in (
        Path.home() / "work" / "aloop" / "dist",
        Path.home() / ".local" / "share" / "aloop" / "dist",
    ):
        if not candidate_base.is_dir():
            continue
        wheels = sorted(candidate_base.glob("aloop-*.whl"))
        if wheels:
            return wheels[-1]  # highest version (lexical sort on semver)
    return None

logger = logging.getLogger("stepwise.containment.rootfs")

DEFAULT_VMM_DIR = Path.home() / ".stepwise" / "vmm"
DEFAULT_ROOTFS_PATH = DEFAULT_VMM_DIR / "rootfs.ext4"

# Base Alpine version
ALPINE_VERSION = "3.21"
ALPINE_MIRROR = "https://dl-cdn.alpinelinux.org/alpine"


def needs_node(agents: dict | None = None) -> bool:
    """Check if any agent needs Node.js (npx command)."""
    if agents is None:
        from stepwise.agent_registry import BUILTIN_AGENTS
        agents = BUILTIN_AGENTS
    return any(
        agent.command and agent.command[0] in ("npx", "node", "npm")
        for agent in agents.values()
    )


def needs_python(agents: dict | None = None) -> bool:
    """Check if any agent needs Python."""
    if agents is None:
        from stepwise.agent_registry import BUILTIN_AGENTS
        agents = BUILTIN_AGENTS
    return any(
        agent.command and agent.command[0] in ("python", "python3", "aloop", "pip")
        for agent in agents.values()
    )


def build_rootfs(
    output_path: str | Path | None = None,
    size_mb: int = 2048,
    include_node: bool | None = None,
    include_python: bool | None = None,
    extra_packages: list[str] | None = None,
    extra_npm_packages: list[str] | None = None,
    extra_pip_packages: list[str] | None = None,
) -> Path:
    """Build a rootfs ext4 image for containment VMs.

    Uses Docker to build the filesystem contents, then exports
    to an ext4 image.
    """
    output = Path(output_path or DEFAULT_ROOTFS_PATH)
    output.parent.mkdir(parents=True, exist_ok=True)

    # Auto-detect requirements from agent registry
    if include_node is None:
        include_node = needs_node()
    if include_python is None:
        include_python = needs_python()

    logger.info(
        "Building rootfs: node=%s python=%s size=%dMB output=%s",
        include_node, include_python, size_mb, output,
    )

    # Build Dockerfile content
    dockerfile = _generate_dockerfile(
        include_node=include_node,
        include_python=include_python,
        extra_packages=extra_packages or [],
        extra_npm_packages=extra_npm_packages or [],
        extra_pip_packages=extra_pip_packages or [],
    )

    with tempfile.TemporaryDirectory(prefix="stepwise-rootfs-") as build_dir:
        build_path = Path(build_dir)

        # Copy local aloop wheel into the build context if present, so
        # the Dockerfile can `pip install` it. aloop isn't on public PyPI
        # in a version we use; bio-zack's local tree at ~/work/aloop
        # ships wheels under dist/. Without this the in-VM aloop agent
        # errors with "aloop: not found" at spawn time.
        aloop_wheel = _find_local_aloop_wheel()
        if aloop_wheel:
            shutil.copy2(aloop_wheel, build_path / aloop_wheel.name)
            extra_pip_packages = list(extra_pip_packages or [])
            extra_pip_packages.append(f"/build/{aloop_wheel.name}")
            # Regenerate Dockerfile now that we have the extra pip pkg.
            dockerfile = _generate_dockerfile(
                include_node=include_node,
                include_python=include_python,
                extra_packages=[],
                extra_npm_packages=[],
                extra_pip_packages=extra_pip_packages,
            )

        # Write Dockerfile
        (build_path / "Dockerfile").write_text(dockerfile)

        # Write guest agent + ACP containment bridge
        (build_path / "guest-agent.py").write_text(GUEST_AGENT_SCRIPT)
        (build_path / "acp-bridge.py").write_text(ACP_BRIDGE_SCRIPT)
        (build_path / "init.sh").write_text(GUEST_INIT_SCRIPT)

        # Build container
        tag = "stepwise-rootfs-builder:latest"
        logger.info("Building container image...")
        subprocess.run(
            ["docker", "build", "-t", tag, "."],
            cwd=build_dir,
            check=True,
            capture_output=True,
        )

        # Create container (don't start it)
        container_id = subprocess.run(
            ["docker", "create", tag],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        try:
            # Export filesystem as tar
            tar_path = build_path / "rootfs.tar"
            logger.info("Exporting container filesystem...")
            with open(tar_path, "wb") as f:
                subprocess.run(
                    ["docker", "export", container_id],
                    stdout=f,
                    check=True,
                )

            # Create ext4 image from tar
            logger.info("Creating ext4 image (%dMB)...", size_mb)
            _tar_to_ext4(tar_path, output, size_mb)

        finally:
            # Clean up container
            subprocess.run(
                ["docker", "rm", container_id],
                capture_output=True,
            )
            # Clean up image
            subprocess.run(
                ["docker", "rmi", tag],
                capture_output=True,
            )

    logger.info("Rootfs built: %s (%.1f MB)", output, output.stat().st_size / 1024 / 1024)
    return output


def _generate_dockerfile(
    include_node: bool,
    include_python: bool,
    extra_packages: list[str],
    extra_npm_packages: list[str],
    extra_pip_packages: list[str],
) -> str:
    """Generate a Dockerfile for the rootfs."""
    # Why debian-slim, not alpine:
    # Alpine uses musl libc. @zed-industries/codex-acp ships a prebuilt
    # glibc-linked binary (codex-acp-linux-x64/bin/codex-acp) with no
    # musl variant. Alpine's `gcompat` shim covers basic glibc symbols
    # but not codex's full set (fcntl64, __res_init, cap_from_name all
    # missing, plus libcap.so.2 needs an extra package, plus libstdc++
    # linkage issues). Debian slim has glibc natively — no compat
    # layer, binaries just work. The rootfs grows from ~360MB Alpine
    # to ~500MB debian-slim, which is fine on our 2GB budget.
    # debian:trixie ships Python 3.13 (aloop requires >=3.12).
    # bookworm's 3.11 is too old.
    lines = [
        "FROM debian:trixie-slim",
        "",
        "# Base system + basic tools used by init.sh + healthchecks.",
        "RUN apt-get update && apt-get install -y --no-install-recommends "
        "bash coreutils util-linux mount procps iproute2 wget ca-certificates "
        "&& rm -rf /var/lib/apt/lists/*",
        "",
    ]

    if include_python:
        lines.extend([
            "# Python",
            "RUN apt-get update && apt-get install -y --no-install-recommends "
            "python3 python3-pip && rm -rf /var/lib/apt/lists/*",
            "",
        ])

    if include_node:
        lines.extend([
            "# Node.js",
            "RUN apt-get update && apt-get install -y --no-install-recommends "
            "nodejs npm && rm -rf /var/lib/apt/lists/*",
            "",
        ])

    if extra_packages:
        lines.extend([
            "# Extra packages",
            "RUN apt-get update && apt-get install -y --no-install-recommends "
            f"{' '.join(extra_packages)} && rm -rf /var/lib/apt/lists/*",
            "",
        ])

    if include_node:
        # Pre-install ACP adapters. Baking them into the rootfs avoids
        # a multi-minute npx download on every fresh VM boot (would
        # otherwise redownload per-VM to the ephemeral npm_config_cache
        # on /tmp/.npm-cache tmpfs).
        npm_packages = [
            "@agentclientprotocol/claude-agent-acp",
            "@zed-industries/codex-acp",
        ]
        npm_packages.extend(extra_npm_packages)
        lines.extend([
            "# ACP adapters",
            f"RUN npm install -g {' '.join(npm_packages)}",
            "",
        ])

    if include_python and extra_pip_packages:
        # Split between PyPI names (pip install NAME) and local files
        # (need COPY into image first, then pip install <path>). Both
        # run with --break-system-packages because alpine's py3-pip
        # refuses otherwise. Ugly, not exploitable — the rootfs is
        # read-only at runtime.
        pypi_pkgs = [p for p in extra_pip_packages if not p.startswith("./") and not p.endswith(".whl") and not p.endswith(".tar.gz")]
        file_pkgs = [p for p in extra_pip_packages if p not in pypi_pkgs]
        if pypi_pkgs:
            lines.extend([
                "# Python packages (PyPI)",
                f"RUN pip install --break-system-packages {' '.join(pypi_pkgs)}",
                "",
            ])
        for f in file_pkgs:
            basename = Path(f).name
            lines.extend([
                f"# Python package from local build: {basename}",
                f"COPY {basename} /tmp/{basename}",
                f"RUN pip install --break-system-packages /tmp/{basename} && rm /tmp/{basename}",
                "",
            ])

    lines.extend([
        "# Guest agent + ACP containment bridge",
        "RUN mkdir -p /opt/stepwise",
        "COPY guest-agent.py /opt/stepwise/guest-agent.py",
        "COPY acp-bridge.py /opt/stepwise/acp-bridge.py",
        "COPY init.sh /init.sh",
        "RUN chmod +x /init.sh /opt/stepwise/guest-agent.py /opt/stepwise/acp-bridge.py",
        "",
        "# Mount points — workspace (virtiofs from host job workspace) and",
        "# per-agent credential homes. init.sh mounts claude_home and",
        "# codex_home virtiofs shares here when the host opts in via",
        "# ContainmentConfig.host_auth_mounts.",
        "RUN mkdir -p /mnt/workspace /root/.claude /root/.codex",
    ])

    return "\n".join(lines) + "\n"


def _tar_to_ext4(tar_path: Path, output: Path, size_mb: int) -> None:
    """Convert a tar archive to an ext4 filesystem image."""
    # Create sparse image
    subprocess.run(
        ["dd", "if=/dev/zero", f"of={output}", "bs=1M",
         "count=0", f"seek={size_mb}"],
        check=True,
        capture_output=True,
    )

    # Format as ext4
    subprocess.run(
        ["mkfs.ext4", "-F", "-q", str(output)],
        check=True,
        capture_output=True,
    )

    # Mount and extract tar
    mount_dir = tempfile.mkdtemp(prefix="stepwise-rootfs-mount-")
    try:
        subprocess.run(
            ["sudo", "mount", "-o", "loop", str(output), mount_dir],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["sudo", "tar", "xf", str(tar_path), "-C", mount_dir],
            check=True,
            capture_output=True,
        )
        # Post-extract fixup: make /etc/resolv.conf a symlink into
        # /tmp (tmpfs mounted by init.sh). We can't do this via
        # Dockerfile RUN because Docker bind-mounts /etc/resolv.conf
        # read-only during build. We can't do it at VM runtime either
        # because cloud-hypervisor write-protects the rootfs disk
        # (sector 0 deny). The only writable window is here, while
        # we hold the mounted ext4 image.
        subprocess.run(
            ["sudo", "ln", "-sf", "/tmp/resolv.conf",
             f"{mount_dir}/etc/resolv.conf"],
            check=True, capture_output=True,
        )
        # Same trick for /root/.claude.json. Claude Code (the child
        # process spawned by claude-agent-acp during session/new) looks
        # for ~/.claude.json — a FLAT FILE at HOME, not inside
        # ~/.claude/. virtiofs mounts a directory, not a file, so we
        # can't project ~/.claude.json directly from the host. Instead
        # we symlink to /tmp and have init.sh populate /tmp/.claude.json
        # at boot from the latest backup in /root/.claude/backups/
        # (claude-code auto-maintains these).
        subprocess.run(
            ["sudo", "ln", "-sf", "/tmp/.claude.json",
             f"{mount_dir}/root/.claude.json"],
            check=True, capture_output=True,
        )
    finally:
        subprocess.run(
            ["sudo", "umount", mount_dir],
            capture_output=True,
        )
        shutil.rmtree(mount_dir, ignore_errors=True)


def check_rootfs(path: str | Path | None = None) -> dict:
    """Check if a rootfs image exists and is valid."""
    rootfs = Path(path or DEFAULT_ROOTFS_PATH)
    result = {
        "exists": rootfs.exists(),
        "path": str(rootfs),
        "size_mb": rootfs.stat().st_size / 1024 / 1024 if rootfs.exists() else 0,
    }
    return result
