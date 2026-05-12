# EDAC — Event-Driven Agentic Core

<p align="center">
  <strong>A lightweight, event-driven SDK and server for building agentic systems in Python.</strong>
</p>

<p align="center">
  <a href="https://github.com/Philosopher0707/edac-sdk/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT" /></a>
  <a href="https://pypi.org/project/edac/"><img src="https://img.shields.io/badge/pypi-v0.3.2-blue" alt="PyPI: 0.3.2" /></a>
  <img src="https://img.shields.io/badge/tests-514%20passed-green" alt="Tests: 514 passed" />
  <img src="https://img.shields.io/badge/python-3.11%2B-purple" alt="Python: 3.11+" />
  <img src="https://img.shields.io/badge/fastapi-0.110%2B-orange" alt="FastAPI: 0.110+" />
</p>

<p align="center">
  <a href="https://edac-team.github.io/edac">📖 Documentation</a> &nbsp;|&nbsp;
  <a href="#quickstart">🚀 Quickstart</a> &nbsp;|&nbsp;
  <a href="#cli-reference">🛠️ CLI</a> &nbsp;|&nbsp;
  <a href="#architecture">🏗️ Architecture</a>
</p>

---

## What is EDAC?

EDAC is a **complete production-grade stack** for building event-driven agentic systems — from a single `@agent` decorator to a full multi-agent server with human-in-the-loop (HITL) approvals, streaming, webhooks, and observability.

Unlike frameworks that only orchestrate LLM calls, EDAC treats **events as first-class citizens**: every action, result, approval, and failure flows through a typed `EventBus`, giving you causality tracking, replay, and full audit trails out of the box.

## Features

| Feature | Status | Notes |
|---------|--------|-------|
| **Async + Sync HTTP Client** | ✅ Ready | `EdacClient` and `EdacClientSync` with identical APIs |
| **Streaming (SSE + WebSocket)** | ✅ Ready | `stream_events()`, `watch_task()`, `pip install edac[stream]` |
| **Retry / Exponential Backoff** | ✅ Ready | `RetryConfig` with automatic retry on 429, 500, 502, 503, 504 |
| **Pagination** | ✅ Ready | `PaginatedList[T]` with header-based metadata |
| **Batch Operations** | ✅ Ready | Submit/create/delete many items in one round-trip |
| **Webhooks** | ✅ v0.3.2 | Fire-and-forget callbacks when tasks complete |
| **Error Correlation** | ✅ v0.3.2 | Every exception carries `request_id` from server logs |
| **Rate Limiting** | ✅ Ready | Token-bucket per API key or IP |
| **HITL Approvals** | ✅ Ready | Pause task execution pending human approval |
| **Auth (RBAC)** | ✅ Ready | Admin / Operator / Viewer roles |
| **OpenAPI / Swagger** | ✅ Ready | Auto-generated at `/docs` and `/redoc` |
| **Observability** | ✅ Ready | Metrics, tracing spans, structured JSON logs |

---

## Install

```bash
pip install edac
```

For WebSocket streaming support:

```bash
pip install edac[stream]
```

For AI model providers:

```bash
pip install edac[anthropic]   # Claude
pip install edac[openai]      # GPT-4 / GPT-3.5
pip install edac[local]       # Transformers + PyTorch for local models
```

All extras at once:

```bash
pip install edac[all]
```

Requires **Python 3.11+**.

---

## Quickstart

### 1. Start the Server

```bash
edac serve --host 0.0.0.0 --port 8000
```

Open http://localhost:8000/docs for interactive API documentation.

### 2. Submit a Task (Async)

```python
import asyncio
from edac.client.client import EdacClient
from edac.server.schemas import SubmitTaskRequest

async def main():
    async with EdacClient("http://localhost:8000") as client:
        task = await client.submit_task(
            SubmitTaskRequest(
                goal="Write a haiku about Python events",
                pattern="pipeline",
                agents=[
                    {"name": "planner", "role": "orchestrator"},
                    {"name": "writer", "role": "worker"},
                ],
            )
        )
        print(f"Task {task.id} — status: {task.status}")

        # Wait until terminal without polling boilerplate
        result = await client.wait_for_task(task.id, timeout=60.0)
        print(f"Final: {result.status}")
        if result.result:
            print(result.result)

asyncio.run(main())
```

### 3. Synchronous Client (Jupyter, Scripts, Notebooks)

```python
from edac.client.sync_client import EdacClientSync
from edac.server.schemas import SubmitTaskRequest

client = EdacClientSync("http://localhost:8000", api_key="sk-xxx")

task = client.submit_task(
    SubmitTaskRequest(goal="Analyze this dataset", pattern="pipeline")
)
print(client.wait_for_task(task.id))

client.close()         # Idempotent — safe to call twice
```

### 4. Batch Operations

```python
# Submit 10 tasks in a single round-trip
requests = [SubmitTaskRequest(goal=f"Task {i}") for i in range(10)]
results = client.submit_tasks_batch(requests)
for item in results:
    if hasattr(item, "id"):
        print(f"Created: {item.id}")
    else:
        print(f"Failed: {item.error}")
```

### 5. Streaming Events

```python
# Server-Sent Events (SSE)
async for event in client.stream_events(topics=["agent.results"], timeout=30):
    print(event)

# WebSocket task watching (requires edac[stream])
async for update in client.watch_task("task-123", timeout=30):
    print(f"Status: {update.status}")
```

### 6. Watch from CLI

```bash
# Submit and watch via WebSocket
edac run --watch --goal "Research quantum computing"

# Follow all SSE events in real time
edac events follow --topics agent.results --count 50

# Watch a specific task
edac events watch task-abc123
```

---

## Async Client API

Every method is `async` and context-manager friendly:

```python
async with EdacClient("http://localhost:8000", api_key="sk-xxx") as client:
    # Agents
    agents = await client.list_agents(limit=10)
    agent = await client.create_agent(CreateAgentRequest(name="worker"))
    await client.delete_agent("agent-id")

    # Tasks
    task = await client.submit_task(SubmitTaskRequest(goal="..."))
    tasks = await client.list_tasks(status="running")
    await client.cancel_task("task-id")

    # Wait for terminal status
    result = await client.wait_for_task("task-id", poll_interval=1.0, timeout=60.0)

    # Streaming
    async for event in client.stream_events(topics=["agent.results"]):
        ...
    async for update in client.watch_task("task-id"):
        ...

    # System
    health = await client.get_health()
```

**Constructor options:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `base_url` | required | Server URL |
| `api_key` | `None` | Authentication key |
| `timeout` | `30.0` | Request timeout (seconds) |
| `limits` | `httpx.Limits(...)` | Connection pool tuning |
| `retry` | `RetryConfig()` | Retry/backoff strategy |
| `http2` | `False` | Enable HTTP/2 |

---

## Sync Client API

`EdacClientSync` wraps the async client with a background event-loop thread. Every method has the **exact same signature** as the async version:

```python
with EdacClientSync("http://localhost:8000") as client:
    agents = client.list_agents()
    task = client.submit_task(...)
    result = client.wait_for_task("task-id")
```

Streaming works via queue bridging:

```python
for event in client.stream_events(topics=["agent.results"]):
    print(event)  # Blocking sync iterator

for update in client.watch_task("task-id"):
    print(update.status)
```

---

## Error Handling

All errors are typed and carry correlation IDs:

```python
from edac.client.exceptions import (
    EdacClientError,     # Base — all errors inherit this
    EdacAPIError,        # 4xx/5xx from server
    EdacAuthError,       # 401/403
    EdacNotFoundError,   # 404
    EdacRetryExhausted,  # All retries failed
    EdacStreamError,     # SSE/WebSocket failure
)

try:
    task = await client.get_task("nonexistent")
except EdacNotFoundError as e:
    print(f"Not found: {e.message}")       # "Task not found"
    print(f"Request ID: {e.request_id}")   # "abc123de" — for server log lookup
    print(f"Status: {e.status_code}")      # 404
```

**Request ID correlation:** The server generates an `X-Request-ID` for every request and includes it in error responses. The client extracts this into `exception.request_id`, making production debugging seamless.

---

## Retry Configuration

Automatic retry with exponential backoff on transient errors:

```python
from edac.client.retry import RetryConfig

client = EdacClient(
    "http://localhost:8000",
    retry=RetryConfig(
        max_retries=5,        # Default: 3
        backoff_base=0.5,       # Default: 1.0s
        backoff_max=30.0,      # Default: 60.0s
        retry_statuses=(429, 500, 502, 503, 504),
    )
)
```

**Never retried:** `401` (auth), `404` (not found), `ValueError`, `TypeError`.

---

## Webhook Callbacks

Receive a POST callback when a task completes — no polling required.

### Register a webhook

```bash
# CLI
curl -X POST http://localhost:8000/tasks/{task_id}/webhooks \
  -H "Content-Type: application/json" \
  -d '{"url": "https://myapp.com/webhooks/edac", "events": ["task.completed"]}'

# Or via CLI
edac webhooks register task-abc123 \
  --url https://myapp.com/webhooks/edac \
  --events task.completed,task.failed
```

### Delivery payload

```json
{
  "task_id": "task-abc123",
  "status": "completed",
  "result": {"output": "..."},
  "timestamp": 1746945623.5
}
```

- Retries: 3× with exponential backoff on 5xx/network errors
- Fire-and-forget: delivery runs in background, never blocks task completion
- Per-task: webhooks are scoped to individual tasks

---

## CLI Reference

The `edac` CLI provides server management, CRUD operations, streaming, and webhook administration:

```bash
# Server
edac serve --host 0.0.0.0 --port 8000 --workers 4

# Tasks
edac tasks submit --goal "Write code" --watch
edac tasks batch --goals "goal1" "goal2" "goal3"
edac tasks list --limit 50
edac tasks get task-abc123
edac tasks cancel task-abc123

# Agents
edac agents list

# Events (streaming)
edac events follow --topics agent.results --count 10
edac events watch task-abc123

# Webhooks
edac webhooks register task-abc123 --url https://example.com/hook
edac webhooks list task-abc123
edac webhooks delete task-abc123 --url https://example.com/hook

# System
edac status        # Health check
edac --version
```

Global options: `--config /path/to.json`, `--verbose`, `--version`.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Client Layer                           │
│  EdacClient (async)  │  EdacClientSync (sync)               │
│  - Retry / Backoff     - Same API, blocking thread           │
│  - Pagination          - Stream bridging                    │
│  - Batch ops           - Context manager                    │
└──────────────────────┬──────────────────────────────────────┘
                       │ HTTP / WebSocket
┌──────────────────────▼──────────────────────────────────────┐
│                    FastAPI Server                           │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐        │
│  │  Tasks  │  │ Agents  │  │ Events  │  │ Webhooks│        │
│  └─────────┘  └─────────┘  └─────────┘  └─────────┘        │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐        │
│  │  A2A    │  │  MCP    │  │Approval │  │ Memory  │        │
│  └─────────┘  └─────────┘  └─────────┘  └─────────┘        │
├──────────────────────┬──────────────────────────────────────┤
│         Middleware: Auth → Rate Limit → Tracing → CORS      │
├──────────────────────┼──────────────────────────────────────┤
│  EventBus  │  AgentRuntime  │  PlanEngine  │  ContextManager  │
│  (pub/sub) │  (lifecycle)   │  (DAG exec)  │  (budgets)       │
├──────────────────────┴──────────────────────────────────────┤
│  ModelRegistry: Ollama | Anthropic | OpenAI | Local        │
│  Observability: Metrics │ Spans │ Audit Log │ JSON Logs      │
└─────────────────────────────────────────────────────────────┘
```

| Layer | Module | Purpose |
|-------|--------|---------|
| **Event** | `edac.event` | Async bus, schema, router, priority queues |
| **Agent** | `edac.agent` | Runtime, registry, lifecycle, spawner, supervisor |
| **Plan** | `edac.plan` | Mutable DAG, topological ordering, parallel execution |
| **Tool** | `edac.tool` | Registry, MCP client, skill loader, sandbox |
| **Memory** | `edac.memory` | Working, short-term, long-term, episodic |
| **Context** | `edac.context` | Token budget, compression, model routing |
| **Human** | `edac.human` | HITL state machine, approval gates, SSE stream |
| **Modality** | `edac.modality` | Text, code, image, audio, video, artifact adapters |
| **Observability** | `edac.observability` | Tracing, metrics, trajectory replay, structured logging |
| **Protocol** | `edac.protocol` | A2A bridge, MCP bridge, SSE endpoint |
| **SDK** | `edac.sdk` | Agent builder, workflow runner, decorators |
| **Security** | `edac.security` | Guardrails, network proxy, sandbox, secrets |

---

## Docker

```bash
# Build
docker build -t edac:latest .

# Run
docker run -p 8000:8000 \
  -e EDAC_API_KEY=sk-xxx \
  -e EDAC_OLLAMA_BASE_URL=http://host.docker.internal:11434 \
  edac:latest
```

---

## Documentation

Auto-generated API documentation is built with MkDocs + mkdocstrings:

```bash
# Serve locally
mkdocs serve

# Build
mkdocs build --strict
```

Hosted at: https://edac-team.github.io/edac

---

## Tests

```bash
# Full suite (506 tests, excluding benchmarks)
python -m pytest tests/ -v

# Specific modules
python -m pytest tests/test_client_extended.py -v
python -m pytest tests/test_webhook.py -v

# With coverage
python -m pytest tests/ --cov=edac --cov-report=html
```

**Status:** 506 passed, 6 skipped, 0 failures.

---

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feat/amazing-feature`)
3. Run tests and lint: `pytest && ruff check .`
4. Commit with [Conventional Commits](https://www.conventionalcommits.org/) format
5. Push and open a Pull Request

Development dependencies:

```bash
pip install -e ".[dev]"
```

---

## License

[MIT](https://github.com/Philosopher0707/edac-sdk/blob/main/LICENSE)

---

<p align="center">
  Built with ❤️ by the EDAC Team
</p>
