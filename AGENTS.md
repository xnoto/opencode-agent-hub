# Agent Hub Internals

Operational reference for AI agents and developers working with the injection pipeline, message routing, and test authoring.

## Hub Server API

The daemon communicates with the OpenCode hub server (`opencode serve`) on the configured port.

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/session` | GET | List active sessions |
| `/session` | POST | Create session |
| `/session/{id}/prompt_async` | POST | Inject message and trigger LLM |
| `/session/{id}/message` | GET | List session messages |
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
3. **Always pass both `model` and `agent` on `prompt_async`.** Without either, the hub server applies defaults that may not match the session.
4. **No hardcoded agent names.** End users have varied agent configurations. All fallbacks use `DEFAULT_AGENT` (configurable, defaults to None).
5. **Coordinator bypasses SQLite detection.** Its model and agent are resolved from `opencode.json` at startup.
6. **Explicit `"model"` takes priority over `"agent"` lookup in config.** The `"agent"` field is for labeling; `"model"` specifies the provider and model to call.

## Testing

Tests are in `tests/`, organized by feature area. Run with `uv run pytest`. Test names follow `test_[action]_[condition]_[expected]` and read as complete sentences. Use Given-When-Then structure with named constants and descriptive assertion messages.

When modifying injection behavior, ensure tests verify:
- The correct `model` and `agent` appear in the `prompt_async` payload
- `None` values are omitted from the payload (not passed as null)
- No agent name strings are hardcoded — use `DEFAULT_AGENT` or config values
- Both the coordinator and regular session code paths are covered

Pre-commit hooks enforce all checks automatically. Run `pre-commit run --all-files` to verify locally.
