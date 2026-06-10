"""Tests for _hoist_global_flags (global flag relocation before the subcommand)."""

from stepwise.cli import _hoist_global_flags, build_parser


class TestHoistGlobalFlags:
    def test_no_flags_passthrough(self):
        assert _hoist_global_flags(["jobs"]) == ["jobs"]

    def test_pre_subcommand_flags_untouched(self):
        argv = ["--project-dir", "/x", "jobs"]
        assert _hoist_global_flags(argv) == argv

    def test_hoists_bool_flag_after_subcommand(self):
        assert _hoist_global_flags(["jobs", "--standalone"]) == ["--standalone", "jobs"]

    def test_hoists_value_flag_after_subcommand(self):
        assert _hoist_global_flags(["jobs", "--server", "http://x"]) == [
            "--server", "http://x", "jobs",
        ]

    def test_does_not_split_value_flag_pair(self):
        """Regression (F32): hoisted flags must not land between a value flag
        and its value."""
        result = _hoist_global_flags(["--project-dir", "/x", "jobs", "--standalone"])
        assert result == ["--project-dir", "/x", "--standalone", "jobs"]

    def test_does_not_split_server_flag_pair(self):
        result = _hoist_global_flags(["--server", "http://a", "jobs", "--standalone"])
        assert result == ["--server", "http://a", "--standalone", "jobs"]

    def test_multiple_pre_subcommand_value_flags(self):
        result = _hoist_global_flags(
            ["--project-dir", "/x", "--server", "http://a", "jobs", "--standalone"]
        )
        assert result == [
            "--project-dir", "/x", "--server", "http://a", "--standalone", "jobs",
        ]

    def test_hoisted_argv_parses(self):
        """The hoisted argv must be accepted by the real parser."""
        argv = _hoist_global_flags(["--project-dir", "/x", "jobs", "--standalone"])
        args = build_parser().parse_args(argv)
        assert args.project_dir == "/x"
        assert args.standalone is True
        assert args.command == "jobs"

    def test_hoisted_server_argv_parses(self):
        argv = _hoist_global_flags(["--server", "http://x:1", "jobs", "--standalone"])
        args = build_parser().parse_args(argv)
        assert args.server == "http://x:1"
        assert args.standalone is True
        assert args.command == "jobs"
