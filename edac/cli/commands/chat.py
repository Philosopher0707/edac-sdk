"""`edac chat` — Interactive conversational REPL.

Connects to a running EDAC server (or runs a local
standalone agent if --standalone is passed) for
multi-turn streaming chat sessions.
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import sys
import uuid
from typing import Optional

import click

logger = logging.getLogger("edac.cli.chat")


# ── ANSI helpers ────────────────────────────────────────────

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
CYAN = "\x1b[36m"
GREEN = "\x1b[32m"
MAGENTA = "\x1b[35m"
YELLOW = "\x1b[33m"


def _print_banner() -> None:
    click.echo(f"{BOLD}{CYAN}╔═══════════════════════════════════════╗{RESET}")
    click.echo(f"{BOLD}{CYAN}║     EDAC Chat — Interactive Agent     ║{RESET}")
    click.echo(f"{BOLD}{CYAN}╚═══════════════════════════════════════╝{RESET}")
    click.echo(f"  {DIM}Type your message and press Enter.{RESET}")
    click.echo(f"  {DIM}Commands: /clear  /history  /exit{RESET}")
    click.echo(f"  {DIM}Ctrl+C or /quit to exit.{RESET}\n")


# ── Standalone mode (local agent, no server) ───────────────────


async def _standalone_loop(
    model: Optional[str],
    provider: str,
    system_prompt: str,
    persona: Optional[str],
) -> None:
    """Run a local chat session with a standalone ChatAgent."""
    from edac.agent.handler_registry import HandlerRegistry
    from edac.agent.lifecycle import AgentConfig
    from edac.agent.runtime import AgentRuntime
    from edac.chat.agent import ChatAgent
    from edac.chat.store import ChatStore
    from edac.context.manager import ContextConfig, ContextManager
    from edac.event.bus import EventBus
    from edac.model import ChatMessage, ModelRegistry
    from edac.model.ollama import OllamaProvider

    bus = EventBus()
    await bus.start()
    runtime = AgentRuntime(bus)
    await runtime.start()

    registry = ModelRegistry()
    ollama = OllamaProvider()
    registry.register("ollama", ollama, fallback=True)

    ctx = ContextManager(registry=registry)

    chat_store = ChatStore()
    session = chat_store.create_session(
        agent_id="standalone",
        title="Standalone Chat",
    )

    agent = ChatAgent(
        agent_id="standalone",
        session_id=session.session_id,
        bus=bus,
        registry=registry,
        context_manager=ctx,
        settings={"system_prompt": system_prompt, "persona": persona},
    )

    # Register and spawn agent
    HandlerRegistry.get_default().register("standalone_chat", agent._run_factory)
    config = AgentConfig(
        name="standalone_chat",
        model=model,
        agent_type="chat",
        goal=" conversational agent",
    )
    runtime.spawn_future = asyncio.create_task(runtime.spawn(config))

    _print_banner()

    try:
        while True:
            try:
                text = click.prompt(f"{BOLD}{GREEN}You{RESET}", type=str).strip()
            except click.exceptions.Abort:
                break

            if not text:
                continue

            if text.startswith("/"):
                if text in ("/quit", "/exit"):
                    break
                if text == "/clear":
                    ctx.clear_window("standalone")
                    click.echo(f"  {DIM}Cleared conversation.{RESET}")
                    continue
                if text == "/history":
                    msgs = chat_store.get_messages(session.session_id)
                    click.echo(f"\n{DIM}--- History ---{RESET}")
                    for m in msgs:
                        role_color = GREEN if m.role == "user" else CYAN
                        click.echo(f"{role_color}{m.role}{RESET}: {m.content}")
                    click.echo(f"{DIM}---------------{RESET}\n")
                    continue
                click.echo(f"  {YELLOW}Unknown command: {text}{RESET}")
                continue

            chat_store.add_message(session.session_id, "user", text)

            # Stream response
            click.echo(f"{BOLD}{MAGENTA}Agent{RESET}: ", nl=False)

            # Build messages from window
            messages: list[ChatMessage] = []
            for entry in ctx.get_window("standalone").get_window():
                role = entry.role if entry.role in ("system", "user", "assistant", "tool") else "assistant"
                messages.append(ChatMessage(role=role, content=entry.content))
            messages.append(ChatMessage(role="user", content=text))

            prov = registry.get(provider) or await registry.get_default()
            full_response = ""
            if prov is not None:
                try:
                    async for chunk in prov.stream(messages, model=model):
                        if chunk.content:
                            click.echo(chunk.content, nl=False)
                            full_response += chunk.content
                        if chunk.finish_reason:
                            break
                except Exception as e:
                    click.echo(f"\n{YELLOW}Error: {e}{RESET}", err=True)
            click.echo("")

            if full_response:
                chat_store.add_message(session.session_id, "assistant", full_response)
                ctx.add_to_window("standalone", "assistant", full_response)

    except KeyboardInterrupt:
        pass
    finally:
        click.echo(f"\n{DIM}Shutting down...{RESET}")
        await runtime.stop()
        await bus.stop()


# ── Server-connected mode (via REST) ──────────────────────────


async def _server_loop(
    server: str,
    api_key: Optional[str],
    session_id: Optional[str],
    model: Optional[str],
    provider: str,
) -> None:
    """Run a chat session connected to an EDAC server via WebSocket."""
    import httpx
    import websockets

    headers = {}
    if api_key:
        headers["x-api-key"] = api_key

    # Create or resume a session via REST, then connect via WebSocket
    sid = session_id or str(uuid.uuid4())
    ws_url = server.replace("http", "ws").rstrip("/") + "/chat/sessions/" + sid + "/ws"

    _print_banner()
    click.echo(f"  {DIM}Connected to {server} (session: {sid[:8]}...){RESET}\n")

    try:
        async with websockets.connect(ws_url, additional_headers=headers) as ws:
            # Server auto-accepts — no join needed

            async def receive_task():
                while True:
                    raw = await ws.recv()
                    msg = json.loads(raw)
                    msg_type = msg.get("type")
                    if msg_type == "token":
                        click.echo(msg.get("text", ""), nl=False)
                    elif msg_type == "done":
                        click.echo("")
                        click.echo(f"{DIM}[turn complete]{RESET}")
                    elif msg_type == "error":
                        click.echo(f"\n{YELLOW}Error: {msg.get('message')}{RESET}", err=True)

            recv_task = asyncio.create_task(receive_task())

            while True:
                try:
                    text = click.prompt(f"{BOLD}{GREEN}You{RESET}", type=str).strip()
                except click.exceptions.Abort:
                    break

                if not text:
                    continue

                if text.startswith("/"):
                    if text in ("/quit", "/exit"):
                        break
                    if text == "/clear":
                        # Reset session via DELETE + recreate would be ideal, but for now
                        # just acknowledge — server will reset via new session on next connect
                        click.echo(f"  {DIM}Cleared conversation.{RESET}")
                        continue
                    if text == "/history":
                        # Fetch history via REST
                        resp = httpx.get(
                            f"{server}/chat/sessions/{sid}",
                            headers=headers,
                            timeout=10.0,
                        )
                        resp.raise_for_status()
                        msgs = resp.json().get("messages", [])
                        click.echo(f"\n{DIM}--- History ---{RESET}")
                        for m in msgs:
                            role_color = GREEN if m["role"] == "user" else CYAN
                            click.echo(f"{role_color}{m['role']}{RESET}: {m['content']}")
                        click.echo(f"{DIM}---------------{RESET}\n")
                        continue

                click.echo(f"{BOLD}{MAGENTA}Agent{RESET}: ", nl=False)
                await ws.send(json.dumps({
                    "action": "send",
                    "message": text,
                }))

            recv_task.cancel()
            try:
                await recv_task
            except asyncio.CancelledError:
                pass

    except websockets.exceptions.InvalidStatusCode as e:
        click.echo(f"\n{YELLOW}Connection failed ({e.status_code}):{RESET}", err=True)
        click.echo(f"  Is the EDAC server running at {server}?", err=True)
    except ConnectionRefusedError:
        click.echo(
            f"\n{YELLOW}Connection refused.{RESET} {DIM}Is the server running?{RESET}", err=True
        )
    except Exception as e:
        click.echo(f"\n{YELLOW}Connection error: {e}{RESET}", err=True)


# ── Click command ─────────────────────────────────────────────


@click.command()
@click.option("--server", default="http://localhost:8000", help="EDAC server URL")
@click.option("--standalone", is_flag=True, help="Run standalone (no server)")
@click.option("--model", default=None, help="Model to use")
@click.option("--provider", default="ollama", help="Model provider")
@click.option("--system-prompt", default="You are a helpful AI assistant.", help="System prompt")
@click.option("--persona", default=None, help="Persona name (e.g. 'coder', 'teacher')")
@click.option("--session-id", default=None, help="Resume an existing session")
@click.option("--api-key", default=None, help="API key for server authentication")
def chat(
    server: str,
    standalone: bool,
    model: Optional[str],
    provider: str,
    system_prompt: str,
    persona: Optional[str],
    session_id: Optional[str],
    api_key: Optional[str],
) -> None:
    """Interactive chat with an EDAC agent."""
    if standalone:
        asyncio.run(_standalone_loop(model, provider, system_prompt, persona))
    else:
        asyncio.run(_server_loop(server, api_key, session_id, model, provider))


@click.group()
def _chat_group() -> None:
    """Chat commands."""
    pass


@_chat_group.command(name="history")
@click.argument("session_id")
@click.option("--server", default="http://localhost:8000", help="EDAC server URL")
@click.option("--api-key", default=None, help="API key")
def chat_history(session_id: str, server: str, api_key: Optional[str]) -> None:
    """Show message history for a session."""
    import httpx

    headers = {}
    if api_key:
        headers["x-api-key"] = api_key

    try:
        resp = httpx.get(
            f"{server}/chat/{session_id}/history",
            headers=headers,
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        msgs = data.get("messages", [])
        if not msgs:
            click.echo(f"No messages in session {session_id}")
            return
        click.echo(f"Session {session_id} — {len(msgs)} messages:")
        for m in msgs:
            role = m["role"]
            content = m["content"]
            click.echo(f"  [{role}] {content[:200]}{'...' if len(content) > 200 else ''}")
    except httpx.HTTPStatusError as e:
        click.echo(f"Error: {e.response.status_code} — {e.response.text}", err=True)
    except httpx.ConnectError:
        click.echo(f"Cannot connect to {server}. Is the server running?", err=True)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
