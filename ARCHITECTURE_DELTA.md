# Architecture Delta — v0.2.0

## Summary

This document records the key architectural decisions and changes introduced in v0.2.0, contrasting them with the pre-v0.2.0 baseline.

---

## 1. HandlerRegistry (Sync, Import-Safe)

| Before | After |
|--------|-------|
| `@agent`, `@on_event` decorators were stubs or unimplemented | Decorators register factory functions into a global `HandlerRegistry` at import time |
| No way to discover agent/event handlers without manual wiring | Registry auto-discovers via `RegistryAwareSpawner` at runtime |

**Why sync?**  
Decorators execute at Python import time (`def` → decorator call). The registry itself therefore cannot have `async` methods because `await` is illegal at module-load time. All registration is synchronous in-memory mutation.

**File:** `edac/decorators.py`, `edac/registry.py`

---

## 2. Test Infrastructure: Manual ASGI Lifespan

| Before | After |
|--------|-------|
| `TestClient` drove lifespan automatically | `httpx.AsyncClient` + `ASGITransport` requires manual lifespan driving |
| pytest-asyncio managed loop (conflicted with background tasks) | Each sync test creates an isolated `asyncio.new_event_loop()` |

**Root cause of hang:**  
The original `LifespanContext` helper deadlocked because `recv()` and `send()` waited on each other via futures. The fixed implementation uses `asyncio.Event` objects in a strict sequence: send `lifespan.startup` → wait for `lifespan.startup.complete` → run test → send `lifespan.shutdown` → wait for `lifespan.shutdown.complete`.

**Key pattern (used in `test_client.py`):**

```python
def _run_with_lifespan(app, coro, *, setup=None, timeout=15.0):
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        ...
    finally:
        loop.close()
```

**File:** `tests/test_client.py`

---

## 3. Auth Middleware Conditional Installation

| Before | After |
|--------|-------|
| Auth middleware always present (broken when no API key configured) | Middleware only installed when `cfg.api_key` is truthy |

**Consequence for tests:**  When testing auth (401/403), tests must pass `api_key="dummy"` at app creation to force middleware installation, then register the desired keys in a `setup` hook after lifespan startup (when `app.state.auth` exists).

**File:** `edac/server/api.py` (lines 296–312), `tests/test_client.py`

---

## 4. Client SDK (`EdacClient`)

| Before | After |
|--------|-------|
| No HTTP client; server tested only with `TestClient` | Full async `EdacClient` with typed exception hierarchy |

**Exception hierarchy:**

```
EdacError
├── EdacAPIError (any non-2xx)
│   ├── EdacAuthError (401/403)
│   └── EdacNotFoundError (404)
```

**Files:** `edac/client/client.py`, `edac/client/exceptions.py`

---

## 5. Agents Router (CRUD + Lifecycle)

| Before | After |
|--------|-------|
| No dedicated endpoints for agents | Full REST router: `POST`, `GET`, `DELETE`, `POST /{id}/restart`, `POST /{id}/pause`, `POST /{id}/resume` |
| No RBAC on agent endpoints | Every endpoint guards with `app.state.auth.is_allowed(...)` and emits audit log via `log_audit` |

**Files:** `edac/server/routers/agents_router.py`, `edac/server/schemas.py`

---

## File Inventory

| File(s) | Role | New or Modified |
|---------|------|-----------------|
| `edac/client/client.py` | Async HTTP SDK | New |
| `edac/client/exceptions.py` | Typed exceptions | New |
| `edac/server/routers/agents_router.py` | Agent CRUD REST API | New |
| `edac/server/schemas.py` | `CreateAgentRequest`, `AgentInfo` | Modified |
| `edac/server/api.py` | App factory + conditional auth middleware | Modified |
| `edac/server/config.py` | `ServerConfig` with `api_key` field | Modified |
| `tests/test_client.py` | 17 end-to-end SDK tests | New |
| `tests/conftest.py` | Shared fixtures | Modified |

---

## Backward Compatibility

- `AgentRuntime()` without a custom spawner continues to use `RegistryAwareSpawner`
- `set_agent_factory()` still works as a fallback override
- All 23 original `test_server.py` tests pass unchanged
- All 11 `test_handler_registry.py` tests pass
- All 2 `test_decorator_integration.py` tests pass
