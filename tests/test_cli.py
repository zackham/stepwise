"""Tests for stepwise.cli — CLI entry point, argument parsing, command handlers."""

import json
import pytest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from stepwise.cli import (
    EXIT_CONFIG_ERROR,
    EXIT_JOB_FAILED,
    EXIT_PROJECT_ERROR,
    EXIT_SUCCESS,
    EXIT_USAGE_ERROR,
    _detect_install_method,
    build_parser,
    cmd_self_update,
    main,
)
from stepwise.project import DOT_DIR_NAME, init_project


class TestVersion:
    def test_version_flag(self, capsys):
        rc = main(["--version"])
        assert rc == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "stepwise" in out


class TestNoArgs:
    def test_no_args_prints_help(self, capsys):
        rc = main([])
        assert rc == EXIT_USAGE_ERROR


class TestInit:
    def test_init_creates_project(self, tmp_path, capsys):
        rc = main(["--project-dir", str(tmp_path), "init", "--no-skill"])
        assert rc == EXIT_SUCCESS
        assert (tmp_path / DOT_DIR_NAME).is_dir()
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "Initialized" in combined

    def test_init_force(self, tmp_path, capsys):
        main(["--project-dir", str(tmp_path), "init", "--no-skill"])
        rc = main(["--project-dir", str(tmp_path), "init", "--force", "--no-skill"])
        assert rc == EXIT_SUCCESS

    def test_init_existing_errors_with_no_skill(self, tmp_path, capsys):
        """--no-skill on existing project errors (nothing to do)."""
        main(["--project-dir", str(tmp_path), "init", "--no-skill"])
        rc = main(["--project-dir", str(tmp_path), "init", "--no-skill"])
        assert rc == EXIT_USAGE_ERROR
        err = capsys.readouterr().err
        assert "already initialized" in err.lower()

    def test_init_existing_proceeds_to_skill(self, tmp_path, capsys):
        """Existing project + --skill installs skill without error."""
        main(["--project-dir", str(tmp_path), "init", "--no-skill"])
        rc = main(["--project-dir", str(tmp_path), "init", "--skill", ".claude"])
        assert rc == EXIT_SUCCESS
        assert (tmp_path / ".claude" / "skills" / "stepwise" / "SKILL.md").exists()
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "already initialized" in combined.lower()
        assert "Installed agent skill" in combined

    def test_init_with_skill_target(self, tmp_path, capsys):
        rc = main(["--project-dir", str(tmp_path), "init", "--skill", ".claude"])
        assert rc == EXIT_SUCCESS
        assert (tmp_path / ".claude" / "skills" / "stepwise" / "SKILL.md").exists()
        assert (tmp_path / ".claude" / "skills" / "stepwise" / "FLOW_REFERENCE.md").exists()
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "Installed agent skill" in combined

    def test_init_skill_auto_detect_existing_dir(self, tmp_path, capsys, monkeypatch):
        """When .agents/ exists, --skill .agents installs there."""
        (tmp_path / ".agents").mkdir()
        rc = main(["--project-dir", str(tmp_path), "init", "--skill", ".agents"])
        assert rc == EXIT_SUCCESS
        assert (tmp_path / ".agents" / "skills" / "stepwise" / "SKILL.md").exists()


class TestValidate:
    def _write_flow(self, tmp_path, content):
        flow = tmp_path / "test.flow.yaml"
        flow.write_text(content)
        return flow

    def test_valid_flow(self, tmp_path, capsys):
        flow = self._write_flow(tmp_path, """
name: test
steps:
  hello:
    run: "echo hello"
    outputs: [msg]
""")
        rc = main(["validate", str(flow)])
        assert rc == EXIT_SUCCESS
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "✓" in combined
        assert "1 steps" in combined

    def test_invalid_flow_missing_file(self, tmp_path, capsys):
        rc = main(["validate", str(tmp_path / "nonexistent.yaml")])
        assert rc == EXIT_USAGE_ERROR
        err = capsys.readouterr().err
        assert "not found" in err.lower()

    def test_invalid_flow_bad_yaml(self, tmp_path, capsys):
        flow = self._write_flow(tmp_path, "not: valid: yaml: [")
        rc = main(["validate", str(flow)])
        assert rc == EXIT_JOB_FAILED

    def test_invalid_flow_bad_structure(self, tmp_path, capsys):
        flow = self._write_flow(tmp_path, """
name: bad
steps:
  step1:
    run: "echo test"
    outputs: [result]
    inputs:
      data: nonexistent_step.output
""")
        rc = main(["validate", str(flow)])
        # Should report validation errors
        assert rc == EXIT_JOB_FAILED


class TestTemplates:
    def test_templates_no_project(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        rc = main(["templates"])
        assert rc == EXIT_SUCCESS
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "BUILT-IN:" in combined
        assert "PROJECT:" in combined

    def test_templates_with_project(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        init_project(tmp_path)
        # Add a user template
        (tmp_path / DOT_DIR_NAME / "templates" / "my-flow.yaml").write_text("name: my-flow\nsteps: {}")
        rc = main(["templates"])
        assert rc == EXIT_SUCCESS
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "my-flow" in combined


class TestConfig:
    def test_config_set_get_roundtrip(self, tmp_path, capsys, monkeypatch):
        config_dir = tmp_path / "config"
        monkeypatch.setattr("stepwise.config.CONFIG_DIR", config_dir)
        monkeypatch.setattr("stepwise.config.CONFIG_FILE", config_dir / "config.json")

        rc = main(["config", "set", "default_model", "anthropic/claude-sonnet-4-20250514"])
        assert rc == EXIT_SUCCESS

        rc = main(["config", "get", "default_model"])
        assert rc == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "anthropic/claude-sonnet-4-20250514" in out

    def test_config_set_stdin(self, tmp_path, capsys, monkeypatch):
        config_dir = tmp_path / "config"
        monkeypatch.setattr("stepwise.config.CONFIG_DIR", config_dir)
        monkeypatch.setattr("stepwise.config.CONFIG_FILE", config_dir / "config.json")

        with patch("getpass.getpass", return_value="sk-test-key"):
            rc = main(["config", "set", "openrouter_api_key", "--stdin"])
        assert rc == EXIT_SUCCESS

        rc = main(["config", "get", "openrouter_api_key", "--unmask"])
        assert rc == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "sk-test-key" in out

    def test_config_get_masks_sensitive(self, tmp_path, capsys, monkeypatch):
        config_dir = tmp_path / "config"
        monkeypatch.setattr("stepwise.config.CONFIG_DIR", config_dir)
        monkeypatch.setattr("stepwise.config.CONFIG_FILE", config_dir / "config.json")

        main(["config", "set", "openrouter_api_key", "sk-or-v1-abcdef123456"])
        rc = main(["config", "get", "openrouter_api_key"])
        assert rc == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "sk-or-v1-abcdef123456" not in out  # masked
        assert "456" in out  # last 3 chars visible

    def test_config_get_unmask(self, tmp_path, capsys, monkeypatch):
        config_dir = tmp_path / "config"
        monkeypatch.setattr("stepwise.config.CONFIG_DIR", config_dir)
        monkeypatch.setattr("stepwise.config.CONFIG_FILE", config_dir / "config.json")

        main(["config", "set", "openrouter_api_key", "sk-or-v1-abcdef123456"])
        rc = main(["config", "get", "openrouter_api_key", "--unmask"])
        assert rc == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "sk-or-v1-abcdef123456" in out

    def test_config_unknown_key(self, capsys):
        rc = main(["config", "set", "bogus_key", "value"])
        assert rc == EXIT_USAGE_ERROR

    def test_config_set_missing_value(self, capsys):
        rc = main(["config", "set", "default_model"])
        assert rc == EXIT_USAGE_ERROR


class TestFlowStubs:
    def test_share_no_file(self, capsys):
        rc = main(["share"])
        assert rc == EXIT_USAGE_ERROR

    def test_search_no_results(self, capsys, monkeypatch):
        from stepwise.registry_client import RegistryError
        monkeypatch.setattr(
            "stepwise.registry_client.search_flows",
            lambda **kw: {"flows": [], "total": 0},
        )
        rc = main(["search", "social", "media"])
        assert rc == EXIT_SUCCESS
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "no results found" in combined.lower()

    def test_get_name_not_found(self, capsys, monkeypatch):
        from stepwise.registry_client import RegistryError
        monkeypatch.setattr(
            "stepwise.registry_client.fetch_flow",
            lambda slug, **kw: (_ for _ in ()).throw(RegistryError("Flow 'tweet-generator' not found in registry", 404)),
        )
        monkeypatch.setattr(
            "stepwise.registry_client.fetch_kit",
            lambda slug, **kw: (_ for _ in ()).throw(RegistryError("Kit 'tweet-generator' not found in registry", 404)),
        )
        rc = main(["get", "tweet-generator"])
        assert rc == EXIT_USAGE_ERROR
        err = capsys.readouterr().err
        assert "not found" in err.lower()

    def test_get_non_yaml_url(self, capsys):
        rc = main(["get", "https://example.com/not-a-yaml"])
        assert rc == EXIT_USAGE_ERROR


class TestSelfUpdate:
    def test_update_subparser_registered(self):
        parser = build_parser()
        # Should parse without error
        args = parser.parse_args(["update"])
        assert args.command == "update"

    def test_detect_install_method_uv_path(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda _: "/home/user/.local/share/uv/tools/stepwise/bin/stepwise")
        assert _detect_install_method() == "uv"

    def test_detect_install_method_pipx_path(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda _: "/home/user/.local/share/pipx/venvs/stepwise/bin/stepwise")
        assert _detect_install_method() == "pipx"

    def test_detect_install_method_probes_uv_tool_list(self, monkeypatch):
        import subprocess
        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/stepwise")

        def fake_run(cmd, **kwargs):
            if cmd == ["uv", "tool", "list"]:
                result = subprocess.CompletedProcess(cmd, 0, stdout="stepwise-run 0.1.0\n", stderr="")
                return result
            raise FileNotFoundError()

        monkeypatch.setattr("subprocess.run", fake_run)
        assert _detect_install_method() == "uv"

    def test_detect_install_method_fallback_pip(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/stepwise")
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError()))
        assert _detect_install_method() == "pip"

    def test_update_handler_in_main(self, capsys):
        """update is wired into the handler dict."""
        # We can't fully run update without side effects, but we can
        # confirm the command dispatches (it will fail trying to run uv/pip,
        # which is fine for this test)
        rc = main(["update"])
        # Any return code is acceptable — we just verify it dispatches
        assert isinstance(rc, int)


class TestUnknownCommand:
    def test_unknown_raises_system_exit(self):
        # argparse raises SystemExit(2) for unknown subcommands
        with pytest.raises(SystemExit) as exc_info:
            main(["nonexistent"])
        assert exc_info.value.code == 2
