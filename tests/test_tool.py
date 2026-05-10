"""Tests for tool registry, MCP client, skill loader, and sandbox."""

import asyncio
import tempfile
from pathlib import Path

import pytest

from edac.tool.registry import ToolRegistry, ToolSpec, ToolNotFound, ToolTimeout
from edac.tool.mcp import MCPClient
from edac.tool.skill import SkillLoader, Skill
from edac.tool.sandbox import Sandbox, SandboxConfig, SandboxPool


class TestToolRegistry:
    def test_register_and_get(self):
        reg = ToolRegistry()
        spec = ToolSpec(name="echo", description="echoes input", parameters={"msg": {"type": "string"}})
        async def handler(msg):
            return msg
        reg.register(spec, handler)
        assert reg.has("echo")
        assert reg.get("echo").spec.name == "echo"

    def test_search(self):
        reg = ToolRegistry()
        reg.register(ToolSpec(name="echo", description="echoes"), lambda: None)
        reg.register(ToolSpec(name="add", description="adds numbers"), lambda: None)
        results = reg.search("echo")
        assert len(results) == 1
        assert results[0].name == "echo"

    @pytest.mark.asyncio
    async def test_execute_success(self):
        reg = ToolRegistry()
        async def double(x):
            return x * 2
        reg.register(ToolSpec(name="double", description="d"), double)
        result = await reg.execute("double", {"x": 3})
        assert result == 6

    @pytest.mark.asyncio
    async def test_execute_not_found(self):
        reg = ToolRegistry()
        with pytest.raises(ToolNotFound):
            await reg.execute("missing", {})

    @pytest.mark.asyncio
    async def test_execute_timeout(self):
        reg = ToolRegistry()
        spec = ToolSpec(name="slow", description="d", timeout_seconds=0.05)
        async def handler():
            await asyncio.sleep(1)
        reg.register(spec, handler)
        with pytest.raises(ToolTimeout):
            await reg.execute("slow", {})

    def test_stats(self):
        reg = ToolRegistry()
        reg.register(ToolSpec(name="a", description="d"), lambda: None)
        stats = reg.get_stats()
        assert stats["registered_tools"] == 1
        assert stats["total_executions"] == 0


class TestMCPClient:
    @pytest.mark.asyncio
    async def test_connect_stdio(self):
        client = MCPClient(ToolRegistry())
        conn = await client.connect_stdio("test", "python", ["-m", "mcp.server"])
        assert conn.name == "test"
        assert conn.transport == "stdio"

    def test_disconnect(self):
        client = MCPClient(ToolRegistry())
        # no-op if not connected
        client.disconnect("missing")


class TestSkillLoader:
    def test_load_skill(self, tmp_path):
        skill_file = tmp_path / "test_skill.md"
        skill_file.write_text("""---
name: test-skill
description: A test skill
applies_when: testing
---

# Test Skill

Some instructions.
""")
        loader = SkillLoader()
        skill = loader.load(skill_file)
        assert skill.name == "test-skill"
        assert skill.description == "A test skill"
        assert "Test Skill" in skill.body

    def test_find_matching(self, tmp_path):
        skill_file = tmp_path / "test_skill.md"
        skill_file.write_text("""---
name: python-refactor
description: Refactor Python code
applies_when: python refactoring
---

# Python Refactor

Instructions.
""")
        loader = SkillLoader()
        loader.load(skill_file)
        matches = loader.find_matching("refactor python")
        assert len(matches) == 1


class TestSandbox:
    @pytest.mark.asyncio
    async def test_run_echo(self):
        box = Sandbox()
        result = await box.run(["echo", "hello"])
        assert result.returncode == 0
        assert "hello" in result.stdout

    @pytest.mark.asyncio
    async def test_run_timeout(self):
        box = Sandbox(SandboxConfig(timeout_seconds=0.05))
        result = await box.run(["python3", "-c", "import time; time.sleep(10)"])
        assert result.returncode == -1
        assert "Timeout" in result.stderr

    def test_cleanup(self):
        box = Sandbox()
        box._create_temp_dir()
        box.cleanup()
        assert box._temp_dir is None


class TestSandboxPool:
    @pytest.mark.asyncio
    async def test_acquire_release(self):
        pool = SandboxPool(max_size=2)
        box1 = await pool.acquire()
        assert box1 in pool._in_use
        pool.release(box1)
        assert box1 in pool._available
