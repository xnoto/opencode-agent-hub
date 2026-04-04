"""Prometheus-compatible metrics collection for the agent hub daemon.

This module provides thread-safe metrics collection with Prometheus text format export.
"""

import threading
import time


class PrometheusMetrics:
    """Thread-safe Prometheus-compatible metrics collector."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._start_time = time.time()

        # Counters (only increase)
        self._counters: dict[str, int] = {
            "agent_hub_messages_total": 0,
            "agent_hub_messages_failed_total": 0,
            "agent_hub_injections_total": 0,
            "agent_hub_injections_failed_total": 0,
            "agent_hub_injections_retried_total": 0,
            "agent_hub_sessions_oriented_total": 0,
            "agent_hub_agents_auto_created_total": 0,
            "agent_hub_cache_hits_total": 0,
            "agent_hub_cache_misses_total": 0,
            "agent_hub_orientation_retries_total": 0,
            "agent_hub_orientation_gave_up_total": 0,
            "agent_hub_gc_runs_total": 0,
            "agent_hub_gc_sessions_cleaned_total": 0,
            "agent_hub_gc_agents_cleaned_total": 0,
            "agent_hub_gc_messages_archived_total": 0,
            "agent_hub_coordinator_tokens_input": 0,
            "agent_hub_coordinator_tokens_output": 0,
            "agent_hub_coordinator_tokens_cache_read": 0,
            "agent_hub_coordinator_tokens_cache_write": 0,
            "agent_hub_coordinator_messages_total": 0,
        }

        # Gauges (can increase or decrease)
        self._gauges: dict[str, int | float] = {
            "agent_hub_active_agents": 0,
            "agent_hub_oriented_sessions": 0,
            "agent_hub_injection_queue_size": 0,
            "agent_hub_message_queue_size": 0,
            "agent_hub_coordinator_estimated_cost_usd": 0.0,
        }

        # Metadata for metrics
        self._help: dict[str, str] = {
            "agent_hub_messages_total": "Total messages processed successfully",
            "agent_hub_messages_failed_total": "Total messages that failed processing",
            "agent_hub_injections_total": "Total message injections sent to sessions",
            "agent_hub_injections_failed_total": "Total injection failures after retries",
            "agent_hub_injections_retried_total": "Total injection retry attempts",
            "agent_hub_sessions_oriented_total": "Total sessions that received orientation",
            "agent_hub_agents_auto_created_total": "Total agents auto-created from sessions",
            "agent_hub_cache_hits_total": "Total session cache hits",
            "agent_hub_cache_misses_total": "Total session cache misses",
            "agent_hub_orientation_retries_total": "Total orientation retry attempts for unresponsive sessions",
            "agent_hub_orientation_gave_up_total": "Total sessions that never responded after all orientation retries",
            "agent_hub_gc_runs_total": "Total garbage collection runs",
            "agent_hub_gc_sessions_cleaned_total": "Total stale sessions cleaned by GC",
            "agent_hub_gc_agents_cleaned_total": "Total stale agents cleaned by GC",
            "agent_hub_gc_messages_archived_total": "Total messages archived by GC",
            "agent_hub_coordinator_tokens_input": "Coordinator cumulative input tokens",
            "agent_hub_coordinator_tokens_output": "Coordinator cumulative output tokens",
            "agent_hub_coordinator_tokens_cache_read": "Coordinator cumulative cache read tokens",
            "agent_hub_coordinator_tokens_cache_write": "Coordinator cumulative cache write tokens",
            "agent_hub_coordinator_messages_total": "Coordinator total assistant messages processed",
            "agent_hub_coordinator_estimated_cost_usd": "Estimated coordinator cost in USD",
            "agent_hub_active_agents": "Current number of registered agents",
            "agent_hub_oriented_sessions": "Current number of oriented sessions",
            "agent_hub_injection_queue_size": "Current injection queue depth",
            "agent_hub_message_queue_size": "Current message queue depth",
            "agent_hub_start_time_seconds": "Unix timestamp when daemon started",
        }

    def inc(self, name: str, value: int = 1) -> None:
        """Increment a counter."""
        with self._lock:
            if name in self._counters:
                self._counters[name] += value

    def set_gauge(self, name: str, value: int | float) -> None:
        """Set a gauge value."""
        with self._lock:
            self._gauges[name] = value

    def set_counter(self, name: str, value: int) -> None:
        """Set a counter to an absolute value (use sparingly)."""
        with self._lock:
            if name in self._counters:
                self._counters[name] = value

    def get(self, name: str) -> float:
        """Get current value of a metric."""
        with self._lock:
            if name in self._counters:
                return self._counters[name]
            if name in self._gauges:
                return self._gauges[name]
            return 0

    def to_prometheus(self) -> str:
        """Export all metrics in Prometheus text format."""
        lines: list[str] = []
        with self._lock:
            # Add start time as a gauge
            lines.append(
                f"# HELP agent_hub_start_time_seconds {self._help['agent_hub_start_time_seconds']}"
            )
            lines.append("# TYPE agent_hub_start_time_seconds gauge")
            lines.append(f"agent_hub_start_time_seconds {self._start_time}")

            # Counters
            for name, value in self._counters.items():
                if name in self._help:
                    lines.append(f"# HELP {name} {self._help[name]}")
                lines.append(f"# TYPE {name} counter")
                lines.append(f"{name} {value}")

            # Gauges
            for gname, gvalue in self._gauges.items():
                if gname in self._help:
                    lines.append(f"# HELP {gname} {self._help[gname]}")
                lines.append(f"# TYPE {gname} gauge")
                lines.append(f"{gname} {gvalue}")

        return "\n".join(lines) + "\n"

    def log_summary(self) -> str:
        """Return a human-readable summary for logging."""
        with self._lock:
            uptime = time.time() - self._start_time
            hours, remainder = divmod(int(uptime), 3600)
            minutes, seconds = divmod(remainder, 60)
            uptime_str = (
                f"{hours}h{minutes}m{seconds}s"
                if hours
                else f"{minutes}m{seconds}s"
                if minutes
                else f"{seconds}s"
            )

            coord_cost = self._gauges.get("agent_hub_coordinator_estimated_cost_usd", 0)
            coord_msgs = self._counters.get("agent_hub_coordinator_messages_total", 0)

            return (
                f"uptime={uptime_str} "
                f"msgs={self._counters['agent_hub_messages_total']}/{self._counters['agent_hub_messages_failed_total']} "
                f"inj={self._counters['agent_hub_injections_total']}/{self._counters['agent_hub_injections_failed_total']} "
                f"orient={self._counters['agent_hub_sessions_oriented_total']} "
                f"cache={self._counters['agent_hub_cache_hits_total']}/{self._counters['agent_hub_cache_misses_total']} "
                f"gc={self._counters['agent_hub_gc_runs_total']} "
                f"coord=${coord_cost:.4f}/{coord_msgs}msgs"
            )


# Global metrics instance
metrics = PrometheusMetrics()
