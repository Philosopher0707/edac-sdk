# EDAC — Event-Driven Agentic Core

A lightweight, event-driven SDK and server for building agentic systems in Python.

## Install

```bash
pip install edac
```

For streaming support (WebSocket task watching):

```bash
pip install edac[stream]
```

Requires Python 3.11+.

## Quickstart (Async)

```python
import asyncio
from edac import EventBus, EventType, create_event
from edac.sdk import agent

@agent(name="greeter", model="claude-sonnet")
async def greeter(event):
    name = event.payload.get("name", "world")
    return create_event(
        EventType.MODALITY_TEXT,
        source="agent:greeter",
        topic="agent.greeter.results",
        payload={"message": f"Hello, {name}!"},
    )

async def main():
    async with EventBus() as bus:
        bus.subscribe(greeter, topics=["agent.greeter.requests"])
        req = create_event(
            EventType.AGENT_SPAWN,
            source="human:dev",
            topic="agent.greeter.requests",
            payload={"name": "EDAC"},
        )
        await bus.emit(req)
        await asyncio.sleep(0.1)

asyncio.run(main())
```

## Sync Client

```python
from edac.client.sync_client import EdacClientSync

client = EdacClientSync("http://localhost:8000", api_key="sk-xxx")

# All async methods exposed synchronously via a background thread
agents = client.list_agents()  # returns PaginatedList[Agent]
task = client.submit_task(goal="write a haiku")
client.close()
```

## Streaming

### SSE Events
```python
async for event in client.stream_events(topics=["agent.results"]):
    print(event)
```

### WebSocket Task Watching (requires `edac[stream]`)
```python
# Async
async for update in client.watch_task("task-123"):
    print(update)

# Sync
for update in sync_client.watch_task("task-123"):
    print(update)
```

## CLI

```bash
# Server
edac server start

# Submit a task
edac tasks submit --goal "write a haiku"

# Submit and watch via WebSocket
edac run --watch --goal "write a haiku"

# Follow SSE event stream
edac events follow --topics agent.results --count 10

# Watch a specific task
edac events watch <task_id>

# List agents/tasks
edac agents list
edac tasks list

# Batch submit
edac tasks batch --goals "goal1" "goal2"

# Status
edac status
```

## Pagination & Batch Operations

List endpoints return `PaginatedList[T]`:

```python
page = client.list_tasks(limit=10, offset=0)
print(page.items)   # list of tasks
print(page.total)   # total count
```

Batch submit tasks:

```python
tasks = client.submit_tasks_batch([
    {"goal": "task 1"},
    {"goal": "task 2"},
])
```

## Retry & Error Handling

The client automatically retries on 429, 500, 502, 503, 504 with exponential backoff.

```python
from edac.client.client import EdacClient, RetryConfig

client = EdacClient(
    "http://localhost:8000",
    retry=RetryConfig(max_retries=5, backoff_base=1.0, backoff_max=30.0)
)
```

## Architecture

| Layer | Module | Purpose |
|-------|--------|---------|
| Event | `edac.event` | Async bus, schema, router, priority queues |
| Agent | `edac.agent` | Runtime, registry, lifecycle, spawner, supervisor |
| Plan | `edac.plan` | Mutable DAG, topological ordering, parallel execution |
| Tool | `edac.tool` | Registry, MCP client, skill loader, sandbox |
| Memory | `edac.memory` | Working, short-term, long-term, episodic |
| Context | `edac.context` | Token budget, compression, model routing |
| Human | `edac.human` | HITL state machine, approval gates, SSE stream |
| Modality | `edac.modality` | Text, code, image, audio, video, artifact adapters |
| Observability | `edac.observability` | Tracing, metrics, trajectory replay |
| Protocol | `edac.protocol` | A2A bridge, MCP bridge, SSE endpoint |
| SDK | `edac.sdk` | Agent builder, workflow runner, decorators |
| Security | `edac.security` | Guardrails, network proxy, sandbox, secrets |

## Interactive API Docs

When running the server:

```bash
edac server start
```

Interactive OpenAPI docs are available at:

- **Swagger UI** → `http://localhost:8000/docs`
- **ReDoc** → `http://localhost:8000/redoc`

## Tests

```bash
python -m pytest tests/ -v
```

490 tests across 20+ test files.

## License

MIT
