"""Human interface layer for EDAC.

HITL state machine, approval gates, and SSE streaming.
"""

from edac.human.hitl import HITLStateMachine, HITLTask, HITLState
from edac.human.approval import ApprovalGate, ApprovalManager
from edac.human.stream import EventStream, SSEStream

__all__ = [
    "HITLStateMachine",
    "HITLTask",
    "HITLState",
    "ApprovalGate",
    "ApprovalManager",
    "EventStream",
    "SSEStream",
]
