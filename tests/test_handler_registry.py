"""Tests for the sync, import-safe HandlerRegistry."""

from __future__ import annotations

import pytest

from edac.agent.handler_registry import HandlerRegistry


class TestHandlerRegistry:
    """Core unit tests for the handler registry."""

    def test_get_default_returns_singleton(self):
        r1 = HandlerRegistry.get_default()
        r2 = HandlerRegistry.get_default()
        assert r1 is r2

    def test_register_and_get(self):
        reg = HandlerRegistry()

        def factory(a):
            pass

        reg.register("worker", factory)
        assert reg.get("worker") is factory

    def test_get_missing_returns_none(self):
        reg = HandlerRegistry()
        assert reg.get("nonexistent") is None

    def test_list_returns_keys(self):
        reg = HandlerRegistry()
        reg.register("a", lambda x: x)
        reg.register("b", lambda y: y)
        assert sorted(reg.list()) == ["a", "b"]

    def test_list_empty(self):
        reg = HandlerRegistry()
        assert reg.list() == []

    def test_unregister_removes_entry(self):
        reg = HandlerRegistry()
        reg.register("tmp", lambda x: x)
        removed = reg.unregister("tmp")
        assert removed is not None
        assert reg.get("tmp") is None

    def test_unregister_missing_returns_none(self):
        reg = HandlerRegistry()
        assert reg.unregister("missing") is None

    def test_replace_existing_handler_raises(self):
        reg = HandlerRegistry()

        def first(a):
            pass

        def second(a):
            pass

        reg.register("worker", first)
        with pytest.raises(ValueError, match="already registered"):
            reg.register("worker", second)

    def test_reregister_same_factory_is_idempotent(self):
        reg = HandlerRegistry()

        def factory(a):
            pass

        reg.register("worker", factory)
        reg.register("worker", factory)  # should not raise
        assert reg.get("worker") is factory
        assert reg.list() == ["worker"]

    def test_clear(self):
        reg = HandlerRegistry()
        reg.register("a", lambda x: x)
        reg.register("b", lambda y: y)
        assert sorted(reg.list()) == ["a", "b"]
        reg.clear()
        assert reg.list() == []

    def test_registry_isolated_instances(self):
        reg_a = HandlerRegistry()
        reg_b = HandlerRegistry()
        reg_a.register("x", lambda: None)
        assert reg_b.get("x") is None


class TestHandlerRegistrySingletonIsolation:
    """Ensure that tests do not pollute the global singleton."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset the global singleton before/after each test."""
        old = HandlerRegistry._DEFAULT
        HandlerRegistry._DEFAULT = None
        yield
        HandlerRegistry._DEFAULT = old

    def test_singleton_fresh_per_test(self):
        reg = HandlerRegistry.get_default()
        reg.register("test", lambda: None)
        # After reset (done in fixture teardown), a new test sees a fresh instance.
