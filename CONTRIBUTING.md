# Contributing to opencode-agent-hub

## Development Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/xnoto/opencode-agent-hub
   cd opencode-agent-hub
   ```

2. Install dependencies with uv:
   ```bash
   uv sync --all-extras
   ```

3. Run the daemon locally:
   ```bash
   uv run agent-hub-daemon
   ```

### Using Devfile (Standardized Environment)

This project includes a `devfile.yaml` for consistent development environments. You can use tools like `odo` or cloud IDEs (Eclipse Che, OpenShift Dev Spaces) to automatically provision the environment.

1. Initialize and start the environment (using `odo`):
   ```bash
   odo init
   odo dev
   ```

2. Run tests:
   ```bash
   odo run --command test
   ```

3. Run the daemon:
   ```bash
   odo run --command run-daemon
   ```

## Pre-commit Hooks

Install pre-commit hooks (required):

```bash
uv run pre-commit install
uv run pre-commit install --hook-type commit-msg
```

This enforces:
- Code linting and formatting (ruff)
- YAML/TOML validation
- **Conventional commit messages** (required for automated releases)

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

Use a scope to indicate what area is affected:
- `daemon` - Main daemon code
- `watch` - Dashboard script
- `config` - Configuration/env vars/config file
- `docs` - Documentation
- `ci` - GitHub Actions
- `deps` - Dependencies
- `tests` - Test files

### Examples

```bash
# Good
git commit -m "feat(daemon): add rate limiting for agent messages"
git commit -m "fix(watch): handle missing agents directory"
git commit -m "docs(readme): add rate limiting configuration"

# Breaking change (major version bump)
git commit -m "feat(daemon)!: change message format to v2"
```

### Releases

Releases are automated via [release-please](https://github.com/google-github-actions/release-please-action):

1. Conventional commits on `main` auto-update a Release PR
2. Release PR contains version bump + CHANGELOG
3. Merging the Release PR triggers PyPI publish

## Pull Requests

1. Fork the repository
2. Create a feature branch (`git checkout -b feat/my-feature`)
3. Make your changes
4. Run linting and tests
5. Commit with a descriptive message
6. Push to your fork
7. Open a Pull Request

## Architecture Overview

```
~/.agent-hub/
├── agents/                 # Agent registration files
├── messages/               # Message queue (JSON files)
│   └── archive/            # Processed messages
├── threads/                # Conversation tracking
├── session_agents.json     # Session-to-agent identity mapping
└── oriented_sessions.json  # Session orientation cache

~/.config/agent-hub-daemon/
├── config.json             # Optional config file (env vars override)
├── AGENTS.md               # Optional coordinator instructions override
└── COORDINATOR.md          # Alias for AGENTS.md

Daemon watches these directories and:
1. Detects new messages via watchdog
2. Looks up target agent's OpenCode session (by session ID, not directory)
3. Injects message via OpenCode HTTP API
4. Marks message as delivered
```

### Session-Based Agent Identity

Multiple OpenCode sessions in the same directory each get a unique agent identity:
- Agent ID derived from session slug (e.g., "cosmic-panda") or session ID
- Enables parallel agents working on the same codebase without conflicts
- Session-agent mapping persisted in `~/.agent-hub/session_agents.json`

## Testing

Tests use pytest:
```bash
uv run pytest -v
```

For coverage:
```bash
uv run pytest --cov=opencode_agent_hub --cov-report=html
```
