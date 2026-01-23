#!/usr/bin/env python3
# opencode-agent-hub - Multi-agent coordination daemon for OpenCode
# Copyright (c) 2025 xnoto
# /// script
# requires-python = ">=3.11"
# dependencies = ["watchdog"]
# ///
"""Agent Hub Dashboard - Real-time view of agents, threads, and messages."""

import json
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

# Synchronization
refresh_event = Event()
display_lock = Lock()


class HubEventHandler(FileSystemEventHandler):
    """Trigger refresh on any filesystem change."""

    def on_any_event(self, event):
        if event.event_type not in ["created", "deleted", "modified", "moved"]:
            return

        if event.src_path.endswith(".json"):
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


def print_header():
    print("═" * 70)
    print("                        AGENT HUB DASHBOARD")
    print("═" * 70)
    print()


def print_daemon_status():
    print("🔧 DAEMON STATUS")
    print("─" * 70)

    import subprocess

    result = subprocess.run(["pgrep", "-f", "agent-hub-daemon"], capture_output=True, text=True)
    pids = result.stdout.strip().split("\n")
    pids = [p for p in pids if p]

    if pids:
        print(f"  Status: RUNNING (PID {pids[0]})")
    else:
        print("  Status: STOPPED")
    print()


def print_agents():
    print("📡 REGISTERED AGENTS")
    print("─" * 70)

    if not AGENTS_DIR.exists():
        print("  (no agents directory)")
        print()
        return

    agents = list(AGENTS_DIR.glob("*.json"))
    if not agents:
        print("  (no agents registered)")
        print()
        return

    for agent_file in sorted(agents):
        data = load_json(agent_file)
        if not data:
            continue

        agent_id = data.get("id", "?")[:15]
        role = data.get("role", "?")[:30]
        status = data.get("status", "?")[:10]
        last_seen = data.get("lastSeen", 0)
        time_str = relative_time(last_seen)

        print(f"  {agent_id:<15} {role:<30} {status:<10} {time_str}")

    print()


def print_threads():
    print("🧵 ACTIVE THREADS")
    print("─" * 70)

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

    for t in sorted(open_threads, key=lambda x: x.get("createdAt", 0), reverse=True)[:5]:
        thread_id = t.get("id", "?")[:15]
        created_by = t.get("createdBy", "?")[:12]
        participants = ", ".join(t.get("participants", []))[:25]
        created_at = relative_time(t.get("createdAt", 0))

        print(f"  {thread_id:<15} by {created_by:<12} [{participants:<25}] {created_at}")

    print()


def print_messages():
    print("💬 RECENT MESSAGES (last 10)")
    print("─" * 70)

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

    for msg in messages[:10]:
        from_agent = msg.get("from", "?")[:12]
        to_agent = msg.get("to", "?")[:12]
        msg_type = msg.get("type", "?")[:10]
        content = msg.get("content", "")[:50].replace("\n", " ")
        is_read = msg.get("read", False)
        priority = msg.get("priority", "normal")

        marker = " " if is_read else "●"
        priority_marker = "!" if priority == "urgent" else " "

        print(
            f"  {marker}{priority_marker} {from_agent:<12} → {to_agent:<12} [{msg_type:<10}] {content}"
        )

    print()


def print_archive_stats():
    archive_dir = MESSAGES_DIR / "archive"
    if archive_dir.exists():
        count = len(list(archive_dir.glob("*.json")))
        print(f"  📦 Archived: {count} messages")
    print()


def render_dashboard():
    """Render the full dashboard."""
    with display_lock:
        clear_screen()
        print_header()
        print_daemon_status()
        print_agents()
        print_threads()
        print_messages()
        print_archive_stats()
        print("─" * 70)
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

    observer.start()

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
