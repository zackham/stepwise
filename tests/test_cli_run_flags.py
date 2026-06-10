"""Tests for threading run flags through --wait/--async/--watch modes (F31).

`--rerun` (cache bypass) and friends were silently dropped outside the default
headless mode. These tests pin the threading through each mode's entry point.
"""

import json
from unittest.mock import patch

import pytest

from stepwise.cli import main, _watch_job_body, EXIT_SUCCESS
from stepwise.project import init_project
from stepwise.runner import _rerun_config_payload
from stepwise.store import SQLiteStore


SIMPLE_FLOW = """\
name: simple
author: test
steps:
  hello:
    run: 'echo "{\\"msg\\": \\"hi\\"}"'
    outputs: [msg]
"""


def _write_flow(tmp_path):
    flow = tmp_path / "test.flow.yaml"
    flow.write_text(SIMPLE_FLOW)
    return flow


def _capture_stdout(argv):
    import io
    import sys

    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        code = main(argv)
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout
    return code, output


class TestRerunConfigPayload:
    def test_none_for_empty(self):
        assert _rerun_config_payload(None) is None
        assert _rerun_config_payload([]) is None

    def test_builds_config_metadata(self):
        assert _rerun_config_payload(["analyze"]) == {
            "metadata": {"rerun_steps": ["analyze"]}
        }


class TestWaitAsyncFlagThreading:
    def test_wait_passes_rerun_steps(self, tmp_path):
        init_project(tmp_path)
        flow = _write_flow(tmp_path)
        with patch("stepwise.runner.run_wait", return_value=0) as mock_wait:
            rc = main([
                "--project-dir", str(tmp_path),
                "run", str(flow), "--wait", "--rerun", "hello",
            ])
        assert rc == 0
        assert mock_wait.call_args.kwargs["rerun_steps"] == ["hello"]

    def test_async_passes_rerun_steps(self, tmp_path):
        init_project(tmp_path)
        flow = _write_flow(tmp_path)
        with patch("stepwise.runner.run_async", return_value=0) as mock_async:
            rc = main([
                "--project-dir", str(tmp_path),
                "run", str(flow), "--async", "--rerun", "hello",
            ])
        assert rc == 0
        assert mock_async.call_args.kwargs["rerun_steps"] == ["hello"]

    def test_wait_local_records_rerun_on_job(self, tmp_path):
        """End-to-end: --wait --local stores rerun_steps in job config
        metadata, which is what the engine's cache bypass reads."""
        project = init_project(tmp_path)
        flow = _write_flow(tmp_path)
        code, output = _capture_stdout([
            "--project-dir", str(tmp_path),
            "run", str(flow), "--wait", "--local", "--rerun", "hello",
        ])
        assert code == EXIT_SUCCESS
        job_id = json.loads(output)["job_id"]

        store = SQLiteStore(str(project.db_path))
        try:
            job = store.load_job(job_id)
        finally:
            store.close()
        assert job.config.metadata["rerun_steps"] == ["hello"]


class TestWatchFlagThreading:
    def test_watch_body_carries_meta_notify_rerun(self, tmp_path, monkeypatch):
        """--watch submissions must carry --meta/--notify/--rerun."""
        init_project(tmp_path)
        monkeypatch.chdir(tmp_path)
        flow = _write_flow(tmp_path)

        with patch("stepwise.server_detect.detect_server",
                   return_value="http://localhost:9999"), \
             patch("stepwise.cli._submit_watch_job", return_value=EXIT_SUCCESS) as mock_submit:
            rc = main([
                "run", str(flow), "--watch", "--no-open",
                "--meta", "app.ticket=T-1",
                "--notify", "http://hooks.test/x",
                "--rerun", "hello",
            ])

        assert rc == EXIT_SUCCESS
        body = mock_submit.call_args[0][1]
        assert body["metadata"]["app"]["ticket"] == "T-1"
        assert body["notify_url"] == "http://hooks.test/x"
        assert body["config"] == {"metadata": {"rerun_steps": ["hello"]}}

    def test_watch_job_body_minimal(self):
        class _WF:
            def to_dict(self):
                return {"steps": {}}

        body = _watch_job_body(_WF(), "obj", {})
        assert body == {"objective": "obj", "workflow": {"steps": {}}, "inputs": None}
        assert "config" not in body
        assert "notify_url" not in body
        assert "metadata" not in body

    def test_watch_job_body_full(self):
        class _WF:
            def to_dict(self):
                return {"steps": {}}

        body = _watch_job_body(
            _WF(), "obj", {"a": 1},
            name="my-job",
            metadata={"sys": {}, "app": {"k": "v"}},
            notify_url="http://n",
            notify_context={"c": 1},
            rerun_steps=["s1", "s2"],
        )
        assert body["name"] == "my-job"
        assert body["inputs"] == {"a": 1}
        assert body["metadata"] == {"sys": {}, "app": {"k": "v"}}
        assert body["notify_url"] == "http://n"
        assert body["notify_context"] == {"c": 1}
        assert body["config"] == {"metadata": {"rerun_steps": ["s1", "s2"]}}
