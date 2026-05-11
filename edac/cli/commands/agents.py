"""CLI subcommands for agent management."""

from __future__ import annotations

import json
import sys
from typing import List, Optional

import click

from edac.client.sync_client import EdacClientSync


@click.group(name="agents")
@click.option("--base-url", default="http://localhost:8000", help="EDAC server URL")
@click.option("--api-key", default=None, help="API key for authentication")
@click.pass_context
def agents(ctx: click.Context, base_url: str, api_key: Optional[str]) -> None:
    """Manage agents."""
    ctx.ensure_object(dict)
    ctx.obj["base_url"] = base_url
    ctx.obj["api_key"] = api_key


@agents.command(name="list")
@click.option("--limit", type=int, default=100, help="Maximum items to return")
@click.option("--offset", type=int, default=0, help="Pagination offset")
@click.pass_context
def list_agents(ctx: click.Context, limit: int, offset: int) -> None:
    """List all active agents."""
    client = EdacClientSync(ctx.obj["base_url"], api_key=ctx.obj.get("api_key"))
    try:
        result = client.list_agents(limit=limit, offset=offset)
        click.echo(f"Total: {result.total} | Limit: {result.limit} | Offset: {result.offset}")
        click.echo("-" * 60)
        for a in result.items:
            click.echo(f"{a.agent_id:<20} {a.name:<16} {a.agent_type:<12} {a.state}")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    finally:
        client.close()


@agents.command(name="get")
@click.argument("agent_id")
@click.pass_context
def get_agent(ctx: click.Context, agent_id: str) -> None:
    """Get information about a single agent."""
    client = EdacClientSync(ctx.obj["base_url"], api_key=ctx.obj.get("api_key"))
    try:
        a = client.get_agent(agent_id)
        click.echo(json.dumps(a.model_dump(), indent=2))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    finally:
        client.close()


@agents.command(name="create")
@click.option("--name", required=True, help="Agent name")
@click.option("--type", "agent_type", default="generic", help="Agent type")
@click.option("--model", default=None, help="Model identifier")
@click.option("--goal", default=None, help="Agent goal")
@click.option("--sandbox", is_flag=True, help="Enable sandbox mode")
@click.option("--skills", multiple=True, help="Agent skills")
@click.pass_context
def create_agent(
    ctx: click.Context,
    name: str,
    agent_type: str,
    model: Optional[str],
    goal: Optional[str],
    sandbox: bool,
    skills: tuple,
) -> None:
    """Create a new agent."""
    from edac.server.schemas import CreateAgentRequest

    client = EdacClientSync(ctx.obj["base_url"], api_key=ctx.obj.get("api_key"))
    try:
        req = CreateAgentRequest(
            name=name,
            agent_type=agent_type,
            model=model,
            goal=goal,
            sandbox=sandbox,
            skills=list(skills) if skills else None,
        )
        a = client.create_agent(req)
        click.echo(f"Created agent {a.agent_id}: {a.name} ({a.state})")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    finally:
        client.close()


@agents.command(name="delete")
@click.argument("agent_id")
@click.option("--yes", is_flag=True, help="Skip confirmation")
@click.pass_context
def delete_agent(ctx: click.Context, agent_id: str, yes: bool) -> None:
    """Delete an agent."""
    if not yes:
        if not click.confirm(f"Delete agent {agent_id}?"):
            return
    client = EdacClientSync(ctx.obj["base_url"], api_key=ctx.obj.get("api_key"))
    try:
        result = client.delete_agent(agent_id)
        click.echo(f"Deleted: {result['status']}")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    finally:
        client.close()


@agents.command(name="restart")
@click.argument("agent_id")
@click.pass_context
def restart_agent(ctx: click.Context, agent_id: str) -> None:
    """Restart an agent."""
    client = EdacClientSync(ctx.obj["base_url"], api_key=ctx.obj.get("api_key"))
    try:
        a = client.restart_agent(agent_id)
        click.echo(f"Restarted: {a.agent_id} ({a.state})")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    finally:
        client.close()


@agents.command(name="pause")
@click.argument("agent_id")
@click.pass_context
def pause_agent(ctx: click.Context, agent_id: str) -> None:
    """Pause a running agent."""
    client = EdacClientSync(ctx.obj["base_url"], api_key=ctx.obj.get("api_key"))
    try:
        a = client.pause_agent(agent_id)
        click.echo(f"Paused: {a.agent_id} ({a.state})")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    finally:
        client.close()


@agents.command(name="resume")
@click.argument("agent_id")
@click.pass_context
def resume_agent(ctx: click.Context, agent_id: str) -> None:
    """Resume a paused agent."""
    client = EdacClientSync(ctx.obj["base_url"], api_key=ctx.obj.get("api_key"))
    try:
        a = client.resume_agent(agent_id)
        click.echo(f"Resumed: {a.agent_id} ({a.state})")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    finally:
        client.close()
