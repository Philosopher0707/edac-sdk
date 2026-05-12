"""SDK Decorators — @agent, @skill, @workflow.

Thin wrappers over existing builders. Attach metadata to functions/classes
for runtime discovery.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TypeVar

from edac.agent.lifecycle import AgentConfig
from edac.sdk.agent_builder import AgentBuilder
from edac.sdk.skill_builder import SkillBuilder
from edac.sdk.workflow import Workflow
from edac.tool.skill import Skill, SkillLoader

F = TypeVar("F", bound=Callable[..., Any])


def agent(
    name: str,
    model: Optional[str] = None,
    skills: Optional[List[str]] = None,
    sandbox: bool = False,
    max_restarts: int = 3,
    goal: Optional[str] = None,
    auto_register: bool = True,
) -> Callable[[F], F]:
    """Decorator that marks a function as an agent handler.

    When *auto_register* is ``True`` (the default) the decorated coroutine is
    registered with the global `HandlerRegistry`.  `AgentRuntime` will then
    use the correct factory automatically when `AgentRuntime.spawn()` is
    called with a matching ``config.name``.

    Usage:
        @agent(name="coder", model="claude-sonnet")
        async def my_coder(agent: AgentInstance) -> None:
            ...
    """

    def decorator(func: F) -> F:
        config = (
            AgentBuilder()
            .name(name)
            .model(model)
            .skills(skills or [])
            .sandbox(sandbox)
            .max_restarts(max_restarts)
            .goal(goal)
            .build()
        )
        func._edac_agent_config = config  # type: ignore[attr-defined]
        if auto_register:
            # Deferred import avoids circular dependencies during type-checking
            from edac.agent.handler_registry import HandlerRegistry  # noqa: F811

            HandlerRegistry.get_default().register(name, func)
        return func

    return decorator


def skill(
    path: Optional[str] = None,
    name: Optional[str] = None,
    description: str = "",
    applies_when: str = "",
) -> Callable[[F], F]:
    """Decorator that attaches a skill to a function.

    Usage:
        @skill("skills/python.md")
        async def my_coder(event: Event) -> Event:
            ...
    """

    def decorator(func: F) -> F:
        if path:
            s = SkillLoader().load(Path(path))
        elif name:
            s = (
                SkillBuilder()
                .name(name)
                .description(description)
                .applies_when(applies_when)
                .build()
            )
        else:
            raise ValueError("skill decorator requires path or name")
        func._edac_skill = s  # type: ignore[attr-defined]
        return func

    return decorator


def workflow(steps: List[Dict[str, Any]]) -> Callable[[F], F]:
    """Decorator that attaches a workflow definition to a function.

    Usage:
        @workflow([
            {"agent": "planner", "task": "plan"},
            {"agent": "coder", "task": "code"},
        ])
        async def my_pipeline(event: Event) -> Event:
            ...
    """

    def decorator(func: F) -> F:
        wf = Workflow(steps=steps)
        func._edac_workflow = wf  # type: ignore[attr-defined]
        return func

    return decorator
