# Agent Hub Internals

Operational reference for AI agents and developers working with the injection pipeline.

## Hub Server API

The daemon communicates with the OpenCode hub server (`opencode serve`) on the configured port.

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/session` | GET | List active sessions |
| `/session` | POST | Create session |
| `/session/{id}/prompt_async` | POST | Inject message + trigger LLM |
| `/session/{id}/message` | GET | List session messages |
| `/config` | PATCH | Update server configuration |

### `prompt_async`

| Field | Required | Description |
|-------|----------|-------------|
| `parts` | Yes | `[{"type": "text", "text": "..."}]` |
| `model` | No | `{"providerID": "...", "modelID": "..."}` — overrides which LLM is called |
| `agent` | No | Agent name string — controls the agent label in the UI |

Without `agent`, the hub server labels all injected messages as its default agent (typically `claude`), even when the correct model is being used. Without `model`, the hub server calls its default model.

### `PATCH /config`

Sets the hub server's default model at runtime. The daemon calls this at startup with `HUB_MODEL` to ensure API-created sessions use the configured free model instead of the server's built-in default.

## OpenCode SQLite Message Schema

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

## Model Resolution Priority

When injecting a message, the daemon resolves the model in this order:

1. **Coordinator session** — uses explicit `COORDINATOR_MODEL` from `opencode.json`
2. **Regular session with detected agent** — looks up agent in `AGENT_MODELS` (built at startup from `opencode debug config`)
3. **Regular session, agent undetected** — relies on hub server's default model (set via `HUB_MODEL`)

For `opencode.json` (coordinator config), when both `"agent"` and `"model"` are specified, the explicit `"model"` field takes priority. The `"agent"` field is used only for the `prompt_async` agent label.

## Agent Detection

Each OpenCode session has an agent (e.g. `gpt`, `kimi`, `claude`) set by the user's `--agent` flag. The daemon detects this by querying the **first user message** in SQLite:

```sql
SELECT json_extract(data, '$.agent')
  FROM message
 WHERE session_id = ? AND json_extract(data, '$.role') = 'user'
 ORDER BY time_created ASC LIMIT 1
```

New sessions are not oriented until their first user message appears. This prevents the daemon from injecting before the real agent is detectable.

### Invariants

1. **Query user messages, not assistant messages.** The hub server auto-creates assistant messages with `agent='claude'` on session creation. Only user messages (from the TUI) carry the real agent.

2. **Query the first user message (ASC order).** Later user messages may be daemon-injected. The first one is always the real user's prompt.

3. **Always pass both `model` and `agent` on `prompt_async`.** Without `model`, the hub server uses its default. Without `agent`, messages get labeled incorrectly in the UI.

4. **No hardcoded agent names.** End users may have completely different agent configurations. All fallbacks use `DEFAULT_AGENT` (configurable, defaults to None).

5. **Coordinator bypasses SQLite detection.** The coordinator session has no TUI user. Its model and agent are resolved from `opencode.json` at startup and stored in `config.COORDINATOR_MODEL` / `config.COORDINATOR_AGENT`.

6. **Explicit `"model"` takes priority over `"agent"` lookup in `opencode.json`.** The `"agent"` field is for labeling; the `"model"` field specifies the exact provider/model to use.

These invariants are enforced by 13 tests in `test_model_routing.py`.
