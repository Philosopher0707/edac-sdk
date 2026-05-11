# EDAC — Event-Driven Agentic Core

A lightweight, event-driven SDK for building agentic systems in Python.

## Install

```bash
pip install -e .
```

Requires Python 3.11+.

## Quickstart

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
        # subscribe greeter to requests
        bus.subscribe(greeter, topics=["agent.greeter.requests"])

        # emit a request
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

## Decorators

```python
from edac.sdk import agent, skill, workflow

@agent(name="coder", model="claude-sonnet", skills=["python"], sandbox=True)
async def coder(event):
    ...

@skill("skills/python.md")
async def coder_with_skill(event):
    ...

@workflow([
    {"agent": "planner", "task": "plan"},
    {"agent": "coder", "task": "code"},
    {"agent": "reviewer", "task": "review"},
])
async def dev_pipeline(event):
    ...
```

## Secrets

```python
from edac.security.secrets import SecretsManager

mgr = SecretsManager()
mgr.set("api_key", "sk-xxx", scope="agent:prod", budget=1000)
mgr.get("api_key", scope="agent:prod")
mgr.rotate("api_key")
```

## A2A Protocol

```python
from edac.protocol.a2a import A2ABridge, A2ATask, A2AMessage

bridge = A2ABridge(registry)
card_json = bridge.generate_agent_card(card)
task = bridge.create_task("task-1", A2AMessage.from_text("user", "hello"))
```

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

422 tests across 20 test files.

## License

MIT
