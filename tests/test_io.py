"""Tests for stepwise.io — IOAdapter, PlainAdapter, QuietAdapter, TerminalAdapter."""

from io import StringIO

import pytest

from stepwise.io import (
    IOAdapter,
    LiveFlowHandle,
    PlainAdapter,
    QuietAdapter,
    TerminalAdapter,
    create_adapter,
)


# ── PlainAdapter output ──────────────────────────────────────────────


class TestPlainAdapterOutput:
    def test_log_info(self):
        out = StringIO()
        adapter = PlainAdapter(output=out)
        adapter.log("info", "hello")
        assert "hello" in out.getvalue()

    def test_log_success(self):
        out = StringIO()
        adapter = PlainAdapter(output=out)
        adapter.log("success", "done")
        assert "✓" in out.getvalue()
        assert "done" in out.getvalue()

    def test_log_error(self):
        out = StringIO()
        adapter = PlainAdapter(output=out)
        adapter.log("error", "boom")
        assert "✗" in out.getvalue()
        assert "boom" in out.getvalue()

    def test_log_warn(self):
        out = StringIO()
        adapter = PlainAdapter(output=out)
        adapter.log("warn", "careful")
        assert "⚠" in out.getvalue()

    def test_banner(self):
        out = StringIO()
        adapter = PlainAdapter(output=out)
        adapter.banner("Stepwise v1.0", "http://localhost:8340")
        text = out.getvalue()
        assert "▸" in text
        assert "Stepwise v1.0" in text
        assert "localhost:8340" in text

    def test_banner_no_subtitle(self):
        out = StringIO()
        adapter = PlainAdapter(output=out)
        adapter.banner("Title only")
        text = out.getvalue()
        assert "Title only" in text

    def test_step_status_running(self):
        out = StringIO()
        adapter = PlainAdapter(output=out)
        adapter.step_status("build", "running")
        text = out.getvalue()
        assert "⠋" in text
        assert "build" in text
        assert "running" in text

    def test_step_status_completed_with_cost(self):
        out = StringIO()
        adapter = PlainAdapter(output=out)
        adapter.step_status("deploy", "completed", duration=2.3, cost=0.042)
        text = out.getvalue()
        assert "✓" in text
        assert "2.3s" in text
        assert "$0.042" in text

    def test_step_status_failed(self):
        out = StringIO()
        adapter = PlainAdapter(output=out)
        adapter.step_status("deploy", "failed", error="timeout")
        text = out.getvalue()
        assert "✗" in text
        assert "timeout" in text

    def test_step_status_suspended(self):
        out = StringIO()
        adapter = PlainAdapter(output=out)
        adapter.step_status("review", "suspended")
        text = out.getvalue()
        assert "◆" in text

    def test_flow_complete(self):
        out = StringIO()
        adapter = PlainAdapter(output=out)
        adapter.flow_complete(5, 12.3, cost=0.15)
        text = out.getvalue()
        assert "✓" in text
        assert "5 steps" in text
        assert "12.3s" in text
        assert "$0.150" in text

    def test_flow_complete_no_cost(self):
        out = StringIO()
        adapter = PlainAdapter(output=out)
        adapter.flow_complete(3, 5.0)
        text = out.getvalue()
        assert "3 steps" in text
        assert "$" not in text

    def test_flow_failed(self):
        out = StringIO()
        adapter = PlainAdapter(output=out)
        adapter.flow_failed("connection timeout")
        text = out.getvalue()
        assert "✗" in text
        assert "failed" in text.lower()
        assert "connection timeout" in text

    def test_flow_failed_no_error(self):
        out = StringIO()
        adapter = PlainAdapter(output=out)
        adapter.flow_failed()
        text = out.getvalue()
        assert "failed" in text.lower()

    def test_table(self):
        out = StringIO()
        adapter = PlainAdapter(output=out)
        adapter.table(
            ["ID", "STATUS", "NAME"],
            [
                ["j1", "completed", "test-flow"],
                ["j2", "running", "build"],
            ],
        )
        text = out.getvalue()
        assert "ID" in text
        assert "STATUS" in text
        assert "j1" in text
        assert "completed" in text
        assert "j2" in text

    def test_table_empty(self):
        out = StringIO()
        adapter = PlainAdapter(output=out)
        adapter.table([], [])
        assert out.getvalue() == ""

    def test_note(self):
        out = StringIO()
        adapter = PlainAdapter(output=out)
        adapter.note("Review the results.", title="Human Input")
        text = out.getvalue()
        assert "Human Input" in text
        assert "Review the results." in text

    def test_note_no_title(self):
        out = StringIO()
        adapter = PlainAdapter(output=out)
        adapter.note("Just some info.")
        text = out.getvalue()
        assert "Just some info." in text


# ── PlainAdapter input ───────────────────────────────────────────────


class TestPlainAdapterInput:
    def test_prompt_confirm_yes(self):
        inp = StringIO("y\n")
        out = StringIO()
        adapter = PlainAdapter(output=out, input_stream=inp)
        assert adapter.prompt_confirm("Proceed?") is True

    def test_prompt_confirm_no(self):
        inp = StringIO("n\n")
        out = StringIO()
        adapter = PlainAdapter(output=out, input_stream=inp)
        assert adapter.prompt_confirm("Proceed?") is False

    def test_prompt_confirm_default_true(self):
        inp = StringIO("\n")
        out = StringIO()
        adapter = PlainAdapter(output=out, input_stream=inp)
        assert adapter.prompt_confirm("Proceed?", default=True) is True

    def test_prompt_confirm_default_false(self):
        inp = StringIO("\n")
        out = StringIO()
        adapter = PlainAdapter(output=out, input_stream=inp)
        assert adapter.prompt_confirm("Proceed?", default=False) is False

    def test_prompt_select_by_number(self):
        inp = StringIO("2\n")
        out = StringIO()
        adapter = PlainAdapter(output=out, input_stream=inp)
        result = adapter.prompt_select("Pick:", ["a", "b", "c"])
        assert result == "b"

    def test_prompt_select_by_text(self):
        inp = StringIO("c\n")
        out = StringIO()
        adapter = PlainAdapter(output=out, input_stream=inp)
        result = adapter.prompt_select("Pick:", ["a", "b", "c"])
        assert result == "c"

    def test_prompt_multi_select(self):
        inp = StringIO("1\n3\n\n")
        out = StringIO()
        adapter = PlainAdapter(output=out, input_stream=inp)
        result = adapter.prompt_multi_select("Pick:", ["a", "b", "c"])
        assert result == ["a", "c"]

    def test_prompt_multi_select_toggle(self):
        inp = StringIO("1\n1\n2\n\n")
        out = StringIO()
        adapter = PlainAdapter(output=out, input_stream=inp)
        result = adapter.prompt_multi_select("Pick:", ["a", "b", "c"])
        assert result == ["b"]  # toggled a off, added b

    def test_prompt_text(self):
        inp = StringIO("hello world\n")
        out = StringIO()
        adapter = PlainAdapter(output=out, input_stream=inp)
        result = adapter.prompt_text("Enter:")
        assert result == "hello world"

    def test_prompt_text_default(self):
        inp = StringIO("\n")
        out = StringIO()
        adapter = PlainAdapter(output=out, input_stream=inp)
        result = adapter.prompt_text("Enter:", default="fallback")
        assert result == "fallback"

    def test_prompt_text_multiline(self):
        inp = StringIO("line one\nline two\n\n")
        out = StringIO()
        adapter = PlainAdapter(output=out, input_stream=inp)
        result = adapter.prompt_text("Enter:", multiline=True)
        assert result == "line one\nline two"

    def test_prompt_number(self):
        inp = StringIO("7.5\n")
        out = StringIO()
        adapter = PlainAdapter(output=out, input_stream=inp)
        result = adapter.prompt_number("Score:")
        assert result == 7.5

    def test_prompt_number_default(self):
        inp = StringIO("\n")
        out = StringIO()
        adapter = PlainAdapter(output=out, input_stream=inp)
        result = adapter.prompt_number("Score:", default=5.0)
        assert result == 5.0

    def test_prompt_number_retry(self):
        inp = StringIO("abc\n7\n")
        out = StringIO()
        adapter = PlainAdapter(output=out, input_stream=inp)
        result = adapter.prompt_number("Score:")
        assert result == 7.0

    def test_prompt_number_min_max(self):
        inp = StringIO("15\n8\n")
        out = StringIO()
        adapter = PlainAdapter(output=out, input_stream=inp)
        result = adapter.prompt_number("Score:", min_val=0, max_val=10)
        assert result == 8.0


# ── collect_field ─────────────────────────────────────────────────────


class TestCollectField:
    def test_str_field(self):
        inp = StringIO("hello\n")
        out = StringIO()
        adapter = PlainAdapter(output=out, input_stream=inp)
        name, val = adapter.collect_field("name", {"type": "str"}, True)
        assert name == "name"
        assert val == "hello"

    def test_str_default(self):
        inp = StringIO("\n")
        out = StringIO()
        adapter = PlainAdapter(output=out, input_stream=inp)
        _, val = adapter.collect_field("name", {"type": "str", "default": "world"}, True)
        assert val == "world"

    def test_str_optional_blank(self):
        inp = StringIO("\n")
        out = StringIO()
        adapter = PlainAdapter(output=out, input_stream=inp)
        _, val = adapter.collect_field("name", {"type": "str", "required": False}, True)
        assert val is None

    def test_bool_field(self):
        inp = StringIO("y\n")
        out = StringIO()
        adapter = PlainAdapter(output=out, input_stream=inp)
        _, val = adapter.collect_field("approved", {"type": "bool"}, True)
        assert val is True

    def test_number_field(self):
        inp = StringIO("7.5\n")
        out = StringIO()
        adapter = PlainAdapter(output=out, input_stream=inp)
        _, val = adapter.collect_field("score", {"type": "number"}, True)
        assert val == 7.5

    def test_number_with_range(self):
        inp = StringIO("15\n8\n")
        out = StringIO()
        adapter = PlainAdapter(output=out, input_stream=inp)
        _, val = adapter.collect_field(
            "score", {"type": "number", "min": 0, "max": 10}, True,
        )
        assert val == 8.0

    def test_choice_field(self):
        inp = StringIO("2\n")
        out = StringIO()
        adapter = PlainAdapter(output=out, input_stream=inp)
        _, val = adapter.collect_field(
            "pick", {"type": "choice", "options": ["a", "b", "c"]}, True,
        )
        assert val == "b"

    def test_choice_multiple(self):
        inp = StringIO("1\n3\n\n")
        out = StringIO()
        adapter = PlainAdapter(output=out, input_stream=inp)
        _, val = adapter.collect_field(
            "picks",
            {"type": "choice", "options": ["a", "b", "c"], "multiple": True},
            True,
        )
        assert val == ["a", "c"]

    def test_text_multiline(self):
        inp = StringIO("line one\nline two\n\n")
        out = StringIO()
        adapter = PlainAdapter(output=out, input_stream=inp)
        _, val = adapter.collect_field("notes", {"type": "text"}, True)
        assert val == "line one\nline two"

    def test_text_optional_blank(self):
        inp = StringIO("\n")
        out = StringIO()
        adapter = PlainAdapter(output=out, input_stream=inp)
        _, val = adapter.collect_field("notes", {"type": "text", "required": False}, True)
        assert val is None

    def test_no_schema_falls_back_to_str(self):
        inp = StringIO("hello\n")
        out = StringIO()
        adapter = PlainAdapter(output=out, input_stream=inp)
        _, val = adapter.collect_field("answer", None, True)
        assert val == "hello"

    def test_description_shown(self):
        inp = StringIO("hello\n")
        out = StringIO()
        adapter = PlainAdapter(output=out, input_stream=inp)
        adapter.collect_field("name", {"type": "str", "description": "Your name"}, True)
        assert "Your name" in out.getvalue()


# ── collect_human_input ──────────────────────────────────────────────


class TestCollectHumanInput:
    def test_single_field(self):
        inp = StringIO("Alice\n")
        out = StringIO()
        adapter = PlainAdapter(output=out, input_stream=inp)
        payload = adapter.collect_human_input("What's your name?", ["name"])
        assert payload == {"name": "Alice"}

    def test_multi_field(self):
        inp = StringIO("y\nhello\n")
        out = StringIO()
        adapter = PlainAdapter(output=out, input_stream=inp)
        payload = adapter.collect_human_input(
            "Review",
            ["approved", "feedback"],
            {"approved": {"type": "bool"}, "feedback": {"type": "str"}},
        )
        assert payload["approved"] is True
        assert payload["feedback"] == "hello"

    def test_prompt_shown(self):
        inp = StringIO("ok\n")
        out = StringIO()
        adapter = PlainAdapter(output=out, input_stream=inp)
        adapter.collect_human_input("Please review this.", ["response"])
        assert "Please review this" in out.getvalue()


# ── QuietAdapter ─────────────────────────────────────────────────────


class TestQuietAdapter:
    def test_output_suppressed(self):
        adapter = QuietAdapter()
        # None of these should raise or produce visible output
        adapter.log("info", "should not appear")
        adapter.banner("title")
        adapter.step_status("build", "running")
        adapter.flow_complete(5, 10.0)
        adapter.flow_failed("error")
        adapter.table(["A"], [["x"]])
        adapter.note("content")

    def test_input_still_works(self):
        inp = StringIO("y\n")
        adapter = QuietAdapter(input_stream=inp)
        assert adapter.prompt_confirm("ok?") is True

    def test_live_flow_works(self):
        from stepwise.io import StepNode
        adapter = QuietAdapter()
        with adapter.live_flow("test", ["a", "b"]) as handle:
            handle.render_tree([StepNode("a", "running")])
            handle.render_tree([StepNode("a", "completed", duration=1.0)])
            handle.flush_all()


# ── PlainAdapter live_flow ────────────────────────────────────────────


class TestPlainLiveFlow:
    def test_updates_produce_output(self):
        from stepwise.io import StepNode
        out = StringIO()
        adapter = PlainAdapter(output=out)
        with adapter.live_flow("test-flow", ["a", "b"]) as handle:
            handle.render_tree([StepNode("a", "running")])
            handle.render_tree([StepNode("a", "completed", duration=1.0)])
        text = out.getvalue()
        assert "completed" in text

    def test_pause_resume_noop(self):
        out = StringIO()
        adapter = PlainAdapter(output=out)
        with adapter.live_flow("test", ["a"]) as handle:
            handle.pause_for_input()
            handle.resume_after_input()


# ── create_adapter factory ───────────────────────────────────────────


class TestCreateAdapter:
    def test_quiet_returns_quiet(self):
        adapter = create_adapter(quiet=True)
        assert isinstance(adapter, QuietAdapter)

    def test_force_plain_returns_plain(self):
        adapter = create_adapter(force_plain=True)
        assert isinstance(adapter, PlainAdapter)

    def test_non_tty_returns_plain(self):
        # StringIO has no isatty, so it's non-TTY
        adapter = create_adapter(output=StringIO())
        assert isinstance(adapter, PlainAdapter)

    def test_force_plain_with_stringio(self):
        out = StringIO()
        inp = StringIO("test\n")
        adapter = create_adapter(force_plain=True, output=out, input_stream=inp)
        assert isinstance(adapter, PlainAdapter)


# ── TerminalAdapter smoke tests ──────────────────────────────────────


class TestTerminalAdapterSmoke:
    def test_instantiation(self):
        # Just verify it can be created without errors
        adapter = TerminalAdapter()
        assert isinstance(adapter, IOAdapter)

    def test_log_methods(self):
        adapter = TerminalAdapter()
        adapter.log("info", "test info")
        adapter.log("success", "test success")
        adapter.log("warn", "test warn")
        adapter.log("error", "test error")

    def test_banner(self):
        adapter = TerminalAdapter()
        adapter.banner("Test Banner", "subtitle here")

    def test_step_status(self):
        adapter = TerminalAdapter()
        adapter.step_status("build", "running")
        adapter.step_status("build", "completed", duration=1.5, cost=0.01)
        adapter.step_status("deploy", "failed", error="timeout")

    def test_flow_complete(self):
        adapter = TerminalAdapter()
        adapter.flow_complete(3, 5.2, cost=0.15)

    def test_flow_failed(self):
        adapter = TerminalAdapter()
        adapter.flow_failed("connection error")

    def test_table(self):
        adapter = TerminalAdapter()
        adapter.table(["A", "B"], [["1", "2"], ["3", "4"]])

    def test_note(self):
        adapter = TerminalAdapter()
        adapter.note("Some content", title="Title")
