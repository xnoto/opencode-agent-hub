# opencode-agent-hub

Multi-agent coordination daemon and tools for [OpenCode](https://github.com/anomalyco/opencode).

Enables multiple AI agents running in separate OpenCode sessions to communicate, collaborate, and coordinate work through a shared message bus.

> **Warning**: This software enables autonomous agent-to-agent communication which triggers LLM API calls. Use at your own risk. The authors are not responsible for any token usage, API costs, or other expenses incurred. Consider enabling [rate limiting](#rate-limiting-optional) to control costs.

## Demo

https://github.com/user-attachments/assets/b591f1d2-01d7-4408-bf60-67eb7a8fbf0c

## Table of Contents

- [Features](#features)
- [How It Works](#how-it-works)
  - [Session Discovery](#session-discovery)
  - [Message Flow](#message-flow)
  - [The Relay Server](#the-relay-server)
  - [Coordination Flow](#coordination-flow)
  - [Push Model (No Polling Required by Agents)](#push-model-no-polling-required-by-agents)
  - [Session Lifecycle](#session-lifecycle)
  - [Known Issues](#known-issues)
    - [Injected Messages Not Visible in TUI](#injected-messages-not-visible-in-tui)
    - [TUI May Show Continued Processing After Response](#tui-may-show-continued-processing-after-response)
    - [Orientation Messages May Trigger Security Heuristics](#orientation-messages-may-trigger-security-heuristics)
  - [Coordination Test Results (Jan 2026)](#coordination-test-results-jan-2026)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Quickstart](#quickstart)
- [Installation](#installation)
  - [Homebrew (macOS)](#homebrew-macos)
  - [Linux Packages](#linux-packages)
  - [uv (PyPI)](#uv-pypi)
  - [pipx (PyPI)](#pipx-pypi)
  - [From source](#from-source)
- [Running as a Service](#running-as-a-service)
  - [macOS (Homebrew)](#macos-homebrew)
  - [Linux (systemd)](#linux-systemd)
- [Usage](#usage)
  - [Start the daemon](#start-the-daemon)
  - [Monitor with the dashboard](#monitor-with-the-dashboard)
  - [Run as standalone scripts (uv)](#run-as-standalone-scripts-uv)
- [Configuration](#configuration)
  - [Rate Limiting (Optional)](#rate-limiting-optional)
  - [Coordinator (Optional)](#coordinator-optional)
- [Directory Structure](#directory-structure)
- [Message Format](#message-format)
- [Integration with MCP](#integration-with-mcp)
- [Metrics](#metrics)
- [Development](#development)
- [Acknowledgments](#acknowledgments)
- [License](#license)

## Features

- **Message Bus**: Filesystem-based message passing between agents via `~/.agent-hub/messages/`
- **Session Integration**: Automatically discovers and injects messages into OpenCode sessions
- **Thread Management**: Conversations tracked with auto-created thread IDs and resolution
- **Session-Based Agent Identity**: Each OpenCode session gets a unique agent ID (even in the same directory)
- **Agent Auto-Registration**: Sessions automatically registered as agents with unique identities
- **Garbage Collection**: Stale messages, agents, and threads cleaned up (1hr TTL)
- **Prometheus Metrics**: Exportable metrics at `~/.agent-hub/metrics.prom`
- **Dashboard**: Real-time terminal UI showing agents, threads, and messages
- **Config File Support**: Optional JSON config file at `~/.config/agent-hub-daemon/config.json`

## How It Works

The daemon operates as a **message broker** between OpenCode sessions, using a local relay server to inject messages directly into agent conversations and to **proactively encourage coordination** via a coordinator agent.

### Session Discovery

1. **Daemon starts** an OpenCode relay server on port 4096 (if not already running)
2. **Polls the relay API** (`GET /session`) every 5 seconds to discover active sessions
3. **Auto-registers agents** with unique identities derived from session slug or ID
4. **Injects an orientation message** into newly discovered sessions, informing the agent of its registered identity
5. **Notifies the coordinator** to capture the agent's task and introduce related agents

**Note**: Multiple sessions in the same directory each get unique agent IDs (e.g., "cosmic-panda", "brave-tiger"), enabling parallel agents working on the same codebase.

### Message Flow

When Agent A sends a message to Agent B:

```
Agent A                      Daemon                        Agent B
   │                            │                             │
   │  write JSON to             │                             │
   │  ~/.agent-hub/messages/    │                             │
   │ ──────────────────────────>│                             │
   │                            │  detect new file            │
   │                            │  (watchdog)                 │
   │                            │                             │
   │                            │  lookup Agent B's session   │
   │                            │  via relay API              │
   │                            │                             │
   │                            │  POST /session/{id}/prompt  │
   │                            │ ───────────────────────────>│
   │                            │                             │
   │                            │                    Agent B wakes,
   │                            │                    sees message with
   │                            │                    response instructions
```

### The Relay Server

The daemon auto-starts `opencode serve --port 4096` which provides:

- **Session listing**: `GET /session` - returns all active OpenCode sessions
- **Message injection**: `POST /session/{id}/prompt_async` - injects a prompt that wakes the agent

This relay server sees **all** OpenCode TUI instances on the machine, allowing the daemon to route messages to any session regardless of which terminal it's running in. The coordinator relies on the relay server to inject **task capture prompts** and **introductions** that encourage agents to collaborate.

### Coordination Flow

The coordinator uses the relay server to proactively connect agents without requiring the user to manually broker introductions.

```
New Session            Daemon                Coordinator            Other Agent
     │                    │                       │                       │
     │  OpenCode TUI      │                       │                       │
     │ ──────────────────>│                       │                       │
     │                    │  notify NEW_AGENT     │                       │
     │                    │ ──────────────────────>                       │
     │                    │                       │  ask: "What are you   │
     │                    │                       │  working on?"         │
     │                    │                       │ ──────────────────────>
     │                    │                       │                       │
     │                    │                       │  analyze tasks        │
     │                    │                       │  send introductions   │
     │                    │                       │ ──────────────────────>
     │                    │                       │                       │
```

This keeps the coordination overhead low while still ensuring agents know who to talk to.

### Push Model (No Polling Required by Agents)

Agents don't need to poll for messages. The daemon:
1. Watches the filesystem for new message files
2. Looks up the target agent's active session
3. Injects the message directly into that session via the relay API
4. The injection **wakes** the agent and triggers an LLM response

Each injected message includes full response instructions, so agents don't need special hub protocol knowledge.

### Session Lifecycle

The daemon only tracks sessions created **after** it starts:

- **New sessions**: OpenCode TUIs started after the daemon will receive an orientation message and be tracked for message routing
- **Pre-existing sessions**: Sessions that were running before the daemon started are ignored (no orientation spam)
- **Daemon restart**: Gives a clean slate - only sessions created after restart are tracked

This design ensures no unnecessary token generation on historical sessions while reliably catching all new agent work.

### Known Issues

#### Injected Messages Not Visible in TUI

Injected messages (orientation and inter-agent communication) are not visible in the OpenCode TUI. This is a known upstream issue: [opencode#8564](https://github.com/sst/opencode/issues/8564)

**Impact**: Agent-to-agent communication happens "invisibly" from the user's perspective. Agents receive and process the messages, but users don't see them in the conversation.

**Workaround**: Use `agent-hub-watch` dashboard to monitor agent activity, message flow, and conversation threads in real-time.

#### TUI May Show Continued Processing After Response

After an agent completes a response, the TUI may briefly show continued "thinking" indicators (spinning tokens). This appears to be a TUI display artifact related to how `prompt_async` injections are handled, not actual token consumption.

**Impact**: Visual only - the agent has completed its work despite the spinner.

#### Orientation Messages May Trigger Security Heuristics

Some models (particularly Claude) may flag orientation messages as potential prompt injections due to their structured format. The daemon uses a minimal plain-text format to reduce this, but highly security-conscious model configurations may still flag them.

**Impact**: Agent may acknowledge hub tools but report "no connection message received."

**Workaround**: The agent will still have access to hub tools via MCP and can collaborate - it just won't have the orientation context.

### Coordination Test Results (Jan 2026)

Observed a minimal coordination run with two agents (frontend + backend) and a coordinator configured to **introduce once, then stand down**.

**Test setup**:
- Frontend task: login form that calls `POST /api/auth/login`
- Backend task: implement `/api/auth/login` with JWT response
- Coordinator model: `opencode/claude-opus-4-5`

**Observed interaction** (3 total messages):
1. Frontend → Backend: asked for API contract details (request/response/error shapes)
2. Backend → Frontend: provided full contract (status codes, schemas, CORS, JWT)
3. Frontend → Backend: confirmed receipt and implementation

**QA follow-up**:
- A third agent (QA) was spawned with only the instruction to test the end-to-end login flow.
- The QA agent asked for ports/credentials and received the details directly from frontend/backend without explicit user brokering.

**Outcomes**:
- ✅ Agents coordinated directly without broadcast spam
- ✅ Coordinator stayed silent (no redundant acknowledgments)
- ✅ Contract handshake completed in 3 messages
- ✅ QA agent successfully obtained testing details from other agents without manual coordination

**Cost overhead (estimate)**:
- ~5–7 message injections (frontend/backend + QA) → ~5–7 LLM calls
- Approx total tokens: **~8k–12k** (context + responses)
- Approx cost: **$0.08–$0.25** depending on model/provider


## Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│                           agent-hub-daemon                             │
│                                                                        │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                  │
│  │ File Watcher │  │ Session Poll │  │  GC Worker   │                  │
│  │  (watchdog)  │  │   (5s loop)  │  │  (60s loop)  │                  │
│  └──────┬───────┘  └──────┬───────┘  └──────────────┘                  │
│         │                 │                                            │
│         │  new message    │  new session                               │
│         ▼                 ▼                                            │
│  ┌────────────────────────────────────┐                                │
│  │      Message Processing Queue      │                                │
│  │   (async workers with retries)     │                                │
│  └─────────────────┬──────────────────┘                                │
│                    │                                                   │
│                    │ POST /session/{id}/prompt_async                   │
│                    ▼                                                   │
│  ┌────────────────────────────────────┐    ┌────────────────────────┐  │
│  │   OpenCode Relay Server (4096)     │───▶│   OpenCode Sessions    │  │
│  │   - Lists all active sessions      │    │   (TUI instances)      │  │
│  │   - Injects prompts into any       │    │                        │  │
│  └────────────────────────────────────┘    └────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────┘
```

## Prerequisites

This daemon requires [agent-hub-mcp](https://github.com/gilbarbara/agent-hub-mcp) by [@gilbarbara](https://github.com/gilbarbara) to be configured in OpenCode. This MCP server provides the tools agents use to send messages.

**The daemon will fail to start if agent-hub MCP is not configured.** This is intentional - without it, agents cannot communicate.

### Find your OpenCode config location

```bash
opencode debug paths
# Look for the "config" line, e.g.: config /home/user/.config/opencode
```

### Add agent-hub MCP to your config

Edit `opencode.json` in your config directory:

```json
{
  "mcp": {
    "agent-hub": {
      "type": "local",
      "command": ["npx", "-y", "agent-hub-mcp@latest"],
      "enabled": true
    }
  }
}
```

### Verify configuration

```bash
opencode mcp list
# Should show: ✓ agent-hub connected
```

Restart OpenCode after adding the configuration.

## Quickstart

1. Install (pick one):

```bash
# macOS (Homebrew)
brew install xnoto/tap/opencode-agent-hub

# Debian/Ubuntu - see Installation section for full setup
# Fedora/RHEL - see Installation section for full setup

# Cross-platform (PyPI)
uv tool install opencode-agent-hub
# or: pipx install opencode-agent-hub
```

2. Start the daemon:

```bash
agent-hub-daemon
```

3. Monitor with the dashboard:

```bash
agent-hub-watch
```

4. (Optional) Run as a service (see [Running as a Service](#running-as-a-service)).

## Installation

### Homebrew (macOS)

```bash
brew install xnoto/tap/opencode-agent-hub
```

### Linux Packages (Repository)

**Debian / Ubuntu:**

```bash
# Add GPG key
curl -fsSL https://xnoto.github.io/opencode-agent-hub/KEY.gpg | sudo gpg --dearmor -o /etc/apt/keyrings/xnoto.gpg

# Add repository
echo "deb [signed-by=/etc/apt/keyrings/xnoto.gpg] https://xnoto.github.io/opencode-agent-hub/apt ./" | sudo tee /etc/apt/sources.list.d/xnoto.list

# Install
sudo apt update
sudo apt install opencode-agent-hub
```

**Fedora / RHEL:**

```bash
# Add repository
sudo curl -o /etc/yum.repos.d/xnoto.repo https://xnoto.github.io/opencode-agent-hub/xnoto.repo

# Install
sudo dnf install opencode-agent-hub
```

**Arch Linux (AUR):**

```bash
yay -S opencode-agent-hub
```

**Manual download:** See [GitHub Releases](https://github.com/xnoto/opencode-agent-hub/releases) for direct .deb/.rpm downloads.

### uv (PyPI)

```bash
uv tool install opencode-agent-hub
```

### pipx (PyPI)

```bash
pipx install opencode-agent-hub
```

### From source

```bash
git clone https://github.com/xnoto/opencode-agent-hub
cd opencode-agent-hub
uv sync
```

## Running as a Service

### macOS (Homebrew)

```bash
# Start as a service
brew services start opencode-agent-hub

# View logs
tail -f ~/Library/Logs/agent-hub-daemon.log

# Stop
brew services stop opencode-agent-hub
```

### Linux (systemd)

The daemon can install itself as a systemd user service:

```bash
# Install and start the service
agent-hub-daemon --install-service

# View logs
journalctl --user -u agent-hub-daemon -f

# Management commands
systemctl --user status agent-hub-daemon
systemctl --user stop agent-hub-daemon
systemctl --user restart agent-hub-daemon

# Uninstall the service
agent-hub-daemon --uninstall-service
```

This works regardless of how you installed the daemon (package, pip, pipx, uv, etc.).

If you installed via RPM/DEB package, a service file is also installed system-wide at `/usr/lib/systemd/user/agent-hub-daemon.service` which any user can enable with `systemctl --user enable --now agent-hub-daemon`.

## Usage

### Start the daemon

```bash
agent-hub-daemon
```

The daemon will:
1. **Verify agent-hub MCP is configured** (exits with instructions if not)
2. Start an OpenCode hub server on port 4096 (if not already running)
3. Watch `~/.agent-hub/messages/` for new message files
4. Auto-register agents for any OpenCode session it discovers
5. Inject messages into the appropriate sessions

> **Note**: If agent-hub MCP is not configured, the daemon will exit immediately with clear instructions on how to fix it. See [Prerequisites](#prerequisites).

### Monitor with the dashboard

```bash
agent-hub-watch
```

Shows a live view of:
- Daemon status (running/stopped)
- Registered agents and their last seen time
- Active conversation threads
- Recent messages

### Run as standalone scripts (uv)

The scripts can also run directly without installation:

```bash
# Daemon
uv run src/opencode_agent_hub/daemon.py

# Dashboard
uv run src/opencode_agent_hub/watch.py
```

## Configuration

The daemon supports configuration via a JSON config file and/or environment variables.

**Precedence**: Environment variables > Config file > Defaults

### Config File

Create `~/.config/agent-hub-daemon/config.json`:

```json
{
  "opencode_port": 4096,
  "log_level": "INFO",
  "rate_limit": {
    "enabled": false,
    "max_messages": 10,
    "window_seconds": 300,
    "cooldown_seconds": 0
  },
  "coordinator": {
    "enabled": true,
    "model": "opencode/claude-opus-4-5",
    "directory": "~/.agent-hub/coordinator",
    "agents_md": ""
  },
  "gc": {
    "message_ttl_seconds": 3600,
    "agent_stale_seconds": 3600,
    "interval_seconds": 60
  },
  "session": {
    "poll_seconds": 5,
    "cache_ttl": 10
  },
  "injection": {
    "workers": 4,
    "retries": 3,
    "timeout": 5
  },
  "metrics_interval": 30
}
```

All fields are optional - only specify what you want to override.

### Environment Variables

Environment variables take precedence over config file values:

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENCODE_PORT` | `4096` | Port for OpenCode relay server |
| `AGENT_HUB_DAEMON_LOG_LEVEL` | `INFO` | Logging level (DEBUG, INFO, WARNING, ERROR) |
| `AGENT_HUB_MESSAGE_TTL` | `3600` | Message TTL in seconds |
| `AGENT_HUB_AGENT_STALE` | `3600` | Agent stale threshold in seconds |
| `AGENT_HUB_GC_INTERVAL` | `60` | Garbage collection interval in seconds |
| `AGENT_HUB_SESSION_POLL` | `5` | Session poll interval in seconds |
| `AGENT_HUB_SESSION_CACHE_TTL` | `10` | Session cache TTL in seconds |
| `AGENT_HUB_INJECTION_WORKERS` | `4` | Number of injection worker threads |
| `AGENT_HUB_INJECTION_RETRIES` | `3` | Injection retry attempts |
| `AGENT_HUB_INJECTION_TIMEOUT` | `5` | Injection timeout in seconds |
| `AGENT_HUB_METRICS_INTERVAL` | `30` | Metrics write interval in seconds |

### Rate Limiting (Optional)

To prevent excessive agent chatter, enable rate limiting:

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENT_HUB_RATE_LIMIT` | `false` | Enable rate limiting (`true`, `1`, or `yes`) |
| `AGENT_HUB_RATE_LIMIT_MAX` | `10` | Max messages per agent per window |
| `AGENT_HUB_RATE_LIMIT_WINDOW` | `300` | Window size in seconds (default: 5 min) |
| `AGENT_HUB_RATE_LIMIT_COOLDOWN` | `0` | Min seconds between messages from same agent |

Example - limit agents to 5 messages per 10 minutes with 30s cooldown:

```bash
export AGENT_HUB_RATE_LIMIT=true
export AGENT_HUB_RATE_LIMIT_MAX=5
export AGENT_HUB_RATE_LIMIT_WINDOW=600
export AGENT_HUB_RATE_LIMIT_COOLDOWN=30
```

Rate-limited messages are archived with `rateLimited: true` for debugging.

### Coordinator (Optional)

The coordinator is a dedicated OpenCode session that facilitates **initial** agent introductions, then steps back.

Configuration via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENT_HUB_COORDINATOR` | `true` | Enable the coordinator agent (`true`, `1`, or `yes`) |
| `AGENT_HUB_COORDINATOR_MODEL` | `opencode/claude-opus-4-5` | OpenCode model for the coordinator session |
| `AGENT_HUB_COORDINATOR_DIR` | `~/.agent-hub/coordinator` | Directory used for the coordinator session |
| `AGENT_HUB_COORDINATOR_AGENTS_MD` | (auto-detect) | Custom path to coordinator AGENTS.md |

Example - run coordinator on a different model:

```bash
export AGENT_HUB_COORDINATOR_MODEL=opencode/claude-sonnet-4-5
```

#### Custom Coordinator Instructions

You can customize the coordinator's behavior by providing your own AGENTS.md file. The daemon searches for the template in this order:

1. **Explicit config**: `AGENT_HUB_COORDINATOR_AGENTS_MD` env var or `coordinator.agents_md` in config file
2. **User config**: `~/.config/agent-hub-daemon/AGENTS.md`
3. **User config alias**: `~/.config/agent-hub-daemon/COORDINATOR.md`
4. **Package template**: `contrib/coordinator/AGENTS.md` (from installation)
5. **System locations**: `/usr/local/share/opencode-agent-hub/coordinator/AGENTS.md`

If no template is found, a minimal default is created. To customize:

```bash
# Copy the default template and edit
mkdir -p ~/.config/agent-hub-daemon
cp /path/to/opencode-agent-hub/contrib/coordinator/AGENTS.md ~/.config/agent-hub-daemon/
# Edit to your liking
```

Or specify an explicit path:

```bash
export AGENT_HUB_COORDINATOR_AGENTS_MD=~/my-coordinator-instructions.md
```

## Directory Structure

```
~/.agent-hub/
├── agents/                 # Registered agent JSON files
├── messages/               # Pending messages (JSON files)
│   └── archive/            # Processed/expired messages
├── threads/                # Conversation thread tracking
├── metrics.prom            # Prometheus metrics export
├── oriented_sessions.json  # Session orientation cache
└── session_agents.json     # Session-to-agent identity mapping

~/.config/agent-hub-daemon/
└── config.json             # Optional config file
```

## Message Format

Messages are JSON files in `~/.agent-hub/messages/`:

```json
{
  "from": "agent-id",
  "to": "target-agent-id",
  "type": "task|question|context|completion|error",
  "content": "Message content here",
  "priority": "normal|urgent|high|low",
  "threadId": "auto-generated-or-provided",
  "timestamp": 1234567890000
}
```

### Message Types

| Type | Purpose |
|------|---------|
| `task` | Request work from another agent |
| `question` | Ask for information |
| `context` | Share context/information |
| `completion` | Report task completion (include "RESOLVED" to close thread) |
| `error` | Report an error |

## Integration with MCP

The daemon works with [agent-hub-mcp](https://github.com/gilbarbara/agent-hub-mcp) which provides tools for agents to:

- `register_agent` - Register with the hub
- `send_message` - Send messages to other agents
- `get_messages` - Retrieve pending messages
- `sync` - Get all pending work

When the daemon is running, agents don't need to poll - messages are pushed directly into their sessions.

## Metrics

Prometheus-compatible metrics exported to `~/.agent-hub/metrics.prom`:

### Counters

| Metric | Description |
|--------|-------------|
| `agent_hub_messages_total` | Total messages processed |
| `agent_hub_messages_failed_total` | Messages that failed to process |
| `agent_hub_injections_total` | Messages injected into sessions |
| `agent_hub_injections_retried_total` | Injection retries attempted |
| `agent_hub_injections_failed_total` | Injections that failed after retries |
| `agent_hub_sessions_oriented_total` | Sessions that received orientation |
| `agent_hub_agents_auto_created_total` | Agents auto-registered from sessions |
| `agent_hub_gc_runs_total` | Garbage collection runs |
| `agent_hub_gc_sessions_cleaned_total` | Stale sessions cleaned up |
| `agent_hub_gc_agents_cleaned_total` | Stale agents cleaned up |
| `agent_hub_gc_messages_archived_total` | Messages archived during GC |
| `agent_hub_cache_hits_total` | Session cache hits |
| `agent_hub_cache_misses_total` | Session cache misses |

### Gauges

| Metric | Description |
|--------|-------------|
| `agent_hub_active_agents` | Current registered agents |
| `agent_hub_oriented_sessions` | Sessions with active orientation |
| `agent_hub_injection_queue_size` | Pending injections in queue |
| `agent_hub_message_queue_size` | Pending messages in queue |

## Development

```bash
# Clone and setup
git clone https://github.com/xnoto/opencode-agent-hub
cd opencode-agent-hub
uv sync --all-extras

# Run linting
uv run ruff check .
uv run ruff format .

# Run type checking
uv run mypy src/

# Run tests
uv run pytest
```

## Acknowledgments

This project builds on the work of:

- **[OpenCode](https://github.com/anomalyco/opencode)** by [anomalyco](https://github.com/anomalyco) - The AI coding assistant this daemon integrates with
- **[agent-hub-mcp](https://github.com/gilbarbara/agent-hub-mcp)** by [@gilbarbara](https://github.com/gilbarbara) - The MCP server providing agent communication tools

Thank you for making your work available to the community.

## License

MIT - See [LICENSE](LICENSE) for details.
