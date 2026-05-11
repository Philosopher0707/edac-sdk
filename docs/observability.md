# Observability

EDAC provides built-in tracing, metrics, and debugging tools for production monitoring.

## Tracing

The `Tracer` class creates OpenTelemetry-compatible spans.

```python
from edac.observability.tracing import Tracer

tracer = Tracer()

# Sync context manager
with tracer.span("operation") as span:
    span.set_attribute("key", "value")
    span.add_event("checkpoint", {"detail": "done"})

# Async context manager
async with tracer.async_span("async_operation") as span:
    span.set_attribute("agent", "planner")
    result = await do_work()
    span.set_attribute("success", result.success)
```

### Span Export

```python
exported = tracer.export()
for span in exported:
    print(span["name"], span["duration_ms"], span["attributes"])
```

### Automatic Spans

When a `Tracer` is passed to `Swarm` or `AgentExecutor`, spans are created automatically:

| Component | Span Name | Attributes |
|-----------|-----------|------------|
| `Swarm.execute()` | `swarm.execute:{pattern}` | `goal`, `pattern`, `agent_count`, `success` |
| `Swarm._execute_pipeline()` | `swarm.pipeline` | `step_count` |
| `Swarm._execute_mesh()` | `swarm.mesh` | `agent_count` |
| `Swarm._execute_orchestrator_workers()` | `swarm.orchestrator` | `subtask_count` |
| `Swarm._invoke_agent()` | `agent.invoke:{name}` | `agent_name`, `agent_id`, `context_keys`, `status` |
| `AgentExecutor.execute()` | `executor.execute` | `goal`, `pattern`, `agent_count`, `success` |
| `AgentExecutor._call_llm()` | `llm.call:{name}` | `agent_name`, `agent_id`, `provider`, `model`, `prompt_length`, `response_length` |

## Metrics

Prometheus-compatible metrics are exposed at `/metrics`:

| Metric | Type | Description |
|--------|------|-------------|
| `edac_requests_total` | Counter | Total HTTP requests |
| `edac_request_duration_seconds` | Histogram | Request latency |
| `edac_events_emitted_total` | Counter | Total events emitted |
| `edac_agents_spawned_total` | Counter | Total agents spawned |
| `edac_tasks_submitted_total` | Counter | Total tasks submitted |
| `edac_memory_operations_total` | Counter | Memory read/write ops |

## Dead-Letter Queue (DLQ)

Failed events are captured in the DLQ and exposed at `/dlq`:

```bash
curl http://localhost:8000/dlq
```

## Logging

EDAC uses `structlog` for structured logging. Configure via environment:

```bash
export EDAC_LOG_LEVEL=DEBUG
```

Loggers:

- `edac.event.bus` — event routing
- `edac.agent.runtime` — agent lifecycle
- `edac.swarm` — swarm execution
- `edac.server.executor` — task execution
- `edac.observability.tracing` — span lifecycle

## Trajectory Replay

Store and replay agent decision trajectories for debugging:

```python
from edac.observability.trajectory import TrajectoryStore

store = TrajectoryStore()
store.record(agent_id, event, action, result)
trajectory = store.replay(agent_id)
```
