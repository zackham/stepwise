# Agent Containment

Stepwise can run agent steps inside hardware-isolated microVMs using
[Cloud-Hypervisor](https://www.cloudhypervisor.org/). This bounds the
blast radius of autonomous agent sessions: agents can only access the
filesystem paths, credentials, and network endpoints explicitly declared
in their configuration.

## Overview

- **Only agent steps are contained.** Script, LLM, polling, and external
  steps always run on the host — they're deterministic code or stateless calls.
- **Containment is opt-in.** Default is no isolation (same as running
  `claude` with accept-edits). Enable when you want blast-radius bounding.
- **One VM per distinct agent config.** Steps with the same tools, paths,
  and credentials share a VM. Different configs get separate VMs.
- **Host directories are mounted live** via virtiofs — no copy-in/copy-out.
  Agents read and write your workspace at near-native speed.

## Quick Start

### Prerequisites

- **Linux with KVM** (`/dev/kvm` must exist)
- **cloud-hypervisor** v51+ ([releases](https://github.com/cloud-hypervisor/cloud-hypervisor/releases))
- **virtiofsd** 1.x+ (`pacman -S virtiofsd` on Arch, or `cargo install virtiofsd`)
- **Guest kernel** — downloaded automatically or manually from
  [cloud-hypervisor/linux releases](https://github.com/cloud-hypervisor/linux/releases)
- **sudo access** — the VM manager daemon (vmmd) runs as root for
  virtiofs shared memory mapping. You type your sudo password once
  when starting the daemon.

Check prerequisites:

```bash
stepwise doctor --containment
```

### Build the VM rootfs

```bash
stepwise build-rootfs
```

This builds a Debian-trixie-slim-based ext4 image with Python, Node.js,
and the three pre-baked ACP adapters: `aloop` (pip from local wheel
when present at `~/work/aloop/dist/`), `@zed-industries/claude-code-acp`,
and `@zed-industries/codex-acp`. The image is stored at
`~/.stepwise/vmm/rootfs.ext4`. ACP adapters are baked in so each
spawn doesn't reach the npm registry from inside the VM.

### Start the VM manager daemon

```bash
sudo stepwise vmmd start --detach
```

The vmmd daemon runs as root and manages VM lifecycle (virtiofsd,
cloud-hypervisor, shared memory). Stepwise itself runs unprivileged
and talks to vmmd over a Unix socket. The daemon auto-starts when
needed, but you can start it explicitly to avoid sudo prompts
during flow execution.

```bash
stepwise vmmd status    # check if running
stepwise vmmd stop      # stop the daemon
```

### Enable containment

#### Per agent (in stepwise settings)

```yaml
agents:
  claude:
    containment: cloud-hypervisor
    command: ["npx", "@agentclientprotocol/claude-agent-acp"]
    config:
      model:
        flag: "--model"
        default: opus
```

This contains all `claude` agent steps. You can define an uncontained
variant for local development:

```yaml
agents:
  claude-local:
    command: ["npx", "@agentclientprotocol/claude-agent-acp"]
    config:
      model:
        flag: "--model"
        default: opus
```

Then use `agent: claude` for contained execution and `agent: claude-local`
for direct host execution.

#### Per flow

```yaml
containment: cloud-hypervisor

steps:
  research:
    executor: agent
    prompt: "Research this topic"
```

#### Per step

```yaml
steps:
  research:
    executor: agent
    containment: cloud-hypervisor
    prompt: "Research this topic"
```

#### Via CLI flag

```bash
stepwise run my-flow.yaml --containment cloud-hypervisor
```

Override chain: step > flow > agent settings > CLI default.

## How It Works

### Architecture

```
stepwise (unprivileged)          vmmd (root)              GUEST (microVM)
────────────────────             ──────────               ──────────────
CloudHypervisorBackend           VMManagerDaemon
  └─ VMManagerClient ──socket──> boot/destroy             virtiofsd → /mnt/workspace
                                                          virtiofs   → /root/.claude
                                                                       /root/.codex
  └─ VMSpawnContext ─────────────────────────vsock 9999──> guest-agent
       └─ VsockProcessHandle                               └─ aloop / claude-code-acp /
            └─ stdin/stdout ─────────────────vsock──────>     codex-acp
                                                              └─ ACP stdio
```

The vmmd daemon (VM Manager Daemon) runs as root and handles all
privileged operations: virtiofsd, cloud-hypervisor, tap network setup,
shared memory mapping. Stepwise runs unprivileged and talks to vmmd
via a Unix socket at `~/.stepwise/vmm/vmmd.sock`. The ACP data path
(stdin/stdout) goes directly from stepwise to the guest via vsock —
vmmd only handles the control plane (boot/destroy).

**Adapter-in-VM model** (post-2026-04-14, "A1"). All three ACP adapters
(`aloop`, `@zed-industries/claude-code-acp`, `@zed-industries/codex-acp`)
run **inside** the guest VM, not on the host. Their fs/terminal tools
execute in-process — putting the adapter on the host (the earlier "bridge"
design) defeated containment because every Read/Write/Bash ran with host
privileges. The adapter-in-VM design means everything an agent does
(LLM calls excepted, see network) happens inside the hardware boundary.

1. Stepwise resolves the agent config (tools, paths, model, containment)
2. The `ContainmentLayer` checks its `ResourceLifecycleManager`: is there
   a running VM with a matching config? If yes, reuse. If no, boot one.
3. virtiofsd shares the working directory from host to guest at
   `/mnt/workspace`. For `claude` and `codex`, the agent's host
   credential dir (`~/.claude` or `~/.codex`) is virtiofs-mounted
   read-write into the VM at `/root/.claude` or `/root/.codex`.
4. The guest agent (vsock port 9999) accepts a connection, spawns
   the ACP adapter inside the VM, and bridges its stdin/stdout
5. `JsonRpcTransport` talks ACP JSON-RPC over the vsock stream —
   identical to the non-contained path
6. When no more steps need this VM config, the VM is shut down

### Credentials

Each agent's auth path is handled differently:

- **`claude`** — virtiofs mounts host `~/.claude/` (containing
  `.credentials.json` and `projects/`) into the VM at `/root/.claude/`.
  The OAuth refresh tokens live there and the in-VM Claude Code
  process reads them. Empty `ANTHROPIC_API_KEY` env values are
  stripped before VM spawn — `claude-agent-acp` treats `""` as
  "external API key auth selected" and rejects the OAuth file.
- **`codex`** — same pattern with `~/.codex/`.
- **`aloop`** — uses `OPENROUTER_API_KEY` env var. If it isn't already
  in env, stepwise reads `~/.aloop/credentials.json` host-side and
  injects the `api_key` into the VM's spawn env. (The credentials
  file isn't accessible inside the VM; without this, aloop sessions
  silently return 0-token end_turn results.)

### Rootfs writability

The VM rootfs is mounted read-only — cloud-hypervisor's sector-0 write
protection prevents accidental rootfs corruption. Agents that need to
write to dotfile directories use symlinks into tmpfs:

- `/etc/resolv.conf` → `/tmp/resolv.conf`
- `/root/.claude.json` → `/tmp/.claude.json` (Claude Code looks for
  this flat file at HOME, not inside `~/.claude/`)
- `/root/.aloop` → `/tmp/.aloop` (aloop writes session state under
  `~/.aloop/sessions/`; per-VM ephemeral is correct since session
  continuity within a VM is preserved by ResourceLifecycleManager
  reuse, not by file persistence)

### VM reuse and session continuity

When a flow has multiple agent steps for the same agent in the same
session, the ResourceLifecycleManager keeps the VM alive between them.
A two-step flow with `session: my_session` boots one VM, runs step A,
keeps it warm, runs step B in the same VM with the ACP session
preserved. Verified end-to-end at Tier 4: 6 steps × 3 agents → 3
VMs (one per agent), not 6.

### VM Grouping

Steps are grouped into VMs by equivalence on their resolved config:

```python
def vm_needs_new(a, b):
    return not (a.tools == b.tools
                and a.allowed_paths == b.allowed_paths
                and a.credentials == b.credentials
                and a.network == b.network)
```

For example, in a flow with a research agent (web search only) and a
deploy agent (AWS credentials), these run in separate VMs — the research
agent can never access the deploy credentials because they're in different
hardware boundaries.

### Filesystem

Working directories are shared via [virtiofs](https://virtio-fs.gitlab.io/),
a shared filesystem protocol that gives near-native performance. The host
kernel enforces subtree boundaries — the guest can only access the
directories explicitly mounted.

The VM rootfs is mounted read-only. Agents work exclusively on the
virtiofs-mounted workspace.

### Communication

Agent communication uses [vsock](https://man7.org/linux/man-pages/man7/vsock.7.html),
a host-guest socket protocol. The ACP JSON-RPC transport works identically
over vsock as over local stdio — the containment is transparent to the
ACP protocol layer.

## Security Model

### What containment prevents

- **Cross-config credential theft**: research agent can't access deploy
  credentials (different VMs)
- **Host filesystem access**: agent can only access explicitly mounted paths
- **Host kernel exposure**: Cloud-Hypervisor provides hardware VM isolation

### What containment does NOT prevent (v1)

- **Within-VM misbehavior**: agents can use their declared tools liberally
  on poisoned context. This is accepted — the VM envelope bounds the damage.
- **Network exfiltration**: v1 gives VMs full network access. Network
  isolation and credential proxying are planned for v2 (see
  [containment architecture report](../../data/reports/2026-04-10-stepwise-containment-architecture.md)).

### Threat model document

See the full threat model in the
[containment architecture report](../../data/reports/2026-04-10-stepwise-containment-architecture.md),
including council review findings and design evolution.

## Security Audit

View the containment security profile of a flow:

```bash
stepwise audit my-flow.yaml
```

This shows which steps run in VMs, which run on host, how many VM groups
exist, and what each group can access.

## Troubleshooting

### `stepwise doctor --containment` fails

- **KVM not available**: Enable virtualization in BIOS (VT-x / AMD-V)
- **cloud-hypervisor not found**: Download the static binary from GitHub releases
- **virtiofsd not found**: Install from package manager or `cargo install virtiofsd`
- **vhost_vsock module not loaded**: Run `sudo modprobe vhost_vsock`

### VM boot fails with "Kernel panic: Unable to mount root fs"

The rootfs must be mounted read-only (`ro` in kernel cmdline). This is
handled automatically. If you see this error, rebuild the rootfs:

```bash
stepwise build-rootfs
```

### vmmd won't start

vmmd requires root privileges. Start with `sudo`:

```bash
sudo stepwise vmmd start --detach
```

Check status with `stepwise vmmd status`. If vmmd died, check the log
at `~/.stepwise/vmm/vmmd.log`.

### "Permission denied" creating job dirs after a containment run

`virtiofsd` runs as root and during VM teardown can leave the
`.stepwise/jobs/` parent dir owned by root, blocking subsequent
non-root job creation. Reset:

```bash
sudo chown -R "$USER:$USER" .stepwise/jobs/
```

A `stepwise vmmd clean` also drops residual VM state. Both are
idempotent; run either when a job fails at the seed step with
`PermissionError: '.stepwise/jobs/job-...'`.

### aloop returns empty responses (`{"stopReason":"end_turn","usage":{"inputTokens":0,...}}`)

Means aloop never made an LLM call. Most likely the VM doesn't have
the OpenRouter key. Check:

```bash
ls ~/.aloop/credentials.json   # must exist with {"api_key": "sk-or-..."}
echo $OPENROUTER_API_KEY       # or set this before launching stepwise
```

Stepwise auto-injects the credentials.json key when env is unset.
If neither is present, the VM has no way to authenticate.

### "Dead PID detected" / retry storm on a containment step

Pre-2026-04-14 bug: host-side process liveness checks (`os.kill(pid, 0)`)
cannot see guest pids, so they registered "dead" every 15s and triggered
a 22-attempt retry storm. Fixed in v0.43+ by stamping
`executor_state.in_vm = True` on containment spawns and skipping the
host-side liveness check for those runs. If you see this on v0.43+,
verify the run's `executor_state` actually contains `"in_vm": true`
(it should, automatically).

## Verification — the containment staircase

Containment is verified by a staircase of four progressively harder
flow tests, all in the stepwise repo and runnable end-to-end:

| Tier | Flow | What it proves |
|------|------|----------------|
| 1 | `containment-smoke` | Each ACP adapter spawns inside the VM, handshakes ACP, prompts a single token, returns READY. |
| 2 | `containment-toolbox` | Each adapter exercises its fs/read, fs/write, and bash tools — output survives the VM→host boundary via virtiofs. |
| 3 | `containment-boundary` | Each adapter probes the boundary: read `/etc/passwd`, read `~/.ssh/id_rsa`, write to `/tmp`. Verify step compares each against ground truth on the host. Pass = no real escapes. |
| 4 | `containment-multistep` | Two-step session continuity: agent A analyzes, agent B summarizes from A's in-session memory + on-disk artifact. Verify counts new VMs (≤3 = reuse working). |

Run any tier:

```bash
stepwise run containment-smoke --wait
stepwise run containment-multistep --wait
```

The full suite ran in under three minutes (T1: 13s, T2: 51s, T3: 79s,
T4: 90s) end-to-end on a single laptop with the rootfs cached.

## Future Work

- **Network isolation**: Egress proxy with allowlists, credential placeholder
  swap (OpenShell pattern). VMs boot with no network; all outbound goes
  through a host-side proxy.
- **Docker backend**: Weaker isolation (shared kernel) but cross-platform
  (Mac/Windows via Docker Desktop).
- **Remote execution**: Run agent steps on E2B, Modal, or Fly.io instead
  of local VMs.
- **Jailer**: Namespace/cgroup/seccomp wrapper for the cloud-hypervisor
  process itself (defense-in-depth for the VMM).
