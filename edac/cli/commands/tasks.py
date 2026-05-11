"""CLI subcommands for task management."""

from __future__ import annotations

import json
import sys
from typing import Optional

import click

from edac.client.sync_client import EdacClientSync


@click.group(name="tasks")
@click.option("--base-url", default="http://localhost:8000", help="EDAC server URL")
@click.option("--api-key", default=None, help="API key for authentication")
@click.pass_context
def tasks(ctx: click.Context, base_url: str, api_key: Optional[str]) -> None:
    """Manage tasks."""
    ctx.ensure_object(dict)
    ctx.obj["base_url"] = base_url
    ctx.obj["api_key"] = api_key


@tasks.command(name="list")
@click.option("--status", default=None, help="Filter by status")
@click.option("--limit", type=int, default=100, help="Maximum items to return")
@click.option("--offset", type=int, default=0, help="Pagination offset")
@click.pass_context
def list_tasks(ctx: click.Context, status: Optional[str], limit: int, offset: int) -> None:
    """List tasks."""
    client = EdacClientSync(ctx.obj["base_url"], api_key=ctx.obj.get("api_key"))
    try:
        result = client.list_tasks(status=status, limit=limit, offset=offset)
        click.echo(f"Total: {result.total} | Limit: {result.limit} | Offset: {result.offset}")
        click.echo("-" * 60)
        for t in result.items:
            goal_display = t.goal[:40] if t.goal else ""
            click.echo(f"{t.id:<36} {t.status:<12} {goal_display}")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    finally:
        client.close()


@tasks.command(name="get")
@click.argument("task_id")
@click.pass_context
def get_task(ctx: click.Context, task_id: str) -> None:
    """Get a task by ID."""
    client = EdacClientSync(ctx.obj["base_url"], api_key=ctx.obj.get("api_key"))
    try:
        t = client.get_task(task_id)
        click.echo(json.dumps(t.model_dump(), indent=2))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    finally:
        client.close()


@tasks.command(name="submit")
@click.argument("goal")
@click.option("--pattern", default="pipeline", help="Execution pattern")
@click.option("--agents", "agents_json", multiple=True, help="Agent definitions as JSON")
@click.option("--max-parallel", type=int, default=3, help="Max parallel agents")
@click.option("--watch", is_flag=True, help="Watch task via WebSocket after submission")
@click.option("--timeout", type=float, default=30.0, help="WebSocket timeout")
@click.pass_context
def submit_task(
    ctx: click.Context,
    goal: str,
    pattern: str,
    agents_json: tuple,
    max_parallel: int,
    watch: bool,
    timeout: float,
) -> None:
    """Submit a new task."""
    from edac.server.schemas import SubmitTaskRequest

    agent_list = [json.loads(a) for a in agents_json] if agents_json else []

    client = EdacClientSync(ctx.obj["base_url"], api_key=ctx.obj.get("api_key"))
    try:
        req = SubmitTaskRequest(
            goal=goal,
            pattern=pattern,
            agents=agent_list,
            max_parallel=max_parallel,
        )
        task = client.submit_task(req)
        click.echo(f"Submitted: {task.id} ({task.status})")

        if watch:
            click.echo("--- watching ---")
            n = 0
            for update in client.watch_task(task.id, timeout=timeout):
                click.echo(json.dumps(update.model_dump(), indent=2))
                n += 1
                if n >= 5:  # Auto-stop after 5 updates
                    break
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    finally:
        client.close()


@tasks.command(name="cancel")
@click.argument("task_id")
@click.pass_context
def cancel_task(ctx: click.Context, task_id: str) -> None:
    """Cancel a running or queued task."""
    client = EdacClientSync(ctx.obj["base_url"], api_key=ctx.obj.get("api_key"))
    try:
        t = client.cancel_task(task_id)
        click.echo(f"Cancelled: {t.id} ({t.status})")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    finally:
        client.close()


@tasks.command(name="events")
@click.argument("task_id")
@click.pass_context
def task_events(ctx: click.Context, task_id: str) -> None:
    """Get events for a task."""
    client = EdacClientSync(ctx.obj["base_url"], api_key=ctx.obj.get("api_key"))
    try:
        events = client.get_task_events(task_id)
        for evt in events:
            ts = evt.get("timestamp", "")
            et = evt.get("event_type", "")
            payload = json.dumps(evt.get("payload", {}))
            click.echo(f"[{ts}] {et}: {payload}")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    finally:
        client.close()


@tasks.command(name="batch")
@click.argument("goals", nargs=-1)
@click.option("--pattern", default="pipeline", help="Execution pattern")
@click.option("--max-parallel", type=int, default=3, help="Max parallel agents")
@click.pass_context
def submit_tasks_batch(
    ctx: click.Context,
    goals: tuple,
    pattern: str,
    max_parallel: int,
) -> None:
    """Submit multiple tasks in one batch."""
    from edac.server.schemas import SubmitTaskRequest

    if not goals:
        click.echo("No goals provided", err=True)
        sys.exit(1)

    reqs = [SubmitTaskRequest(goal=g, pattern=pattern, max_parallel=max_parallel) for g in goals]
    client = EdacClientSync(ctx.obj["base_url"], api_key=ctx.obj.get("api_key"))
    try:
        results = client.submit_tasks_batch(reqs)
        for r in results:
            if hasattr(r, "id"):
                click.echo(f"OK: {r.id} ({r.status})")
            else:
                click.echo(f"ERR: {r.error} - {r.detail or ''}", err=True)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    finally:
        client.close()
