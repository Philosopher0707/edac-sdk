# Changelog

## v0.3.1 — 2026-05-11

### Added
- **`wait_for_task()`** — Polls `get_task()` until terminal status (`completed`/`failed`/`cancelled`) with configurable `poll_interval` and `timeout`. Available on both async and sync clients.
- **`__repr__`** — Both `EdacClient` and `EdacClientSync` now show helpful debug output (base URL masked, API key hidden as `***`).
- **DEBUG request logging** — `EdacClient._request()` now logs `METHOD path -> status (duration_ms)` at DEBUG level for every successful request, useful for production observability.
- **Idempotent `close()`** — `EdacClient.close()` and `EdacClientSync.close()` are now safe to call multiple times.

### Changed
- `EdacClient.__aenter__` / `__aexit__` cleaned up for proper `async with` usage.

---

## v0.3.0 — 2026-05-11

### Added
- **Sync Client** — `EdacClientSync` wraps the async client with a background event-loop thread, exposing all methods synchronously.
- **Streaming Support** — SSE event streaming (`/events/stream`) and WebSocket task watching (`/tasks/{id}/ws`). Install with `pip install edac[stream]`.
- **Retry / Backoff** — Configurable `RetryConfig` with exponential backoff on 429, 500, 502, 503, 504 status codes (`tenacity>=8.0` dependency).
- **Connection Pooling** — Configurable `httpx.Limits` for connection pool tuning.
- **Pagination** — `PaginatedList[T]` responses from `list_agents()` and `list_tasks()` with header-based metadata (`X-Total-Count`, `X-Limit`, `X-Offset`).
- **Batch Operations** — `submit_tasks_batch()` on client + `POST /tasks/batch` on server with per-item error isolation.
- **CLI v0.3.0** — New commands:
  - `tasks submit/list/get/cancel/events/batch`
  - `agents list`
  - `events follow/watch`
  - `run --watch` (submit + wait for terminal status)
  - `status` (health check)

### Changed
- **Breaking** — `list_agents()` / `list_tasks()` now return `PaginatedList[T]` instead of raw `List[T]`.

### Fixed
- Auth middleware now returns **401** for missing / wrong API key; **403** reserved for authenticated-but-unauthorized users.

---

## v0.2.0 — 2026-04-28

- Typed async HTTP client with full CRUD for agents and tasks.
- Server-side handlers backed by `AgentBuilder` and `PlanEngine`.
- Registry decorator system (`@agent`, `@skill`, `@workflow`) with import-time registration.
- A2A protocol bridge, MCP client, memory router, approval gates, SSE endpoints.
- Comprehensive test suite (422 tests at release).

---

## v0.1.0 — 2026-04-14

- Initial release — EventBus, core event schema, agent lifecycle, and plan DAG.
