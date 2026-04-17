# opencode-agent-hub

Multi-agent coordination for [OpenCode](https://github.com/anomalyco/opencode). Lets multiple AI agents in separate OpenCode sessions talk to each other.

> **Warning**: This enables autonomous agent-to-agent communication which triggers LLM API calls. Use at your own risk. Consider enabling [rate limiting](#rate-limiting) to control costs.

## Demo

https://github.com/user-attachments/assets/b591f1d2-01d7-4408-bf60-67eb7a8fbf0c

## How It Works

- The **daemon** starts an OpenCode hub server (`opencode serve --port 4096`) and discovers sessions by polling OpenCode's shared SQLite database
- A **coordinator** session facilitates introductions between new agents, then steps back
- Agents communicate by writing JSON files to `~/.agent-hub/messages/` via the [agent-hub-mcp](https://github.com/gilbarbara/agent-hub-mcp) tools
- The daemon watches for new message files, looks up the target agent's session, and injects the message via `prompt_async` — agents don't poll, they get woken up

## Known Limitations

- **Injected messages not visible in TUI** — agent-to-agent messages work but users can't see them in the conversation. Upstream issue: [opencode#8564](https://github.com/sst/opencode/issues/8564). Use `agent-hub-watch` to monitor.
- **TUI spinner after response** — the TUI may briefly show "thinking" after an agent finishes responding to an injection. Visual only, no extra token consumption.
- **Orientation may trigger security heuristics** — some models (particularly Claude) may flag orientation messages as prompt injections. The agent still has MCP tools and can collaborate, just without orientation context.

## Prerequisites

[agent-hub-mcp](https://github.com/gilbarbara/agent-hub-mcp) must be configured in OpenCode. The daemon will refuse to start without it.

Find your OpenCode config location with `opencode debug paths`, then add to `opencode.json`:

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

Verify with `opencode mcp list` (should show `agent-hub connected`).

## Quickstart

```bash
git clone https://github.com/xnoto/opencode-agent-hub
cd opencode-agent-hub

# Terminal 1: start the daemon
uv run agent-hub-daemon

# Terminal 2: monitor activity
uv run agent-hub-watch
```

## Installation

### Homebrew (macOS)

```bash
brew install xnoto/opencode-agent-hub/opencode-agent-hub
```

### Linux Packages

**Debian / Ubuntu:**

```bash
curl -fsSL https://xnoto.github.io/opencode-agent-hub/KEY.gpg | sudo gpg --dearmor -o /etc/apt/keyrings/xnoto.gpg
echo "deb [signed-by=/etc/apt/keyrings/xnoto.gpg] https://xnoto.github.io/opencode-agent-hub/apt ./" | sudo tee /etc/apt/sources.list.d/xnoto.list
sudo apt update && sudo apt install opencode-agent-hub
```

**Fedora / RHEL:**

```bash
sudo curl -o /etc/yum.repos.d/xnoto.repo https://xnoto.github.io/opencode-agent-hub/xnoto.repo
sudo dnf install opencode-agent-hub
```

**Arch Linux (AUR):**

```bash
yay -S opencode-agent-hub
```

See [GitHub Releases](https://github.com/xnoto/opencode-agent-hub/releases) for direct .deb/.rpm downloads.

### uv / pipx (PyPI)

```bash
uv tool install opencode-agent-hub
# or
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
brew services start opencode-agent-hub
tail -f ~/Library/Logs/agent-hub-daemon.log
brew services stop opencode-agent-hub
```

### Linux (systemd)

```bash
agent-hub-daemon --install-service     # install + start
journalctl --user -u agent-hub-daemon -f
systemctl --user stop agent-hub-daemon
agent-hub-daemon --uninstall-service   # remove
```

If installed via RPM/DEB, a system-wide service file is included — enable with `systemctl --user enable --now agent-hub-daemon`.

## Configuration

Config file: `~/.config/agent-hub-daemon/config.json` (all fields optional). Environment variables override config file values.

```json
{
  "hub": {
    "port": 4096,
    "model": "opencode/minimax-m2.5-free"
  },
  "log_level": "INFO",
  "rate_limit": {
    "enabled": false,
    "max_messages": 10,
    "window_seconds": 300,
    "cooldown_seconds": 0
  },
  "coordinator": {
    "enabled": true,
    "directory": "~/.agent-hub/coordinator",
    "agents_md": ""
  },
  "gc": { "message_ttl_seconds": 3600, "agent_stale_seconds": 3600, "interval_seconds": 60 },
  "session": { "poll_seconds": 5, "cache_ttl": 10 },
  "injection": { "workers": 4, "retries": 3, "timeout": 5 },
  "metrics_interval": 30
}
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENCODE_PORT` | `4096` | Hub server port |
| `AGENT_HUB_MODEL` | `opencode/minimax-m2.5-free` | Hub server default model (`provider/model`) |
| `AGENT_HUB_DEFAULT_AGENT` | (none) | Agent name for undetectable sessions |
| `AGENT_HUB_DAEMON_LOG_LEVEL` | `INFO` | Log level |
| `AGENT_HUB_MESSAGE_TTL` | `3600` | Message TTL (seconds) |
| `AGENT_HUB_AGENT_STALE` | `3600` | Agent stale threshold (seconds) |
| `AGENT_HUB_GC_INTERVAL` | `60` | GC interval (seconds) |
| `AGENT_HUB_SESSION_POLL` | `5` | Session poll interval (seconds) |
| `AGENT_HUB_SESSION_CACHE_TTL` | `10` | Session cache TTL (seconds) |
| `AGENT_HUB_INJECTION_WORKERS` | `4` | Injection worker threads |
| `AGENT_HUB_INJECTION_RETRIES` | `3` | Injection retry attempts |
| `AGENT_HUB_INJECTION_TIMEOUT` | `5` | Injection timeout (seconds) |
| `AGENT_HUB_METRICS_INTERVAL` | `30` | Metrics write interval (seconds) |

### Rate Limiting

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENT_HUB_RATE_LIMIT` | `false` | Enable rate limiting |
| `AGENT_HUB_RATE_LIMIT_MAX` | `10` | Max messages per agent per window |
| `AGENT_HUB_RATE_LIMIT_WINDOW` | `300` | Window size (seconds) |
| `AGENT_HUB_RATE_LIMIT_COOLDOWN` | `0` | Min seconds between messages |

### Coordinator

The coordinator is a dedicated OpenCode session that introduces agents to each other. It starts non-blocking and uses the same message pipeline as any other agent.

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENT_HUB_COORDINATOR` | `true` | Enable coordinator |
| `AGENT_HUB_COORDINATOR_DIR` | `~/.agent-hub/coordinator` | Coordinator working directory |
| `AGENT_HUB_COORDINATOR_PRESERVE_LOCAL_AGENTS_MD` | `false` | Keep existing AGENTS.md on restart |
| `AGENT_HUB_COORDINATOR_READY_TIMEOUT` | `20` | Bootstrap ready timeout (seconds) |
| `AGENT_HUB_COORDINATOR_STRICT_READY` | `false` | Require exact `READY` acknowledgment |
| `AGENT_HUB_COORDINATOR_BOOTSTRAP_REQUIRED` | `false` | Fail startup if bootstrap times out |
| `AGENT_HUB_COORDINATOR_AGENTS_MD` | (auto-detect) | Custom AGENTS.md path |

The coordinator model is configured in `opencode.json` within the coordinator directory (default: `opencode/minimax-m2.5-free`). Custom coordinator instructions (`AGENTS.md`) are auto-detected from the config directory, package template, or system paths — override with `AGENT_HUB_COORDINATOR_AGENTS_MD`.

## Message Format

Messages are JSON files in `~/.agent-hub/messages/`:

```json
{
  "from": "agent-id",
  "to": "target-agent-id",
  "type": "message|completion|delivery-status|task|question|context|error",
  "content": "Message content",
  "priority": "normal|urgent",
  "threadId": "auto-generated-or-provided",
  "timestamp": 1234567890000
}
```

Required fields: `from`, `to`, `content`. Messages are validated with pydantic on receipt; malformed messages are rejected with delivery feedback to the sender.

## Directory Structure

```
~/.agent-hub/
├── agents/                 # Registered agent files
├── messages/               # Pending messages
│   └── archive/            # Processed messages
├── threads/                # Conversation threads
├── metrics.prom            # Prometheus metrics (prometheus_client format)
├── oriented_sessions.json  # Orientation cache
└── session_agents.json     # Session-to-agent mapping

~/.config/agent-hub-daemon/
└── config.json             # Optional config
```

## Development

Install with `uv sync --all-extras`. Pre-commit hooks enforce linting, formatting, type checking, and tests automatically. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and workflow. See [AGENTS.md](AGENTS.md) for injection pipeline internals and test-writing conventions.

## Acknowledgments

- **[OpenCode](https://github.com/anomalyco/opencode)** by [anomalyco](https://github.com/anomalyco)
- **[agent-hub-mcp](https://github.com/gilbarbara/agent-hub-mcp)** by [@gilbarbara](https://github.com/gilbarbara)

## License

AGPL-3.0 - See [LICENSE](LICENSE) for details.
