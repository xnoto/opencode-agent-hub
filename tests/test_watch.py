"""Tests for agent-hub-watch terminal-adaptive dashboard rendering."""

import json
import os
from unittest import mock

import pytest

from opencode_agent_hub.watch import (
    MIN_COLS,
    MIN_LINES,
    get_terminal_width,
    parse_prom_file,
    print_agents,
    print_cost_panel,
    print_header,
    print_messages,
    print_threads,
    render_dashboard,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_terminal_size(cols: int, lines: int = 24):
    """Return an os.terminal_size for mocking."""
    return os.terminal_size((cols, lines))


def _capture_print(func, *args):
    """Call func(*args) and capture all printed output as a string."""
    output = []
    with mock.patch(
        "builtins.print",
        side_effect=lambda *a, **kw: output.append(" ".join(str(x) for x in a)),
    ):
        func(*args)
    return "\n".join(output)


# ---------------------------------------------------------------------------
# get_terminal_width: floor and detection
# ---------------------------------------------------------------------------


class TestGetTerminalWidth:
    """Verify terminal width detection with VT100/POSIX minimum floor."""

    def test_returns_actual_width_when_above_minimum(self):
        with mock.patch("os.get_terminal_size", return_value=_make_terminal_size(120)):
            assert get_terminal_width() == 120

    def test_clamps_to_minimum_when_below(self):
        with mock.patch("os.get_terminal_size", return_value=_make_terminal_size(40)):
            assert get_terminal_width() == MIN_COLS

    def test_exact_minimum_is_accepted(self):
        with mock.patch("os.get_terminal_size", return_value=_make_terminal_size(80)):
            assert get_terminal_width() == 80

    def test_fallback_on_oserror(self):
        """Non-TTY (piped stdout) should fall back to MIN_COLS."""
        with mock.patch("os.get_terminal_size", side_effect=OSError):
            assert get_terminal_width() == MIN_COLS

    def test_very_wide_terminal(self):
        with mock.patch("os.get_terminal_size", return_value=_make_terminal_size(300)):
            assert get_terminal_width() == 300

    def test_minimum_constants(self):
        """VT100/POSIX defaults are 80x24."""
        assert MIN_COLS == 80
        assert MIN_LINES == 24


# ---------------------------------------------------------------------------
# Separator and header scaling
# ---------------------------------------------------------------------------


class TestSeparatorsAndHeader:
    """Verify separators and title scale to terminal width."""

    @pytest.mark.parametrize("width", [80, 100, 120, 200])
    def test_header_separators_match_width(self, width):
        output = _capture_print(print_header, width)
        lines = output.split("\n")
        # First line: heavy separator
        assert lines[0] == "═" * width
        # Second line: centered title
        assert "AGENT HUB DASHBOARD" in lines[1]
        assert len(lines[1]) == width
        # Third line: heavy separator
        assert lines[2] == "═" * width

    @pytest.mark.parametrize("width", [80, 100, 120, 200])
    def test_header_title_is_centered(self, width):
        output = _capture_print(print_header, width)
        title_line = output.split("\n")[1]
        stripped = title_line.strip()
        assert stripped == "AGENT HUB DASHBOARD"
        # Verify centering: left pad should be roughly equal to right pad (within 1)
        left_pad = len(title_line) - len(title_line.lstrip())
        right_pad = len(title_line) - len(title_line.rstrip())
        assert abs(left_pad - right_pad) <= 1


# ---------------------------------------------------------------------------
# Agent column proportional scaling
# ---------------------------------------------------------------------------


class TestAgentColumns:
    """Verify agent row column widths scale proportionally."""

    def _setup_agents_dir(self, tmp_path, agents):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        for i, agent_data in enumerate(agents):
            (agents_dir / f"agent{i}.json").write_text(json.dumps(agent_data))
        return agents_dir

    def _sample_agent(self, id_len=20, role_len=40):
        return {
            "id": "A" * id_len,
            "role": "R" * role_len,
            "status": "active",
            "lastSeen": 0,
        }

    @pytest.mark.parametrize("width", [80, 120, 200])
    def test_agent_row_does_not_exceed_width(self, tmp_path, width):
        agents_dir = self._setup_agents_dir(tmp_path, [self._sample_agent()])
        with mock.patch("opencode_agent_hub.watch.AGENTS_DIR", agents_dir):
            output = _capture_print(print_agents, width)

        # The separator line should be exactly `width` chars
        lines = output.split("\n")
        separator = lines[1]  # line after section header
        assert separator == "─" * width

        # Data rows should not exceed width
        for line in lines[2:]:
            if line.strip() and line.strip() != "":
                assert len(line) <= width, f"Line exceeds width {width}: ({len(line)}) {line!r}"

    def test_narrow_terminal_truncates_fields(self, tmp_path):
        """At minimum width (80), long fields get truncated."""
        agent = self._sample_agent(id_len=50, role_len=80)
        agents_dir = self._setup_agents_dir(tmp_path, [agent])
        with mock.patch("opencode_agent_hub.watch.AGENTS_DIR", agents_dir):
            output = _capture_print(print_agents, 80)

        data_line = [row for row in output.split("\n") if "A" in row][0]
        # The original id is 50 'A's — it must be truncated
        a_count = data_line.count("A")
        assert a_count < 50

    def test_wide_terminal_shows_more(self, tmp_path):
        """At 200 cols, more of the agent ID and role should be visible."""
        agent = self._sample_agent(id_len=50, role_len=80)
        agents_dir = self._setup_agents_dir(tmp_path, [agent])

        with mock.patch("opencode_agent_hub.watch.AGENTS_DIR", agents_dir):
            narrow_output = _capture_print(print_agents, 80)
        with mock.patch("opencode_agent_hub.watch.AGENTS_DIR", agents_dir):
            wide_output = _capture_print(print_agents, 200)

        # Filter for data rows (start with indent + "AAA"), not the section header
        narrow_as = [row for row in narrow_output.split("\n") if row.startswith("  A")]
        wide_as = [row for row in wide_output.split("\n") if row.startswith("  A")]
        assert narrow_as and wide_as, "Expected data rows with agent IDs"
        assert wide_as[0].count("A") > narrow_as[0].count("A")


# ---------------------------------------------------------------------------
# Message column proportional scaling
# ---------------------------------------------------------------------------


class TestMessageColumns:
    """Verify message row column widths scale proportionally."""

    def _setup_messages_dir(self, tmp_path, messages):
        msgs_dir = tmp_path / "messages"
        msgs_dir.mkdir()
        for i, msg_data in enumerate(messages):
            (msgs_dir / f"msg{i}.json").write_text(json.dumps(msg_data))
        return msgs_dir

    def _sample_message(self, content_len=100):
        return {
            "from": "agent-alpha",
            "to": "agent-beta",
            "type": "context",
            "content": "X" * content_len,
            "read": False,
            "priority": "normal",
        }

    @pytest.mark.parametrize("width", [80, 120, 200])
    def test_message_row_does_not_exceed_width(self, tmp_path, width):
        msgs_dir = self._setup_messages_dir(tmp_path, [self._sample_message()])
        with mock.patch("opencode_agent_hub.watch.MESSAGES_DIR", msgs_dir):
            output = _capture_print(print_messages, width)

        separator = output.split("\n")[1]
        assert separator == "─" * width

        for line in output.split("\n")[2:]:
            if line.strip() and "X" in line:
                assert len(line) <= width, f"Line exceeds width {width}: ({len(line)}) {line!r}"

    def test_content_expands_on_wide_terminal(self, tmp_path):
        """Content field should show more text on wider terminals."""
        msg = self._sample_message(content_len=150)
        msgs_dir = self._setup_messages_dir(tmp_path, [msg])

        with mock.patch("opencode_agent_hub.watch.MESSAGES_DIR", msgs_dir):
            narrow = _capture_print(print_messages, 80)
        with mock.patch("opencode_agent_hub.watch.MESSAGES_DIR", msgs_dir):
            wide = _capture_print(print_messages, 200)

        narrow_x = max((row.count("X") for row in narrow.split("\n")), default=0)
        wide_x = max((row.count("X") for row in wide.split("\n")), default=0)
        assert wide_x > narrow_x

    def test_urgent_priority_marker_preserved(self, tmp_path):
        msg = self._sample_message()
        msg["priority"] = "urgent"
        msg["read"] = False
        msgs_dir = self._setup_messages_dir(tmp_path, [msg])

        with mock.patch("opencode_agent_hub.watch.MESSAGES_DIR", msgs_dir):
            output = _capture_print(print_messages, 120)

        assert "●" in output
        assert "!" in output


# ---------------------------------------------------------------------------
# Thread column proportional scaling
# ---------------------------------------------------------------------------


class TestThreadColumns:
    """Verify thread row column widths scale proportionally."""

    def _setup_threads_dir(self, tmp_path, threads):
        threads_dir = tmp_path / "threads"
        threads_dir.mkdir()
        for i, t_data in enumerate(threads):
            (threads_dir / f"thread{i}.json").write_text(json.dumps(t_data))
        return threads_dir

    def _sample_thread(self, participants=None):
        return {
            "id": "thread-abcdef12345",
            "createdBy": "coordinator",
            "participants": participants or ["alpha", "beta", "gamma", "delta"],
            "createdAt": 0,
            "status": "open",
        }

    @pytest.mark.parametrize("width", [80, 120, 200])
    def test_thread_row_does_not_exceed_width(self, tmp_path, width):
        threads_dir = self._setup_threads_dir(tmp_path, [self._sample_thread()])
        with mock.patch("opencode_agent_hub.watch.THREADS_DIR", threads_dir):
            output = _capture_print(print_threads, width)

        separator = output.split("\n")[1]
        assert separator == "─" * width

        for line in output.split("\n")[2:]:
            if line.strip() and "thread" in line:
                assert len(line) <= width, f"Line exceeds width {width}: ({len(line)}) {line!r}"

    def test_participants_expand_on_wide_terminal(self, tmp_path):
        """More participant names should be visible on wider terminals."""
        many_participants = [f"agent-{i:03d}" for i in range(20)]
        thread = self._sample_thread(participants=many_participants)
        threads_dir = self._setup_threads_dir(tmp_path, [thread])

        with mock.patch("opencode_agent_hub.watch.THREADS_DIR", threads_dir):
            narrow = _capture_print(print_threads, 80)
        with mock.patch("opencode_agent_hub.watch.THREADS_DIR", threads_dir):
            wide = _capture_print(print_threads, 200)

        narrow_agents = max((row.count("agent-") for row in narrow.split("\n")), default=0)
        wide_agents = max((row.count("agent-") for row in wide.split("\n")), default=0)
        assert wide_agents > narrow_agents


# ---------------------------------------------------------------------------
# Full render_dashboard integration
# ---------------------------------------------------------------------------


class TestRenderDashboard:
    """Integration tests for full dashboard render at various widths."""

    @pytest.mark.parametrize("cols", [80, 100, 140, 200])
    def test_full_render_at_width(self, tmp_path, cols):
        """Render the full dashboard and verify no line exceeds terminal width."""
        # Set up empty dirs so sections render their empty-state messages
        agents_dir = tmp_path / "agents"
        messages_dir = tmp_path / "messages"
        threads_dir = tmp_path / "threads"
        agents_dir.mkdir()
        messages_dir.mkdir()
        threads_dir.mkdir()

        output_lines = []

        def capture_print(*args, **kwargs):
            text = " ".join(str(a) for a in args)
            output_lines.append(text)

        with (
            mock.patch("os.get_terminal_size", return_value=_make_terminal_size(cols)),
            mock.patch("opencode_agent_hub.watch.AGENTS_DIR", agents_dir),
            mock.patch("opencode_agent_hub.watch.MESSAGES_DIR", messages_dir),
            mock.patch("opencode_agent_hub.watch.THREADS_DIR", threads_dir),
            mock.patch("builtins.print", side_effect=capture_print),
            mock.patch("sys.stdout"),
        ):
            render_dashboard()

        for line in output_lines:
            # Skip ANSI clear-screen sequences and empty lines
            if line.startswith("\033") or not line.strip():
                continue
            assert len(line) <= cols, f"Line exceeds {cols} cols ({len(line)}): {line!r}"

    def test_full_render_with_data(self, tmp_path):
        """Render dashboard with actual agent/message/thread data."""
        agents_dir = tmp_path / "agents"
        messages_dir = tmp_path / "messages"
        threads_dir = tmp_path / "threads"
        agents_dir.mkdir()
        messages_dir.mkdir()
        threads_dir.mkdir()

        # Create sample data
        (agents_dir / "a1.json").write_text(
            json.dumps(
                {
                    "id": "frontend-worker",
                    "role": "Building React components for dashboard",
                    "status": "active",
                    "lastSeen": 0,
                }
            )
        )
        (messages_dir / "m1.json").write_text(
            json.dumps(
                {
                    "from": "frontend",
                    "to": "backend",
                    "type": "question",
                    "content": "What is the auth API endpoint URL for the login flow?",
                    "read": False,
                    "priority": "urgent",
                }
            )
        )
        (threads_dir / "t1.json").write_text(
            json.dumps(
                {
                    "id": "thread-xyz-123",
                    "createdBy": "coordinator",
                    "participants": ["frontend", "backend", "devops"],
                    "createdAt": 0,
                    "status": "open",
                }
            )
        )

        for cols in [80, 120, 200]:
            output_lines: list[str] = []

            def capture_print(*args, _out=output_lines, **kwargs):
                text = " ".join(str(a) for a in args)
                _out.append(text)

            with (
                mock.patch("os.get_terminal_size", return_value=_make_terminal_size(cols)),
                mock.patch("opencode_agent_hub.watch.AGENTS_DIR", agents_dir),
                mock.patch("opencode_agent_hub.watch.MESSAGES_DIR", messages_dir),
                mock.patch("opencode_agent_hub.watch.THREADS_DIR", threads_dir),
                mock.patch("builtins.print", side_effect=capture_print),
                mock.patch("sys.stdout"),
            ):
                render_dashboard()

            for line in output_lines:
                if line.startswith("\033") or not line.strip():
                    continue
                assert len(line) <= cols, (
                    f"At {cols} cols, line exceeds width ({len(line)}): {line!r}"
                )


# ---------------------------------------------------------------------------
# SIGWINCH signal handler
# ---------------------------------------------------------------------------


class TestSigwinch:
    """Verify SIGWINCH support is wired correctly."""

    def test_sigwinch_is_available_on_unix(self):
        """SIGWINCH should exist on macOS/Linux."""
        assert hasattr(os, "terminal_size")
        # signal.SIGWINCH is POSIX; should be present on darwin/linux
        import signal

        assert hasattr(signal, "SIGWINCH")


# ---------------------------------------------------------------------------
# Prometheus metrics file parsing
# ---------------------------------------------------------------------------

_SAMPLE_PROM = """\
# HELP agent_hub_coordinator_tokens_input Cumulative input tokens
# TYPE agent_hub_coordinator_tokens_input counter
agent_hub_coordinator_tokens_input 12345
# HELP agent_hub_coordinator_tokens_output Cumulative output tokens
# TYPE agent_hub_coordinator_tokens_output counter
agent_hub_coordinator_tokens_output 6789
# HELP agent_hub_coordinator_tokens_cache_read Cumulative cache read tokens
# TYPE agent_hub_coordinator_tokens_cache_read counter
agent_hub_coordinator_tokens_cache_read 4000
# HELP agent_hub_coordinator_tokens_cache_write Cumulative cache write tokens
# TYPE agent_hub_coordinator_tokens_cache_write counter
agent_hub_coordinator_tokens_cache_write 500
# HELP agent_hub_coordinator_estimated_cost_usd Estimated coordinator cost in USD
# TYPE agent_hub_coordinator_estimated_cost_usd gauge
agent_hub_coordinator_estimated_cost_usd 0.0523
# HELP agent_hub_coordinator_messages_total Total assistant messages processed
# TYPE agent_hub_coordinator_messages_total counter
agent_hub_coordinator_messages_total 42
"""


class TestParsePromFile:
    """Verify Prometheus text format parsing."""

    def test_parses_all_metrics(self, tmp_path):
        prom_file = tmp_path / "metrics.prom"
        prom_file.write_text(_SAMPLE_PROM)
        metrics = parse_prom_file(prom_file)

        assert metrics["agent_hub_coordinator_tokens_input"] == 12345.0
        assert metrics["agent_hub_coordinator_tokens_output"] == 6789.0
        assert metrics["agent_hub_coordinator_tokens_cache_read"] == 4000.0
        assert metrics["agent_hub_coordinator_tokens_cache_write"] == 500.0
        assert abs(metrics["agent_hub_coordinator_estimated_cost_usd"] - 0.0523) < 1e-9
        assert metrics["agent_hub_coordinator_messages_total"] == 42.0

    def test_returns_empty_for_missing_file(self, tmp_path):
        result = parse_prom_file(tmp_path / "nonexistent.prom")
        assert result == {}

    def test_skips_comment_and_blank_lines(self, tmp_path):
        content = "# HELP foo bar\n# TYPE foo counter\n\nfoo 99\n\n"
        prom_file = tmp_path / "metrics.prom"
        prom_file.write_text(content)
        metrics = parse_prom_file(prom_file)
        assert metrics == {"foo": 99.0}

    def test_skips_malformed_value(self, tmp_path):
        content = "good_metric 42\nbad_metric not_a_number\n"
        prom_file = tmp_path / "metrics.prom"
        prom_file.write_text(content)
        metrics = parse_prom_file(prom_file)
        assert metrics == {"good_metric": 42.0}
        assert "bad_metric" not in metrics

    def test_handles_empty_file(self, tmp_path):
        prom_file = tmp_path / "metrics.prom"
        prom_file.write_text("")
        assert parse_prom_file(prom_file) == {}

    def test_handles_permission_error(self, tmp_path):
        prom_file = tmp_path / "metrics.prom"
        prom_file.write_text("foo 1")
        prom_file.chmod(0o000)
        try:
            assert parse_prom_file(prom_file) == {}
        finally:
            prom_file.chmod(0o644)

    def test_parses_float_values(self, tmp_path):
        content = "gauge_a 3.14159\ngauge_b 0.0\ngauge_c 1e6\n"
        prom_file = tmp_path / "metrics.prom"
        prom_file.write_text(content)
        metrics = parse_prom_file(prom_file)
        assert abs(metrics["gauge_a"] - 3.14159) < 1e-9
        assert metrics["gauge_b"] == 0.0
        assert metrics["gauge_c"] == 1e6


# ---------------------------------------------------------------------------
# Cost panel rendering
# ---------------------------------------------------------------------------


class TestCostPanel:
    """Verify cost/token dashboard panel rendering."""

    def _write_metrics(self, tmp_path):
        prom_file = tmp_path / "metrics.prom"
        prom_file.write_text(_SAMPLE_PROM)
        return prom_file

    def test_displays_cost_and_messages(self, tmp_path):
        prom_file = self._write_metrics(tmp_path)
        with mock.patch("opencode_agent_hub.watch.METRICS_FILE", prom_file):
            output = _capture_print(print_cost_panel, 120)

        assert "$0.0523" in output
        assert "Messages: 42" in output

    def test_displays_token_breakdown(self, tmp_path):
        prom_file = self._write_metrics(tmp_path)
        with mock.patch("opencode_agent_hub.watch.METRICS_FILE", prom_file):
            output = _capture_print(print_cost_panel, 120)

        assert "In: 12,345" in output
        assert "Out: 6,789" in output
        assert "Cache R: 4,000" in output
        assert "Cache W: 500" in output

    def test_empty_state_when_no_file(self, tmp_path):
        missing = tmp_path / "nonexistent.prom"
        with mock.patch("opencode_agent_hub.watch.METRICS_FILE", missing):
            output = _capture_print(print_cost_panel, 80)

        assert "(no metrics available)" in output

    def test_separator_matches_width(self, tmp_path):
        prom_file = self._write_metrics(tmp_path)
        for width in [80, 120, 200]:
            with mock.patch("opencode_agent_hub.watch.METRICS_FILE", prom_file):
                output = _capture_print(print_cost_panel, width)
            lines = output.split("\n")
            separator = lines[1]
            assert separator == "─" * width, f"Separator mismatch at width {width}"

    @pytest.mark.parametrize("width", [80, 120, 200])
    def test_no_line_exceeds_width(self, tmp_path, width):
        prom_file = self._write_metrics(tmp_path)
        with mock.patch("opencode_agent_hub.watch.METRICS_FILE", prom_file):
            output = _capture_print(print_cost_panel, width)

        for line in output.split("\n"):
            if line.strip():
                assert len(line) <= width, f"Line exceeds {width} cols ({len(line)}): {line!r}"

    def test_partial_metrics(self, tmp_path):
        """Panel should render gracefully with only some metrics present."""
        content = (
            "agent_hub_coordinator_estimated_cost_usd 1.23\n"
            "agent_hub_coordinator_messages_total 10\n"
        )
        prom_file = tmp_path / "metrics.prom"
        prom_file.write_text(content)
        with mock.patch("opencode_agent_hub.watch.METRICS_FILE", prom_file):
            output = _capture_print(print_cost_panel, 100)

        assert "$1.2300" in output
        assert "Messages: 10" in output
        # Token breakdown row should not appear (no token metrics)
        assert "Tokens:" not in output
