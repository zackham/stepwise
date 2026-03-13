"""Tests for the editor LLM agent (acpx-based flow builder)."""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from stepwise.editor_llm import (
    _build_prompt,
    _extract_file_blocks,
    _extract_yaml_blocks,
    _get_flow_dir_listing,
    get_or_create_session,
    clear_session,
    chat_stream,
    get_system_prompt,
)


# ── _build_prompt ────────────────────────────────────────────────────


class TestBuildPrompt:

    def test_basic_message(self):
        result = _build_prompt("create a flow", [], None, None)
        assert "(no flow selected)" in result
        assert "create a flow" in result
        assert "Current Flow" not in result

    def test_with_yaml_context(self):
        yaml = "name: test\nsteps:\n  a:\n    run: echo hi"
        result = _build_prompt("modify step a", [], yaml, None)
        assert "```yaml" in result
        assert yaml in result

    def test_with_yaml_and_selected_step(self):
        yaml = "name: test\nsteps:\n  a:\n    run: echo hi"
        result = _build_prompt("improve this", [], yaml, "a")
        assert yaml in result
        assert "`a` selected" in result

    def test_selected_step_without_yaml_ignored(self):
        result = _build_prompt("hello", [], None, None)
        assert "has step" not in result

    def test_with_history(self):
        history = [
            {"role": "user", "content": "make a flow"},
            {"role": "assistant", "content": "here is a flow"},
        ]
        result = _build_prompt("now modify it", history, None, None)
        assert "**User:** make a flow" in result
        assert "**Assistant:** here is a flow" in result

    def test_history_capped_at_8(self):
        history = [{"role": "user", "content": f"msg-{i}"} for i in range(12)]
        result = _build_prompt("latest", history, None, None)
        assert "msg-4" in result
        assert "msg-11" in result
        assert "msg-3" not in result

    def test_with_flow_dir_listing(self):
        result = _build_prompt("hello", [], None, None, flow_dir_listing="FLOW.yaml\nscripts/\n  fetch.py")
        assert "Flow Directory" in result
        assert "FLOW.yaml" in result
        assert "fetch.py" in result

    def test_with_flow_dir_path(self):
        result = _build_prompt("hello", [], None, None, flow_dir_path="/home/user/flows/my-flow")
        assert "/home/user/flows/my-flow" in result
        assert "(no flow selected)" not in result


# ── File Block Extraction ─────────────────────────────────────────


class TestExtractFileBlocks:

    def test_single_file_block(self):
        text = '```file:FLOW.yaml\nname: test\nsteps: {}\n```'
        blocks = _extract_file_blocks(text)
        assert len(blocks) == 1
        assert blocks[0]["path"] == "FLOW.yaml"
        assert "name: test" in blocks[0]["content"]

    def test_multiple_file_blocks(self):
        text = '```file:FLOW.yaml\nname: test\n```\n\n```file:scripts/fetch.py\nprint("hi")\n```'
        blocks = _extract_file_blocks(text)
        assert len(blocks) == 2
        assert blocks[0]["path"] == "FLOW.yaml"
        assert blocks[1]["path"] == "scripts/fetch.py"

    def test_rejects_absolute_path(self):
        text = '```file:/etc/passwd\nroot:x:0\n```'
        blocks = _extract_file_blocks(text)
        assert len(blocks) == 0

    def test_rejects_path_traversal(self):
        text = '```file:../../etc/passwd\nroot:x:0\n```'
        blocks = _extract_file_blocks(text)
        assert len(blocks) == 0

    def test_rejects_traversal_in_middle(self):
        text = '```file:scripts/../../../etc/shadow\nbad\n```'
        blocks = _extract_file_blocks(text)
        assert len(blocks) == 0

    def test_empty_content(self):
        text = '```file:empty.txt\n```'
        blocks = _extract_file_blocks(text)
        assert len(blocks) == 1
        assert blocks[0]["content"] == ""

    def test_no_file_blocks(self):
        text = "just some text\n```yaml\nname: test\n```"
        blocks = _extract_file_blocks(text)
        assert len(blocks) == 0


class TestExtractYamlBlocks:

    def test_plain_yaml_block(self):
        text = '```yaml\nname: test\nsteps: {}\n```'
        blocks = _extract_yaml_blocks(text)
        assert len(blocks) == 1
        assert "name: test" in blocks[0]

    def test_file_yaml_not_double_counted(self):
        """file:FLOW.yaml blocks should NOT also appear as yaml blocks."""
        text = '```file:FLOW.yaml\nname: test\n```\n\n```yaml\nname: other\n```'
        blocks = _extract_yaml_blocks(text)
        assert len(blocks) == 1
        assert "name: other" in blocks[0]

    def test_no_yaml_blocks(self):
        text = "no code blocks here"
        assert _extract_yaml_blocks(text) == []


# ── Flow Dir Listing ──────────────────────────────────────────────


class TestFlowDirListing:

    def test_directory_flow(self, tmp_path):
        flow_dir = tmp_path / "flows" / "my-flow"
        flow_dir.mkdir(parents=True)
        (flow_dir / "FLOW.yaml").write_text("name: test")
        scripts = flow_dir / "scripts"
        scripts.mkdir()
        (scripts / "fetch.py").write_text("print()")

        listing = _get_flow_dir_listing(tmp_path, "flows/my-flow/FLOW.yaml")
        assert listing is not None
        assert "FLOW.yaml" in listing
        assert "scripts/" in listing
        assert "fetch.py" in listing

    def test_single_file_flow_returns_none(self, tmp_path):
        (tmp_path / "test.flow.yaml").write_text("name: test")
        listing = _get_flow_dir_listing(tmp_path, "test.flow.yaml")
        assert listing is None

    def test_no_flow_path(self, tmp_path):
        assert _get_flow_dir_listing(tmp_path, None) is None


# ── Session Management ────────────────────────────────────────────


class TestSessionManagement:

    def test_create_new_session(self):
        sid, name = get_or_create_session(None)
        assert sid
        assert name.startswith("editor-")
        # Cleanup
        clear_session(sid)

    def test_reuse_existing_session(self):
        sid1, name1 = get_or_create_session(None)
        sid2, name2 = get_or_create_session(sid1)
        assert sid1 == sid2
        assert name1 == name2
        clear_session(sid1)

    def test_clear_session(self):
        sid, _ = get_or_create_session(None)
        clear_session(sid)
        sid2, _ = get_or_create_session(sid)
        assert sid2 != sid  # Should create new after clear


# ── chat_stream routing ──────────────────────────────────────────


class TestChatStreamRouting:

    def test_uses_acpx_when_available(self, tmp_path):
        with patch("stepwise.editor_llm.shutil.which", return_value="/usr/bin/acpx"), \
             patch("stepwise.editor_llm._acpx_agent_loop") as mock_acpx:
            mock_acpx.return_value = iter([{"type": "done"}])
            chunks = list(chat_stream("hello", project_dir=tmp_path))
            mock_acpx.assert_called_once()

    def test_simple_mode_skips_acpx(self, tmp_path):
        mock_config = MagicMock()
        mock_config.openrouter_api_key = "sk-test"

        with patch("stepwise.editor_llm.shutil.which", return_value="/usr/bin/acpx"), \
             patch("stepwise.editor_llm.load_config", return_value=mock_config), \
             patch("stepwise.editor_llm._openrouter_fallback") as mock_or:
            mock_or.return_value = iter([{"type": "done"}])
            list(chat_stream("hello", project_dir=tmp_path, agent="simple"))
            mock_or.assert_called_once()

    def test_error_when_nothing_available(self, tmp_path):
        mock_config = MagicMock()
        mock_config.openrouter_api_key = None

        with patch("stepwise.editor_llm.shutil.which", return_value=None), \
             patch("stepwise.editor_llm.load_config", return_value=mock_config):
            chunks = list(chat_stream("hello", project_dir=tmp_path))
            assert chunks[0]["type"] == "error"


# ── NDJSON event parsing ─────────────────────────────────────────


class TestAcpxEventParsing:

    def _make_event(self, session_update: str, **kwargs) -> bytes:
        event = {"params": {"update": {"sessionUpdate": session_update, **kwargs}}}
        return (json.dumps(event) + "\n").encode()

    def test_text_chunks_streamed(self, tmp_path):
        lines = [
            self._make_event("agent_message_chunk", content={"type": "text", "text": "Hello "}),
            self._make_event("agent_message_chunk", content={"type": "text", "text": "world"}),
        ]
        chunks = self._run(tmp_path, lines)
        text = "".join(c["content"] for c in chunks if c["type"] == "text")
        assert text == "Hello world"

    def test_tool_use_events(self, tmp_path):
        lines = [
            self._make_event("tool_call", title="Read file", toolCallId="tc-1"),
            self._make_event("tool_call_update", toolCallId="tc-1", status="completed"),
        ]
        chunks = self._run(tmp_path, lines)
        assert any(c["type"] == "tool_use" and c["tool_name"] == "Read file" for c in chunks)
        assert any(c["type"] == "tool_result" and c["tool_use_id"] == "tc-1" for c in chunks)

    def test_files_changed_tracked(self, tmp_path):
        """Verify file writes are tracked via tool_call_update with kind=edit."""
        lines = [
            self._make_event("tool_call", title="Write file", toolCallId="tc-1", kind="edit"),
            self._make_event(
                "tool_call_update", toolCallId="tc-1", status="completed",
                kind="edit", title="Write file",
                locations=[{"path": "/tmp/flow/FLOW.yaml"}],
            ),
        ]
        chunks = self._run(tmp_path, lines)
        fc = [c for c in chunks if c["type"] == "files_changed"]
        assert len(fc) == 1
        assert "/tmp/flow/FLOW.yaml" in fc[0]["paths"]

    def test_session_id_emitted(self, tmp_path):
        lines = [self._make_event("agent_message_chunk", content={"type": "text", "text": "hi"})]
        chunks = self._run(tmp_path, lines)
        sessions = [c for c in chunks if c["type"] == "session"]
        assert len(sessions) == 1
        assert sessions[0]["session_id"]

    def test_approve_all_in_args(self, tmp_path):
        """Verify --approve-all is used for direct file writing."""
        lines = [self._make_event("agent_message_chunk", content={"type": "text", "text": "hi"})]

        mock_proc = MagicMock()
        mock_proc.stdout = lines
        mock_proc.wait.return_value = 0

        with patch("subprocess.run"), \
             patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
            from stepwise.editor_llm import _acpx_agent_loop
            list(_acpx_agent_loop("/usr/bin/acpx", "claude", "test", None, None, None, tmp_path))
            args = mock_popen.call_args[0][0]
            assert "--approve-all" in args
            assert "--approve-reads" not in args

    def _run(self, tmp_path, lines: list[bytes]) -> list[dict]:
        from stepwise.editor_llm import _acpx_agent_loop

        mock_proc = MagicMock()
        mock_proc.stdout = lines
        mock_proc.wait.return_value = 0

        with patch("subprocess.run"), \
             patch("subprocess.Popen", return_value=mock_proc):
            return list(_acpx_agent_loop(
                "/usr/bin/acpx", "claude", "test",
                None, None, None, tmp_path,
            ))
