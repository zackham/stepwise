"""Tests for usage limit error classification and reset time parsing."""
import pytest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from stepwise.executors import classify_api_error, parse_usage_reset_time


class TestClassifyApiError:
    def test_usage_limit_reset_full_message(self):
        msg = "You're out of extra usage · resets 3pm (America/Los_Angeles)"
        assert classify_api_error(msg) == "usage_limit_reset"

    def test_usage_limit_reset_no_parens(self):
        assert classify_api_error("out of extra usage resets 3pm") == "usage_limit_reset"

    def test_usage_limit_without_reset_stays_quota(self):
        assert classify_api_error("usage limit exceeded") == "quota_error"

    def test_quota_exceeded_stays_quota(self):
        assert classify_api_error("quota exceeded for this account") == "quota_error"

    def test_billing_stays_quota(self):
        assert classify_api_error("billing account suspended") == "quota_error"

    def test_auth_unchanged(self):
        assert classify_api_error("401 unauthorized") == "auth_error"

    def test_infra_failure_unchanged(self):
        assert classify_api_error("429 rate limit") == "infra_failure"

    def test_timeout_unchanged(self):
        assert classify_api_error("request timed out") == "timeout"

    def test_context_length_unchanged(self):
        assert classify_api_error("context length exceeded") == "context_length"

    def test_unknown_unchanged(self):
        assert classify_api_error("something weird") == "unknown"

    def test_acp_transport_closed_is_infra_failure(self):
        # Server restart kills the ACP JSON-RPC transport mid-prompt.
        # The agent's retry decorator must treat this as transient so a
        # fresh agent is re-spawned on the next attempt.
        assert classify_api_error("ACP error: Transport closed") == "infra_failure"
        assert classify_api_error("Transport closed") == "infra_failure"

    def test_broken_pipe_is_infra_failure(self):
        # IO error mid-prompt (e.g. agent subprocess died unexpectedly).
        assert classify_api_error("BrokenPipeError: [Errno 32] Broken pipe") == "infra_failure"
        assert classify_api_error("broken pipe") == "infra_failure"


class TestParseUsageResetTime:
    def test_3pm_pacific(self):
        result = parse_usage_reset_time(
            "You're out of extra usage · resets 3pm (America/Los_Angeles)")
        assert result is not None
        assert result.hour == 15
        assert result.minute == 0
        assert str(result.tzinfo) == "America/Los_Angeles"

    def test_3_00pm(self):
        result = parse_usage_reset_time("out of extra usage resets 3:00pm (America/Los_Angeles)")
        assert result is not None
        assert result.hour == 15

    def test_24h_format(self):
        result = parse_usage_reset_time("out of extra usage resets 15:00 (UTC)")
        assert result is not None
        assert result.hour == 15
        assert str(result.tzinfo) == "UTC"

    def test_rolls_forward_if_past(self):
        # Use a time that's definitely in the past
        result = parse_usage_reset_time("out of extra usage resets 12:00am (UTC)")
        now = datetime.now(ZoneInfo("UTC"))
        if now.hour > 0:  # Unless it's midnight, 12am is past
            assert result is not None
            assert result > now

    def test_bad_timezone_falls_back_to_utc(self):
        result = parse_usage_reset_time("out of extra usage resets 3pm (Fake/Zone)")
        assert result is not None
        assert str(result.tzinfo) == "UTC"

    def test_no_timezone_falls_back_to_utc(self):
        result = parse_usage_reset_time("out of extra usage resets 3pm")
        assert result is not None
        assert str(result.tzinfo) == "UTC"

    def test_no_match_returns_none(self):
        assert parse_usage_reset_time("billing account suspended") is None

    def test_unparseable_time_returns_none(self):
        assert parse_usage_reset_time("out of extra usage resets xyz (UTC)") is None
