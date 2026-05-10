"""
EDAC: Event-Driven Agentic Core

A comprehensive, event-driven SDK for building agentic systems with full modality support,
dynamic replanning, and production-grade observability.

Core Philosophy: Everything is an event. Agents don't "call functions" — they emit and react to events.
"""

__version__ = "0.1.0"
__author__ = "EDAC Team"

from edac.event.schema import Event
from edac.event.bus import EventBus
from edac.event.router import EventRouter

__all__ = [
    "Event",
    "EventBus",
    "EventRouter",
]
