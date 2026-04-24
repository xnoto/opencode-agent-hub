"""Prometheus-compatible metrics collection for the agent hub daemon.

This module provides thread-safe metrics collection using the standard
prometheus_client library with Prometheus text format export.
"""

import time

from prometheus_client import CollectorRegistry, Counter, Gauge, generate_latest

# Use a custom registry to avoid conflicts with default process/platform collectors
# and to make testing easier (no global state leakage).
REGISTRY = CollectorRegistry()


class PrometheusMetrics:
    """Thread-safe Prometheus metrics collector using prometheus_client.

    Provides the same API surface as the previous custom implementation:
    inc(), set_gauge(), get(), to_prometheus(), log_summary().
    """

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self._registry = registry or REGISTRY
        self._start_time = time.time()

        # ── Help text for all metrics ──────────────────────────────────
        counter_defs: dict[str, str] = {
            "agent_hub_messages_total": "Total messages processed successfully",
            "agent_hub_messages_failed_total": "Total messages that failed processing",
            "agent_hub_messages_validation_failed_total": "Messages rejected due to schema validation failure",
            "agent_hub_messages_routing_failed_total": "Messages that could not be routed to target agent",
            "agent_hub_messages_delivery_failed_total": "Messages that failed injection into target session",
            "agent_hub_messages_rate_limited_total": "Messages rejected due to sender rate limiting",
            "agent_hub_chatty_throttle_triggered_total": "Route-specific throttle cooldowns triggered",
            "agent_hub_chatty_throttle_delayed_total": "Messages delayed by route-specific throttle",
            "agent_hub_chatty_throttle_released_total": "Delayed messages released after route cooldown",
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
        }

        gauge_defs: dict[str, str] = {
            "agent_hub_active_agents": "Current number of registered agents",
            "agent_hub_oriented_sessions": "Current number of oriented sessions",
            "agent_hub_injection_queue_size": "Current injection queue depth",
            "agent_hub_message_queue_size": "Current message queue depth",
            "agent_hub_coordinator_estimated_cost_usd": "Estimated coordinator cost in USD",
        }

        # ── Create prometheus_client objects ───────────────────────────
        self._counters: dict[str, Counter] = {}
        for name, help_text in counter_defs.items():
            self._counters[name] = Counter(name, help_text, registry=self._registry)

        self._gauges: dict[str, Gauge] = {}
        for name, help_text in gauge_defs.items():
            self._gauges[name] = Gauge(name, help_text, registry=self._registry)

        # Start-time gauge (set once)
        self._start_time_gauge = Gauge(
            "agent_hub_start_time_seconds",
            "Unix timestamp when daemon started",
            registry=self._registry,
        )
        self._start_time_gauge.set(self._start_time)

    # ── Public API (unchanged for callers) ─────────────────────────

    def inc(self, name: str, value: int = 1) -> None:
        """Increment a counter."""
        if name in self._counters:
            self._counters[name].inc(value)

    def set_gauge(self, name: str, value: int | float) -> None:
        """Set a gauge value."""
        if name in self._gauges:
            self._gauges[name].set(value)

    def set_counter(self, name: str, value: float) -> None:
        """Set a counter to an absolute value (for metrics that track totals)."""
        if name in self._counters:
            current = float(self._counters[name]._value.get())
            diff = value - current
            if diff > 0:
                self._counters[name].inc(diff)

    def get(self, name: str) -> float:
        """Get current value of a metric."""
        if name in self._counters:
            return float(self._counters[name]._value.get())
        if name in self._gauges:
            return float(self._gauges[name]._value.get())
        return 0

    def to_prometheus(self) -> str:
        """Export all metrics in Prometheus text exposition format."""
        return generate_latest(self._registry).decode("utf-8")

    def write_to_textfile(self, path: str) -> None:
        """Write metrics to a .prom file for node_exporter textfile collector."""
        from prometheus_client import write_to_textfile

        write_to_textfile(path, self._registry)

    def log_summary(self) -> str:
        """Return a human-readable summary for logging."""
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

        coord_cost = self.get("agent_hub_coordinator_estimated_cost_usd")
        coord_msgs = self.get("agent_hub_coordinator_messages_total")

        return (
            f"uptime={uptime_str} "
            f"msgs={int(self.get('agent_hub_messages_total'))}/{int(self.get('agent_hub_messages_failed_total'))} "
            f"inj={int(self.get('agent_hub_injections_total'))}/{int(self.get('agent_hub_injections_failed_total'))} "
            f"orient={int(self.get('agent_hub_sessions_oriented_total'))} "
            f"cache={int(self.get('agent_hub_cache_hits_total'))}/{int(self.get('agent_hub_cache_misses_total'))} "
            f"gc={int(self.get('agent_hub_gc_runs_total'))} "
            f"coord=${coord_cost:.4f}/{int(coord_msgs)}msgs"
        )

    def reset(self) -> None:
        """Reset all counters and gauges to zero. Useful for testing."""
        for c in self._counters.values():
            c._value.set(0)
        for g in self._gauges.values():
            g._value.set(0)


# Global metrics instance
metrics = PrometheusMetrics()
