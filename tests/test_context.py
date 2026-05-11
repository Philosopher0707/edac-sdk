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

    def test_compression_uses_count_based_summary(self):
        """Summary should report counts, not concatenate raw texts."""
        mem = ShortTermMemory(max_tokens=100)
        mem.add(WindowEntry(role="system", content="sys", tokens=5))
        for i in range(10):
            mem.add(WindowEntry(role="assistant", content=f"entry {i}", tokens=10))
        comp = ContextCompressor(target_tokens=30)
        result = comp.compress(mem)
        summary = next(
            (e for e in result.get_window() if e.content.startswith("[Earlier context summarized]")),
            None,
        )
        assert summary is not None
        # Should contain count, not raw text concatenation
        assert "assistant" in summary.content.lower()
        assert "10" in summary.content or "assistant" in summary.content
        # Should NOT contain the raw entries
        assert "entry 0" not in summary.content

    def test_compression_includes_topics(self):
        """Summary should include extracted topics from content."""
        mem = ShortTermMemory(max_tokens=100)
        for _ in range(5):
            mem.add(WindowEntry(role="assistant", content="calculating physics and astronomy", tokens=10))
        comp = ContextCompressor(target_tokens=30)
        result = comp.compress(mem)
        summary = next(
            (e for e in result.get_window() if e.content.startswith("[Earlier context summarized]")),
            None,
        )
        assert summary is not None
        assert "physics" in summary.content.lower() or "astronomy" in summary.content.lower()

    def test_compression_merges_existing_summaries(self):
        """Old summary entries should have their counts merged into new summary."""
        mem = ShortTermMemory(max_tokens=10000)
        mem.add(WindowEntry(role="system", content="sys", tokens=5))
        # First batch
        for i in range(8):
            mem.add(WindowEntry(role="assistant", content=f"first batch {i}", tokens=10))
        # Simulate existing summary (as would happen after a prior compression)
        mem.add(WindowEntry(role="system", content="[Earlier context summarized] 3 assistant", tokens=5))
        # Second batch
        for i in range(8):
            mem.add(WindowEntry(role="assistant", content=f"second batch {i}", tokens=10))
        comp = ContextCompressor(target_tokens=30)
        result = comp.compress(mem)
        # Should only have one system summary, not two
        summaries = [e for e in result.get_window() if e.content.startswith("[Earlier context summarized]")]
        assert len(summaries) == 1
        content = summaries[0].content
        # Should have merged counts from old summary (3) + first batch (8)
        # + second batch (8 minus ~1 kept recent) = ~18 or 19 total
        assert any(c in content for c in ("18", "19"))
        assert "assistant" in content
        # The original system prompt should survive
        assert any(e.content == "sys" for e in result.get_window())

    def test_compression_preserves_most_recent_droppable(self):
        """Most recent droppable entries should survive within remaining budget."""
        mem = ShortTermMemory(max_tokens=10000)
        mem.add(WindowEntry(role="system", content="sys", tokens=5))
        for i in range(20):
            mem.add(WindowEntry(role="assistant", content=str(i), tokens=10))
        comp = ContextCompressor(target_tokens=50)
        result = comp.compress(mem)
        # The most recent entries should be in the window
        entries = result.get_window()
        content_list = [e.content for e in entries]
        assert "sys" in content_list
        assert "19" in content_list  # most recent should survive

    def test_compression_no_unbounded_growth(self):
        """Repeated compression should not create unbounded system messages."""
        comp = ContextCompressor(target_tokens=50)
        mem = ShortTermMemory(max_tokens=10000)
        mem.add(WindowEntry(role="system", content="sys", tokens=5))
        for i in range(20):
            mem.add(WindowEntry(role="assistant", content=f"entry {i}", tokens=10))

        # First compression
        mem = comp.compress(mem)
        assert sum(1 for e in mem.get_window() if e.content.startswith("[Earlier context summarized]")) == 1

        # Add more and compress again
        for i in range(10):
            mem.add(WindowEntry(role="assistant", content=f"second batch {i}", tokens=10))
        mem = comp.compress(mem)
        assert sum(1 for e in mem.get_window() if e.content.startswith("[Earlier context summarized]")) == 1

        # Third round
        for i in range(10):
            mem.add(WindowEntry(role="assistant", content=f"third batch {i}", tokens=10))
        mem = comp.compress(mem)
        assert sum(1 for e in mem.get_window() if e.content.startswith("[Earlier context summarized]")) == 1

    def test_compression_respects_target_tokens_strictly(self):
        """After compression, total tokens must always be <= target_tokens."""
        mem = ShortTermMemory(max_tokens=10000)
        for i in range(50):
            mem.add(WindowEntry(role="assistant", content=f"x" * 100, tokens=25))
        comp = ContextCompressor(target_tokens=30)
        result = comp.compress(mem)
        assert result.total_tokens() <= 30

    def test_compression_critical_entries_preserved(self):
        """User, human, and original system entries are never dropped."""
        mem = ShortTermMemory(max_tokens=10000)
        mem.add(WindowEntry(role="system", content="sys prompt", tokens=5))
        mem.add(WindowEntry(role="user", content="user msg", tokens=5))
        mem.add(WindowEntry(role="human", content="human msg", tokens=5))
        for i in range(20):
            mem.add(WindowEntry(role="assistant", content=f"entry {i}", tokens=10))
        comp = ContextCompressor(target_tokens=30)
        result = comp.compress(mem)
        contents = [e.content for e in result.get_window()]
        assert "sys prompt" in contents
        assert "user msg" in contents
        assert "human msg" in contents


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

    # ── Compression Integration Tests ──

    def test_compression_triggered_when_window_over_budget(self):
        """When adding to window causes it to exceed budget, compression runs."""
        cm = ContextManager(config=ContextConfig(
            max_tokens_per_agent=1000,
            compression_trigger_tokens=50,
        ))
        # Fill the window with non-system entries to trigger compression
        for i in range(20):
            cm.add_to_window("a1", "assistant", f"entry {i}", tokens=10)
        window = cm.get_window("a1")
        # After compression, window should be <= max_tokens
        assert window.total_tokens() <= 100
        # System summary entry should have been added
        entries = window.get_window()
        assert any(e.role == "system" for e in entries)

    def test_compression_skipped_when_under_budget(self):
        """When window stays under budget, no summarization happens."""
        cm = ContextManager(config=ContextConfig(max_tokens_per_agent=1000))
        cm.add_to_window("a1", "user", "hello", tokens=2)
        cm.add_to_window("a1", "assistant", "hi", tokens=2)
        window = cm.get_window("a1")
        entries = window.get_window()
        assert len(entries) == 2
        assert not any(e.role == "system" for e in entries)

    def test_system_prompts_preserved_under_compression(self):
        """System and human entries survive compression."""
        cm = ContextManager(config=ContextConfig(
            max_tokens_per_agent=1000,
            compression_trigger_tokens=50,
        ))
        cm.add_to_window("a1", "system", "You are helpful", tokens=5)
        for i in range(20):
            cm.add_to_window("a1", "assistant", f"entry {i}", tokens=10)
        window = cm.get_window("a1")
        entries = window.get_window()
        assert any(e.content == "You are helpful" for e in entries)
