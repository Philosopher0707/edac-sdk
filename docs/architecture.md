# Architecture

EDAC is organized into 12 layers, each responsible for a distinct concern. All layers communicate via the async `EventBus`.

## Layer Map

| # | Layer | Module | Purpose |
|---|-------|--------|---------|
| 1 | **Event** | `edac.event` | Async bus, schema, routing, priority queues |
| 2 | **Agent** | `edac.agent` | Runtime, registry, lifecycle, spawner, supervisor |
| 3 | **Plan** | `edac.plan` | Mutable DAG, topological ordering, parallel execution |
| 4 | **Tool** | `edac.tool` | Registry, MCP client, skill loader, sandbox |
| 5 | **Memory** | `edac.memory` | Working, short-term, long-term, episodic |
| 6 | **Context** | `edac.context` | Token budget, compression, model routing |
| 7 | **Human** | `edac.human` | HITL state machine, approval gates, SSE stream |
| 8 | **Modality** | `edac.modality` | Text, code, image, audio, video, artifact adapters |
| 9 | **Observability** | `edac.observability` | Tracing, metrics, trajectory replay |
| 10 | **Protocol** | `edac.protocol` | A2A bridge, MCP bridge, SSE endpoint |
| 11 | **SDK** | `edac.sdk` | Agent builder, workflow runner, decorators |
| 12 | **Security** | `edac.security` | Guardrails, network proxy, sandbox, secrets |

## Data Flow

```
Human / External → EventBus → Agent Runtime → Plan Engine → Tool Registry
                                        ↓
                                    Memory / Context / Modality Dispatcher
                                        ↓
                                   LLM Provider (via ModelRegistry)
                                        ↓
                                    EventBus → Human / External
```

### EventBus

The `EventBus` is the central nervous system. Every layer publishes and subscribes to typed `Event` objects.

- **Topics** — dot-separated routing keys (`agent.coder.results`, `memory.short-term.write`)
- **Priority** — events carry a priority level (0 = highest)
- **Queues** — subscribers receive events in FIFO order per topic
- **Persistence** — optional SQLite-backed persistence via `aiosqlite`

### Agent Runtime

The `AgentRuntime` manages the lifecycle of agents:

1. **Spawn** — create an agent from `AgentConfig`
2. **Supervise** — monitor health, restart crashed agents
3. **Terminate** — graceful shutdown with event flushing

### Plan Engine

The `PlanEngine` executes a `PlanDAG`:

- Steps define `action`, `dependencies`, and `description`
- Topological sort determines execution order
- Steps with satisfied dependencies run in parallel
- Results propagate downstream automatically

### Memory

Four tiers of memory:

| Tier | Persistence | Scope | API |
|------|-------------|-------|-----|
| Working | In-process | Per agent key/value | `ContextManager` |
| Short-term | SQLite | Per agent sliding window | `ShortTermMemory` |
| Long-term | SQLite | Global queryable | `LongTermMemory` |
| Episodic | SQLite | Per correlation chain | `EpisodicMemory` |

### Context Compression

The `ContextCompressor` automatically truncates overflowing short-term windows:

- Preserves system, user, and human entries absolutely
- Merges existing summary entries to prevent unbounded growth
- Extracts topics from dropped entries via keyword frequency
- Preserves most recent droppable entries within token budget

### Observability

- **Tracing** — `Tracer` with `async_span()` context managers; spans export to JSON
- **Metrics** — Prometheus-compatible counters and histograms via middleware
- **DLQ** — Dead-letter queue for failed events

## Server Architecture

```
FastAPI App
├── Middleware
│   ├── CORS
│   ├── Tracing (x-request-id + latency)
│   ├── Rate Limiting
│   └── Auth (optional API key)
├── Routers
│   ├── /tasks
│   ├── /agents
│   ├── /approvals
│   ├── /memory
│   ├── /modality
│   ├── /protocol (A2A / MCP / SSE)
│   └── /system (health, metrics, DLQ)
└── Lifespan
    ├── Startup: DB, EventBus, Runtime, Registry, Circuit Breakers, Modality Dispatcher
    └── Shutdown: graceful close of all resources
```

## Security

- **Guardrails** — prompt screening before LLM calls
- **Secrets** — scoped secret store with rotation and budget tracking
- **Sandbox** — optional network proxy and tool execution sandboxing
- **Auth** — optional bearer-token middleware on all endpoints

## Extensibility

Add a new layer by:

1. Creating a module under `edac/<layer>/`
2. Defining events on the `EventBus`
3. Optionally adding a FastAPI router in `edac/server/routers/`
4. Registering in `edac/server/api.py` lifespan
