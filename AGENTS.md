# Agent Hub Internals

Operational reference for AI agents and developers working with the injection pipeline, message routing, and test authoring.

## Hub Server API

The daemon communicates with the OpenCode hub server (`opencode serve`) on the configured port.

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/session` | GET | List active sessions |
| `/session` | POST | Create session |
| `/session/{id}/prompt_async` | POST | Inject message and trigger LLM |
| `/session/{id}/message` | POST | Add orientation context without triggering the LLM |
| `/session/{id}/message` | GET | List session messages |
| `/session/{id}` | DELETE | Delete a coordinator session during lifecycle cleanup |
| `/config` | PATCH | Update server configuration at runtime |

### `prompt_async`

| Field | Required | Description |
|-------|----------|-------------|
| `parts` | Yes | `[{"type": "text", "text": "..."}]` |
| `model` | No | `{"providerID": "...", "modelID": "..."}` — overrides which LLM is called |
| `agent` | No | Agent name string — controls the agent label in the UI |

Without `agent`, the hub server labels all injected messages as its default agent, even when the correct model is being used. Without `model`, the hub server calls its default model.

### `PATCH /config`

Sets the hub server's default model at runtime. The daemon calls this at startup with `HUB_MODEL` to ensure API-created sessions use the configured free model instead of the server's built-in default.

## OpenCode Message Schema

OpenCode stores messages in a SQLite database. The `message` table has a `data` column containing JSON with `role`, `agent`, `modelID`, and `providerID` fields.

The `agent` field reflects which agent processed the message. For user messages from the TUI, this matches the user's `--agent` flag. For messages injected via the API, it reflects whatever the daemon passed (or the hub server's default if omitted).

## Model Resolution

When injecting a message, the daemon resolves the model in this order:

1. **Coordinator session** — explicit model from the coordinator's `opencode.json`
2. **Regular session with detected agent** — agent name looked up in `AGENT_MODELS` (built at startup)
3. **Regular session, agent undetected** — hub server's default model (set via `HUB_MODEL`)

When `opencode.json` specifies both `"agent"` and `"model"`, the explicit `"model"` field takes priority. The `"agent"` field is used only for the `prompt_async` agent label.

## Agent Detection

The daemon detects a session's agent by querying the **first user message** in SQLite (ordered ASC by creation time). New sessions are not oriented until their first user message appears.

### Invariants

1. **Query user messages, not assistant messages.** The hub server auto-creates assistant messages with a default agent on session creation. Only user messages carry the real agent.
2. **Query the first user message (ASC order).** Later user messages may be daemon-injected. The first one is always the real user's prompt.
3. **Pass resolved `model` and `agent` values on `prompt_async`; omit unresolved values.** Never send `null` or invent a fallback. The hub server applies its configured defaults for fields the daemon cannot resolve.
4. **No hardcoded agent names.** End users have varied agent configurations. All fallbacks use `DEFAULT_AGENT` (configurable, defaults to None).
5. **Coordinator bypasses SQLite detection.** Its model and agent are resolved from `opencode.json` at startup.
6. **Explicit `"model"` takes priority over `"agent"` lookup in config.** The `"agent"` field is for labeling; `"model"` specifies the provider and model to call.

## Operational Safeguards

- Startup preflight verifies that the Agent Hub MCP is enabled, its tools are permitted, and configured agent mappings use parseable `provider/model` identifiers before message watching begins. It does not contact providers to prove model availability.
- Coordinator orientation uses `POST /session/{id}/message` so context can be attached without waking the model. Work injection uses `prompt_async`.
- Delivery feedback is written as `delivery-status` system messages. Consumers should not treat those notifications as user work.
- Route-specific chatty throttling is configurable and enabled by default. Preserve the configured window, message limit, and cooldown when changing routing behavior.
- Message and feedback files use atomic writes. Keep filesystem event handling compatible with create and move events.

## Release Packaging and Signing

- `.github/workflows/release-packages.yml` owns Linux release artifacts and the signed APT/RPM repositories published to GitHub Pages. Publishing or manually dispatching it changes external distribution state and requires explicit confirmation.
- Manual recovery dispatches must use an existing immutable `v*` release tag. They rebuild that tag, republish the package repositories, and intentionally do not replace GitHub release assets.
- `GPG_PRIVATE_KEY` and `GPG_PASSPHRASE` are repository secrets in `xnoto/opencode-agent-hub`; they are not Homebrew tap secrets. Never print, download, or expose their values while diagnosing workflows.
- The workflow pins the expected full signing-key fingerprint and derives expiry from the imported secret key. Do not replace this with a hard-coded date or a short key ID.
- After extending the existing key's expiry, export the renewed secret key back into `GPG_PRIVATE_KEY`. A true key rotation also requires deliberately updating the pinned fingerprint and user-facing key-refresh instructions.
- Pull requests build packages but skip signing and publication because repository secrets are unavailable. After an approved publication, verify the deployed `KEY.gpg` fingerprint and expiry, APT and RPM signatures, and published package versions.
- Homebrew formula updates are separate from Linux package signing. The source workflow opens formula-update pull requests in `xnoto/homebrew-opencode-agent-hub`; follow that repository's `AGENTS.md` before changing or merging them.

## Testing

Python 3.11 or newer is required. Set up and verify the repository with:

```bash
uv sync --all-extras
uv lock --check
pre-commit install
pre-commit install --hook-type commit-msg
```

Tests are in `tests/`, organized by feature area. Run with `uv run --frozen pytest`. Test names follow `test_[action]_[condition]_[expected]` and read as complete sentences. Use Given-When-Then structure with named constants and descriptive assertion messages.

When modifying injection behavior, ensure tests verify:
- The correct `model` and `agent` appear in the `prompt_async` payload
- `None` values are omitted from the payload (not passed as null)
- No agent name strings are hardcoded — use `DEFAULT_AGENT` or config values
- Both the coordinator and regular session code paths are covered

Pre-commit hooks enforce Ruff, formatting, YAML/TOML validation, secret checks, mypy, Bandit, Vulture, Conventional Commits, and pytest. Work on a feature branch because `main` is protected and guarded by `no-commit-to-branch`. Run `pre-commit run --all-files` to verify locally, then recheck `git status` in case a package tool attempted to refresh `uv.lock`.
