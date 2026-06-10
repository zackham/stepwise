"""Escalated (PAUSED) jobs must terminate CLI wait loops, not hang them.

Regression tests for the RC hardening pass: every runner wait loop
previously only broke on completed/failed/cancelled, so a flow that hit
an `action: escalate` exit rule left `stepwise run` blocked forever.
"""

import io
import json
import sys
from unittest.mock import patch

from stepwise.cli import main
from stepwise.project import init_project
from stepwise.runner import EXIT_SUSPENDED, _aggregate_exit_code


def _write_escalating_flow(tmp_path):
    flow = tmp_path / "escalate.flow.yaml"
    flow.write_text(
        "name: escalate-flow\n"
        "author: test\n"
        "steps:\n"
        "  flaky:\n"
        "    run: |\n"
        "      echo '{\"status\": \"bad\"}'\n"
        "    outputs: [status]\n"
        "    exits:\n"
        "      - name: ok\n"
        "        when: \"outputs.status == 'good'\"\n"
        "        action: advance\n"
        "      - name: stuck\n"
        "        when: \"True\"\n"
        "        action: escalate\n"
    )
    return flow


def _capture_stdout(argv):
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        code = main(argv)
    finally:
        sys.stdout = old
    return code, buf.getvalue()


class TestPausedJobExitsWaitLoop:
    def test_wait_local_exits_suspended_on_escalate(self, tmp_path):
        init_project(tmp_path)
        flow = _write_escalating_flow(tmp_path)
        code, output = _capture_stdout([
            "--project-dir", str(tmp_path),
            "run", str(flow), "--wait", "--local",
        ])
        assert code == EXIT_SUSPENDED
        result = json.loads(output)
        assert result["status"] == "paused"
        assert result["job_id"]

    def test_aggregate_exit_code_treats_paused_as_suspended(self):
        results = [
            {"status": "completed"},
            {"status": "paused"},
        ]
        assert _aggregate_exit_code(results) == EXIT_SUSPENDED


class TestDelegatedCreateAndStart:
    def test_start_failure_reports_orphaned_job_id(self):
        """If the start POST fails after create succeeded, the error must
        name the job id so the orphaned server job isn't invisible."""
        from stepwise.models import WorkflowDefinition
        from stepwise.runner import _delegated_create_and_start

        wf = WorkflowDefinition(steps={})

        class FakeResp:
            def __init__(self, ok=True, payload=None):
                self._ok = ok
                self._payload = payload or {}

            def raise_for_status(self):
                if not self._ok:
                    raise RuntimeError("HTTP 500")

            def json(self):
                return self._payload

        calls = []

        def fake_post(url, **kwargs):
            calls.append(url)
            if url.endswith("/api/jobs"):
                return FakeResp(payload={"id": "job-abc123"})
            return FakeResp(ok=False)

        with patch("stepwise.runner.httpx.post", side_effect=fake_post):
            job_id, err = _delegated_create_and_start(
                "http://localhost:9", wf, "obj", {}, None,
            )
        assert job_id is None
        assert "job-abc123" in err
        assert len(calls) == 2
