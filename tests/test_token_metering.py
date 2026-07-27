"""Token metering end-to-end: ACP payload -> executor_meta -> job/step status.

Under subscription billing `cost_usd` is zeroed by design (an agent run carries no
dollar charge), so cost-only metering reports 0 for every agent step. That made real
consumption invisible: in production virtually every agent step recorded cost_usd=0
while the account's weekly rate-limit window filled up unobserved over three days. The scarce
resource is quota, denominated in tokens — and cached reads are weighted far cheaper
than fresh input, so the split matters, not just the total.

These tests pin the contract: cost answers "what was billed", usage answers "what was
consumed", and neither is allowed to fake the other.
"""
import json
import os
import tempfile

import pytest

from stepwise.acp_ndjson import extract_usage, normalize_usage

REAL_USAGE = {
    "inputTokens": 754,
    "outputTokens": 943,
    "cachedReadTokens": 79700,
    "cachedWriteTokens": 4078,
    "totalTokens": 85475,
}
NORMALIZED = normalize_usage(REAL_USAGE)


def _ndjson(objs):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as f:
        for o in objs:
            f.write(json.dumps(o) + "\n")
    return path


# ── backend: AgentStatus carries usage ────────────────────────────────

def test_acp_backend_wait_reports_usage():
    from stepwise.acp_backend import ACPBackend
    from stepwise.agent import AgentProcess

    path = _ndjson([
        {"jsonrpc": "2.0", "id": 0, "result": {
            "sessionId": "s-1", "stopReason": "end_turn", "usage": REAL_USAGE}},
    ])
    try:
        proc = AgentProcess(pid=0, pgid=0, output_path=path, working_dir="/tmp",
                            session_id="s-1")
        status = ACPBackend().wait(proc)
        assert status.state == "completed"
        assert status.usage == NORMALIZED
        assert status.usage["cached_read_tokens"] == 79700
    finally:
        os.unlink(path)


def test_acp_backend_usage_is_none_when_transport_silent():
    from stepwise.acp_backend import ACPBackend
    from stepwise.agent import AgentProcess

    path = _ndjson([
        {"jsonrpc": "2.0", "id": 0, "result": {
            "sessionId": "s-1", "stopReason": "end_turn"}},
    ])
    try:
        proc = AgentProcess(pid=0, pgid=0, output_path=path, working_dir="/tmp",
                            session_id="s-1")
        status = ACPBackend().wait(proc)
        assert status.usage is None, "unmeasured must stay None, not a zero dict"
    finally:
        os.unlink(path)


# ── executor: subscription zeroes COST but never TOKENS ───────────────

def _envelope(billing_mode, usage):
    from stepwise.agent import AgentExecutor, AgentStatus

    ex = AgentExecutor.__new__(AgentExecutor)
    ex.config = {"_billing_mode": billing_mode}
    status = AgentStatus(state="completed", exit_code=0, session_id="s-1",
                         cost_usd=0.86, usage=usage)
    return ex._extract_output({"working_dir": "/tmp"}, "effect", status)


def test_subscription_zeroes_cost_but_keeps_tokens():
    """THE REGRESSION THIS SHIPS FOR."""
    env = _envelope("subscription", NORMALIZED)
    assert env.executor_meta["cost_usd"] == 0, "dollars are genuinely zero here"
    assert env.executor_meta["usage"]["total_tokens"] == 85475, (
        "tokens must survive — they are the only consumption signal on a subscription"
    )


def test_api_key_billing_keeps_both():
    env = _envelope("api_key", NORMALIZED)
    assert env.executor_meta["cost_usd"] == 0.86
    assert env.executor_meta["usage"]["total_tokens"] == 85475


def test_usage_key_absent_when_unreported():
    """No `usage` key at all, rather than a zero-filled one."""
    env = _envelope("subscription", None)
    assert "usage" not in env.executor_meta


# ── engine aggregation ────────────────────────────────────────────────

class _Result:
    def __init__(self, meta):
        self.executor_meta = meta
        self.artifact = {}


class _Run:
    def __init__(self, meta, sub_job_id=None):
        self.result = _Result(meta) if meta is not None else None
        self.sub_job_id = sub_job_id
        self.id = "r1"


def _engine_with(runs_by_job):
    from stepwise.engine import Engine

    eng = Engine.__new__(Engine)

    class _Store:
        def runs_for_job(self, job_id):
            return runs_by_job.get(job_id, [])

    eng.store = _Store()
    return eng


def test_run_tokens_reads_executor_meta():
    eng = _engine_with({})
    assert eng._run_tokens(_Run({"usage": NORMALIZED}))["output_tokens"] == 943


def test_run_tokens_empty_when_unmetered():
    eng = _engine_with({})
    assert eng._run_tokens(_Run({"cost_usd": 0})) == {}
    assert eng._run_tokens(_Run(None)) == {}


def test_run_tokens_ignores_unknown_fields():
    """A new field in the ACP payload must not silently start summing garbage."""
    eng = _engine_with({})
    out = eng._run_tokens(_Run({"usage": {"input_tokens": 5, "bogus_tokens": 99}}))
    assert out == {"input_tokens": 5}


def test_job_tokens_sums_across_runs():
    runs = [_Run({"usage": NORMALIZED}), _Run({"usage": NORMALIZED})]
    eng = _engine_with({"j1": runs})
    assert eng.job_tokens("j1")["total_tokens"] == 85475 * 2
    assert eng.job_tokens("j1")["output_tokens"] == 943 * 2


def test_job_tokens_includes_sub_jobs():
    eng = _engine_with({
        "parent": [_Run({"usage": NORMALIZED}, sub_job_id="child")],
        "child": [_Run({"usage": NORMALIZED})],
    })
    assert eng.job_tokens("parent")["total_tokens"] == 85475 * 2


def test_job_tokens_empty_when_nothing_metered():
    """An unmetered job must not render as a metered zero."""
    eng = _engine_with({"j1": [_Run({"cost_usd": 0})]})
    assert eng.job_tokens("j1") == {}
