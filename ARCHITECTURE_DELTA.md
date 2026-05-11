# EDAC: Architecture Delta

> **Purpose**: Map the original [EDAC_DESIGN_PLAN.md](EDAC_DESIGN_PLAN.md) to the actual codebase, documenting intentional deviations and unplanned additions.
> **Version**: 0.2.0  
> **Last Updated**: 2026-05-11

---

## 1. Design Plan → Reality Mapping

| Design Plan File | Actual File(s) | Status | Rationale |
|------------------|----------------|--------|-----------|
| `edac/event/core.py` | `edac/event/bus.py` + `edac/event/router.py` + `edac/event/schema.py` | **Decomposed** | The monolithic "core" was split into focused, testable modules. No functional loss. |
| `edac/event/types.py` | `edac/event/schema.py` | **Merged** | Event type literals and Pydantic models co-located for single-source-of-truth validation. |
| `edac/runtime/executor.py` | `edac/server/executor.py` | **Relocated** | The executor grew into a production-grade service integrating Swarm, ModelRegistry, and circuit breakers — it belongs in `server/`. |
| `edac/secrets/manager.py` | `edac/security/secrets.py` | **Relocated** | Secrets management is a security concern; placing it under `security/` keeps the surface area discoverable. |

---

## 2. Intentional Deviations

### 2.1 Event Layer Decomposition
The design plan proposed a single `edac/event/core.py`. In practice, this became three first-class modules:

- **`edac/event/bus.py`** — Async pub/sub event bus with priority queues, backpressure, and persistent event log.
- **`edac/event/router.py`** — Topic-based router with wildcards, fan-out, filtering, and O(1) exact-match lookup.
- **`edac/event/schema.py`** — Universal Pydantic event schema, validation, serialization, and causality chains.

This separation follows the principle that *event transport*, *event routing*, and *event schema* are independent axes of change.

### 2.2 Runtime / Server Convergence
`edac/runtime/executor.py` was conceptually a lightweight task runner. During implementation it evolved into `edac/server/executor.py`, which:
- Integrates `Swarm`, `ModelRegistry`, `ContextManager`
- Includes circuit breakers and retry policies
- Serves as the primary server-side agent execution entry point

We kept `edac/runtime/asyncio.py` (TaskManager / graceful shutdown) as a shared primitive, while the *executor* graduated to the server layer.

### 2.3 Secrets Under Security
The design plan placed secrets in a top-level `edac/secrets/` package. We only have one secrets module (`manager.py` equivalent), so a dedicated package would be over-engineering. `edac/security/secrets.py` provides scoped, rotated credentials and lives alongside sandbox, network ACLs, and guardrails.

---

## 3. Unplanned Additions

These modules were **not** in the original design plan but were built because the architecture demanded them during implementation.

| Module | Files | Why It Exists |
|--------|-------|---------------|
| **Vector Clock** | `edac/event/vector_clock.py` | Distributed event causality tracking required for replay and multi-node semantics. Integrated into `EventBus.emit()` and `Event` helpers. |
| **Modality Dispatcher** | `edac/modality/dispatcher.py` | Central router for dispatching encoded content to the correct `ModalityAdapter`. Keeps modality logic out of the bus. |
| **Production Server** | `edac/server/` (15 files) | REST API, auth, rate limiting, circuit breaker, job queue, worker pool, and `AgentExecutor`. Needed to turn the SDK into a runnable service. |
| **Context Compression** | `edac/context/compressor.py` | Token-aware semantic compression with topic extraction and summary merging. Emerged from stress-testing context windows against real prompts. |
| **Tracing Instrumentation** | `edac/observability/tracing.py` + spans in `Swarm`/`AgentExecutor` | OpenTelemetry-compatible spans added retroactively after realizing debugging multi-agent swarms without tracing was impossible. |

---

## 4. What’s Truly Missing

Nothing with **functional impact**. The four design-plan files listed in Section 1 are either decomposed, merged, or relocated. No capability gap exists.

If we ever need a pure-client runtime (no server), we can revive `edac/runtime/executor.py` as a lightweight wrapper over `server/executor.py`. Until then, YAGNI.

---

## 5. Test Coverage

All deviations are under test:
- **422 passed, 4 skipped** across 20 test files
- Event layer: bus, router, schema, vector clock
- Runtime: asyncio TaskManager + server executor
- SDK: agent builder, skill builder, workflow runner with real `PlanEngine` execution
- Security: secrets, sandbox, network, guardrails
- Human-in-the-loop: approval gates with async pause/resume, CLI approval commands

The test suite validates that the decomposed architecture behaves as the original monolithic design intended.

---

## 6. Summary

| Metric | Design Plan | Reality |
|--------|-------------|---------|
| Files specified | ~40 | ~55 (including unplanned additions) |
| Lines of code (estimated) | 5,000–10,000 | ~6,500+ (excluding tests) |
| Unplanned modules | 0 | 5+ |
| Missing modules (functional) | 0 | 0 |

**Verdict**: The architecture evolved *beyond* the design plan, not beneath it. Every design intent is preserved; several are exceeded.
