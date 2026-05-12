"""CLI subcommands for event streaming."""

from __future__ import annotations

import json
import signal
import sys
from typing import Optional

import click

from edac.client.sync_client import EdacClientSync


@click.group(name="events")
@click.option("--base-url", default="http://localhost:8000", help="EDAC server URL")
@click.option("--api-key", default=None, help="API key for authentication")
@click.pass_context
def events(ctx: click.Context, base_url: str, api_key: Optional[str]) -> None:
    """Stream and inspect events."""
    ctx.ensure_object(dict)
    ctx.obj["base_url"] = base_url
    ctx.obj["api_key"] = api_key


@events.command(name="follow")
@click.option("--topics", default=None, help="Comma-separated topic patterns")
@click.option("--timeout", type=float, default=30.0, help="SSE read timeout")
@click.option("--count", type=int, default=None, help="Stop after N events")
@click.pass_context
def follow_events(
    ctx: click.Context, topics: Optional[str], timeout: float, count: Optional[int]
) -> None:
    """Follow the SSE event stream."""
    topic_list = topics.split(",") if topics else None
    client = EdacClientSync(ctx.obj["base_url"], api_key=ctx.obj.get("api_key"))

    # Graceful exit on SIGINT
    interrupted = False

    def _on_sigint(signum, frame):  # type: ignore[unused-argument]
        nonlocal interrupted
        interrupted = True

    signal.signal(signal.SIGINT, _on_sigint)

    n = 0
    try:
        for evt in client.stream_events(topics=topic_list, timeout=timeout):
            click.echo(json.dumps(evt, indent=2))
            n += 1
            if interrupted or (count is not None and n >= count):
                break
    except KeyboardInterrupt:
        pass
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    finally:
        client.close()


@events.command(name="watch")
@click.argument("task_id")
@click.option("--timeout", type=float, default=30.0, help="WebSocket read timeout")
@click.option("--count", type=int, default=None, help="Stop after N updates")
@click.pass_context
def watch_task(ctx: click.Context, task_id: str, timeout: float, count: Optional[int]) -> None:
    """Watch real-time updates for a task via WebSocket."""
    client = EdacClientSync(ctx.obj["base_url"], api_key=ctx.obj.get("api_key"))

    interrupted = False

    def _on_sigint(signum, frame):  # type: ignore[unused-argument]
        nonlocal interrupted
        interrupted = True

    signal.signal(signal.SIGINT, _on_sigint)

    n = 0
    try:
        for update in client.watch_task(task_id, timeout=timeout):
            click.echo(json.dumps(update.model_dump(), indent=2))
            n += 1
            if interrupted or (count is not None and n >= count):
                break
    except KeyboardInterrupt:
        pass
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    finally:
        client.close()
