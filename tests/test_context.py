"""Tests for context manager and related components."""

import pytest

from edac.context.budget import BudgetTracker
from edac.context.compressor import ContextCompressor
from edac.context.router import ModelRouter
from edac.context.manager import ContextManager, ContextConfig
from edac.memory.short_term import ShortTermMemory, WindowEntry


class TestBudgetTracker:
    def test_consume_and_usage(self):
        bt = BudgetTracker(agent_limit=100)
        bt.consume("agent-1", 30)
        assert bt.agent_usage("agent-1") == 30
        assert bt.agent_remaining("agent-1") == 70

    def test_over_budget(self):
        bt = BudgetTracker(agent_limit=10)
        bt.consume("agent-1", 10)
        assert bt.is_over_budget("agent-1")

    def test_reset(self):
        bt = BudgetTracker()
        bt.consume("agent-1", 10)
        bt.reset_agent("agent-1")
        assert bt.agent_usage("agent-1") == 0

    def test_snapshot(self):
        bt = BudgetTracker()
        bt.consume("agent-1", 10)
        s = bt.snapshot()
        assert s["agent_usages"]["agent-1"] == 10


class TestContextCompressor:
    def test_no_compress_when_under_budget(self):
        mem = ShortTermMemory(max_tokens=1000)
        mem.add_text("user", "hello")
        comp = ContextCompressor(target_tokens=500)
        result = comp.compress(mem)
        assert len(result.get_window()) == 1

    def test_compress_drops_old(self):
        mem = ShortTermMemory(max_tokens=100)
        mem.add(WindowEntry(role="system", content="sys", tokens=10))
        for i in range(20):
            mem.add(WindowEntry(role="assistant", content=str(i), tokens=10))
        comp = ContextCompressor(target_tokens=50)
        result = comp.compress(mem)
        assert result.total_tokens() <= 50


class TestModelRouter:
    def test_route_simple(self):
        router = ModelRouter()
        r = router.route(1000, "simple")
        assert r.model == router.cheap_model

    def test_route_hard(self):
        router = ModelRouter()
        r = router.route(10000, "hard")
        assert r.model == router.premium_model

    def test_route_default(self):
        router = ModelRouter()
        r = router.route(5000, "normal")
        assert r.model == router.default_model


class TestContextManager:
    def test_get_window(self):
        cm = ContextManager()
        w = cm.get_window("agent-1")
        assert w is not None

    def test_add_to_window(self):
        cm = ContextManager()
        cm.add_to_window("agent-1", "user", "hello", tokens=2)
        assert "hello" in cm.get_context("agent-1")

    def test_select_model(self):
        cm = ContextManager(ContextConfig(cheap_model_threshold=100))
        m = cm.select_model("agent-1", "simple")
        assert m == cm.config.cheap_model

    def test_clear(self):
        cm = ContextManager()
        cm.add_to_window("agent-1", "user", "hello")
        cm.clear("agent-1")
        assert cm.get_context("agent-1") == ""

    def test_stats(self):
        cm = ContextManager()
        stats = cm.get_stats()
        assert "budget" in stats
        assert "active_windows" in stats

    @pytest.mark.asyncio
    async def test_chat_includes_window_history(self):
        from edac.model import ChatCompletion, ChatMessage, ModelRegistry, ModelProvider

        class MockProvider(ModelProvider):
            def __init__(self):
                self.last_messages = []

            @property
            def name(self):
                return "mock"

            def is_available(self):
                return True

            async def chat(self, messages, **kwargs):
                self.last_messages = messages
                return ChatCompletion(content="ok", model="mock")

            async def stream(self, messages, **kwargs):
                pass

            async def close(self):
                pass

        registry = ModelRegistry()
        mock = MockProvider()
        registry.register("mock", mock)
        cm = ContextManager(registry=registry, config=ContextConfig(default_provider="mock"))
        cm.add_to_window("a1", "user", "hello", tokens=1)
        result = await cm.chat("a1", prompt="world", provider="mock")
        assert result == "ok"
        assert len(mock.last_messages) == 2  # history + prompt
        assert mock.last_messages[0].role == "user"
        assert mock.last_messages[0].content == "hello"
        assert mock.last_messages[1].role == "user"
        assert mock.last_messages[1].content == "world"
