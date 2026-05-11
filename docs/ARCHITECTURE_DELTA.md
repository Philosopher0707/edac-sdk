# EDAC Client SDK — Architecture Addition

## Overview
P1–P6 introduced a **typed async client SDK** for the EDAC server, alongside a test suite that integrates client+server end-to-end.

## Key Files
| Layer | Path | Role |
|-------|------|------|
| Client SDK | `edac/client/client.py` | `EdacClient` — async HTTP wrapper |
| Client errors | `edac/client/exceptions.py` | `EdacNotFoundError`, `EdacAuthError`, etc. |
| Client tests | `tests/test_client.py` | 17 e2e tests via manual ASGI lifespan |
| Auth / RBAC | `edac/server/auth.py` | `AuthManager`, `Role`, action constants |
| Agents router | `edac/server/routers/agents_router.py` | CRUD + lifecycle endpoints |
| Server schemas | `edac/server/schemas.py` | `CreateAgentRequest`, `AgentInfo`, `HealthResponse` |

## Design Decisions
1. **Decorators stay sync** — `@agent` and `@handler` run at import time. `HandlerRegistry` is therefore a synchronous singleton.
2. **ASGI lifespan driven manually in tests** — `httpx.ASGITransport` does not run lifespan events. Each sync test method creates an isolated event loop, drives `lifespan.startup`, executes `coro()`, then drives `lifespan.shutdown`. This avoids:
   - pytest-asyncio loop conflicts
   - Background tasks (`EventBus._dispatcher_loop`, etc.) leaking between tests
3. **Auth middleware is conditional** — installed only when `cfg.api_key` is truthy. Tests that exercise auth must pass a dummy key to force middleware installation.

## Test Structure
- **Health** (2 tests) — raw httpx, no auth required
- **Auth** (3 tests) — 401 missing key, 403 insufficient role, 200 valid key  
- **CRUD** (7 tests) — list, create, get, delete, pause/resume, restart, 404
- **SDK** (5 tests) — `EdacClient` method wrappers + exception mapping  
**Total: 17 passed** (isolated event loops, no pytest-asyncio)

## Full Suite Status
454 passed, 4 skipped across all test files.

## Client SDK Design (`edac/client/`)

**Error hierarchy** — `EdacError → EdacAPIError → (EdacAuthError | EdacNotFoundError)`.  Every SDK method calls `_handle_error()` which maps status codes to typed exceptions.

**Import-safe by default** — `EdacClient` has no top-level `await`.  It wraps `httpx.AsyncClient` and is fully async inside its methods.

---

## Registry Design (`edac/agent/registry.py`)

`HandlerRegistry` is **synchronous and import-time safe**.  Decorators (`@edac.task`, `@edac.webhook`) register factory lambdas at import time; `RegistryAwareSpawner` discovers them at runtime without touching any async context.

This decouples **registration** (import) from **resolution** (runtime) — avoiding the classic "decorator deadlock" where import-time code needs an event loop.

---

## Router RBAC (`edac/server/routers/agents_router.py`)

Every endpoint now follows the same guard pattern:

```python
user = request.state.user
if user and not app.state.auth.is_allowed(user, ACTION_...):
    log_audit(..., outcome="denied")
    raise HTTPException(403)
log_audit(..., outcome="success")
```

`AuthManager` is created in `api.py` and mounted on `app.state.auth`.  The middleware is only installed when `ServerConfig.api_key` is set.

---

## Schemas (`edac/server/schemas.py`)

- `AgentInfo` has `agent_id: str` (forward-compatible).
- `CreateAgentRequest` is the create contract.
- `HealthResponse` is the health contract.

All schemas use Pydantic v2 with `.model_validate()` and `.model_dump()`.

---

## Test Infrastructure (`tests/test_client.py`)

**The ASGI lifespan deadlock** — the original test helper tried to await `lifespan.startup.complete` inside a future that was set by `send()` after `recv()` already consumed the event.  The fix uses an `asyncio.Event()` for startup completion and a separate `Event` for shutdown, ensuring `recv()` / `send()` / `yield` flow in the correct order.

**Event loop isolation** — each test method creates `asyncio.new_event_loop()` + `loop.run_until_complete()` to avoid pytest-asyncio loop conflicts with `EventBus._dispatcher_loop`, `TaskWorker._worker_loop`, etc.

---

## Summary

| Deliverable | Tests | Status |
|---|---|---|
| `agents_router.py` CRUD + RBAC | `test_server.py` (23) | ✅ |
| `test_handler_registry.py` | 11 | ✅ |
| `test_decorator_integration.py` | 2 | ✅ |
| `test_client.py` | 17 | ✅ |
| **Total** | **454** | ✅ |
