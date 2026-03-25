"""Test that AgentExecutor rejects output_file paths that escape working_dir."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from stepwise.agent import AgentExecutor, AgentStatus


@pytest.fixture
def agent_executor():
    backend = MagicMock()
    return AgentExecutor(backend=backend, prompt="test", output_mode="file")


class TestOutputFilePathTraversal:
    def test_rejects_dotdot_escape(self, agent_executor, tmp_path):
        state = {"working_dir": str(tmp_path), "output_file": "../../etc/passwd"}
        status = AgentStatus(state="completed", exit_code=0)

        with pytest.raises(ValueError, match="output_file escapes working directory"):
            agent_executor._extract_output(state, "file", status)

    def test_rejects_absolute_path(self, agent_executor, tmp_path):
        state = {"working_dir": str(tmp_path), "output_file": "/etc/passwd"}
        status = AgentStatus(state="completed", exit_code=0)

        with pytest.raises(ValueError, match="output_file escapes working directory"):
            agent_executor._extract_output(state, "file", status)

    def test_allows_safe_relative_path(self, agent_executor, tmp_path):
        # Create the output file so _extract_output can read it
        out = tmp_path / "subdir" / "output.json"
        out.parent.mkdir(parents=True)
        out.write_text('{"result": "ok"}')

        state = {"working_dir": str(tmp_path), "output_file": "subdir/output.json"}
        status = AgentStatus(state="completed", exit_code=0)

        envelope = agent_executor._extract_output(state, "file", status)
        assert envelope.artifact["result"] == "ok"

    def test_allows_plain_filename(self, agent_executor, tmp_path):
        out = tmp_path / "output.json"
        out.write_text('{"val": 42}')

        state = {"working_dir": str(tmp_path), "output_file": "output.json"}
        status = AgentStatus(state="completed", exit_code=0)

        envelope = agent_executor._extract_output(state, "file", status)
        assert envelope.artifact["val"] == 42
