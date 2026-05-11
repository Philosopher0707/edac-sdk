# EDAC — Event-Driven Agentic Core

A lightweight, event-driven SDK for building agentic systems in Python.

## Overview

EDAC provides a modular 12-layer architecture for building production-grade multi-agent systems. Every layer communicates through a unified async event bus, making it easy to compose agents, tools, memory, and protocols.

## Install

```bash
pip install edac
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

## Interactive API Docs

When running the server, interactive OpenAPI docs are available at:

- **Swagger UI** → `/docs`
- **ReDoc** → `/redoc`

```bash
edac server start
```

## What's Next

- [Getting Started](getting-started.md) — full setup, server config, and first workflow
- [Architecture](architecture.md) — deep dive into each layer
- [SDK](sdk.md) — decorators, workflows, and helpers
- [API Reference](api.md) — HTTP/WebSocket endpoints
- [Observability](observability.md) — tracing, metrics, and debugging
