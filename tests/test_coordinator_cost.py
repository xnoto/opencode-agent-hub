"""Tests for coordinator cost tracking.

Verifies that the daemon polls coordinator session messages,
computes token sums and estimated costs, and updates metrics.
"""

from unittest import mock


def _make_assistant_message(
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read: int = 0,
    cache_write: int = 0,
) -> dict:
    """Create a mock assistant message with token data."""
    return {
        "info": {
            "id": "msg_test",
            "role": "assistant",
            "modelID": "claude-opus-4-6",
            "providerID": "anthropic",
            "tokens": {
                "total": input_tokens + output_tokens + cache_read + cache_write,
                "input": input_tokens,
                "output": output_tokens,
                "reasoning": 0,
                "cache": {
                    "read": cache_read,
                    "write": cache_write,
                },
            },
            "cost": 0,  # OpenCode always returns 0
        },
        "parts": [],
    }


def _make_user_message() -> dict:
    """Create a mock user message (no token data)."""
    return {
        "info": {
            "id": "msg_user_test",
            "role": "user",
        },
        "parts": [{"type": "text", "text": "hello"}],
    }


def _reset_metrics():
    """Reset coordinator-related metrics to zero."""
    from opencode_agent_hub import daemon

    with daemon.metrics._lock:
        daemon.metrics._counters["agent_hub_coordinator_tokens_input"] = 0
        daemon.metrics._counters["agent_hub_coordinator_tokens_output"] = 0
        daemon.metrics._counters["agent_hub_coordinator_tokens_cache_read"] = 0
        daemon.metrics._counters["agent_hub_coordinator_tokens_cache_write"] = 0
        daemon.metrics._counters["agent_hub_coordinator_messages_total"] = 0
    daemon.metrics.set_gauge("agent_hub_coordinator_estimated_cost_usd", 0.0)


def test_poll_coordinator_cost_disabled():
    """poll_coordinator_cost is a no-op when coordinator is disabled."""
    from opencode_agent_hub import daemon

    _reset_metrics()

    original_enabled = daemon.COORDINATOR_ENABLED
    daemon.COORDINATOR_ENABLED = False

    try:
        with mock.patch("requests.get") as mock_get:
            daemon.poll_coordinator_cost()
        mock_get.assert_not_called()
    finally:
        daemon.COORDINATOR_ENABLED = original_enabled


def test_poll_coordinator_cost_no_session():
    """poll_coordinator_cost is a no-op when no coordinator session exists."""
    from opencode_agent_hub import daemon

    _reset_metrics()

    original_enabled = daemon.COORDINATOR_ENABLED
    original_session = daemon.COORDINATOR_SESSION_ID
    daemon.COORDINATOR_ENABLED = True
    daemon.COORDINATOR_SESSION_ID = None

    try:
        with mock.patch("requests.get") as mock_get:
            daemon.poll_coordinator_cost()
        mock_get.assert_not_called()
    finally:
        daemon.COORDINATOR_ENABLED = original_enabled
        daemon.COORDINATOR_SESSION_ID = original_session


def test_poll_coordinator_cost_sums_tokens():
    """poll_coordinator_cost sums token counts from assistant messages."""
    from opencode_agent_hub import daemon

    _reset_metrics()

    original_enabled = daemon.COORDINATOR_ENABLED
    original_session = daemon.COORDINATOR_SESSION_ID
    daemon.COORDINATOR_ENABLED = True
    daemon.COORDINATOR_SESSION_ID = "ses_test_cost_001"

    try:
        mock_messages = [
            _make_user_message(),
            _make_assistant_message(
                input_tokens=10, output_tokens=100, cache_read=500, cache_write=200
            ),
            _make_user_message(),
            _make_assistant_message(
                input_tokens=5, output_tokens=50, cache_read=300, cache_write=100
            ),
        ]

        mock_resp = mock.Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_messages
        mock_resp.raise_for_status = mock.Mock()

        with mock.patch("requests.get", return_value=mock_resp):
            daemon.poll_coordinator_cost()

        # Verify token sums
        assert daemon.metrics.get("agent_hub_coordinator_tokens_input") == 15
        assert daemon.metrics.get("agent_hub_coordinator_tokens_output") == 150
        assert daemon.metrics.get("agent_hub_coordinator_tokens_cache_read") == 800
        assert daemon.metrics.get("agent_hub_coordinator_tokens_cache_write") == 300
        assert daemon.metrics.get("agent_hub_coordinator_messages_total") == 2
    finally:
        daemon.COORDINATOR_ENABLED = original_enabled
        daemon.COORDINATOR_SESSION_ID = original_session
        _reset_metrics()


def test_poll_coordinator_cost_estimated_cost():
    """poll_coordinator_cost computes estimated cost using pricing config."""
    from opencode_agent_hub import daemon

    _reset_metrics()

    original_enabled = daemon.COORDINATOR_ENABLED
    original_session = daemon.COORDINATOR_SESSION_ID
    original_pricing = (
        daemon.PRICING_INPUT,
        daemon.PRICING_OUTPUT,
        daemon.PRICING_CACHE_READ,
        daemon.PRICING_CACHE_WRITE,
    )
    daemon.COORDINATOR_ENABLED = True
    daemon.COORDINATOR_SESSION_ID = "ses_test_cost_002"

    # Use simple pricing for easy math: $1/token for each type
    daemon.PRICING_INPUT = 1.0
    daemon.PRICING_OUTPUT = 2.0
    daemon.PRICING_CACHE_READ = 0.5
    daemon.PRICING_CACHE_WRITE = 0.75

    try:
        mock_messages = [
            _make_assistant_message(
                input_tokens=10, output_tokens=20, cache_read=100, cache_write=40
            ),
        ]

        mock_resp = mock.Mock()
        mock_resp.json.return_value = mock_messages
        mock_resp.raise_for_status = mock.Mock()

        with mock.patch("requests.get", return_value=mock_resp):
            daemon.poll_coordinator_cost()

        # Expected: 10*1.0 + 20*2.0 + 100*0.5 + 40*0.75 = 10 + 40 + 50 + 30 = 130
        cost = daemon.metrics.get("agent_hub_coordinator_estimated_cost_usd")
        assert cost == 130.0
    finally:
        daemon.COORDINATOR_ENABLED = original_enabled
        daemon.COORDINATOR_SESSION_ID = original_session
        (
            daemon.PRICING_INPUT,
            daemon.PRICING_OUTPUT,
            daemon.PRICING_CACHE_READ,
            daemon.PRICING_CACHE_WRITE,
        ) = original_pricing
        _reset_metrics()


def test_poll_coordinator_cost_default_pricing():
    """Verify default pricing matches Anthropic Claude Opus 4 rates."""
    from opencode_agent_hub import daemon

    _reset_metrics()

    original_enabled = daemon.COORDINATOR_ENABLED
    original_session = daemon.COORDINATOR_SESSION_ID
    daemon.COORDINATOR_ENABLED = True
    daemon.COORDINATOR_SESSION_ID = "ses_test_cost_003"

    try:
        # Use default pricing (should be Opus 4 rates)
        mock_messages = [
            _make_assistant_message(
                input_tokens=1_000_000,
                output_tokens=1_000_000,
                cache_read=1_000_000,
                cache_write=1_000_000,
            ),
        ]

        mock_resp = mock.Mock()
        mock_resp.json.return_value = mock_messages
        mock_resp.raise_for_status = mock.Mock()

        with mock.patch("requests.get", return_value=mock_resp):
            daemon.poll_coordinator_cost()

        cost = daemon.metrics.get("agent_hub_coordinator_estimated_cost_usd")
        # Expected at default Opus 4 pricing:
        # 1M * $15/MTok + 1M * $75/MTok + 1M * $1.50/MTok + 1M * $18.75/MTok
        # = $15 + $75 + $1.50 + $18.75 = $110.25
        assert abs(cost - 110.25) < 0.01, f"Expected ~$110.25, got ${cost}"
    finally:
        daemon.COORDINATOR_ENABLED = original_enabled
        daemon.COORDINATOR_SESSION_ID = original_session
        _reset_metrics()


def test_poll_coordinator_cost_ignores_user_messages():
    """Only assistant messages contribute to token counts."""
    from opencode_agent_hub import daemon

    _reset_metrics()

    original_enabled = daemon.COORDINATOR_ENABLED
    original_session = daemon.COORDINATOR_SESSION_ID
    daemon.COORDINATOR_ENABLED = True
    daemon.COORDINATOR_SESSION_ID = "ses_test_cost_004"

    try:
        mock_messages = [
            _make_user_message(),
            _make_user_message(),
            _make_user_message(),
        ]

        mock_resp = mock.Mock()
        mock_resp.json.return_value = mock_messages
        mock_resp.raise_for_status = mock.Mock()

        with mock.patch("requests.get", return_value=mock_resp):
            daemon.poll_coordinator_cost()

        assert daemon.metrics.get("agent_hub_coordinator_tokens_input") == 0
        assert daemon.metrics.get("agent_hub_coordinator_tokens_output") == 0
        assert daemon.metrics.get("agent_hub_coordinator_messages_total") == 0
        assert daemon.metrics.get("agent_hub_coordinator_estimated_cost_usd") == 0.0
    finally:
        daemon.COORDINATOR_ENABLED = original_enabled
        daemon.COORDINATOR_SESSION_ID = original_session
        _reset_metrics()


def test_poll_coordinator_cost_api_failure():
    """poll_coordinator_cost handles API failures gracefully."""
    from opencode_agent_hub import daemon

    _reset_metrics()

    original_enabled = daemon.COORDINATOR_ENABLED
    original_session = daemon.COORDINATOR_SESSION_ID
    daemon.COORDINATOR_ENABLED = True
    daemon.COORDINATOR_SESSION_ID = "ses_test_cost_005"

    try:
        import requests as req_module

        with mock.patch(
            "requests.get", side_effect=req_module.RequestException("connection refused")
        ):
            # Should not raise
            daemon.poll_coordinator_cost()

        # Metrics should remain at zero
        assert daemon.metrics.get("agent_hub_coordinator_tokens_input") == 0
        assert daemon.metrics.get("agent_hub_coordinator_estimated_cost_usd") == 0.0
    finally:
        daemon.COORDINATOR_ENABLED = original_enabled
        daemon.COORDINATOR_SESSION_ID = original_session
        _reset_metrics()


def test_poll_coordinator_cost_idempotent():
    """Repeated polls set absolute values, not incremental."""
    from opencode_agent_hub import daemon

    _reset_metrics()

    original_enabled = daemon.COORDINATOR_ENABLED
    original_session = daemon.COORDINATOR_SESSION_ID
    daemon.COORDINATOR_ENABLED = True
    daemon.COORDINATOR_SESSION_ID = "ses_test_cost_006"

    try:
        mock_messages = [
            _make_assistant_message(input_tokens=100, output_tokens=200),
        ]

        mock_resp = mock.Mock()
        mock_resp.json.return_value = mock_messages
        mock_resp.raise_for_status = mock.Mock()

        with mock.patch("requests.get", return_value=mock_resp):
            # Poll three times
            daemon.poll_coordinator_cost()
            daemon.poll_coordinator_cost()
            daemon.poll_coordinator_cost()

        # Should be 100, not 300 (absolute, not cumulative)
        assert daemon.metrics.get("agent_hub_coordinator_tokens_input") == 100
        assert daemon.metrics.get("agent_hub_coordinator_tokens_output") == 200
        assert daemon.metrics.get("agent_hub_coordinator_messages_total") == 1
    finally:
        daemon.COORDINATOR_ENABLED = original_enabled
        daemon.COORDINATOR_SESSION_ID = original_session
        _reset_metrics()


def test_poll_coordinator_cost_missing_token_fields():
    """Messages with missing token fields default to zero."""
    from opencode_agent_hub import daemon

    _reset_metrics()

    original_enabled = daemon.COORDINATOR_ENABLED
    original_session = daemon.COORDINATOR_SESSION_ID
    daemon.COORDINATOR_ENABLED = True
    daemon.COORDINATOR_SESSION_ID = "ses_test_cost_007"

    try:
        # Assistant message with partial/missing token data
        mock_messages = [
            {
                "info": {
                    "id": "msg_sparse",
                    "role": "assistant",
                    "tokens": {},  # Empty tokens dict
                },
                "parts": [],
            },
            {
                "info": {
                    "id": "msg_no_tokens",
                    "role": "assistant",
                    # No tokens key at all
                },
                "parts": [],
            },
        ]

        mock_resp = mock.Mock()
        mock_resp.json.return_value = mock_messages
        mock_resp.raise_for_status = mock.Mock()

        with mock.patch("requests.get", return_value=mock_resp):
            daemon.poll_coordinator_cost()

        # Should be zero for all token types, but count 2 messages
        assert daemon.metrics.get("agent_hub_coordinator_tokens_input") == 0
        assert daemon.metrics.get("agent_hub_coordinator_tokens_output") == 0
        assert daemon.metrics.get("agent_hub_coordinator_messages_total") == 2
    finally:
        daemon.COORDINATOR_ENABLED = original_enabled
        daemon.COORDINATOR_SESSION_ID = original_session
        _reset_metrics()


def test_log_summary_includes_coordinator_cost():
    """log_summary includes coordinator cost and message count."""
    from opencode_agent_hub import daemon

    _reset_metrics()

    with daemon.metrics._lock:
        daemon.metrics._counters["agent_hub_coordinator_messages_total"] = 5
    daemon.metrics.set_gauge("agent_hub_coordinator_estimated_cost_usd", 1.2345)

    try:
        summary = daemon.metrics.log_summary()
        assert "coord=$1.2345/5msgs" in summary
    finally:
        _reset_metrics()


def test_set_gauge_preserves_float():
    """set_gauge should not truncate float values to int."""
    from opencode_agent_hub import daemon

    daemon.metrics.set_gauge("agent_hub_coordinator_estimated_cost_usd", 0.0523)
    value = daemon.metrics.get("agent_hub_coordinator_estimated_cost_usd")
    assert isinstance(value, float)
    assert abs(value - 0.0523) < 0.0001


def test_prometheus_output_includes_coordinator_metrics():
    """to_prometheus includes coordinator cost metrics in output."""
    from opencode_agent_hub import daemon

    _reset_metrics()

    with daemon.metrics._lock:
        daemon.metrics._counters["agent_hub_coordinator_tokens_input"] = 1000
        daemon.metrics._counters["agent_hub_coordinator_tokens_output"] = 500
    daemon.metrics.set_gauge("agent_hub_coordinator_estimated_cost_usd", 0.0523)

    try:
        output = daemon.metrics.to_prometheus()
        assert "agent_hub_coordinator_tokens_input 1000" in output
        assert "agent_hub_coordinator_tokens_output 500" in output
        assert "agent_hub_coordinator_estimated_cost_usd 0.0523" in output
        assert "# HELP agent_hub_coordinator_estimated_cost_usd" in output
        assert "# TYPE agent_hub_coordinator_estimated_cost_usd gauge" in output
    finally:
        _reset_metrics()
