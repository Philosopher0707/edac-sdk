# Getting Started

## Installation

### From PyPI (recommended)

```bash
pip install edac
```

### With optional providers

```bash
# Anthropic models
pip install "edac[anthropic]"

# OpenAI models
pip install "edac[openai]"

# Local / HuggingFace
pip install "edac[local]"

# MCP protocol support
pip install "edac[mcp]"
```

### Development install

```bash
git clone https://github.com/edac-team/edac.git
cd edac
pip install -e ".[dev]"
```

## Running the Server

```bash
edac server start
```

Or programmatically:

```python
from edac.server.api import create_app
import uvicorn

app = create_app()
uvicorn.run(app, host="0.0.0.0", port=8000)
```

The server exposes interactive API docs at `/docs` and `/redoc`.

## First Multi-Agent Workflow

```python
import asyncio
from edac import EventBus
from edac.sdk import workflow, agent

@agent(name="planner", model="claude-sonnet")
async def planner(event):
    return {"plan": ["step1", "step2"], "status": "ok"}

@agent(name="coder", model="claude-sonnet", skills=["python"])
async def coder(event):
    plan = event.payload.get("plan", [])
    return {"code": f"# generated from {plan}", "status": "ok"}

@workflow([
    {"agent": "planner", "task": "plan"},
    {"agent": "coder", "task": "code"},
])
async def dev_pipeline(event):
    pass

async def main():
    async with EventBus() as bus:
        bus.subscribe(planner, topics=["task.plan"])
        bus.subscribe(coder, topics=["task.code"])
        bus.subscribe(dev_pipeline, topics=["task.pipeline"])

        await bus.emit(await dev_pipeline({"goal": "Build API"}))
        await asyncio.sleep(1)

asyncio.run(main())
```

## Configuration

EDAC reads config from environment variables (via Pydantic Settings):

| Variable | Default | Description |
|----------|---------|-------------|
| `EDAC_DEFAULT_PROVIDER` | `ollama` | Default LLM provider |
| `EDAC_LOG_LEVEL` | `INFO` | Logging level |
| `EDAC_API_KEY` | — | Optional bearer token for server auth |

## Next Steps

- Read the [Architecture overview](architecture.md)
- Browse the [API Reference](api.md)
- Explore [Observability](observability.md) for production monitoring
