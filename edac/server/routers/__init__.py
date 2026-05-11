"""EDAC server routers package."""

from edac.server.routers import agents_router
from edac.server.routers import approval_router
from edac.server.routers import memory_router
from edac.server.routers import modality_router
from edac.server.routers import protocol_router
from edac.server.routers import system_router
from edac.server.routers import tasks_router

__all__ = [
    "agents_router",
    "approval_router",
    "memory_router",
    "modality_router",
    "protocol_router",
    "system_router",
    "tasks_router",
]
