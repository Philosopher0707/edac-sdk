"""User agent auto-loader (Pillar 3).

Imports all `.py` files from the `agents/` directory at server startup.
Since `@agent` decorators run at import time, this auto-registers all
user-defined agents into HandlerRegistry without any manual intervention.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from typing import List

logger = logging.getLogger("edac.server.user_agents")


def load_user_agents(agents_dir: str = "agents") -> List[str]:
    """Dynamically import all `.py` modules from *agents_dir*.

    Each `.py` file is expected to contain `@agent(name=...)` decorated
    functions that auto-register into HandlerRegistry at import time.

    Args:
        agents_dir: Path to the agents directory (relative to project root).

    Returns:
        List of module names successfully loaded.
    """
    loaded: List[str] = []
    path = Path(agents_dir)

    if not path.exists() or not path.is_dir():
        logger.warning("Agents directory not found: %s", agents_dir)
        return loaded

    for py_file in sorted(path.glob("*.py")):
        if py_file.name.startswith("_") or py_file.name.startswith("."):
            continue  # Skip __init__.py, hidden files

        module_name = f"agents.{py_file.stem}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, py_file)
            if spec is None or spec.loader is None:
                logger.warning("Could not create spec for %s", py_file)
                continue

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            loaded.append(module_name)
            logger.info("Loaded user agent module: %s", module_name)
        except Exception as e:
            logger.error("Failed to load %s: %s", module_name, e)

    if loaded:
        from edac.agent.handler_registry import HandlerRegistry
        reg = HandlerRegistry.get_default()
        names = reg.list()
        logger.info("HandlerRegistry now has %d agents: %s", len(names), names)

    return loaded
