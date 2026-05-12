"""Click subcommands for webhook management."""

from __future__ import annotations

import click
import httpx


@click.group(name="webhooks")
def webhooks() -> None:
    """Manage webhook callbacks for task events."""
    pass


@webhooks.command(name="register")
@click.argument("task_id")
@click.option("--url", required=True, help="Webhook URL to POST to")
@click.option("--events", default="task.completed,task.failed", help="Comma-separated event types")
@click.option("--server", default="http://localhost:8000", help="Server URL")
@click.option("--api-key", default=None, help="API key for authentication")
def register_webhook(task_id: str, url: str, events: str, server: str, api_key: str) -> None:
    """Register a webhook for a task."""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    try:
        resp = httpx.post(
            f"{server}/tasks/{task_id}/webhooks",
            json={"url": url, "events": events.split(",")},
            headers=headers,
        )
        resp.raise_for_status()
        click.echo(f"Webhook registered for task {task_id}: {url}")
    except Exception as e:
        click.echo(f"Failed to register webhook: {e}", err=True)


@webhooks.command(name="list")
@click.argument("task_id")
@click.option("--server", default="http://localhost:8000", help="Server URL")
@click.option("--api-key", default=None, help="API key for authentication")
def list_webhooks(task_id: str, server: str, api_key: str) -> None:
    """List webhooks registered for a task."""
    headers = {}
    if api_key:
        headers["X-API-Key"] = api_key
    try:
        resp = httpx.get(f"{server}/tasks/{task_id}/webhooks", headers=headers)
        resp.raise_for_status()
        data = resp.json()
        configs = data.get("webhooks", [])
        if not configs:
            click.echo(f"No webhooks for task {task_id}.")
            return
        click.echo(f"Webhooks for task {task_id}:")
        for c in configs:
            events = ", ".join(c.get("events", []))
            click.echo(f"  {c['url']} ({events})")
    except Exception as e:
        click.echo(f"Failed to list webhooks: {e}", err=True)


@webhooks.command(name="delete")
@click.argument("task_id")
@click.option("--url", default=None, help="Specific webhook URL to remove (optional)")
@click.option("--server", default="http://localhost:8000", help="Server URL")
@click.option("--api-key", default=None, help="API key for authentication")
def delete_webhook(task_id: str, url: str, server: str, api_key: str) -> None:
    """Delete webhooks for a task."""
    headers = {}
    if api_key:
        headers["X-API-Key"] = api_key
    try:
        params = {}
        if url:
            params["url"] = url
        resp = httpx.delete(f"{server}/tasks/{task_id}/webhooks", headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()
        click.echo(f"Deleted: {data.get('removed', 0)} webhook(s) for task {task_id}")
    except Exception as e:
        click.echo(f"Failed to delete webhook: {e}", err=True)
