"""RBAC authentication and authorization.

Roles:
  admin    — full access
  operator — submit tasks, read status, no config changes
  viewer   — read-only
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Set


class Role(Enum):
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"


@dataclass(frozen=True)
class User:
    api_key: str
    role: Role
    name: Optional[str] = None


class AuthManager:
    """Simple in-memory RBAC. Production: swap for DB-backed store."""

    def __init__(self) -> None:
        self._users: Dict[str, User] = {}

    def register(self, api_key: str, role: Role, name: Optional[str] = None) -> None:
        self._users[api_key] = User(api_key=api_key, role=role, name=name)

    def authenticate(self, api_key: str) -> Optional[User]:
        return self._users.get(api_key)

    def is_allowed(self, user: User, action: str) -> bool:
        perms = _PERMISSIONS.get(user.role, set())
        return action in perms


# Action identifiers
ACTION_SUBMIT_TASK = "submit_task"
ACTION_LIST_TASKS = "list_tasks"
ACTION_GET_TASK = "get_task"
ACTION_GET_EVENTS = "get_events"
ACTION_CREATE_AGENT = "create_agent"
ACTION_LIST_AGENTS = "list_agents"
ACTION_GET_AGENT = "get_agent"
ACTION_DELETE_AGENT = "delete_agent"
ACTION_RESTART_AGENT = "restart_agent"
ACTION_PAUSE_AGENT = "pause_agent"
ACTION_RESUME_AGENT = "resume_agent"
ACTION_GET_METRICS = "get_metrics"
ACTION_GET_HEALTH = "get_health"

_PERMISSIONS: Dict[Role, Set[str]] = {
    Role.ADMIN: {
        ACTION_SUBMIT_TASK,
        ACTION_LIST_TASKS,
        ACTION_GET_TASK,
        ACTION_GET_EVENTS,
        ACTION_LIST_AGENTS, 
        ACTION_CREATE_AGENT,
        ACTION_GET_AGENT,
        ACTION_DELETE_AGENT,
        ACTION_RESTART_AGENT,
        ACTION_PAUSE_AGENT,
        ACTION_RESUME_AGENT,
        ACTION_GET_METRICS,
        ACTION_GET_HEALTH,
    },
    Role.OPERATOR: {
        ACTION_SUBMIT_TASK,
        ACTION_LIST_TASKS,
        ACTION_GET_TASK,
        ACTION_GET_EVENTS,
        ACTION_LIST_AGENTS,
        ACTION_GET_AGENT,
        ACTION_GET_HEALTH,
    },
    Role.VIEWER: {
        ACTION_LIST_TASKS,
        ACTION_GET_TASK,
        ACTION_GET_EVENTS,
        ACTION_GET_HEALTH,
    },
}
