"""Shared pytest fixtures."""

import pytest

# Add edac to path if needed
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
