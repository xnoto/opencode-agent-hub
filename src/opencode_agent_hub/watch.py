#!/usr/bin/env python3
# opencode-agent-hub - Multi-agent coordination daemon for OpenCode
# Copyright (c) 2025 xnoto
# /// script
# requires-python = ">=3.11"
# dependencies = ["watchdog"]
# ///
"""Agent Hub Dashboard - Real-time view of agents, threads, and messages."""

import json
import os
import signal
import sys
import time
from pathlib import Path
from threading import Event, Lock

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

HUB_DIR = Path.home() / ".agent-hub"
MESSAGES_DIR = HUB_DIR / "messages"
AGENTS_DIR = HUB_DIR / "agents"
THREADS_DIR = HUB_DIR / "threads"
METRICS_FILE = HUB_DIR / "metrics.prom"

# VT100 / POSIX minimum terminal dimensions
MIN_COLS = 80
MIN_LINES = 24

# Synchronization
refresh_event = Event()
display_lock = Lock()


def get_terminal_width() -> int:
    """Get current terminal width, clamped to the VT100/POSIX minimum of 80 columns."""
    try:
        cols = os.get_terminal_size().columns
    except OSError:
        cols = MIN_COLS
    return max(cols, MIN_COLS)


class HubEventHandler(FileSystemEventHandler):
    """Trigger refresh on any filesystem change."""

    def on_any_event(self, event):
        if event.event_type not in ["created", "deleted", "modified", "moved"]:
            return

        if event.src_path.endswith((".json", ".prom")):
            refresh_event.set()


def clear_screen():
    print("\033[2J\033[H", end="")


def relative_time(timestamp_ms: int) -> str:
    """Convert millisecond timestamp to relative time string."""
    now = time.time()
    ago = int(now - (timestamp_ms / 1000))
    if ago < 0:
        return "just now"
    elif ago < 60:
        return f"{ago}s ago"
    elif ago < 3600:
        return f"{ago // 60}m ago"
    elif ago < 86400:
        return f"{ago // 3600}h ago"
    else:
        return f"{ago // 86400}d ago"


def load_json(path: Path) -> dict | None:
    """Safely load JSON file."""
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError, PermissionError):
        return None


def print_header(w: int) -> None:
    print("═" * w)
    print("AGENT HUB DASHBOARD".center(w))
    print("═" * w)
    print()


def print_daemon_status(w: int) -> None:
    print("🔧 DAEMON STATUS")
    print("─" * w)

    import subprocess

    result = subprocess.run(["pgrep", "-f", "agent-hub-daemon"], capture_output=True, text=True)
    pids = result.stdout.strip().split("\n")
    pids = [p for p in pids if p]

    if pids:
        print(f"  Status: RUNNING (PID {pids[0]})")
    else:
        print("  Status: STOPPED")
    print()


def print_agents(w: int) -> None:
    print("📡 REGISTERED AGENTS")
    print("─" * w)

    if not AGENTS_DIR.exists():
        print("  (no agents directory)")
        print()
        return

    agents = list(AGENTS_DIR.glob("*.json"))
    if not agents:
        print("  (no agents registered)")
        print()
        return

    # Column layout: "  {agent_id} {role} {status} {time_str}"
    # Fixed chars: 2 (indent) + 3 (spaces between 4 fields) = 5
    # time_str is variable-length (e.g. "5s ago" .. "20498d ago"); reserve 10 for worst case
    time_reserve = 10
    available = w - 5 - time_reserve
    # Proportions: agent_id ~25%, role ~50%, status ~25%  (of available)
    id_w = max(8, available * 25 // 100)
    role_w = max(10, available * 50 // 100)
    status_w = max(6, available - id_w - role_w)

    for agent_file in sorted(agents):
        data = load_json(agent_file)
        if not data:
            continue

        agent_id = data.get("id", "?")[:id_w]
        role = data.get("role", "?")[:role_w]
        status = data.get("status", "?")[:status_w]
        last_seen = data.get("lastSeen", 0)
        time_str = relative_time(last_seen)

        print(f"  {agent_id:<{id_w}} {role:<{role_w}} {status:<{status_w}} {time_str}")

    print()


def print_threads(w: int) -> None:
    print("🧵 ACTIVE THREADS")
    print("─" * w)

    if not THREADS_DIR.exists():
        print("  (no threads directory)")
        print()
        return

    threads = list(THREADS_DIR.glob("*.json"))
    open_threads = []

    for thread_file in threads:
        data = load_json(thread_file)
        if data and data.get("status") == "open":
            open_threads.append(data)

    if not open_threads:
        print("  (no open threads)")
        print()
        return

    # Column layout: "  {thread_id} by {created_by} [{participants}] {created_at}"
    # Fixed chars: 2 (indent) + 4 (" by ") + 3 (" [" + "]") + 1 (space) = 10
    # time_str reserve: 10 (worst case "20498d ago")
    time_reserve = 10
    available = w - 10 - time_reserve
    # Proportions: thread_id ~25%, created_by ~20%, participants ~55%
    tid_w = max(8, available * 25 // 100)
    by_w = max(6, available * 20 // 100)
    part_w = max(10, available - tid_w - by_w)

    for t in sorted(open_threads, key=lambda x: x.get("createdAt", 0), reverse=True)[:5]:
        thread_id = t.get("id", "?")[:tid_w]
        created_by = t.get("createdBy", "?")[:by_w]
        participants = ", ".join(t.get("participants", []))[:part_w]
        created_at = relative_time(t.get("createdAt", 0))

        print(
            f"  {thread_id:<{tid_w}} by {created_by:<{by_w}} [{participants:<{part_w}}] {created_at}"
        )

    print()


def print_messages(w: int) -> None:
    print("💬 RECENT MESSAGES (last 10)")
    print("─" * w)

    if not MESSAGES_DIR.exists():
        print("  (no messages directory)")
        print()
        return

    # Get messages sorted by modification time (newest first)
    messages = []
    for msg_file in MESSAGES_DIR.glob("*.json"):
        if msg_file.is_file():
            data = load_json(msg_file)
            if data:
                data["_mtime"] = msg_file.stat().st_mtime
                messages.append(data)

    messages.sort(key=lambda x: x.get("_mtime", 0), reverse=True)

    if not messages:
        print("  (no messages)")
        print()
        return

    # Column layout: "  {marker}{priority} {from} → {to} [{type}] {content}"
    # Fixed overhead: 2 (indent) + 2 (markers) + 1 (space) + 3 (" → ") + 3 (" [" + "]") + 1 = 12
    available = w - 12
    # Proportions: from ~15%, to ~15%, type ~12%, content ~58%
    from_w = max(6, available * 15 // 100)
    to_w = max(6, available * 15 // 100)
    type_w = max(6, available * 12 // 100)
    content_w = max(10, available - from_w - to_w - type_w)

    for msg in messages[:10]:
        from_agent = msg.get("from", "?")[:from_w]
        to_agent = msg.get("to", "?")[:to_w]
        msg_type = msg.get("type", "?")[:type_w]
        content = msg.get("content", "")[:content_w].replace("\n", " ")
        is_read = msg.get("read", False)
        priority = msg.get("priority", "normal")

        marker = " " if is_read else "●"
        priority_marker = "!" if priority == "urgent" else " "

        print(
            f"  {marker}{priority_marker} {from_agent:<{from_w}} → {to_agent:<{to_w}}"
            f" [{msg_type:<{type_w}}] {content}"
        )

    print()


def print_archive_stats() -> None:
    archive_dir = MESSAGES_DIR / "archive"
    if archive_dir.exists():
        count = len(list(archive_dir.glob("*.json")))
        print(f"  📦 Archived: {count} messages")
    print()


def parse_prom_file(path: Path) -> dict[str, float]:
    """Parse a Prometheus text format file and return metric name → value mapping.

    Skips comment lines (# HELP, # TYPE) and blank lines.
    Returns an empty dict if the file is missing or malformed.
    """
    metrics: dict[str, float] = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(None, 1)
                if len(parts) == 2:
                    try:
                        metrics[parts[0]] = float(parts[1])
                    except ValueError:
                        continue
    except (FileNotFoundError, PermissionError, OSError):
        pass
    return metrics


# Metric names from the interface contract with the daemon
_COST_METRICS = {
    "tokens_input": "agent_hub_coordinator_tokens_input",
    "tokens_output": "agent_hub_coordinator_tokens_output",
    "cache_read": "agent_hub_coordinator_tokens_cache_read",
    "cache_write": "agent_hub_coordinator_tokens_cache_write",
    "cost_usd": "agent_hub_coordinator_estimated_cost_usd",
    "messages": "agent_hub_coordinator_messages_total",
}


def print_cost_panel(w: int) -> None:
    """Display coordinator cost and token usage from the metrics.prom file."""
    print("💰 COORDINATOR COST")
    print("─" * w)

    metrics = parse_prom_file(METRICS_FILE)
    if not metrics:
        print("  (no metrics available)")
        print()
        return

    cost = metrics.get(_COST_METRICS["cost_usd"])
    msgs = metrics.get(_COST_METRICS["messages"])
    tok_in = metrics.get(_COST_METRICS["tokens_input"])
    tok_out = metrics.get(_COST_METRICS["tokens_output"])
    cache_r = metrics.get(_COST_METRICS["cache_read"])
    cache_w = metrics.get(_COST_METRICS["cache_write"])

    # Row 1: cost and messages
    parts = []
    if cost is not None:
        parts.append(f"Cost: ${cost:.4f}")
    if msgs is not None:
        parts.append(f"Messages: {int(msgs)}")
    if parts:
        print(f"  {' │ '.join(parts)}")

    # Row 2: token breakdown
    tok_parts = []
    if tok_in is not None:
        tok_parts.append(f"In: {int(tok_in):,}")
    if tok_out is not None:
        tok_parts.append(f"Out: {int(tok_out):,}")
    if cache_r is not None:
        tok_parts.append(f"Cache R: {int(cache_r):,}")
    if cache_w is not None:
        tok_parts.append(f"Cache W: {int(cache_w):,}")
    if tok_parts:
        print(f"  Tokens: {' │ '.join(tok_parts)}")

    print()


def render_dashboard() -> None:
    """Render the full dashboard, adapting to current terminal width."""
    w = get_terminal_width()
    with display_lock:
        clear_screen()
        print_header(w)
        print_daemon_status(w)
        print_agents(w)
        print_threads(w)
        print_messages(w)
        print_archive_stats()
        print_cost_panel(w)
        print("─" * w)
        print("  Watching for changes... (Ctrl+C to exit)")
        sys.stdout.flush()


def main():
    # Ensure directories exist
    for d in [MESSAGES_DIR, AGENTS_DIR, THREADS_DIR]:
        d.mkdir(parents=True, exist_ok=True)

    # Set up filesystem observer
    observer = Observer()
    handler = HubEventHandler()

    observer.schedule(handler, str(MESSAGES_DIR), recursive=False)
    observer.schedule(handler, str(AGENTS_DIR), recursive=False)
    observer.schedule(handler, str(THREADS_DIR), recursive=False)
    # Watch HUB_DIR itself for metrics.prom changes
    observer.schedule(handler, str(HUB_DIR), recursive=False)

    observer.start()

    # Re-render on terminal resize (SIGWINCH) if supported
    if hasattr(signal, "SIGWINCH"):
        signal.signal(signal.SIGWINCH, lambda *_: refresh_event.set())

    try:
        # Initial render
        render_dashboard()

        while True:
            # Wait for filesystem event or timeout (for daemon status updates)
            triggered = refresh_event.wait(timeout=10)
            if triggered:
                refresh_event.clear()
                # Small debounce to batch rapid changes
                time.sleep(0.05)
            render_dashboard()

    except KeyboardInterrupt:
        print("\n  Exiting...")
    finally:
        observer.stop()
        observer.join()


if __name__ == "__main__":
    main()
