"""ChatAgent — conversational agent that listens for human input and streams LLM responses.

Uses the EventBus for all communication:
  Input:  chat.{session_id}.input   (HUMAN_MESSAGE events)
  Output: chat.{session_id}.output  (MODALITY_TEXT events, streamed)

The agent maintains its own ShortTermMemory window via ContextManager.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Dict, List, Optional

from edac.agent.lifecycle import AgentConfig, AgentInstance
from edac.context.manager import ContextManager
from edac.event.bus import EventBus
from edac.event.schema import (
    Event,
    EventPriority,
    EventType,
    create_event,
)
from edac.model import ChatMessage, ModelRegistry, StreamingChunk

logger = logging.getLogger("edac.chat.agent")


class ChatAgent:
    """Conversational agent factory.

    Spawned by AgentRuntime. Subscribes to input events on the bus and
    emits streamed response tokens back.
    """

    def __init__(
        self,
        agent_instance: AgentInstance,
        bus: EventBus,
        registry: ModelRegistry,
        ctx: ContextManager,
        session_id: Optional[str] = None,
        system_prompt: Optional[str] = None,
        provider: str = "ollama",
        model: Optional[str] = None,
    ):
        self.instance = agent_instance
        self.bus = bus
        self.registry = registry
        self.ctx = ctx
        self.session_id = session_id or str(uuid.uuid4())
        self.agent_id = agent_instance.state.agent_id
        self.system_prompt = system_prompt or (
            "You are a helpful assistant integrated into EDAC, "
            "an event-driven agentic core. Be concise and accurate."
        )
        self.provider = provider
        self.model = model

        # ensure the system prompt is in the window
        self.ctx.add_to_window(self.agent_id, "system", self.system_prompt)

    async def run(self) -> None:
        """Main loop: subscribe to input topic and process messages."""
        input_topic = f"chat.{self.session_id}.input"
        logger.info(
            "ChatAgent %s started (session=%s, topic=%s)",
            self.agent_id,
            self.session_id,
            input_topic,
        )

        subscription = self.bus.subscribe(
            self._on_input,
            topics=[input_topic],
            event_types=[EventType.HUMAN_MESSAGE],
        )

        try:
            # run until agent is terminated
            while self.instance.state.state not in (
                "terminated",
                "error",
            ):
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            logger.info("ChatAgent %s cancelled", self.agent_id)
            raise
        finally:
            self.bus.unsubscribe(subscription)
            logger.info("ChatAgent %s stopped", self.agent_id)

    async def _on_input(self, event: Event) -> None:
        """Handle a single human message."""
        text = event.payload.get("text", "")
        if not text:
            return

        logger.debug("ChatAgent %s received: %.40s...", self.agent_id, text)

        # add user message to context window
        self.ctx.add_to_window(self.agent_id, "user", text)

        # build messages for the LLM
        messages = self._build_messages()

        # stream the response, emitting tokens as events
        full_response = ""
        output_topic = f"chat.{self.session_id}.output"

        try:
            async for chunk in self.registry.stream(
                self.provider,
                messages,
                model=self.model,
                fallback=True,
            ):
                token = chunk.content
                if token:
                    full_response += token
                    # emit each token as a modality.text event
                    await self.bus.emit(
                        create_event(
                            event_type=EventType.MODALITY_TEXT,
                            source=f"agent:{self.agent_id}",
                            topic=output_topic,
                            correlation_id=event.correlation_id,
                            payload={
                                "text": token,
                                "role": "assistant",
                                "session_id": self.session_id,
                                "delta": True,
                            },
                            priority=EventPriority.HIGH,
                        )
                    )

            # emit final complete message
            await self.bus.emit(
                create_event(
                    event_type=EventType.MODALITY_TEXT,
                    source=f"agent:{self.agent_id}",
                    topic=output_topic,
                    correlation_id=event.correlation_id,
                    payload={
                        "text": full_response,
                        "role": "assistant",
                        "session_id": self.session_id,
                        "delta": False,
                        "done": True,
                    },
                    priority=EventPriority.NORMAL,
                )
            )

            # store in context window
            self.ctx.add_to_window(self.agent_id, "assistant", full_response)

        except Exception as e:
            logger.error("ChatAgent %s stream error: %s", self.agent_id, e)
            await self.bus.emit(
                create_event(
                    event_type=EventType.SYSTEM_ERROR,
                    source=f"agent:{self.agent_id}",
                    topic=output_topic,
                    correlation_id=event.correlation_id,
                    payload={
                        "error": str(e),
                        "session_id": self.session_id,
                    },
                    priority=EventPriority.CRITICAL,
                )
            )

    def _build_messages(self) -> List[ChatMessage]:
        """Build ChatMessage list from the agent's short-term window."""
        messages: List[ChatMessage] = []
        window = self.ctx.get_window(self.agent_id)
        for entry in window.get_window():
            role = entry.role
            if role == "system":
                messages.append(ChatMessage(role="system", content=entry.content))
            elif role == "user":
                messages.append(ChatMessage(role="user", content=entry.content))
            elif role == "assistant":
                messages.append(ChatMessage(role="assistant", content=entry.content))
            else:
                messages.append(ChatMessage(role="user", content=entry.content))
        return messages

    async def send_message(self, text: str) -> None:
        """Programmatically inject a user message (used by server/CLI)."""
        await self.bus.emit(
            create_event(
                event_type=EventType.HUMAN_MESSAGE,
                source="human:user",
                topic=f"chat.{self.session_id}.input",
                payload={"text": text, "session_id": self.session_id},
                priority=EventPriority.HIGH,
            )
        )


# ── Factory for @agent decorator ────────────────────────────────────────────

async def chat_agent_factory(agent: AgentInstance) -> None:
    """Factory coroutine for AgentRuntime.spawn().

    Reads agent config to extract session_id, provider, model, etc.
    """
    config = agent.config
    bus: EventBus = agent.runtime.bus  # type: ignore[attr-defined]
    ctx: ContextManager = agent.runtime.ctx  # type: ignore[attr-defined]
    registry: ModelRegistry = agent.runtime.registry  # type: ignore[attr-defined]

    session_id = config.goal if config.goal else None  # reuse goal as session_id
    provider = config.model or "ollama"
    model_name = None

    chat = ChatAgent(
        agent_instance=agent,
        bus=bus,
        registry=registry,
        ctx=ctx,
        session_id=session_id,
        provider=provider,
        model=model_name,
    )
    await chat.run()
