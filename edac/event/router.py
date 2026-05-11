"""
Event Router — Topic-based routing with priority queues, fan-out, and filtering.

The router is the traffic controller of the event system. It:
- Routes events to subscribers based on topic patterns (wildcards supported)
- Respects priority levels (negative = higher priority)
- Handles fan-out (one event → many subscribers)
- Supports filtering (subscribers can specify predicates)
- Maintains routing tables with O(1) lookup for exact matches, O(n) for wildcards
"""

from __future__ import annotations

import fnmatch
import heapq
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import (
    Any,
    Callable,
    Coroutine,
    Dict,
    List,
    Optional,
    Protocol,
    Set,
    runtime_checkable,
)

from .schema import Event, EventPriority

logger = logging.getLogger("edac.event.router")


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

EventHandler = Callable[[Event], Coroutine[None, None, None]]
EventFilter = Callable[[Event], bool]


@runtime_checkable
class Subscriber(Protocol):
    """Protocol for event subscribers."""

    async def on_event(self, event: Event) -> None:
        ...


# ---------------------------------------------------------------------------
# Subscription
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Subscription:
    """A subscription to a topic pattern.

    Attributes:
        id: Unique subscription identifier
        topic_pattern: Topic to subscribe to (supports wildcards: * and **)
        handler: Async callable that receives matching events
        filter: Optional predicate to further filter events
        priority: Priority level for this subscriber
        max_depth: Max topic depth for ** wildcard (default: unlimited)
    """

    id: str
    topic_pattern: str
    handler: EventHandler
    filter: Optional[EventFilter] = None
    priority: EventPriority = EventPriority.NORMAL
    max_depth: Optional[int] = None

    def matches(self, topic: str) -> bool:
        """Check if a topic matches this subscription's pattern."""
        # Handle ** wildcard (matches any number of segments)
        if "**" in self.topic_pattern:
            pattern = self.topic_pattern.replace("**", "*")
            # ** matches across segments, so we need special handling
            parts_pattern = pattern.split(".")
            parts_topic = topic.split(".")

            # Simple case: ** at end
            if self.topic_pattern.endswith(".**"):
                prefix = self.topic_pattern[:-3]  # Remove .**
                return topic == prefix or topic.startswith(prefix + ".")

            # General case: use fnmatch
            return fnmatch.fnmatch(topic, pattern)

        # Handle * wildcard (matches single segment)
        return fnmatch.fnmatch(topic, self.topic_pattern)

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Subscription):
            return NotImplemented
        return self.id == other.id


# ---------------------------------------------------------------------------
# Routing Table
# ---------------------------------------------------------------------------

class RoutingTable:
    """Efficient topic-to-subscriber lookup.

    Uses two-level structure:
    - Exact matches: Dict[topic, Set[Subscription]] for O(1) lookup
    - Wildcard patterns: List[Subscription] for O(n) scan
    """

    def __init__(self) -> None:
        self._exact: Dict[str, Set[Subscription]] = defaultdict(set)
        self._wildcards: List[Subscription] = []
        self._all_subscriptions: Dict[str, Subscription] = {}  # id -> Subscription

    def add(self, subscription: Subscription) -> None:
        """Add a subscription to the routing table."""
        self._all_subscriptions[subscription.id] = subscription

        if "*" in subscription.topic_pattern:
            self._wildcards.append(subscription)
        else:
            self._exact[subscription.topic_pattern].add(subscription)

    def remove(self, subscription_id: str) -> Optional[Subscription]:
        """Remove a subscription by ID. Returns the removed subscription or None."""
        sub = self._all_subscriptions.pop(subscription_id, None)
        if sub is None:
            return None

        if "*" in sub.topic_pattern:
            self._wildcards = [s for s in self._wildcards if s.id != subscription_id]
        else:
            self._exact[sub.topic_pattern].discard(sub)
            if not self._exact[sub.topic_pattern]:
                del self._exact[sub.topic_pattern]

        return sub

    def lookup(self, topic: str) -> List[Subscription]:
        """Find all subscriptions matching a topic.

        Returns deduplicated list sorted by priority (highest first).
        """
        matches: Set[Subscription] = set()

        # Exact matches
        if topic in self._exact:
            matches.update(self._exact[topic])

        # Wildcard matches
        for sub in self._wildcards:
            if sub.matches(topic):
                matches.add(sub)

        # Sort by priority (lower numeric value = higher priority)
        return sorted(matches, key=lambda s: s.priority.value)

    def get_stats(self) -> Dict[str, int]:
        """Return routing table statistics."""
        return {
            "exact_subscriptions": sum(len(subs) for subs in self._exact.values()),
            "wildcard_subscriptions": len(self._wildcards),
            "unique_topics": len(self._exact),
            "total_subscriptions": len(self._all_subscriptions),
        }


# ---------------------------------------------------------------------------
# Priority Queue for Events
# ---------------------------------------------------------------------------

@dataclass(order=True)
class PrioritizedEvent:
    """Event wrapper for priority queue ordering.

    Uses heapq-compatible ordering: lower priority value = processed first.
    Sequence number breaks ties for FIFO ordering within same priority.
    """

    priority: int
    sequence: int
    event: Event = field(compare=False)


class PriorityEventQueue:
    """Priority queue for events with backpressure support.

    Features:
    - Priority ordering (negative = higher priority)
    - FIFO within same priority
    - Size limits with configurable backpressure strategy
    - Peek support for monitoring
    """

    def __init__(
        self,
        max_size: int = 10000,
        backpressure_strategy: str = "block",
    ) -> None:
        self._heap: List[PrioritizedEvent] = []
        self._sequence = 0
        self._max_size = max_size
        self._backpressure = backpressure_strategy
        self._size_by_priority: Dict[int, int] = defaultdict(int)

    def put(self, event: Event) -> bool:
        """Add event to queue. Returns True if accepted, False if dropped.

        Backpressure strategies:
        - "block": Wait until space available (not implemented here, handled by caller)
        - "drop": Silently drop event if queue full
        - "drop_low_priority": Drop lowest priority events to make room
        - "raise": Raise QueueFull exception
        """
        if len(self._heap) >= self._max_size:
            if self._backpressure == "drop":
                logger.warning(f"Queue full, dropping event: {event.event_id}")
                return False
            elif self._backpressure == "drop_low_priority":
                # Drop lowest priority event if this one is higher priority
                if self._heap and event.priority < self._heap[-1].priority:
                    dropped = heapq.heappop(self._heap)
                    self._size_by_priority[dropped.priority] -= 1
                    logger.debug(f"Dropped low-priority event to make room: {dropped.event.event_id}")
                else:
                    logger.warning(f"Queue full, dropping event: {event.event_id}")
                    return False
            elif self._backpressure == "raise":
                raise QueueFull(f"Queue at capacity ({self._max_size})")
            # "block" strategy: caller must handle backpressure

        self._sequence += 1
        heapq.heappush(
            self._heap,
            PrioritizedEvent(
                priority=event.priority,
                sequence=self._sequence,
                event=event,
            ),
        )
        self._size_by_priority[event.priority] += 1
        return True

    def get(self) -> Optional[Event]:
        """Get highest priority event. Returns None if queue empty."""
        if not self._heap:
            return None

        prioritized = heapq.heappop(self._heap)
        self._size_by_priority[prioritized.priority] -= 1
        return prioritized.event

    def peek(self) -> Optional[Event]:
        """Peek at highest priority event without removing."""
        if not self._heap:
            return None
        return self._heap[0].event

    def __len__(self) -> int:
        return len(self._heap)

    def is_empty(self) -> bool:
        return len(self._heap) == 0

    def get_stats(self) -> Dict[str, int]:
        """Return queue statistics."""
        return {
            "total_events": len(self._heap),
            "max_size": self._max_size,
            "by_priority": dict(self._size_by_priority),
        }


class QueueFull(Exception):
    """Raised when event queue is at capacity."""
    pass


# ---------------------------------------------------------------------------
# Config / Rule / Matcher / Stats (for __init__ exports)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RouterConfig:
    """Configuration for the event router."""
    default_priority: EventPriority = EventPriority.NORMAL
    max_queue_size: int = 10000
    backpressure: str = "drop_low_priority"


@dataclass(frozen=True)
class RouteRule:
    """A routing rule for topic transformation or forwarding."""
    source_pattern: str
    target_topic: str
    transform: Optional[Callable[[Event], Event]] = None


class TopicMatcher:
    """Utility for matching topics against patterns."""

    @staticmethod
    def matches(topic: str, pattern: str) -> bool:
        if pattern == "*" or pattern == "#":
            return True
        if pattern.endswith(".*"):
            prefix = pattern[:-2]
            return topic.startswith(prefix + ".")
        if pattern.endswith(".**"):
            prefix = pattern[:-2]
            return topic.startswith(prefix)
        return fnmatch.fnmatch(topic, pattern)


class RoutingStats:
    """Immutable snapshot of router statistics."""

    def __init__(self, router: EventRouter) -> None:
        self._stats = router.get_stats()

    def to_dict(self) -> Dict[str, Any]:
        return dict(self._stats)


# ---------------------------------------------------------------------------
# Event Router
# ---------------------------------------------------------------------------

class EventRouter:
    """Central event router with topic-based pub/sub.

    The router is the traffic controller:
    - Publishers emit events to topics
    - Subscribers receive events matching their topic patterns
    - Supports wildcards, filtering, and priority ordering
    - Maintains routing statistics for observability
    """

    def __init__(self) -> None:
        self._table = RoutingTable()
        self._stats: Dict[str, int] = {
            "events_routed": 0,
            "events_dropped": 0,
            "subscriptions_added": 0,
            "subscriptions_removed": 0,
        }

    def subscribe(
        self,
        topic_pattern: str,
        handler: EventHandler,
        subscription_id: Optional[str] = None,
        filter: Optional[EventFilter] = None,
        priority: EventPriority = EventPriority.NORMAL,
    ) -> Subscription:
        """Subscribe to events matching a topic pattern.

        Args:
            topic_pattern: Topic to subscribe to. Supports wildcards:
                - "agent.*" matches "agent.spawn", "agent.heartbeat", etc.
                - "agent.**" matches "agent.spawn", "agent.plan.step.start", etc.
                - "*.complete" matches any topic ending in ".complete"
            handler: Async callable receiving matching events
            subscription_id: Optional unique ID (auto-generated if None)
            filter: Optional predicate for additional filtering
            priority: Priority for event delivery order

        Returns:
            Subscription object that can be used to unsubscribe
        """
        import uuid

        sub = Subscription(
            id=subscription_id or f"sub-{uuid.uuid4().hex[:8]}",
            topic_pattern=topic_pattern,
            handler=handler,
            filter=filter,
            priority=priority,
        )
        self._table.add(sub)
        self._stats["subscriptions_added"] += 1
        logger.debug(f"Subscribed {sub.id} to '{topic_pattern}'")
        return sub

    def unsubscribe(self, subscription_id: str) -> bool:
        """Remove a subscription. Returns True if found and removed."""
        sub = self._table.remove(subscription_id)
        if sub:
            self._stats["subscriptions_removed"] += 1
            logger.debug(f"Unsubscribed {subscription_id}")
            return True
        return False

    async def route(self, event: Event) -> int:
        """Route an event to all matching subscribers.

        Returns:
            Number of subscribers the event was delivered to
        """
        subscribers = self._table.lookup(event.topic)
        delivered = 0

        for sub in subscribers:
            # Apply filter if present
            if sub.filter and not sub.filter(event):
                continue

            try:
                await sub.handler(event)
                delivered += 1
            except Exception as e:
                logger.error(f"Handler error for subscription {sub.id}: {e}", exc_info=True)
                # Don't let one handler break routing to others
                continue

        self._stats["events_routed"] += 1
        return delivered

    def get_subscribers(self, topic: str) -> List[Subscription]:
        """Get all subscribers for a topic (for introspection)."""
        return self._table.lookup(topic)

    def get_stats(self) -> Dict:
        """Return router statistics."""
        return {
            **self._stats,
            **self._table.get_stats(),
        }


# ---------------------------------------------------------------------------
# Convenience Decorator
# ---------------------------------------------------------------------------

def on(
    topic_pattern: str,
    filter: Optional[EventFilter] = None,
    priority: EventPriority = EventPriority.NORMAL,
):
    """Decorator for subscribing to events.

    Usage:
        @on("agent.spawn")
        async def handle_spawn(event: Event) -> None:
            print(f"Agent spawned: {event.payload}")

        @on("plan.step.*", priority=EventPriority.HIGH)
        async def handle_steps(event: Event) -> None:
            print(f"Step event: {event.event_type}")
    """
    def decorator(func: EventHandler) -> EventHandler:
        # Store metadata on function for later registration
        func._edac_subscription = {  # type: ignore
            "topic_pattern": topic_pattern,
            "filter": filter,
            "priority": priority,
        }
        return func

    return decorator
