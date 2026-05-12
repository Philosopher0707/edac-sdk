# Changelog

## v0.5.0 — 2026-05-13

### Added
- **Persistent chat memory (SQLite)** — `SqliteChatStore` replaces the in-memory `ChatStore`. Chat sessions and messages are stored in SQLite (`data/chat.db`), surviving server restarts. `edac chat --session-id abc123` now resumes past conversations.
- **Real tool calling in chat** — the WebSocket chat handler and REST `/send` endpoint now detect `{"tool": "...", "arguments": {...}}` patterns in LLM responses. Tools are executed via `ToolRegistry`, results fed back into context, and the model re-streams. Max 3 tool calls per turn with ReAct-style loop. Client receives `tool_call` events.
- **Green CI** — all 528 tests pass on GitHub CI. `test_chat.py` fixed (7 tests). Ruff formatting applied project-wide.
- **PyPI publish workflow** — new CI job triggers on tag push (`v*`), builds wheel + sdist, publishes to PyPI via trusted publishing.

### Changed
- `EdacClientSync` now handles `EdacClientError` with request correlation IDs in batch operations.
- `ChatStore` base class has `close()` method (no-op for in-memory version).
- `list_sessions()` returns raw lists (not paginated dicts) for REST consistency.

### Fixed
- **Chat `"no close frame"` WebSocket crash** — `_safe_send_json`/`_safe_close` helpers prevent connection drops on errors.
- **Chat test suite** — 7 broken tests repaired (status codes, URLs, constructor signatures).
- **CI configuration** — removed `continue-on-error` from lint, re-enabled chat tests, excluded benchmarks only.

---

## v0.4.0 — 2026-05-13

### Added
- **User-extensible agentic platform** — three pillars:
  - Pillar 1 — HandlerRegistry wired into AgentExecutor
  - Pillar 2 — Chat tools (health, list_agents, submit_task, get_version, help)
  - Pillar 3 — Auto-loaded user agents from `agents/` directory
- `GET /agents/templates` REST endpoint with `AgentTemplate` schema.
- Starter example agent (`agents/example_coder.py`).
- Comprehensive EDAC knowledge injected into every chat session (9KB system prompt).

### Changed
- Default model: `llama3.2` → `kimi-k2.6:cloud`.

### Fixed
- `"no close frame"` WebSocket crash — safe send/close helpers, content truncation.
- Missing `ACTION_CHAT_*` auth constants.

---

## v0.3.2 — 2026-05-12

### Added
- Richer exceptions with `request_id` correlation.
- Webhook callbacks + REST endpoints + CLI.
- MkDocs with auto-generated API docs + GitHub Pages workflow.

---

## v0.3.1 — 2026-05-11

### Added
- `wait_for_task()`, `__repr__` on clients, DEBUG request logging, idempotent `close()`.

---

## v0.3.0 — 2026-05-11

### Added
- Sync client, streaming (SSE + WebSocket), retry/backoff, pagination, batch ops, full CLI.

### Changed
- **Breaking** — `list_agents()` / `list_tasks()` return `PaginatedList[T]`.

### Fixed
- Auth: missing key → 401, wrong role → 403.

---

## v0.2.0 — 2026-04-28

- Typed async HTTP client, server CRUD, registry decorator system, A2A/MCP bridges, 422 tests.

---

## v0.1.0 — 2026-04-14

- Initial release — EventBus, core event schema, agent lifecycle, plan DAG.
