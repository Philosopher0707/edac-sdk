"""Data Analysis Agent — Pipeline pattern with modality adapters.

Demonstrates full EDAC stack:
- EventBus for inter-agent communication
- AgentRuntime for lifecycle management
- PlanDAG for execution tracking
- Swarm orchestration with pipeline pattern
- Modality adapters (CSV artifact → analysis → chart image)
- Tool system integration (custom data tools)
- Observability (tracing + metrics)

Usage:
    PYTHONPATH=/Users/philosopher/Documents python3 edac/examples/data_analysis_agent.py
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict

from edac import EventBus
from edac.agent.runtime import AgentRuntime
from edac.context.manager import ContextConfig, ContextManager
from edac.modality.dispatcher import ModalityDispatcher
from edac.model import ModelRegistry
from edac.observability.metrics import MetricsCollector
from edac.observability.tracing import Tracer
from edac.server.executor import AgentExecutor
from edac.tool.registry import ToolRegistry, ToolSpec

from edac.examples._utils import make_registry_with_mock

logging.basicConfig(level=logging.INFO, format="%(name)s | %(levelname)s | %(message)s")
logger = logging.getLogger("edac.examples.data_analysis")


# ── Custom Data Analysis Tools ──


async def analyze_csv(filepath: str) -> Dict[str, Any]:
    """Mock CSV analysis returning summary statistics."""
    # In production this would read a real CSV with pandas
    return {
        "columns": 5,
        "rows": 1000,
        "mean_values": {"sales": 14200.5, "customers": 340},
        "missing": 12,
        "summary": "Strong Q4 performance. Sales up 23% YoY.",
    }


async def generate_chart(data: Dict[str, Any]) -> Dict[str, Any]:
    """Mock chart generation returning base64 image metadata."""
    # In production this would use matplotlib/plotly
    return {
        "chart_type": "bar",
        "title": "Q4 Sales Performance",
        "format": "png",
        "size_bytes": 15234,
        "summary": "Chart showing sales by region. North America leads.",
    }


async def write_report(findings: Dict[str, Any]) -> Dict[str, Any]:
    """Mock report writer returning structured report."""
    return {
        "title": "Q4 2024 Sales Analysis",
        "sections": ["Executive Summary", "Methodology", "Findings", "Recommendations"],
        "recommendations": [
            "Increase inventory in North America",
            "Launch targeted marketing in Europe",
            "Investigate Q3 anomaly in APAC",
        ],
    }


def register_data_tools(registry: ToolRegistry) -> None:
    """Register the custom data analysis tools."""
    registry.register(
        ToolSpec(
            name="analyze_csv",
            description="Analyze a CSV file and return summary statistics",
            parameters={"filepath": {"type": "string", "description": "Path to CSV file"}},
            returns={"type": "object"},
            category="data",
        ),
        analyze_csv,
    )
    registry.register(
        ToolSpec(
            name="generate_chart",
            description="Generate a chart from analyzed data",
            parameters={"data": {"type": "object", "description": "Analysis output"}},
            returns={"type": "object"},
            category="data",
        ),
        generate_chart,
    )
    registry.register(
        ToolSpec(
            name="write_report",
            description="Write a structured report from findings",
            parameters={"findings": {"type": "object", "description": "Analysis findings"}},
            returns={"type": "object"},
            category="data",
        ),
        write_report,
    )


async def main():
    # ── Setup ──
    tracer = Tracer()
    metrics = MetricsCollector()
    modality = ModalityDispatcher()

    # Mock provider tuned for data analysis
    registry = make_registry_with_mock(
        responses={
            "analyze": (
                "Data analysis complete. Key insight: North America sales "
                "are 34% above forecast. Europe underperformed by 12%. "
                "Recommend inventory reallocation."
            ),
            "visualize": (
                "Chart generated: bar chart comparing regional performance. "
                "North America green (above target), Europe yellow (below target). "
                "Clear visual for executive presentation."
            ),
            "report": (
                "Report drafted: Executive Summary highlights 23% YoY growth. "
                "Recommendations: reallocate inventory, increase Europe marketing, "
                "investigate APAC anomaly. Ready for stakeholder review."
            ),
        },
        default_response="Analysis complete.",
    )

    # Register custom tools
    tool_registry = ToolRegistry()
    register_data_tools(tool_registry)
    logger.info(f"Registered tools: {[t.name for t in tool_registry.list_tools()]}")

    async with EventBus() as bus:
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
                tools=tool_registry,
            )

            # ── Execute Pipeline ──
            # Stage 1: Data ingest + analyze
            # Stage 2: Visualization
            # Stage 3: Report writing
            with tracer.span("data_analysis_run") as span:
                result = await executor.execute(
                    goal="Analyze Q4 2024 sales data and produce executive report",
                    pattern="pipeline",
                    agents=[
                        {
                            "name": "data-engineer",
                            "role": "worker",
                            "provider": "mock",
                            "system_prompt": (
                                "You are a data engineer. Load CSV data, run statistical "
                                "analysis, and extract actionable business insights. "
                                "Use the analyze_csv tool when needed."
                            ),
                        },
                        {
                            "name": "viz-designer",
                            "role": "worker",
                            "provider": "mock",
                            "system_prompt": (
                                "You are a data visualization specialist. Create clear, "
                                "informative charts for executive audiences. Use the "
                                "generate_chart tool when needed."
                            ),
                        },
                        {
                            "name": "report-writer",
                            "role": "worker",
                            "provider": "mock",
                            "system_prompt": (
                                "You are a business analyst. Write structured reports "
                                "with executive summary, findings, and recommendations. "
                                "Use the write_report tool when needed."
                            ),
                        },
                    ],
                    max_parallel=1,  # Pipeline is sequential
                )

                metrics.counter("analysis_runs").inc()
                metrics.counter("analysis_artifacts").inc(len(result.artifacts))
                span.set_attribute("artifact_count", len(result.artifacts))

            # ── Results ──
            logger.info(f"Pipeline success: {result.success}")
            logger.info(f"Artifacts: {len(result.artifacts)}")
            for art in result.artifacts:
                logger.info(f"  - {art.get('agent', '?')}: {str(art.get('output', ''))[:120]}...")

            # ── Modality Demo ──
            # Show how the dispatcher auto-detects content types
            csv_sample = "region,sales,customers\nNA,14200,340\nEU,9800,210\nAPAC,11200,280"
            detected = modality.detect(csv_sample, filename="sales.csv")
            logger.info(f"Modality detected for CSV: {detected.value}")

            # Process the CSV into a ModalityContent
            content = modality.process(csv_sample, filename="sales.csv", mime_type="text/csv")
            logger.info(
                f"Processed content: modality={content.modality.value}, valid={modality.validate(content)}"
            )

            # ── Tool Execution Demo ──
            # Directly invoke our custom tools (separate from the swarm)
            analysis = await tool_registry.execute("analyze_csv", {"filepath": "data/q4_sales.csv"})
            chart = await tool_registry.execute("generate_chart", {"data": analysis})
            report = await tool_registry.execute("write_report", {"findings": analysis})

            print("\n--- Tool Results ---")
            print(f"CSV Analysis: {json.dumps(analysis, indent=2)}")
            print(f"Chart: {json.dumps(chart, indent=2)}")
            print(f"Report: {json.dumps(report, indent=2)}")

            # ── Metrics ──
            print("\n--- Metrics ---")
            print(metrics.export())

            # ── Traces ──
            exported = tracer.export()
            print(f"\n--- Traces: {len(exported)} spans ---")
            for s in exported:
                print(f"  {s['name']} ({s.get('status', 'ok')})")

    return result


if __name__ == "__main__":
    asyncio.run(main())
