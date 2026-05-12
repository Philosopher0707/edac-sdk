"""Starter agent: Example Coder.

This demonstrates the user-facing agentic platform:
1. Define with @agent decorator
2. Auto-loaded on server start via agents/ directory
3. Executed when a task references agent name 'example-coder'

To try it:
  edac serve
  edac tasks submit --goal "Write a hello world function" --agents '[{"name": "example-coder"}]'
"""

from __future__ import annotations

import asyncio

from edac.agent.lifecycle import AgentInstance
from edac.sdk import agent


@agent(name="example-coder", model="kimi-k2.6:cloud", auto_register=True)
async def example_coder(instance: AgentInstance) -> None:
    """An example coding agent that would process tasks programmatically.

    This is a starter template — real agents would:
    - Subscribe to EventBus for incoming tasks
    - Call LLM via ModelRegistry for reasoning
    - Emit results via EventBus
    - Use ToolRegistry for side effects

    For now, it simply logs its activation and waits.
    """
    import logging

    logger = logging.getLogger("agents.example_coder")
    logger.info("Example Coder agent activated (id=%s, goal=%s)",
                instance.config.name, instance.config.goal)

    # Keep running until terminated (real agent would do work here)
    while instance.state not in ("terminated", "error"):
        await asyncio.sleep(1)

    logger.info("Example Coder agent terminated")
