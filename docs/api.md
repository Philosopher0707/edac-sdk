# API Reference

EDAC exposes a FastAPI server with interactive docs at `/docs` (Swagger UI) and `/redoc` (ReDoc).

## Base URL

```
http://localhost:8000
```

## Authentication

When `EDAC_API_KEY` is set, all endpoints require:

```
Authorization: Bearer <token>
```

## Endpoints

### Tasks

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/tasks` | Submit a new multi-agent task |
| `GET` | `/tasks` | List all tasks |
| `GET` | `/tasks/{task_id}` | Get task by ID |
| `DELETE` | `/tasks/{task_id}/cancel` | Cancel a running task |
| `GET` | `/tasks/{task_id}/events` | Get events for a task |
| `WS` | `/tasks/{task_id}/ws` | WebSocket stream of task events |

**Submit a task:**

```bash
curl -X POST http://localhost:8000/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "goal": "Build a REST API",
    "agents": [
      {"name": "planner", "role": "planner", "provider": "openai", "model": "gpt-4o"},
      {"name": "coder", "role": "worker", "provider": "openai", "model": "gpt-4o"}
    ],
    "pattern": "pipeline"
  }'
```

### Agents

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/agents` | List registered agents |

### Approvals (Human-in-the-Loop)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/approvals/gates` | List active approval gates |
| `POST` | `/approvals/gates` | Create a new approval gate |
| `POST` | `/approvals/gates/{trigger_on}/approve` | Approve a gate |

### Memory

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/memory/stats` | Memory layer statistics |
| `GET` | `/memory/agents/{agent_id}/short-term` | Read agent short-term memory |
| `POST` | `/memory/agents/{agent_id}/short-term` | Write to short-term memory |
| `DELETE` | `/memory/agents/{agent_id}/short-term` | Clear short-term memory |
| `GET` | `/memory/working/{agent_id}` | Read working memory |
| `GET` | `/memory/working/{agent_id}/{key}` | Read working memory key |
| `POST` | `/memory/working/{agent_id}` | Write to working memory |
| `DELETE` | `/memory/working/{agent_id}` | Clear working memory |
| `GET` | `/memory/long-term` | Query long-term memory |
| `POST` | `/memory/long-term` | Store in long-term memory |
| `DELETE` | `/memory/long-term/{entry_id}` | Delete long-term entry |
| `GET` | `/memory/episodic/{correlation_id}` | Read episodic chain |
| `GET` | `/memory/episodic` | Query episodic memory |

### Modality

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/modality` | List supported modalities |
| `POST` | `/modality/detect` | Detect modality of input |
| `POST` | `/modality/process` | Process a modality payload |
| `POST` | `/modality/validate` | Validate a modality payload |
| `GET` | `/modality/stats` | Modality dispatcher statistics |

### Protocol (A2A / MCP / SSE)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/a2a/agents` | List A2A-visible agents |
| `GET` | `/a2a/agents/{name}/card` | Get A2A agent card |
| `POST` | `/a2a/tasks` | Create A2A task |
| `GET` | `/events/stream` | SSE event stream |
| `GET` | `/mcp/tools` | List MCP tools |
| `POST` | `/mcp/tools/{tool_name}` | Call an MCP tool |
| `GET` | `/tools` | List local tools |

### System

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check |
| `GET` | `/metrics` | Prometheus-compatible metrics |
| `GET` | `/dlq` | Dead-letter queue contents |

## WebSocket

Connect to a task's live event stream:

```javascript
const ws = new WebSocket("ws://localhost:8000/tasks/task-123/ws");
ws.onmessage = (msg) => {
    const event = JSON.parse(msg.data);
    console.log(event.event_type, event.payload);
};
```

## Server-Sent Events

Subscribe to all system events:

```bash
curl -N http://localhost:8000/events/stream
```

## Response Codes

| Code | Meaning |
|------|---------|
| `200` | Success |
| `202` | Accepted (async task) |
| `400` | Bad request |
| `401` | Unauthorized (missing API key) |
| `404` | Not found |
| `409` | Conflict (task already running) |
| `429` | Rate limited |
| `500` | Internal server error |
