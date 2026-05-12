"""Auto-load all user agent modules at import time."""

from edac.server.user_agents import load_user_agents

# This runs on `import agents` → server calls load_user_agents()
# which imports all .py files in the agents/ directory.
# @agent decorators in those files auto-register into HandlerRegistry.

load_user_agents()
