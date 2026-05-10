"""Workflow Runner — Execute multi-step workflows with agents.

Usage:
    workflow = Workflow([
        {"agent": "planner", "task": "plan the refactor"},
        {"agent": "coder", "task": "execute the plan"},
        {"agent": "reviewer", "task": "review the code"},
    ])
    result = await WorkflowRunner(runtime, workflow).run()
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from edac.agent.runtime import AgentRuntime
from edac.event.schema import Event

logger = logging.getLogger("edac.sdk.workflow")


@dataclass
class Workflow:
    """A linear workflow definition."""
    steps: List[Dict[str, Any]] = field(default_factory=list)

    def add_step(self, agent: str, task: str, **kwargs: Any) -> Workflow:
        self.steps.append({"agent": agent, "task": task, **kwargs})
        return self


class WorkflowRunner:
    """Executes a workflow sequentially."""

    def __init__(self, runtime: AgentRuntime, workflow: Workflow) -> None:
        self.runtime = runtime
        self.workflow = workflow
        self.results: List[Dict[str, Any]] = []

    async def run(self) -> List[Dict[str, Any]]:
        for step in self.workflow.steps:
            agent_name = step["agent"]
            task = step["task"]
            logger.info(f"Workflow step: {agent_name} → {task}")

            agent = self.runtime.registry.get(agent_name)
            if agent is None:
                raise ValueError(f"Agent '{agent_name}' not found in runtime")

            # In a real implementation, this would emit an event and wait
            result = {"agent": agent_name, "task": task, "status": "done"}
            self.results.append(result)

        return self.results
