"""Vector clock implementation for distributed event causality.

Vector clocks track logical time across nodes in a distributed system.
Each node maintains a counter; when nodes communicate, they merge clocks.
This enables detecting causal relationships (happens-before) and concurrency.

Reference: Leslie Lamport, "Time, Clocks, and the Ordering of Events in a
Distributed System" (1978); Colin Fidge, "Timestamps in Message-Passing
Systems That Preserve the Partial Ordering" (1988).
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Optional


class VectorClock:
    """A vector clock for tracking distributed causality.

    Usage:
        vc = VectorClock()
        vc.increment("node-a")  # {node-a: 1}
        vc2 = VectorClock({"node-a": 1, "node-b": 3})
        vc.merge(vc2)         # {node-a: 1, node-b: 3}
        vc.increment("node-a")  # {node-a: 2, node-b: 3}
    """

    def __init__(self, clock: Optional[Dict[str, int]] = None) -> None:
        self._clock: Dict[str, int] = dict(clock) if clock else {}

    # ── Mutation ──

    def increment(self, node_id: str) -> "VectorClock":
        """Increment this node's logical counter. Returns self for chaining."""
        self._clock[node_id] = self._clock.get(node_id, 0) + 1
        return self

    def merge(self, other: "VectorClock") -> "VectorClock":
        """Merge another clock into this one (taking max per node). Returns self."""
        for node, timestamp in other._clock.items():
            self._clock[node] = max(self._clock.get(node, 0), timestamp)
        return self

    def update(self, node_id: str, timestamp: int) -> "VectorClock":
        """Set a specific node's timestamp (used when receiving remote events).

        Only updates if the new timestamp is greater than the current value.
        """
        current = self._clock.get(node_id, 0)
        if timestamp > current:
            self._clock[node_id] = timestamp
        return self

    # ── Comparison ──

    def compare(self, other: "VectorClock") -> int:
        """Compare two vector clocks.

        Returns:
            -2  if self is strictly before other (happens-before)
            2   if self is strictly after other
            0   if they are equal
            1   if they are concurrent (neither happens before the other)
        """
        nodes = set(self._clock.keys()) | set(other._clock.keys())

        all_self_le = True   # self <= other (all entries)
        all_other_le = True  # other <= self (all entries)
        any_self_lt = False  # self < other (at least one strict)
        any_other_lt = False # other < self (at least one strict)

        for node in nodes:
            s = self._clock.get(node, 0)
            o = other._clock.get(node, 0)
            if s < o:
                all_self_le = False
                any_self_lt = True
            elif s > o:
                all_other_le = False
                any_other_lt = True

        if any_self_lt and not any_other_lt and all_other_le:
            return -2  # self strictly before other
        if any_other_lt and not any_self_lt and all_self_le:
            return 2   # self strictly after other
        if not any_self_lt and not any_other_lt:
            return 0   # equal
        return 1       # concurrent

    def happens_before(self, other: "VectorClock") -> bool:
        """Return True if self strictly happens-before other."""
        return self.compare(other) == -2

    def happens_after(self, other: "VectorClock") -> bool:
        """Return True if other strictly happens-before self."""
        return self.compare(other) == 2

    def concurrent_with(self, other: "VectorClock") -> bool:
        """Return True if self and other are concurrent (no causal relation)."""
        return self.compare(other) == 1

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, VectorClock):
            return NotImplemented
        return self.compare(other) == 0

    def __le__(self, other: "VectorClock") -> bool:
        c = self.compare(other)
        return c in (-2, 0)

    def __lt__(self, other: "VectorClock") -> bool:
        return self.compare(other) == -2

    def __ge__(self, other: "VectorClock") -> bool:
        c = self.compare(other)
        return c in (2, 0)

    def __gt__(self, other: "VectorClock") -> bool:
        return self.compare(other) == 2

    # ── Serialization ──

    def copy(self) -> "VectorClock":
        """Return an independent copy."""
        return VectorClock(deepcopy(self._clock))

    def as_dict(self) -> Dict[str, int]:
        """Export as a plain dict."""
        return deepcopy(self._clock)

    @classmethod
    def from_dict(cls, d: Dict[str, int]) -> "VectorClock":
        """Create from a dict."""
        return cls(dict(d))

    # ── Representation ──

    def __repr__(self) -> str:
        return f"VectorClock({self._clock})"

    def __len__(self) -> int:
        return len(self._clock)

    def __contains__(self, node_id: str) -> bool:
        return node_id in self._clock

    def __getitem__(self, node_id: str) -> int:
        return self._clock[node_id]
