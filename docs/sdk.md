# SDK

The EDAC SDK provides decorators and helpers for quickly building agents and workflows.

## Decorators

### `@agent`

Register a function as an agent with metadata.

```python
from edac.sdk import agent

@agent(
    name="coder",
    model="claude-sonnet",
    skills=["python", "sql"],
    sandbox=True,
    max_iterations=5,
)
async def coder(event):
    goal = event.payload.get("goal", "")
    # ... do work ...
    return {
        "code": "...",
        "status": "ok",
    }
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | `str` | required | Agent identifier |
| `model` | `str` | `None` | LLM model slug |
| `skills` | `List[str]` | `[]` | Loaded skill documents |
| `sandbox` | `bool` | `False` | Run in sandbox mode |
| `max_iterations` | `int` | `10` | Max ReAct tool loops |

### `@skill`

Load a skill document (markdown) and attach it to an agent.

```python
from edac.sdk import skill

@skill("skills/python.md")
async def python_expert(event):
    # The markdown content is injected into the agent's system prompt
    ...
```

### `@workflow`

Define a multi-agent pipeline as a decorator.

```python
from edac.sdk import workflow

@workflow([
    {"agent": "planner", "task": "plan"},
    {"agent": "coder", "task": "code"},
    {"agent": "reviewer", "task": "review"},
])
async def dev_pipeline(event):
    # Orchestrator spawns agents and runs the plan
    pass
```

## Agent Builder

```python
from edac.sdk import AgentBuilder

builder = AgentBuilder()
builder.with_name("researcher")
builder.with_model("claude-sonnet")
builder.with_skill("skills/research.md")
builder.with_tool("web_search")
builder.with_memory("short-term")

agent = builder.build()
```

## Workflow Runner

```python
from edac.sdk import WorkflowRunner
from edac.swarm import Swarm

runner = WorkflowRunner(swarm)
result = await runner.run([
    {"agent": "planner", "task": "Plan API"},
    {"agent": "coder", "task": "Implement API"},
    {"agent": "tester", "task": "Write tests"},
])
```

## Event Helpers

```python
from edac import create_event, EventType

event = create_event(
    EventType.AGENT_SPAWN,
    source="agent:planner",
    topic="task.plan",
    payload={"goal": "Build API"},
)
```

## Context Helpers

```python
from edac.context.manager import ContextManager, ContextConfig

ctx = ContextManager(config=ContextConfig(
    default_provider="ollama",
    default_model="llama3",
    max_tokens=4096,
))

response = await ctx.chat(
    agent_id="agent-1",
    prompt="Hello",
    provider="openai",
    model="gpt-4o",
)
```
