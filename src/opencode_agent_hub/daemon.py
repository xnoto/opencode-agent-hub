#!/usr/bin/env python3
# opencode-agent-hub - Multi-agent coordination daemon for OpenCode
# Copyright (c) 2025 xnoto
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "requests",
#     "watchdog",
# ]
# ///
"""Agent Hub Daemon - Watch ~/.agent-hub/messages and inject into OpenCode sessions.

WARNING: USE AT YOUR OWN RISK. This daemon enables autonomous agent-to-agent
communication which triggers LLM API calls. The authors are not responsible
for any token usage, API costs, or other expenses incurred by running this
software. Enable rate limiting if you are concerned about runaway costs.

Features:
- Hub server management (auto-starts headless OpenCode server on port 4096)
- Session polling to detect new agents and inject orientation
- Push notifications to OpenCode sessions (always wake, always expect response)
- Thread management (auto-create, track, resolve)
- Garbage collection (expire stale messages/threads)
- Rate limiting (optional, via environment variables)
- Self-contained response instructions in every injection

Hub server:
- Daemon auto-starts `opencode serve --port 4096` if not already running
- Hub server uses the user's normal OpenCode config (no isolation)
- TUI instances discover and connect to the hub server for message injection
- Session discovery uses direct SQLite queries (hub API listing is stale)
- Daemon injects messages via POST /session/{id}/prompt_async

Wake behavior: ALL messages wake agents (noReply: false)
- Agents don't need hub protocol in their definitions
- Daemon injects full response instructions with each message
- Running daemon = opt-in to coordination

Session polling:
- Polls OpenCode /session endpoint periodically
- Auto-creates unique agent identity per session (from slug or session ID)
- Injects orientation message on first match (identifies agent, no action required)
- Routes messages to agents by session ID (not directory)
- Tracks oriented sessions to avoid re-injection
- Agents do NOT need to proactively sync - messages are pushed to them

Thread lifecycle:
- Messages without threadId get one auto-assigned
- Threads tracked in ~/.agent-hub/threads/
- Thread resolved when owner sends type="completion" with "RESOLVED" in content
- Threads expire when all participants are stale (>1hr lastSeen)
- Messages expire after 1 hour regardless
- Stale agents (>10min lastSeen) are removed automatically
"""

import argparse
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from watchdog.observers import Observer

from opencode_agent_hub import __version__
from opencode_agent_hub.config import (
    AGENTS_DIR,
    ARCHIVE_DIR,
    COORDINATOR_ENABLED,
    COORDINATOR_SESSION_ID,
    GC_INTERVAL_SECONDS,
    INJECTION_WORKERS,
    MESSAGES_DIR,
    METRICS_FILE,
    METRICS_INTERVAL,
    OPENCODE_STORAGE_DIR,
    ORIENTED_SESSIONS,
    SESSION_AGENTS,
    SESSION_POLL_SECONDS,
    THREADS_DIR,
    log,
)
from opencode_agent_hub.coordinator import (
    find_coordinator_session,
    poll_coordinator_cost,
    start_coordinator,
    stop_coordinator,
)
from opencode_agent_hub.garbage_collector import run_gc
from opencode_agent_hub.hub_server import (
    get_hub_server_process,
    start_hub_server,
    stop_hub_server,
)
from opencode_agent_hub.messaging import (
    AgentHandler,
    MessageHandler,
    SessionHandler,
    get_injection_queue,
    get_message_queue,
    injection_worker,
    message_worker,
    session_worker,
)
from opencode_agent_hub.metrics import metrics
from opencode_agent_hub.models import PreflightError
from opencode_agent_hub.persistence import (
    load_agents,
    load_oriented_sessions,
    load_session_agents,
)
from opencode_agent_hub.preflight import check_agent_hub_mcp_configured
from opencode_agent_hub.service import install_systemd_service, uninstall_systemd_service
from opencode_agent_hub.sessions import check_orientation_retries, poll_active_sessions

DAEMON_START_TIME_MS: int = 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Agent Hub Daemon - Multi-agent coordination for OpenCode",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  agent-hub-daemon                    # Run daemon in foreground
  agent-hub-daemon --install-service  # Install as systemd user service
  agent-hub-daemon --uninstall-service
""",
    )
    parser.add_argument(
        "--install-service", action="store_true", help="Install and start as systemd user service"
    )
    parser.add_argument(
        "--uninstall-service", action="store_true", help="Remove systemd user service"
    )
    parser.add_argument("--version", action="store_true", help="Show version and exit")

    args = parser.parse_args()

    if args.version:
        print(f"agent-hub-daemon {__version__}")
        sys.exit(0)

    if args.install_service:
        sys.exit(0 if install_systemd_service() else 1)

    if args.uninstall_service:
        sys.exit(0 if uninstall_systemd_service() else 1)

    # Preflight: verify agent-hub MCP is configured before starting
    try:
        check_agent_hub_mcp_configured()
    except PreflightError as e:
        log.error(f"Preflight check failed: {e}")
        raise SystemExit(1) from None

    # Ensure directories exist
    MESSAGES_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    THREADS_DIR.mkdir(parents=True, exist_ok=True)
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)

    # Purge stale agent registrations from previous daemon runs.
    # Agents are ephemeral — live agents re-register via MCP on their own.
    # Without this, the coordinator would broadcast to dead agents on startup.
    purged = 0
    for agent_file in AGENTS_DIR.glob("*.json"):
        try:
            agent_file.unlink()
            purged += 1
        except OSError:
            pass
    if purged:
        log.info(f"Purged {purged} stale agent registrations from previous run")

    # Load persisted state
    # Only sessions created AFTER daemon starts will be oriented
    import opencode_agent_hub.config as config_module
    from opencode_agent_hub.config import ORIENTATION_PENDING

    DAEMON_START_TIME_MS = int(time.time() * 1000)
    config_module.DAEMON_START_TIME_MS = DAEMON_START_TIME_MS

    loaded_oriented = load_oriented_sessions()
    ORIENTED_SESSIONS.update(loaded_oriented)

    loaded_session_agents = load_session_agents()
    SESSION_AGENTS.update(loaded_session_agents)
    ORIENTATION_PENDING.clear()

    log.info(
        f"Daemon starting at {DAEMON_START_TIME_MS} - {len(ORIENTED_SESSIONS)} sessions already oriented"
    )
    log.info(f"Watching messages: {MESSAGES_DIR}")
    log.info(f"Watching sessions: {OPENCODE_STORAGE_DIR}")
    log.info(f"Watching agents: {AGENTS_DIR}")
    log.info(f"Message TTL: 3600s, GC interval: {GC_INTERVAL_SECONDS}s")
    if COORDINATOR_ENABLED:
        from opencode_agent_hub.config import COORDINATOR_DIR

        log.info(f"Coordinator: enabled, dir={COORDINATOR_DIR}")
    else:
        log.info("Coordinator: disabled")

    # Start hub server if not already running
    start_hub_server()

    # Start coordinator (after hub server is ready)
    if not start_coordinator():
        log.error("Failed to start coordinator - daemon cannot function without it")
        # Clean up resources before exiting
        stop_hub_server()
        sys.exit(1)

    # Shared agents dict - updated by AgentHandler
    agents = load_agents()
    log.info(f"Loaded {len(agents)} registered agents: {list(agents.keys())}")

    # Set up observers
    observer = Observer()

    # Watch messages directory
    message_handler = MessageHandler()
    observer.schedule(message_handler, str(MESSAGES_DIR), recursive=False)

    # Watch OpenCode sessions directory (if exists)
    # Watches recursively to catch both global/ and project-specific subdirectories
    # Only triggers on NEW session files - does NOT scan existing sessions on startup
    # This prevents spamming hundreds of sessions with orientation messages
    if OPENCODE_STORAGE_DIR.exists():
        session_handler = SessionHandler()
        observer.schedule(session_handler, str(OPENCODE_STORAGE_DIR), recursive=True)
        log.info(f"Watching for new sessions in {OPENCODE_STORAGE_DIR} (recursive)")
    else:
        log.warning(f"Sessions directory not found: {OPENCODE_STORAGE_DIR}")

    # Watch agents directory for registration changes
    agent_handler = AgentHandler(agents)
    observer.schedule(agent_handler, str(AGENTS_DIR), recursive=False)

    observer.start()

    # Shutdown event for coordinating thread termination
    shutdown_event = threading.Event()

    # Handle signals for graceful shutdown
    def shutdown_handler(signum: int, _frame: Any) -> None:
        log.info(f"Received signal {signum}, shutting down...")
        shutdown_event.set()
        observer.stop()
        stop_coordinator()
        stop_hub_server()

    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, shutdown_handler)

    def session_poller() -> None:
        """Background thread that polls for new active sessions."""
        while not shutdown_event.is_set():
            try:
                poll_active_sessions(agents, shutdown_event)
                check_orientation_retries(agents)
            except Exception as e:
                log.error(f"Session poller error: {e}")
            shutdown_event.wait(SESSION_POLL_SECONDS)

    def gc_worker() -> None:
        """Background thread for garbage collection."""
        while not shutdown_event.is_set():
            try:
                run_gc(agents)
            except Exception as e:
                log.error(f"GC error: {e}")
            shutdown_event.wait(GC_INTERVAL_SECONDS)

    def hub_monitor() -> None:
        """Background thread to monitor hub server health."""
        while not shutdown_event.is_set():
            hub_process = get_hub_server_process()
            if hub_process is not None and hub_process.poll() is not None:
                log.warning("Hub server died, restarting...")
                start_hub_server()
            shutdown_event.wait(10)  # Check every 10 seconds

    def coordinator_monitor() -> None:
        """Background thread to monitor coordinator session on hub server."""
        while not shutdown_event.is_set():
            if COORDINATOR_ENABLED and COORDINATOR_SESSION_ID is not None:
                try:
                    session = find_coordinator_session()
                    if session is None:
                        log.warning("Coordinator session disappeared from hub")
                        # Note: We don't restart - coordinator is only created at daemon startup
                except Exception:  # nosec B110
                    pass  # Logged by find_coordinator_session
            shutdown_event.wait(30)  # Check every 30 seconds

    def metrics_worker() -> None:
        """Background thread to write metrics and log summaries."""
        injection_queue = get_injection_queue()
        message_queue = get_message_queue()

        while not shutdown_event.is_set():
            try:
                # Update queue gauges
                metrics.set_gauge("agent_hub_injection_queue_size", injection_queue.qsize())
                metrics.set_gauge("agent_hub_message_queue_size", message_queue.qsize())

                # Update coordinator cost metrics
                poll_coordinator_cost()

                # Write Prometheus metrics file
                metrics.write_to_textfile(str(METRICS_FILE))

                # Log summary
                log.info(f"Metrics: {metrics.log_summary()}")
            except Exception as e:
                log.error(f"Metrics worker error: {e}")
            shutdown_event.wait(METRICS_INTERVAL)

    # Set initial gauge values
    metrics.set_gauge("agent_hub_active_agents", len(agents))
    metrics.set_gauge("agent_hub_oriented_sessions", len(ORIENTED_SESSIONS))

    # Start background threads via ThreadPoolExecutor for graceful shutdown
    num_workers = 5 + INJECTION_WORKERS + 2  # 5 service threads + injection + message/session
    executor = ThreadPoolExecutor(max_workers=num_workers, thread_name_prefix="agent-hub")

    futures = []
    futures.append(executor.submit(session_poller))
    futures.append(executor.submit(gc_worker))
    futures.append(executor.submit(hub_monitor))
    futures.append(executor.submit(coordinator_monitor))
    futures.append(executor.submit(metrics_worker))
    futures.append(executor.submit(message_worker, agents, shutdown_event))
    futures.append(executor.submit(session_worker, agents, shutdown_event))

    # Submit injection workers (pool for concurrent injections)
    for _i in range(INJECTION_WORKERS):
        futures.append(executor.submit(injection_worker, shutdown_event))

    log.info(f"Started {len(futures)} background threads ({INJECTION_WORKERS} injection workers)")

    try:
        # Main thread just waits - all work happens in threads and watchdog callbacks
        while not shutdown_event.is_set():
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Shutting down")
    finally:
        shutdown_event.set()
        observer.stop()
        # Wait for in-progress work to complete (threads check shutdown_event)
        executor.shutdown(wait=True)
        stop_coordinator()
        stop_hub_server()
    observer.join()


if __name__ == "__main__":
    main()
