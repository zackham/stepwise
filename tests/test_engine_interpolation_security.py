"""Tests for shell injection prevention in _interpolate_config()."""
import shlex

from stepwise.engine import _interpolate_config


class TestInterpolateConfigQuoting:
    def test_command_value_quoted(self):
        config = {"command": "curl $url"}
        result = _interpolate_config(config, {"url": "http://x; rm -rf /"})
        assert result["command"] == f"curl {shlex.quote('http://x; rm -rf /')}"

    def test_check_command_value_quoted(self):
        config = {"check_command": "gh pr view $pr_number --json state"}
        result = _interpolate_config(config, {"pr_number": "123; cat /etc/passwd"})
        expected = f"gh pr view {shlex.quote('123; cat /etc/passwd')} --json state"
        assert result["check_command"] == expected

    def test_simple_value_no_extra_quotes(self):
        config = {"check_command": "gh pr view $pr_number --json state"}
        result = _interpolate_config(config, {"pr_number": "123"})
        assert result["check_command"] == "gh pr view 123 --json state"

    def test_command_substitution_attack_blocked(self):
        config = {"command": "echo $user_input"}
        result = _interpolate_config(config, {"user_input": "$(cat /etc/shadow)"})
        assert result["command"] == "echo '$(cat /etc/shadow)'"

    def test_backtick_attack_blocked(self):
        config = {"command": "echo $user_input"}
        result = _interpolate_config(config, {"user_input": "`cat /etc/shadow`"})
        assert result["command"] == "echo '`cat /etc/shadow`'"

    def test_prompt_not_quoted(self):
        config = {"prompt": "Hello $name", "command": "echo $name"}
        result = _interpolate_config(config, {"name": "it's a test"})
        assert result["prompt"] == "Hello it's a test"
        assert "'" in result["command"]  # shlex.quote wraps it

    def test_model_not_quoted(self):
        config = {"model": "$model_name"}
        result = _interpolate_config(config, {"model_name": "anthropic/claude-sonnet-4-20250514"})
        assert result["model"] == "anthropic/claude-sonnet-4-20250514"

    def test_no_change_returns_original(self):
        config = {"command": "echo hello"}
        result = _interpolate_config(config, {"unused": "value"})
        assert result is config  # same object — no copy
