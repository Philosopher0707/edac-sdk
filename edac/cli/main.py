"""EDAC CLI entry point.

Commands:
    edac serve     — Start the production server
    edac run       — Run a single task from CLI
    edac status    — Check server health
    edac version   — Show version
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Optional

import click
import uvicorn

from edac.server.api import create_app
from edac.server.config import ServerConfig

logger = logging.getLogger("edac.cli")


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


@click.group()
@click.option("--config", "-c", type=click.Path(exists=True), help="Path to config file")
@click.option("--verbose", "-v", is_flag=True, help="Verbose output")
@click.pass_context
def cli(ctx: click.Context, config: Optional[str], verbose: bool) -> None:
    """EDAC — Event-Driven Agentic Core CLI."""
    cfg = ServerConfig()
    if config:
        # Override with file if provided
        pass
    ctx.ensure_object(dict)
    ctx.obj["config"] = cfg
    ctx.obj["verbose"] = verbose
    _setup_logging("debug" if verbose else cfg.log_level)


@cli.command()
@click.option("--host", default=None, help="Bind host")
@click.option("--port", type=int, default=None, help="Bind port")
@click.option("--workers", type=int, default=None, help="Number of workers")
@click.option("--reload", is_flag=True, help="Enable auto-reload")
@click.pass_context
def serve(ctx: click.Context, host: Optional[str], port: Optional[int], workers: Optional[int], reload: bool) -> None:
    """Start the EDAC production server."""
    cfg: ServerConfig = ctx.obj["config"]

    app = create_app(config=cfg)

    uvicorn.run(
        app,
        host=host or cfg.host,
        port=port or cfg.port,
        workers=workers or cfg.workers,
        reload=reload or cfg.reload,
        log_level=cfg.log_level,
    )


@cli.command()
@click.argument("goal")
@click.option("--pattern", default="pipeline", help="Execution pattern")
@click.option("--agents", "-a", multiple=True, help="Agent definitions as JSON")
@click.option("--max-parallel", type=int, default=3, help="Max parallel agents")
@click.option("--provider", default="ollama", help="Model provider")
@click.option("--model", default=None, help="Model name")
@click.option("--server", default="http://localhost:8000", help="Server URL")
@click.pass_context
def run(
    ctx: click.Context,
    goal: str,
    pattern: str,
    agents: tuple,
    max_parallel: int,
    provider: str,
    model: Optional[str],
    server: str,
) -> None:
    """Run a single task via the EDAC server or locally."""
    import httpx

    # Parse agents
    agent_list = []
    for a in agents:
        agent_list.append(json.loads(a))

    # Default agents if none provided
    if not agent_list:
        agent_list = [
            {"name": "planner", "role": "orchestrator", "task": "Plan", "model": model or "llama3.2"},
            {"name": "coder", "role": "worker", "task": "Code", "model": model or "llama3.2"},
            {"name": "reviewer", "role": "critic", "task": "Review", "model": model or "llama3.2"},
        ]

    payload = {
        "goal": goal,
        "pattern": pattern,
        "agents": agent_list,
        "max_parallel": max_parallel,
    }

    async def _submit() -> None:
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{server}/tasks", json=payload)
            resp.raise_for_status()
            data = resp.json()
            click.echo(f"Task submitted: {data['id']}")
            click.echo(f"Status: {data['status']}")

            # Poll for completion
            task_id = data["id"]
            while True:
                await asyncio.sleep(1)
                resp = await client.get(f"{server}/tasks/{task_id}")
                resp.raise_for_status()
                task = resp.json()
                if task["status"] in ("completed", "failed"):
                    click.echo(f"\nFinal status: {task['status']}")
                    if task.get("result"):
                        click.echo(json.dumps(task["result"], indent=2))
                    if task.get("error"):
                        click.echo(f"Error: {task['error']}", err=True)
                    break
                click.echo(".", nl=False)

    asyncio.run(_submit())


@cli.command()
@click.option("--server", default="http://localhost:8000", help="Server URL")
def status(server: str) -> None:
    """Check EDAC server health."""
    import httpx

    try:
        resp = httpx.get(f"{server}/health")
        resp.raise_for_status()
        data = resp.json()
        click.echo(f"Server: {data['status']} (v{data['version']})")
    except Exception as e:
        click.echo(f"Server unreachable: {e}", err=True)
        sys.exit(1)


@cli.command()
def version() -> None:
    """Show EDAC version."""
    click.echo("EDAC 0.2.0")


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
