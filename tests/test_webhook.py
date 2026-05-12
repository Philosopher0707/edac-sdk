"""Tests for webhook delivery system."""

from __future__ import annotations

import pytest

from edac.server.webhook import WebhookConfig, WebhookDelivery, WebhookDispatcher, TaskWebhookRegistry


class TestWebhookRegistry:
    def test_register_and_get(self):
        reg = TaskWebhookRegistry()
        cfg = WebhookConfig(url="https://example.com/hook")
        reg.register("task-1", cfg)
        assert len(reg.get("task-1")) == 1
        assert reg.get("task-1")[0].url == "https://example.com/hook"

    def test_remove_by_url(self):
        reg = TaskWebhookRegistry()
        reg.register("task-1", WebhookConfig(url="https://a.com"))
        reg.register("task-1", WebhookConfig(url="https://b.com"))
        removed = reg.remove("task-1", url="https://a.com")
        assert removed is True
        assert len(reg.get("task-1")) == 1
        assert reg.get("task-1")[0].url == "https://b.com"

    def test_remove_all(self):
        reg = TaskWebhookRegistry()
        reg.register("task-1", WebhookConfig(url="https://a.com"))
        removed = reg.remove("task-1")
        assert removed is True
        assert len(reg.get("task-1")) == 0

    def test_list_all(self):
        reg = TaskWebhookRegistry()
        reg.register("task-1", WebhookConfig(url="https://a.com"))
        reg.register("task-2", WebhookConfig(url="https://b.com"))
        all_hooks = reg.list_all()
        assert len(all_hooks) == 2


class TestWebhookDispatcher:
    @pytest.mark.asyncio
    async def test_deliver_success(self):
        """Webhook delivery succeeds on 2xx."""
        dispatcher = WebhookDispatcher(max_retries=1)
        cfg = WebhookConfig(url="https://example.com/hook")

        # Mock httpx post
        class FakeResponse:
            status_code = 200

        async def fake_post(*args, **kwargs):
            return FakeResponse()

        dispatcher._client.post = fake_post  # type: ignore[assignment]

        delivery = await dispatcher.deliver(cfg, "task-1", {"status": "completed"})
        assert delivery.response_status == 200
        assert delivery.error is None
        assert delivery.attempt == 1
        await dispatcher.close()

    @pytest.mark.asyncio
    async def test_deliver_retries_on_500(self):
        """Webhook retries on 5xx then succeeds."""
        dispatcher = WebhookDispatcher(max_retries=3, backoff_base=0.01)
        cfg = WebhookConfig(url="https://example.com/hook")

        calls = []

        class Fake500:
            status_code = 500

        class Fake200:
            status_code = 200

        async def fake_post(*args, **kwargs):
            calls.append(len(calls))
            if len(calls) == 1:
                return Fake500()
            return Fake200()

        dispatcher._client.post = fake_post  # type: ignore[assignment]

        delivery = await dispatcher.deliver(cfg, "task-1", {"status": "completed"})
        assert delivery.response_status == 200
        assert delivery.attempt == 2
        await dispatcher.close()

    @pytest.mark.asyncio
    async def test_deliver_exhausts_retries(self):
        """Webhook exhausts retries and reports failure."""
        dispatcher = WebhookDispatcher(max_retries=2, backoff_base=0.01)
        cfg = WebhookConfig(url="https://example.com/hook")

        class Fake500:
            status_code = 500

        async def fake_post(*args, **kwargs):
            return Fake500()

        dispatcher._client.post = fake_post  # type: ignore[assignment]

        delivery = await dispatcher.deliver(cfg, "task-1", {"status": "failed"})
        assert delivery.response_status == 500
        assert delivery.error is not None
        assert delivery.attempt == 2
        await dispatcher.close()

    @pytest.mark.asyncio
    async def test_deliver_network_error(self):
        """Webhook reports error on network failure."""
        dispatcher = WebhookDispatcher(max_retries=1)
        cfg = WebhookConfig(url="https://example.com/hook")

        async def fake_post(*args, **kwargs):
            raise ConnectionError("network failed")

        dispatcher._client.post = fake_post  # type: ignore[assignment]

        delivery = await dispatcher.deliver(cfg, "task-1", {"status": "completed"})
        assert delivery.error is not None
        assert "network failed" in delivery.error
        await dispatcher.close()


class TestWebhookSchemas:
    def test_webhook_config_model(self):
        from edac.server.schemas import WebhookConfig as SchemaWebhookConfig

        cfg = SchemaWebhookConfig(url="https://example.com", events=["task.completed"])
        assert cfg.url == "https://example.com"
        assert cfg.events == ["task.completed"]

    def test_webhook_delivery_model(self):
        from edac.server.schemas import WebhookDelivery as SchemaWebhookDelivery

        d = SchemaWebhookDelivery(
            url="https://example.com",
            task_id="t-1",
            status="completed",
            payload={"ok": True},
            attempt=1,
            response_status=200,
        )
        assert d.url == "https://example.com"
        assert d.response_status == 200
