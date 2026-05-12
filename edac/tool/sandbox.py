"""Sandbox — Isolated execution environment for tools.

Provides filesystem/network isolation for untrusted tool execution.
Production implementations use gVisor, Bubblewrap, or Docker.
This module provides the interface and a fallback subprocess-based sandbox.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("edac.tool.sandbox")


@dataclass
class SandboxConfig:
    """Configuration for sandboxed execution."""

    allow_network: bool = False
    allow_filesystem: bool = True
    read_only_paths: List[str] = None
    write_paths: List[str] = None
    env_vars: Dict[str, str] = None
    timeout_seconds: float = 30.0
    memory_limit_mb: int = 512
    cpu_limit: Optional[float] = None
    cleanup_temp_dir: bool = True

    def __post_init__(self):
        if self.read_only_paths is None:
            self.read_only_paths = []
        if self.write_paths is None:
            self.write_paths = []
        if self.env_vars is None:
            self.env_vars = {}


@dataclass
class SandboxResult:
    """Result of sandboxed execution."""

    stdout: str
    stderr: str
    returncode: int
    duration_ms: float


class Sandbox:
    """Subprocess-based sandbox with filesystem/network restrictions.

    Uses a temporary directory as the working directory and
    subprocess-level restrictions. For production, replace with
    gVisor/Bubblewrap/Docker integration.
    """

    def __init__(self, config: Optional[SandboxConfig] = None):
        self.config = config or SandboxConfig()
        self._temp_dir: Optional[Path] = None

    async def run(
        self,
        command: List[str],
        stdin: Optional[str] = None,
        cwd: Optional[Path] = None,
    ) -> SandboxResult:
        """Run a command inside the sandbox."""
        work_dir = cwd or self._create_temp_dir()

        env = os.environ.copy()
        env.update(self.config.env_vars)
        if not self.config.allow_network:
            # Best-effort: unset proxy vars
            for key in ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"]:
                env.pop(key, None)

        logger.debug(f"Sandbox run: {' '.join(command)} (cwd={work_dir})")

        start = asyncio.get_event_loop().time()
        proc = None

        async def _run():
            nonlocal proc
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.PIPE if stdin else None,
                cwd=work_dir,
                env=env,
            )
            stdout_b, stderr_b = await proc.communicate(stdin.encode() if stdin else None)
            return stdout_b, stderr_b

        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                _run(),
                timeout=self.config.timeout_seconds,
            )
            elapsed = (asyncio.get_event_loop().time() - start) * 1000
            return SandboxResult(
                stdout=stdout_b.decode("utf-8", errors="replace"),
                stderr=stderr_b.decode("utf-8", errors="replace"),
                returncode=proc.returncode or 0,
                duration_ms=round(elapsed, 2),
            )
        except asyncio.TimeoutError:
            elapsed = (asyncio.get_event_loop().time() - start) * 1000
            if proc is not None:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass
            logger.warning(f"Sandbox command timed out: {' '.join(command)}")
            return SandboxResult(
                stdout="",
                stderr=f"Timeout after {self.config.timeout_seconds}s",
                returncode=-1,
                duration_ms=round(elapsed, 2),
            )
        finally:
            if not cwd and self._temp_dir and self.config.cleanup_temp_dir:
                try:
                    import shutil

                    shutil.rmtree(self._temp_dir, ignore_errors=True)
                    self._temp_dir = None
                except Exception:
                    pass

    def _create_temp_dir(self) -> Path:
        if self._temp_dir is None:
            self._temp_dir = Path(tempfile.mkdtemp(prefix="edac_sandbox_"))
        return self._temp_dir

    def cleanup(self) -> None:
        if self._temp_dir and self._temp_dir.exists():
            import shutil

            shutil.rmtree(self._temp_dir, ignore_errors=True)
            self._temp_dir = None


class SandboxPool:
    """Pool of reusable sandboxes to reduce startup overhead."""

    def __init__(self, max_size: int = 10):
        self.max_size = max_size
        self._available: List[Sandbox] = []
        self._in_use: List[Sandbox] = []

    async def acquire(self, config: Optional[SandboxConfig] = None) -> Sandbox:
        if self._available:
            box = self._available.pop()
        else:
            box = Sandbox(config)
        self._in_use.append(box)
        return box

    def release(self, box: Sandbox) -> None:
        if box in self._in_use:
            self._in_use.remove(box)
        if len(self._available) < self.max_size:
            self._available.append(box)
        else:
            box.cleanup()
