# Contributing to opencode-agent-hub

Thank you for your interest in contributing! This guide covers everything you need to know to develop, test, and contribute to the agent hub.

---

## Table of Contents

1. [Development Setup](#development-setup)
2. [Development Workflow](#development-workflow)
3. [Commit Messages](#commit-messages)
4. [Testing Standards](#testing-standards)
5. [Self-Evident Test Patterns](#self-evident-test-patterns)
6. [Test Coverage](#test-coverage)
7. [Architecture Overview](#architecture-overview)
8. [Common Tasks](#common-tasks)
9. [Pull Requests](#pull-requests)

---

## Development Setup

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) for dependency management

### Installation

```bash
# Clone the repository
git clone https://github.com/xnoto/opencode-agent-hub
cd opencode-agent-hub

# Install dependencies
uv sync --all-extras

# Install pre-commit hooks (required)
uv run pre-commit install
uv run pre-commit install --hook-type commit-msg
```

Pre-commit hooks enforce:
- Code linting and formatting (ruff)
- YAML/TOML validation
- **Conventional commit messages** (required for automated releases)

---

## Development Workflow

### Running Tests

```bash
# Run all tests (150 tests must pass)
uv run pytest -v

# Run with coverage
uv run pytest --cov=opencode_agent_hub --cov-report=html

# Run specific test file
uv run pytest tests/test_config.py -v

# Run with fail-fast (stop on first failure)
uv run pytest -x

# Run single test with debug output
uv run pytest tests/test_config.py::test_env_var_takes_precedence -v -s
```

### Running the Daemon Locally

```bash
# Terminal 1: Start daemon
uv run agent-hub-daemon

# Terminal 2: Monitor activity
uv run agent-hub-watch

# Terminal 3: Start a TUI session
opencode --agent test-agent
```

### Making Changes

1. **Write tests first** - Follow TDD when adding features
2. **Follow test patterns** - See [Self-Evident Test Patterns](#self-evident-test-patterns)
3. **Run full test suite** - All 150 tests must pass
4. **Commit with conventional commits** - Required for automated releases

---

## Commit Messages

This project uses [Conventional Commits](https://www.conventionalcommits.org/) (enforced by pre-commit).

**Format**: `type(scope): description`

### Types (required)

| Type | Description | Version Bump |
|------|-------------|--------------|
| `feat` | New feature | Minor (0.1.0 → 0.2.0) |
| `fix` | Bug fix | Patch (0.1.0 → 0.1.1) |
| `docs` | Documentation only | None |
| `refactor` | Code change (no feature/fix) | None |
| `test` | Adding/updating tests | None |
| `chore` | Maintenance tasks | None |
| `perf` | Performance improvement | Patch |
| `ci` | CI/CD changes | None |

### Scope (recommended)

- `daemon` - Main daemon entry point
- `watch` - Dashboard script
- `config` - Configuration/env vars/config file
- `messaging` - Message injection and processing
- `sessions` - Session discovery and orientation
- `coordinator` - Coordinator agent management
- `rate-limiting` - Rate limiting logic
- `persistence` - File I/O and storage
- `metrics` - Prometheus metrics collection
- `gc` - Garbage collection
- `models` - Data classes
- `utils` - Utility functions
- `docs` - Documentation
- `ci` - GitHub Actions
- `deps` - Dependencies
- `tests` - Test files

### Examples

```bash
# Good
git commit -m "feat(daemon): add rate limiting for agent messages"
git commit -m "fix(watch): handle missing agents directory"
git commit -m "test(config): add nested config path coverage"

# Breaking change (major version bump)
git commit -m "feat(daemon)!: change message format to v2"
```

### Releases

Releases are automated via [release-please](https://github.com/google-github-actions/release-please-action):

1. Conventional commits on `main` auto-update a Release PR
2. Release PR contains version bump + CHANGELOG
3. Merging the Release PR triggers PyPI publish

---

## Testing

Tests are in `tests/`, organized by feature. Run with `uv run pytest`. See [AGENTS.md](AGENTS.md) for test-writing conventions and injection-specific testing guidance.

Pre-commit hooks enforce all checks automatically. Run `pre-commit run --all-files` to verify locally.

### Integration Testing

To test session discovery, orientation, and multi-agent coordination end-to-end:

**Terminal 1 — Daemon:**
```bash
uv run agent-hub-daemon
```

**Terminal 2 — Watch dashboard:**
```bash
uv run agent-hub-watch
```

**Terminal 3+ — TUI sessions:**
```bash
opencode                    # default model
opencode --agent kimi       # specific agent/model
```

**What to verify:**
- The daemon detects new TUI sessions within ~5 seconds (watch the daemon logs)
- New sessions receive an orientation message from the coordinator
- The watch dashboard shows the session and its agent identity
- Messages sent between agents are delivered (check `~/.agent-hub/messages/`)

The daemon discovers sessions by querying OpenCode's shared SQLite database at
`~/.local/share/opencode/opencode.db`. All OpenCode processes (TUI and serve)
share this database, so the daemon sees every session regardless of how it was
started.

---

## Architecture Overview

### Project Structure

```
opencode-agent-hub/
├── src/opencode_agent_hub/
│   ├── __init__.py          # Version info
│   ├── daemon.py            # Main entry point (~200 lines)
│   ├── watch.py             # Dashboard TUI (~400 lines)
│   ├── utils.py             # Atomic file operations, path validation
│   ├── models.py            # Data classes (InjectionTask, MessageTask, etc.)
│   ├── config.py            # Configuration management and constants
│   ├── persistence.py       # File I/O, agent/thread storage
│   ├── rate_limiting.py     # Rate limiting logic
│   ├── metrics.py           # Prometheus metrics collection
│   ├── hub_server.py        # OpenCode hub server lifecycle
│   ├── coordinator.py       # Coordinator agent management
│   ├── sessions.py          # Session discovery and orientation
│   ├── messaging.py         # Message injection and processing
│   └── garbage_collector.py # GC logic for agents/sessions
├── tests/
│   ├── test_config.py       # Configuration loading tests
│   ├── test_coordinator.py  # Coordinator session management
│   ├── test_coordinator_cost.py  # Token/cost tracking
│   ├── test_orientation_retry.py # Session orientation retry
│   ├── test_rate_limiting.py     # Rate limiting tests
│   ├── test_session_agents.py    # Session-agent mapping
│   ├── test_watch.py        # Dashboard rendering tests
│   └── test_placeholder.py  # Integration tests
├── contrib/coordinator/     # Coordinator template files
│   └── AGENTS.md            # Coordinator instructions
├── pyproject.toml           # Project metadata, dependencies
└── CONTRIBUTING.md          # This file
```

### Data Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                         OpenCode Hub Server                      │
│                      (opencode serve --port 4096)               │
└─────────────────────────────────────────────────────────────────┘
                                    │
            ┌───────────────────────┼───────────────────────┐
            │                       │                       │
            ▼                       ▼                       ▼
   ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
   │   Session A     │    │   Session B     │    │  Coordinator    │
   │  (agent: dev)   │    │  (agent: test)  │    │  (orchestrator) │
   └─────────────────┘    └─────────────────┘    └─────────────────┘
            │                       │                       │
            └───────────────────────┼───────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Agent Hub Daemon                            │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │  File Watch  │  │  SQLite Poll │  │  Message Injection   │  │
│  │ ~/.agent-hub/│  │ opencode.db  │  │ prompt_async API     │  │
│  └──────────────┘  └──────────────┘  └──────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### Directory Structure (Runtime)

```
~/.agent-hub/
├── agents/                    # Agent registration JSON files
│   ├── coordinator.json       # Coordinator session agent
│   └── <agent-id>.json        # One file per registered agent
├── messages/                  # Message queue
│   ├── <uuid>.json           # Pending messages
│   └── archive/              # Processed messages
├── threads/                   # Thread tracking
│   └── <thread-id>.json      # Thread metadata
├── coordinator/              # Coordinator workspace
├── session_agents.json       # Session → Agent ID mapping
├── oriented_sessions.json    # Sessions that received orientation
└── metrics.prom              # Prometheus metrics export

~/.config/agent-hub-daemon/
├── config.json               # User configuration (optional)
├── AGENTS.md                 # Optional coordinator instructions override
└── COORDINATOR.md            # Alias for AGENTS.md

~/.local/share/opencode/
└── opencode.db               # Shared SQLite database (all sessions)
```

### Key Components

| Component | Purpose | Test File |
|-----------|---------|-----------|
| `start_hub_server()` (hub_server.py) | Manages OpenCode hub process | ❌ Needs tests |
| `poll_active_sessions()` (sessions.py) | Discovers new TUI sessions | `test_session_agents.py` |
| `process_message_file()` (messaging.py) | Routes messages to agents | ❌ Needs tests |
| `inject_message()` (messaging.py) | Injects via OpenCode API | ❌ Needs tests |
| `start_coordinator()` (coordinator.py) | Creates coordinator session | `test_coordinator.py` |
| `run_gc()` (garbage_collector.py) | Cleans up stale data | ❌ Needs tests |
| `check_rate_limit()` (rate_limiting.py) | Throttles excessive messages | `test_rate_limiting.py` |

### Session-Based Agent Identity

Multiple OpenCode sessions in the same directory each get a unique agent identity:
- Agent ID derived from session slug (e.g., "cosmic-panda") or session ID
- Enables parallel agents working on the same codebase without conflicts
- Session-agent mapping persisted in `~/.agent-hub/session_agents.json`

Daemon workflow (coordinated across modules):
1. **sessions.py**: Polls SQLite DB for new sessions (primary discovery mechanism)
2. **sessions.py**: Detects session agent from **first user message** in SQLite (not assistant — see below)
3. **messaging.py**: Detects new messages via watchdog on `~/.agent-hub/messages/`
4. **messaging.py**: Looks up target agent's OpenCode session (by session ID, not directory)
5. **messaging.py**: Resolves model from `AGENT_MODELS` lookup, passes both `model` and `agent` on `prompt_async`
6. **persistence.py**: Marks message as delivered

For hub server API details, message schema, model resolution, and agent detection invariants, see [AGENTS.md](AGENTS.md).

---

## Common Tasks

### Adding a New Configuration Option

1. Add to `config.py` defaults section:
```python
MY_NEW_SETTING = _get_config_value(
    "AGENT_HUB_MY_SETTING",
    ["my_setting"],
    default="default_value",
    _CONFIG,
    str,
)
```

2. Add test to `test_config.py`:
```python
def test_my_setting_uses_env_var_when_set():
    """MY_SETTING uses env var AGENT_HUB_MY_SETTING when available."""
    with mock.patch.dict(os.environ, {"AGENT_HUB_MY_SETTING": "custom"}):
        result = _get_config_value(...)
    assert result == "custom"
```

3. Update README.md documentation

### Adding a New MCP Tool

1. Define tool schema in `messaging.py` or appropriate module
2. Implement handler function
3. Add test covering:
   - Success case
   - Error handling
   - Edge cases (empty input, invalid params)

### Debugging Test Failures

```bash
# Run single test with verbose output
uv run pytest tests/test_config.py::test_env_var_takes_precedence -v -s

# Run with debugger
uv run pytest tests/test_config.py --pdb

# Run with detailed assertion info
uv run pytest tests/test_config.py -vv
```

---

## Pull Requests

1. Fork the repository
2. Create a feature branch (`git checkout -b feat/my-feature`)
3. Make your changes following the patterns above
4. **Run the full test suite** - all 150 tests must pass
5. Commit with a descriptive message following conventional commits
6. Push to your fork
7. Open a Pull Request

### PR Checklist

- [ ] Tests added/updated for new functionality
- [ ] All 150 tests pass
- [ ] Self-evident test patterns followed
- [ ] Code follows existing style (enforced by pre-commit)
- [ ] Commit messages follow conventional format
- [ ] Documentation updated (README.md if user-facing changes)
