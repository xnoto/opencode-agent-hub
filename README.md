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

The coordinator model is set in `~/.agent-hub/coordinator/opencode.json` via the `"model"` field (default: `opencode/minimax-m2.5-free`). An optional `"agent"` field controls the agent label on injected messages; if omitted, the hub server's default applies.

Custom coordinator instructions are searched in order:
1. `AGENT_HUB_COORDINATOR_AGENTS_MD` env var
2. `~/.config/agent-hub-daemon/AGENTS.md` (or `COORDINATOR.md`)
3. Package template (`contrib/coordinator/AGENTS.md`)
4. `/usr/local/share/opencode-agent-hub/coordinator/AGENTS.md`
5. Auto-generated minimal default

## Message Format

Messages are JSON files in `~/.agent-hub/messages/`:

```json
{
  "from": "agent-id",
  "to": "target-agent-id",
  "type": "message|completion|delivery-status",
  "content": "Message content",
  "priority": "normal|urgent",
  "threadId": "auto-generated-or-provided",
  "timestamp": 1234567890000
}
```

Required fields: `from`, `to`, `content`. The hub validates message schema on receipt and rejects malformed messages.

**Delivery feedback**: When a message is delivered (or delivery fails), the hub sends a `delivery-status` message back to the original sender with the outcome.

## Injection Pipeline

The daemon injects messages into OpenCode sessions via the hub server's HTTP API. Understanding this pipeline is critical for avoiding model/agent routing bugs.

### How Agent Detection Works

Each OpenCode session has an agent (e.g. `gpt`, `kimi`, `claude`) set by the user's `--agent` flag. The daemon detects this by querying the **first user message** in OpenCode's SQLite database:

```sql
SELECT json_extract(data, '$.agent')
  FROM message
 WHERE session_id = ? AND json_extract(data, '$.role') = 'user'
 ORDER BY time_created ASC LIMIT 1
```

**Why user messages, not assistant messages?** The hub server auto-creates assistant messages with `agent='claude'` when a session is created, regardless of what agent the user chose. Only user messages (from the TUI) carry the correct agent.

**Deferred orientation**: New sessions are not oriented until their first user message appears in SQLite. This prevents the daemon from injecting before the real agent is detectable.

### Hub Server API

The daemon communicates with the OpenCode hub server (`opencode serve`) on the configured port.

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/session` | GET | List active sessions |
| `/session` | POST | Create session |
| `/session/{id}/prompt_async` | POST | Inject message + trigger LLM |
| `/session/{id}/message` | GET | List session messages |
| `/config` | PATCH | Update server configuration |

**`prompt_async` fields:**
- `parts` (required): Message content as `[{"type": "text", "text": "..."}]`
- `model` (optional): `{"providerID": "...", "modelID": "..."}` — overrides which LLM is called
- `agent` (optional): Agent name string — controls the agent label on injected messages in the UI

Without the `agent` field, the hub server labels all injected messages as its default agent (typically `claude`), even when the correct model is being used.

**`PATCH /config`** sets the hub server's default model at runtime. The daemon calls this at startup with `HUB_MODEL` to ensure API-created sessions use the free model instead of the server's built-in default.

### OpenCode SQLite Message Schema

OpenCode stores messages in `~/.local/share/opencode/opencode.db`. The `message` table has a `data` column containing JSON:

**User message:**
```json
{"role": "user", "agent": "gpt", "model": {"providerID": "openai", "modelID": "gpt-5.4"}}
```

**Assistant message:**
```json
{"role": "assistant", "agent": "gpt", "modelID": "gpt-5.4", "providerID": "openai", "tokens": {...}}
```

The `agent` field reflects which agent processed the message. For user messages from the TUI, this matches the user's `--agent` flag. For messages injected via the API, this reflects whatever the daemon passed (or the hub server's default if omitted).

### Model Resolution Priority

When injecting a message into a session, the daemon resolves the model in this order:

1. **Coordinator session** → uses explicit `COORDINATOR_MODEL` from `opencode.json`
2. **Regular session with detected agent** → looks up agent in `AGENT_MODELS` (built at startup from `opencode debug config`)
3. **Regular session, agent undetected** → relies on hub server's default model (set via `HUB_MODEL`)

For `opencode.json` (coordinator config), when both `"agent"` and `"model"` are specified, the explicit `"model"` field takes priority. The `"agent"` field is used only for the prompt_async agent label.

## Directory Structure

```
~/.agent-hub/
├── agents/                 # Registered agent files
├── messages/               # Pending messages
│   └── archive/            # Processed messages
├── threads/                # Conversation threads
├── metrics.prom            # Prometheus metrics
├── oriented_sessions.json  # Orientation cache
└── session_agents.json     # Session-to-agent mapping

~/.config/agent-hub-daemon/
└── config.json             # Optional config
```

## Development

```bash
uv sync --all-extras
uv run ruff check .
uv run ruff format .
uv run mypy src/
uv run pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for integration testing and architecture details.

## Acknowledgments

- **[OpenCode](https://github.com/anomalyco/opencode)** by [anomalyco](https://github.com/anomalyco)
- **[agent-hub-mcp](https://github.com/gilbarbara/agent-hub-mcp)** by [@gilbarbara](https://github.com/gilbarbara)

## License

AGPL-3.0 - See [LICENSE](LICENSE) for details.
