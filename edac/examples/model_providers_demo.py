"""Model Providers Demo — Ollama + Cloud models.

Demonstrates:
- Ollama local inference
- Anthropic Claude (cloud)
- OpenAI GPT (cloud)
- Automatic fallback between providers
- Token budget tracking across providers

Usage:
    # Terminal 1: start ollama
    ollama serve

    # Terminal 2: run demo
    PYTHONPATH=/Users/philosopher/Documents python3 examples/model_providers_demo.py

Required env vars for cloud:
    export ANTHROPIC_API_KEY=sk-ant-...
    export OPENAI_API_KEY=sk-...
"""

from __future__ import annotations

import asyncio
import logging
import os

from edac.model import ChatMessage, ModelRegistry
from edac.model.ollama import OllamaProvider
from edac.model.anthropic import AnthropicProvider
from edac.model.openai import OpenAIProvider
from edac.context.manager import ContextManager, ContextConfig

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("edac.examples.model_providers")


async def demo_ollama(registry: ModelRegistry) -> None:
    """Demo local Ollama inference."""
    provider = registry.get("ollama")
    if not provider or not await provider.is_available():
        logger.warning("Ollama not available — skipping local demo")
        return

    logger.info("=== Ollama Demo ===")
    messages = [
        ChatMessage(role="system", content="You are a helpful coding assistant."),
        ChatMessage(role="user", content="Write a Python function to reverse a string."),
    ]

    try:
        result = await provider.chat(messages, model="llama3.2")
        logger.info(f"Model: {result.model}")
        logger.info(f"Response: {result.content[:200]}...")
        logger.info(f"Usage: {result.usage}")
    except Exception as e:
        logger.error(f"Ollama error: {e}")


async def demo_anthropic(registry: ModelRegistry) -> None:
    """Demo Anthropic Claude."""
    provider = registry.get("anthropic")
    if not provider or not await provider.is_available():
        logger.warning("Anthropic not available — skipping cloud demo")
        return

    logger.info("=== Anthropic Demo ===")
    messages = [
        ChatMessage(role="user", content="Explain quantum computing in 3 sentences."),
    ]

    try:
        result = await provider.chat(messages, model="claude-sonnet-4-6")
        logger.info(f"Model: {result.model}")
        logger.info(f"Response: {result.content[:200]}...")
        logger.info(f"Usage: {result.usage}")
    except Exception as e:
        logger.error(f"Anthropic error: {e}")


async def demo_openai(registry: ModelRegistry) -> None:
    """Demo OpenAI GPT."""
    provider = registry.get("openai")
    if not provider or not await provider.is_available():
        logger.warning("OpenAI not available — skipping cloud demo")
        return

    logger.info("=== OpenAI Demo ===")
    messages = [
        ChatMessage(role="user", content="What is the capital of France?"),
    ]

    try:
        result = await provider.chat(messages, model="gpt-4o")
        logger.info(f"Model: {result.model}")
        logger.info(f"Response: {result.content[:200]}...")
        logger.info(f"Usage: {result.usage}")
    except Exception as e:
        logger.error(f"OpenAI error: {e}")


async def demo_context_manager(registry: ModelRegistry) -> None:
    """Demo ContextManager with model routing."""
    logger.info("=== Context Manager + Model Routing ===")

    ctx = ContextManager(
        config=ContextConfig(
            default_provider="ollama",
            cheap_model="llama3.2",
            default_model="llama3.2",
            premium_model="claude-opus-4-7",
        ),
        registry=registry,
    )

    # Check available providers
    available = await registry.get_available()
    logger.info(f"Available providers: {available}")

    if not available:
        logger.warning("No providers available")
        return

    # Chat via context manager (auto-routes to default provider)
    try:
        response = await ctx.chat(
            agent_id="demo-agent",
            prompt="Write a haiku about Python programming.",
            system_prompt="You are a creative writer.",
        )
        logger.info(f"Context manager response: {response[:200]}...")
    except ValueError as e:
        logger.warning(f"Context manager chat skipped: {e}")
    except Exception as e:
        logger.error(f"Context manager error: {e}")

    stats = ctx.get_stats()
    logger.info(f"Stats: {stats}")


async def main() -> None:
    # ── Setup Registry ──
    registry = ModelRegistry()

    # Register Ollama (local, default)
    ollama = OllamaProvider(
        base_url="http://localhost:11434",
        default_model="llama3.2",
    )
    registry.register("ollama", ollama, fallback=True)

    # Register Anthropic (cloud)
    anthropic = AnthropicProvider()
    registry.register("anthropic", anthropic)

    # Register OpenAI (cloud)
    openai = OpenAIProvider()
    registry.register("openai", openai)

    # ── Run Demos ──
    await demo_ollama(registry)
    await demo_anthropic(registry)
    await demo_openai(registry)
    await demo_context_manager(registry)

    # ── Cleanup ──
    await ollama.close()
    await anthropic.close()
    await openai.close()

    logger.info("Demo complete")


if __name__ == "__main__":
    asyncio.run(main())
