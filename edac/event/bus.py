"""
Event Bus — Async pub/sub with topic routing, priority queues, and backpressure.

The Event Bus is the central nervous system of EDAC. All communication flows through it:
- Agents emit events to communicate
- Tools emit events when they complete
- Humans emit events to intervene
- The system emits events for observability

Design decisions:
- Topic-based routing (not direct addressing) for loose coupling
- EventPriority queues for urgent events (e.g., human approval, errors)
- Backpressure to prevent memory exhaustion under load
- Persistent event log for replay and debugging
- Structured concurrency for clean shutdown
"""

from __future__ import annotations

import asyncio
import logging
import json
from abc import ABC, abstractmethod
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import (
    Any,
    AsyncIterator,
    Callable,
    Coroutine,
    Dict,
    List,
    Optional,
    Set,
    TypeVar,
)
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict

from .schema import Event, EventType, EventPriority
from .vector_clock import VectorClock

logger = logging.getLogger("edac.event_bus")

T = TypeVar("T")

# Type alias for event handlers
EventHandler = Callable[[Event], Coroutine[Any, Any, Optional[Event]]]


class EventBusConfig(BaseModel):
    """Configuration for the event bus."""

    model_config = ConfigDict(extra="allow")
    max_queue_depth: int = 10000
    default_priority: EventPriority = EventPriority.NORMAL
    enable_persistence: bool = True
    persistence_path: Optional[str] = None
    max_log_size: int = 100000


class EventPersistenceBackend(ABC):
    """Abstract backend for persisting events."""

    @abstractmethod
    async def append(self, event: Event) -> None: ...

    @abstractmethod
    async def get_log(
        self,
        correlation_id: Optional[UUID] = None,
        event_types: Optional[List[EventType]] = None,
        limit: int = 1000,
    ) -> List[Event]: ...


class InMemoryBackend(EventPersistenceBackend):
    """In-memory event persistence backend."""

    def __init__(self, max_size: int = 100000) -> None:
        self._log: List[Event] = []
        self._max_size = max_size

    async def append(self, event: Event) -> None:
        self._log.append(event)
        if len(self._log) > self._max_size:
            self._log = self._log[self._max_size // 2 :]

    async def get_log(
        self,
        correlation_id: Optional[UUID] = None,
        event_types: Optional[List[EventType]] = None,
        limit: int = 1000,
    ) -> List[Event]:
        events = list(self._log)
        if correlation_id:
            events = [e for e in events if e.correlation_id == correlation_id]
        if event_types:
            events = [e for e in events if e.event_type in event_types]
        return events[-limit:][::-1]


@dataclass(frozen=True)
class Subscription:
    """A subscription to a topic or event pattern."""

    id: UUID
    handler: EventHandler
    topics: frozenset = field(default_factory=frozenset)
    event_types: frozenset = field(default_factory=frozenset)
    priority_filter: Optional[EventPriority] = None  # Only receive events at or above this priority
    source_filter: Optional[str] = None  # Only receive from specific source

    def __hash__(self) -> int:
        return hash(self.id)

    def matches(self, event: Event) -> bool:
        """Check if this subscription matches an event."""
        # Topic match (empty set means match all)
        if self.topics and event.topic not in self.topics:
            return False

        # Event type match
        if self.event_types and event.event_type not in self.event_types:
            return False

        # EventPriority filter: lower numeric value = higher priority
        # We want to RECEIVE events at or above our filter threshold
        # e.g. if filter=HIGH(-50), we should receive CRITICAL(-100) and HIGH(-50)
        if self.priority_filter is not None:
            if event.priority.value > self.priority_filter.value:
                return False

        # Source filter
        if self.source_filter and event.source != self.source_filter:
            return False

        return True


@dataclass
class BusStats:
    """Runtime statistics for the event bus."""

    events_emitted: int = 0
    events_delivered: int = 0
    events_dropped: int = 0
    active_subscriptions: int = 0
    queue_depth: int = 0
    max_queue_depth: int = 0
    backpressure_triggered: int = 0

    def snapshot(self) -> Dict[str, Any]:
        return {
            "events_emitted": self.events_emitted,
            "events_delivered": self.events_delivered,
            "events_dropped": self.events_dropped,
            "active_subscriptions": self.active_subscriptions,
            "queue_depth": self.queue_depth,
            "max_queue_depth": self.max_queue_depth,
            "backpressure_triggered": self.backpressure_triggered,
        }


class EventBus:
    """
    Async event bus with topic-based routing, priority queues, and backpressure.

    Usage:
        async with EventBus() as bus:
            # Subscribe to events
            sub = bus.subscribe(
                handler=my_handler,
                topics=["agent.swarm.coding"],
                event_types=[EventType.PLAN_STEP_START, EventType.PLAN_STEP_COMPLETE]
            )

            # Emit an event
            await bus.emit(Event(...))

            # Stream all events (for debugging)
            async for event in bus.stream():
                print(event)

    Backpressure strategy:
        When queue depth exceeds max_queue_depth, new events are:
        1. Dropped if priority is LOW or NORMAL
        2. Accepted if priority is HIGH or CRITICAL
        3. The emitter is notified (can retry or handle)

    Graceful shutdown:
        On shutdown, the bus:
        1. Stops accepting new events
        2. Waits for queued events to be processed (with timeout)
        3. Cancels any remaining tasks
        4. Closes all subscriptions
    """

    def __init__(
        self,
        max_queue_depth: int = 10_000,
        default_priority: EventPriority = EventPriority.NORMAL,
        enable_persistence: bool = True,
        persistence_path: Optional[str] = None,
        redis_url: Optional[str] = None,
        tracer: Optional[Any] = None,
        node_id: Optional[str] = None,
    ):
        self.max_queue_depth = max_queue_depth
        self.default_priority = default_priority
        self.enable_persistence = enable_persistence
        self.persistence_path = persistence_path
        self.redis_url = redis_url
        self.tracer = tracer

        # Vector clock for distributed causality
        self.node_id = node_id or f"node-{uuid4().hex[:8]}"
        self._vector_clock = VectorClock()

        # Redis state
        self._redis: Optional[Any] = None
        self._redis_pubsub: Optional[Any] = None
        self._redis_task: Optional[asyncio.Task] = None

        # EventPriority queues: one per priority level
        # Higher priority = processed first
        self._queues: Dict[EventPriority, asyncio.Queue[Event]] = {
            EventPriority.CRITICAL: asyncio.Queue(),
            EventPriority.HIGH: asyncio.Queue(),
            EventPriority.NORMAL: asyncio.Queue(),
            EventPriority.LOW: asyncio.Queue(),
        }

        # Subscriptions by topic for fast lookup
        self._subscriptions_by_topic: Dict[str, Set[Subscription]] = defaultdict(set)
        # All subscriptions (for wildcard matching)
        self._all_subscriptions: Set[Subscription] = set()

        # Event log (for replay and debugging)
        self._event_log: List[Event] = []
        self._max_log_size = 100_000  # Rotate after this many events

        # Runtime state
        self._running = False
        self._dispatcher_task: Optional[asyncio.Task] = None
        self._stats = BusStats()
        self._lock = asyncio.Lock()

        # Stream listeners (for bus.stream())
        self._stream_queues: Set[asyncio.Queue[Event]] = set()

    async def __aenter__(self) -> EventBus:
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.stop()

    async def start(self) -> None:
        """Start the event bus dispatcher."""
        if self._running:
            return

        self._running = True
        self._dispatcher_task = asyncio.create_task(
            self._dispatcher_loop(), name="event_bus_dispatcher"
        )

        # Start Redis pub/sub if configured
        if self.redis_url:
            try:
                import redis.asyncio as aioredis

                self._redis = await aioredis.from_url(self.redis_url, decode_responses=True)
                self._redis_pubsub = self._redis.pubsub()
                await self._redis_pubsub.subscribe("edac:events")
                self._redis_task = asyncio.create_task(
                    self._redis_listener_loop(), name="event_bus_redis_listener"
                )
                logger.info("Event bus Redis pub/sub started")
            except Exception as e:
                logger.warning(f"Failed to connect to Redis: {e}")
                self._redis = None
                self._redis_pubsub = None

        logger.info("Event bus started")

    async def stop(self, timeout: float = 30.0) -> None:
        """Stop the event bus gracefully."""
        if not self._running:
            return

        self._running = False

        # Wake up dispatcher by putting a sentinel event
        try:
            self._queues[EventPriority.NORMAL].put_nowait(None)
        except asyncio.QueueFull:
            pass

        # Signal dispatcher to finish
        if self._dispatcher_task:
            # Wait for queued events to be processed
            try:
                await asyncio.wait_for(self._wait_for_empty_queues(), timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning("Event bus shutdown timed out, cancelling remaining tasks")

            # Cancel dispatcher
            self._dispatcher_task.cancel()
            try:
                await self._dispatcher_task
            except asyncio.CancelledError:
                pass

        # Stop Redis listener
        if self._redis_task:
            self._redis_task.cancel()
            try:
                await self._redis_task
            except asyncio.CancelledError:
                pass

        if self._redis_pubsub:
            await self._redis_pubsub.unsubscribe("edac:events")
            await self._redis_pubsub.close()

        if self._redis:
            await self._redis.close()

        logger.info("Event bus stopped")

    async def _wait_for_empty_queues(self) -> None:
        """Wait until all priority queues are empty or dispatcher exits."""
        while any(not q.empty() for q in self._queues.values()):
            if self._dispatcher_task and self._dispatcher_task.done():
                break
            await asyncio.sleep(0.1)

    def subscribe(
        self,
        handler: EventHandler,
        topics: Optional[List[str]] = None,
        event_types: Optional[List[EventType]] = None,
        priority_filter: Optional[EventPriority] = None,
        source_filter: Optional[str] = None,
    ) -> Subscription:
        """
        Subscribe to events matching the given criteria.

        Args:
            handler: Async function called when matching events arrive
            topics: List of topics to subscribe to (None = all topics)
            event_types: Filter by event type (None = all types)
            priority_filter: Only receive events at or above this priority
            source_filter: Only receive from specific source

        Returns:
            Subscription object (use to unsubscribe)
        """
        sub = Subscription(
            id=uuid4(),
            handler=handler,
            topics=frozenset(topics or []),
            event_types=frozenset(event_types or []),
            priority_filter=priority_filter,
            source_filter=source_filter,
        )

        self._all_subscriptions.add(sub)

        # Index by topic for fast lookup
        if sub.topics:
            for topic in sub.topics:
                self._subscriptions_by_topic[topic].add(sub)

        self._stats.active_subscriptions = len(self._all_subscriptions)

        logger.debug(
            f"Subscription created: {sub.id} (topics={sub.topics}, types={sub.event_types})"
        )

        return sub

    def unsubscribe(self, subscription: Subscription) -> None:
        """Remove a subscription."""
        self._all_subscriptions.discard(subscription)

        if subscription.topics:
            for topic in subscription.topics:
                self._subscriptions_by_topic[topic].discard(subscription)

        self._stats.active_subscriptions = len(self._all_subscriptions)

        logger.debug(f"Subscription removed: {subscription.id}")

    async def emit(self, event: Event) -> bool:
        """
        Emit an event to the bus.

        Args:
            event: The event to emit

        Returns:
            True if event was accepted, False if dropped (backpressure)
        """
        if not self._running:
            raise RuntimeError("Event bus is not running")

        async with self._lock:
            # Stamp vector clock: increment local node, merge with event's existing clock
            self._vector_clock.increment(self.node_id)
            merged_vc = self._vector_clock.copy()
            if event.causality_vector:
                merged_vc.merge(VectorClock.from_dict(event.causality_vector))
            stamped_event = event.model_copy(update={"causality_vector": merged_vc.as_dict()})

            # Check backpressure
            total_depth = sum(q.qsize() for q in self._queues.values())

            if total_depth >= self.max_queue_depth:
                event_priority = stamped_event.priority

                # Drop low/normal priority events under backpressure
                if event_priority in (EventPriority.LOW, EventPriority.NORMAL):
                    self._stats.events_dropped += 1
                    self._stats.backpressure_triggered += 1
                    logger.warning(
                        f"Event dropped due to backpressure: {stamped_event.event_type} "
                        f"(queue_depth={total_depth})"
                    )
                    return False

                # Accept high/critical priority events even under backpressure
                logger.warning(
                    f"Accepting high-priority event despite backpressure: "
                    f"{stamped_event.event_type}"
                )

            # Add to appropriate priority queue
            priority = stamped_event.priority
            await self._queues[priority].put(stamped_event)

            self._stats.events_emitted += 1
            self._stats.queue_depth = total_depth + 1
            self._stats.max_queue_depth = max(self._stats.max_queue_depth, total_depth + 1)

        # Add to event log
        if self.enable_persistence:
            self._event_log.append(stamped_event)
            if len(self._event_log) > self._max_log_size:
                # Rotate log (keep last 50%)
                self._event_log = self._event_log[self._max_log_size // 2 :]

        logger.debug(
            f"Event emitted: {stamped_event.event_type} (priority={stamped_event.priority}, vc={stamped_event.causality_vector})"
        )

        # Publish to Redis for distributed propagation
        if self._redis:
            try:
                await self._redis.publish("edac:events", stamped_event.model_dump_json())
            except Exception as e:
                logger.warning(f"Failed to publish event to Redis: {e}")

        return True

    async def _dispatcher_loop(self) -> None:
        """Main dispatcher loop — processes events from priority queues."""
        while self._running:
            event = await self._get_next_event()

            if event is None:
                # No events available, brief pause
                await asyncio.sleep(0.01)
                continue

            try:
                await self._dispatch_event(event)
            except Exception as e:
                logger.exception(f"Error dispatching event {event.event_id}: {e}")

    async def _redis_listener_loop(self) -> None:
        """Listen for events from Redis and re-emit them locally."""
        if self._redis_pubsub is None:
            return
        try:
            async for message in self._redis_pubsub.listen():
                if not self._running:
                    break
                if message["type"] != "message":
                    continue
                try:
                    data = json.loads(message["data"])
                    event = Event.model_validate(data)
                    # Merge remote vector clock into local clock before re-emitting
                    if event.causality_vector:
                        self._vector_clock.merge(VectorClock.from_dict(event.causality_vector))
                    # Re-emit locally without re-stamping (already has merged clock)
                    async with self._lock:
                        priority = event.priority
                        await self._queues[priority].put(event)
                        self._stats.events_emitted += 1
                    if self.enable_persistence:
                        self._event_log.append(event)
                except Exception as e:
                    logger.warning(f"Failed to process Redis event: {e}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"Redis listener error: {e}")

    async def _get_next_event(self) -> Optional[Event]:
        """Get the next event from priority queues (highest priority first)."""
        for priority in [
            EventPriority.CRITICAL,
            EventPriority.HIGH,
            EventPriority.NORMAL,
            EventPriority.LOW,
        ]:
            queue = self._queues[priority]
            if not queue.empty():
                try:
                    return queue.get_nowait()
                except asyncio.QueueEmpty:
                    continue
        return None

    async def _dispatch_event(self, event: Event) -> None:
        """Dispatch an event to all matching subscribers."""
        # Start trace span if tracer is configured (wraps the entire dispatch)
        emit_span = None
        if self.tracer is not None:
            try:
                emit_span = self.tracer.start_span(f"event:{event.event_type.value}")
                emit_span.attributes["event_type"] = event.event_type.value
                emit_span.attributes["topic"] = event.topic
                emit_span.attributes["source"] = event.source
                if event.correlation_id:
                    emit_span.attributes["correlation_id"] = str(event.correlation_id)
            except Exception:
                emit_span = None
                logger.debug("Tracing failed to start span for event", exc_info=True)

        try:
            # Find matching subscribers
            matching_subs = self._find_matching_subscribers(event)

            if not matching_subs:
                logger.debug(f"No subscribers for event: {event.event_type}")
                return

            # Dispatch to all matching subscribers concurrently
            tasks = []
            for sub in matching_subs:
                task = asyncio.create_task(
                    self._invoke_handler(sub, event), name=f"handler_{sub.id}"
                )
                tasks.append(task)

            # Wait for all handlers (with timeout to prevent blocking)
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Log any handler errors
            for sub, result in zip(matching_subs, results):
                if isinstance(result, Exception):
                    logger.exception(f"Handler error for subscription {sub.id}: {result}")

            self._stats.events_delivered += len(matching_subs)

            # Also broadcast to stream listeners
            await self._broadcast_to_streams(event)
        finally:
            # Finish trace span if it was started (dispatch done)
            if emit_span is not None and self.tracer is not None:
                try:
                    self.tracer.end_span(emit_span)
                except Exception:
                    logger.debug("Tracing failed to end span for event", exc_info=True)

    def _find_matching_subscribers(self, event: Event) -> Set[Subscription]:
        """Find all subscribers that match an event."""
        # Fast path: look up by topic
        if event.topic in self._subscriptions_by_topic:
            candidates = self._subscriptions_by_topic[event.topic].copy()
        else:
            candidates = set()

        # Add wildcard subscribers (subscribed to all topics)
        for sub in self._all_subscriptions:
            if not sub.topics:  # No topic filter = wildcard
                candidates.add(sub)

        # Filter by subscription criteria
        matching = {sub for sub in candidates if sub.matches(event)}

        return matching

    async def _invoke_handler(self, sub: Subscription, event: Event) -> None:
        """Invoke a subscriber's handler with error isolation."""
        try:
            result = await sub.handler(event)

            # If handler returns an event, emit it (chaining)
            if result is not None and isinstance(result, Event):
                await self.emit(result)

        except Exception as e:
            logger.exception(f"Handler error in subscription {sub.id}: {e}")
            # Don't re-raise — isolate errors per handler

    async def _broadcast_to_streams(self, event: Optional[Event]) -> None:
        """Broadcast event to all stream listeners."""
        if event is None:
            return
        dead_queues = set()

        for queue in self._stream_queues:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                dead_queues.add(queue)

        # Remove full queues
        for queue in dead_queues:
            self._stream_queues.discard(queue)

    @asynccontextmanager
    async def stream(
        self,
        topics: Optional[List[str]] = None,
        event_types: Optional[List[EventType]] = None,
        max_buffer: int = 1000,
    ) -> AsyncIterator[AsyncIterator[Event]]:
        """
        Stream events from the bus.

        Usage:
            async with bus.stream(topics=["agent.*"]) as events:
                async for event in events:
                    print(event)
        """
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=max_buffer)
        self._stream_queues.add(queue)

        try:

            async def event_generator() -> AsyncIterator[Event]:
                while self._running and queue in self._stream_queues:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=0.5)

                        # Apply filters
                        if topics and event.topic not in topics:
                            continue
                        if event_types and event.event_type not in event_types:
                            continue

                        yield event
                    except asyncio.TimeoutError:
                        continue

            yield event_generator()
        finally:
            self._stream_queues.discard(queue)

    def get_vector_clock(self) -> VectorClock:
        """Return a copy of the bus's current vector clock."""
        return self._vector_clock.copy()

    def get_stats(self) -> BusStats:
        """Get current bus statistics."""
        self._stats.queue_depth = sum(q.qsize() for q in self._queues.values())
        return self._stats

    def get_event_log(
        self,
        correlation_id: Optional[UUID] = None,
        event_types: Optional[List[EventType]] = None,
        limit: int = 1000,
    ) -> List[Event]:
        """
        Retrieve events from the event log.

        Args:
            correlation_id: Filter by correlation ID
            event_types: Filter by event types
            limit: Maximum number of events to return

        Returns:
            List of matching events (most recent first)
        """
        events = list(self._event_log)

        if correlation_id:
            events = [e for e in events if e.correlation_id == correlation_id]

        if event_types:
            events = [e for e in events if e.event_type in event_types]

        # Return most recent first
        return events[-limit:][::-1]

    async def replay(
        self,
        correlation_id: UUID,
        from_step: Optional[int] = None,
    ) -> None:
        """
        Replay events from a previous session.

        This is powerful for:
        - Debugging: replay exactly what happened
        - Recovery: resume from a checkpoint
        - Testing: deterministically reproduce scenarios
        """
        events = self.get_event_log(correlation_id=correlation_id)

        if from_step is not None:
            events = [e for e in events if e.step_index >= from_step]

        logger.info(f"Replaying {len(events)} events for correlation {correlation_id}")

        for event in events:
            await self.emit(event)
            # Small delay to prevent overwhelming
            await asyncio.sleep(0.001)

    def get_trace_spans(self) -> List[Dict[str, Any]]:
        """Export trace spans from the attached tracer."""
        if self.tracer is not None:
            return self.tracer.export()
        return []
