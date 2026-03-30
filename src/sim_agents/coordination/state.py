"""CRDT-based shared state backed by Upstash Redis.

Implements three Conflict-free Replicated Data Types:
- G-Counter (AgentTaskTracker): grow-only counter, each agent has own counter, merge = max per agent
- OR-Set (ActiveTaskSet): observed-remove set, supports concurrent add/remove, add wins
- LWW-Register (FileHealthScore): last-writer-wins based on timestamp

All backed by Upstash Redis for persistence and cross-agent convergence.
No external CRDT packages used — fully manual implementation.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class GCounter:
    """Grow-only Counter CRDT (AgentTaskTracker).

    Each agent maintains its own counter. Merge takes the max per agent.
    Total is the sum of all agent counters. Counters can only increment.
    """

    def __init__(self, node_id: str, redis_client: Any | None = None, key: str = "crdt:gcounter") -> None:
        self.node_id = node_id
        self._redis = redis_client
        self._key = key
        self._counters: dict[str, int] = {}

    def _load(self) -> None:
        """Load state from Redis."""
        if self._redis:
            raw = self._redis.get(self._key)
            if raw:
                self._counters = json.loads(raw) if isinstance(raw, str) else raw
            else:
                self._counters = {}

    def _save(self) -> None:
        """Persist state to Redis."""
        if self._redis:
            self._redis.set(self._key, json.dumps(self._counters))

    def increment(self, amount: int = 1) -> int:
        """Increment this node's counter.

        Args:
            amount: Amount to increment (must be positive).

        Returns:
            New value of this node's counter.

        Raises:
            ValueError: If amount is not positive.
        """
        if amount < 1:
            raise ValueError("G-Counter can only increment by positive amounts")
        self._load()
        current = self._counters.get(self.node_id, 0)
        self._counters[self.node_id] = current + amount
        self._save()
        logger.debug("gcounter_increment", node=self.node_id, value=self._counters[self.node_id])
        return self._counters[self.node_id]

    def value(self) -> int:
        """Get total counter value (sum of all nodes).

        Returns:
            Sum of all node counters.
        """
        self._load()
        return sum(self._counters.values())

    def node_value(self, node_id: str | None = None) -> int:
        """Get counter value for a specific node.

        Args:
            node_id: Node to query. Defaults to this node.

        Returns:
            Counter value for the specified node.
        """
        self._load()
        return self._counters.get(node_id or self.node_id, 0)

    def merge(self, other: GCounter) -> None:
        """Merge another G-Counter into this one (take max per node).

        Args:
            other: Another GCounter to merge with.
        """
        self._load()
        other._load()
        all_nodes = set(self._counters.keys()) | set(other._counters.keys())
        for node in all_nodes:
            self._counters[node] = max(
                self._counters.get(node, 0),
                other._counters.get(node, 0),
            )
        self._save()
        logger.debug("gcounter_merged", total=self.value())

    def state(self) -> dict[str, int]:
        """Get the full counter state."""
        self._load()
        return dict(self._counters)


@dataclass
class _ORSetElement:
    """Element in an OR-Set with a unique tag."""
    value: str
    tag: str
    timestamp: float = field(default_factory=time.time)


class ORSet:
    """Observed-Remove Set CRDT (ActiveTaskSet).

    Supports concurrent add/remove. Each add generates a unique tag.
    Remove only removes observed tags. Add wins over concurrent remove.
    """

    def __init__(self, node_id: str, redis_client: Any | None = None, key: str = "crdt:orset") -> None:
        self.node_id = node_id
        self._redis = redis_client
        self._key = key
        # elements: list of {value, tag, timestamp}
        self._elements: list[dict[str, Any]] = []
        # tombstones: set of tags that have been removed
        self._tombstones: set[str] = set()

    def _load(self) -> None:
        """Load state from Redis."""
        if self._redis:
            raw = self._redis.get(self._key)
            if raw:
                data = json.loads(raw) if isinstance(raw, str) else raw
                self._elements = data.get("elements", [])
                self._tombstones = set(data.get("tombstones", []))
            else:
                self._elements = []
                self._tombstones = set()

    def _save(self) -> None:
        """Persist state to Redis."""
        if self._redis:
            data = {
                "elements": self._elements,
                "tombstones": list(self._tombstones),
            }
            self._redis.set(self._key, json.dumps(data))

    def _generate_tag(self) -> str:
        """Generate a unique tag for an add operation."""
        import uuid
        return f"{self.node_id}:{uuid.uuid4().hex[:8]}"

    def add(self, value: str) -> str:
        """Add an element to the set.

        Args:
            value: The value to add.

        Returns:
            The unique tag for this add operation.
        """
        self._load()
        tag = self._generate_tag()
        self._elements.append({
            "value": value,
            "tag": tag,
            "timestamp": time.time(),
        })
        self._save()
        logger.debug("orset_add", value=value, tag=tag, node=self.node_id)
        return tag

    def remove(self, value: str) -> bool:
        """Remove an element from the set.

        Removes all currently observed tags for this value. If a concurrent
        add happens with a new tag, that add wins (add-wins semantics).

        Args:
            value: The value to remove.

        Returns:
            True if any tags were removed, False if value not found.
        """
        self._load()
        tags_to_remove = [
            e["tag"] for e in self._elements
            if e["value"] == value and e["tag"] not in self._tombstones
        ]
        if not tags_to_remove:
            return False

        self._tombstones.update(tags_to_remove)
        self._save()
        logger.debug("orset_remove", value=value, tags_removed=len(tags_to_remove))
        return True

    def contains(self, value: str) -> bool:
        """Check if a value is in the set.

        Args:
            value: The value to check.

        Returns:
            True if the value has at least one non-tombstoned tag.
        """
        self._load()
        return any(
            e["value"] == value and e["tag"] not in self._tombstones
            for e in self._elements
        )

    def elements(self) -> set[str]:
        """Get all current elements in the set.

        Returns:
            Set of all non-tombstoned values.
        """
        self._load()
        return {
            e["value"] for e in self._elements
            if e["tag"] not in self._tombstones
        }

    def merge(self, other: ORSet) -> None:
        """Merge another OR-Set into this one.

        Union of elements, union of tombstones. Add-wins semantics means
        a value with a non-tombstoned tag in either set is present.

        Args:
            other: Another ORSet to merge with.
        """
        self._load()
        other._load()

        # Merge elements: union of all elements
        existing_tags = {e["tag"] for e in self._elements}
        for elem in other._elements:
            if elem["tag"] not in existing_tags:
                self._elements.append(elem)

        # Merge tombstones: union
        self._tombstones |= other._tombstones

        self._save()
        logger.debug("orset_merged", elements=len(self.elements()))

    def size(self) -> int:
        """Get the number of elements in the set."""
        return len(self.elements())

    def state(self) -> dict[str, Any]:
        """Get the full set state."""
        self._load()
        return {
            "elements": self._elements,
            "tombstones": list(self._tombstones),
        }


class LWWRegister:
    """Last-Writer-Wins Register CRDT (FileHealthScore).

    Each write includes a timestamp. The value with the highest timestamp wins.
    No coordination needed — most recent update wins.
    """

    def __init__(
        self,
        node_id: str,
        redis_client: Any | None = None,
        key: str = "crdt:lww",
    ) -> None:
        self.node_id = node_id
        self._redis = redis_client
        self._key = key
        self._value: Any = None
        self._timestamp: float = 0.0
        self._writer: str = ""

    def _load(self) -> None:
        """Load state from Redis."""
        if self._redis:
            raw = self._redis.get(self._key)
            if raw:
                data = json.loads(raw) if isinstance(raw, str) else raw
                self._value = data.get("value")
                self._timestamp = data.get("timestamp", 0.0)
                self._writer = data.get("writer", "")

    def _save(self) -> None:
        """Persist state to Redis."""
        if self._redis:
            data = {
                "value": self._value,
                "timestamp": self._timestamp,
                "writer": self._writer,
            }
            self._redis.set(self._key, json.dumps(data))

    def set(self, value: Any, timestamp: float | None = None) -> bool:
        """Set the register value.

        Only succeeds if the timestamp is newer than the current value.

        Args:
            value: The new value.
            timestamp: Optional timestamp. Uses current time if not provided.

        Returns:
            True if the value was updated, False if a newer value exists.
        """
        ts = timestamp or time.time()
        self._load()

        if ts > self._timestamp:
            self._value = value
            self._timestamp = ts
            self._writer = self.node_id
            self._save()
            logger.debug("lww_set", node=self.node_id, value=value, ts=ts)
            return True
        return False

    def get(self) -> Any:
        """Get the current register value.

        Returns:
            The current value, or None if never set.
        """
        self._load()
        return self._value

    def get_with_metadata(self) -> dict[str, Any]:
        """Get the value with timestamp and writer metadata.

        Returns:
            Dict with value, timestamp, and writer fields.
        """
        self._load()
        return {
            "value": self._value,
            "timestamp": self._timestamp,
            "writer": self._writer,
        }

    def merge(self, other: LWWRegister) -> None:
        """Merge another LWW-Register (take the one with higher timestamp).

        Args:
            other: Another LWWRegister to merge with.
        """
        self._load()
        other._load()
        if other._timestamp > self._timestamp:
            self._value = other._value
            self._timestamp = other._timestamp
            self._writer = other._writer
            self._save()
        logger.debug("lww_merged", value=self._value, ts=self._timestamp)

    def state(self) -> dict[str, Any]:
        """Get the full register state."""
        self._load()
        return {
            "value": self._value,
            "timestamp": self._timestamp,
            "writer": self._writer,
        }
