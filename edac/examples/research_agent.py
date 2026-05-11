"""Research Agent — Multi-agent swarm with mesh coordination.

Demonstrates full EDAC stack:
- EventBus for inter-agent communication
- AgentRuntime for lifecycle management
- PlanDAG for execution tracking
- Swarm orchestration with mesh pattern (parallel research)
- Episodic Memory for event trajectory
- Skill loader integration
- Observability (tracing + metrics)

Usage:
    PYTHONPATH=/Users/philosopher/Documents python3 edac/examples/research_agent.py
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from edac import EventBus
from edac.agent.runtime import AgentRuntime
from edac.context.manager import ContextConfig, ContextManager
from edac.event.schema import Event, EventType, create_event
from edac.memory.episodic import EpisodicMemory
from edac.model import ModelRegistry
from edac.observability.metrics import MetricsCollector
from edac.observability.tracing import Tracer
from edac.server.executor import AgentExecutor
from edac.tool.skill import SkillLoader

from edac.examples._utils import make_registry_with_mock

logging.basicConfig(level=logging.INFO, format="%(name)s | %(levelname)s | %(message)s")
logger = logging.getLogger("edac.examples.research")


async def main():
    # ── Setup ──
    tracer = Tracer()
    metrics = MetricsCollector()

    # Load research skill
    skill_loader = SkillLoader()
    skill_loader.load(Path(__file__).parent.parent.parent / "skills" / "research-skill.md")
    logger.info(f"Loaded skills: {list(skill_loader._skills.keys())}")

    # Mock provider tuned for research-style responses
    registry = make_registry_with_mock(
        responses={
            "research": (
                "## Summary\n"
                "Neural-symbolic AI combines neural networks with symbolic reasoning.\n\n"
                "## Key Findings\n"
                "- Neuro-symbolic integration improves explainability (confidence: 0.92)\n"
                "- Hybrid architectures outperform pure approaches on reasoning tasks (confidence: 0.88)\n\n"
                "## Sources\n"
                "- [Garcez et al., 2020] Neural-Symbolic Computing: An Effective Methodology\n"
                "- [Marcus, 2020] The Next Decade in AI\n\n"
                "## Open Questions\n"
                "- How to scale to billion-parameter models?\n"
            ),
            "critic": (
                "## Review\n"
                "Score: 8/10\n\n"
                "Strengths:\n"
                "- Good source coverage\n"
                "- Clear confidence scoring\n\n"
                "Issues:\n"
                "- Missing 2024 papers on LLM reasoning\n"
                "- No quantitative comparison tables\n\n"
                "Recommendations:\n"
                "- Add arXiv search for recent work\n"
            ),
            "synthesize": (
                "## Final Research Report\n\n"
                "Neural-symbolic AI is a fast-growing field bridging deep learning "
                "and symbolic reasoning. Recent advances (2020–2024) show hybrid "
                "systems can achieve higher accuracy with better interpretability.\n\n"
                "Confidence-weighted findings:\n"
                "- Integration improves explainability (0.92)\n"
                "- Hybrid outperforms pure on reasoning (0.88)\n"
                "- Scalability remains open (0.65)\n\n"
                "Next steps: consult 2024 arXiv preprints.\n"
            ),
        },
        default_response="Research complete.",
    )

    # Episodic memory to record all events
    episodic = EpisodicMemory(path=Path("/tmp/edac_research_episodes.jsonl"))

    async with EventBus() as bus:
        # Record every emitted event in episodic memory
        async def episode_subscriber(event: Event):
            episodic.append(event)
            return True

        bus.subscribe(episode_subscriber, topics=["research."])

        async with AgentRuntime(bus) as runtime:
            ctx = ContextManager(
                registry=registry,
                config=ContextConfig(
                    default_provider="mock",
                    compression_enabled=False,
                ),
            )

            executor = AgentExecutor(
                bus=bus,
                runtime=runtime,
                registry=registry,
                ctx_manager=ctx,
                tracer=tracer,
            )

            # ── Execute Research Swarm ──
            with tracer.span("research_run") as span:
                result = await executor.execute(
                    goal="Research neural-symbolic AI advances (2020–2024)",
                    pattern="mesh",
                    agents=[
                        {
                            "name": "researcher-1",
                            "role": "worker",
                            "provider": "mock",
                            "system_prompt": (
                                "You are a research analyst. Search academic sources, "
                                "extract key findings with confidence scores, and cite sources."
                            ),
                        },
                        {
                            "name": "researcher-2",
                            "role": "worker",
                            "provider": "mock",
                            "system_prompt": (
                                "You are a technical critic. Review research findings for "
                                "gaps, contradictions, and missing recent work."
                            ),
                        },
                        {
                            "name": "synthesizer",
                            "role": "orchestrator",
                            "provider": "mock",
                            "system_prompt": (
                                "You are a senior research lead. Synthesize parallel findings "
                                "into a coherent report with actionable next steps."
                            ),
                        },
                    ],
                    max_parallel=3,
                )

                metrics.counter("research_runs").inc()
                metrics.counter("research_artifacts").inc(len(result.artifacts))
                span.set_attribute("artifact_count", len(result.artifacts))

            # ── Results ──
            logger.info(f"Swarm success: {result.success}")
            logger.info(f"Artifacts: {len(result.artifacts)}")
            for art in result.artifacts:
                logger.info(f"  - {art.get('agent', '?')}: {str(art.get('output', ''))[:120]}...")

            # ── Episodic Memory Replay ──
            # In a real deployment, we'd get the correlation_id from the result events.
            # For the demo, we just show the episodic store stats.
            logger.info(f"Episodic memory recorded: {len(episodic._events)} events")

            # ── Metrics ──
            print("\n--- Metrics ---")
            print(metrics.export())

            # ── Traces ──
            exported = tracer.export()
            print(f"\n--- Traces: {len(exported)} spans ---")
            for s in exported:
                print(f"  {s['name']} ({s.get('status', 'ok')})")

    # Cleanup
    Path("/tmp/edac_research_episodes.jsonl").unlink(missing_ok=True)
    return result


if __name__ == "__main__":
    asyncio.run(main())
