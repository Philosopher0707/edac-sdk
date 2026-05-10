"""Context management for EDAC.

Token budgets, compression strategies, and model routing.
"""

from edac.context.manager import ContextManager, ContextConfig
from edac.context.budget import BudgetTracker
from edac.context.compressor import ContextCompressor
from edac.context.router import ModelRouter, ModelRoute

__all__ = [
    "ContextManager",
    "ContextConfig",
    "BudgetTracker",
    "ContextCompressor",
    "ModelRouter",
    "ModelRoute",
]
