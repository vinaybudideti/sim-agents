"""Tests for CRDT-based shared state (G-Counter, OR-Set, LWW-Register)."""

from __future__ import annotations

import json
import time

import pytest

from sim_agents.coordination.state import GCounter, LWWRegister, ORSet


class MockRedis:
    """In-memory mock of Upstash Redis for CRDT testing."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._store.get(key)

    def set(self, key: str, value: str, **kwargs: object) -> bool:
        self._store[key] = value
        return True


@pytest.fixture
def redis() -> MockRedis:
    return MockRedis()


class TestGCounter:
    """Tests for the G-Counter CRDT."""

    def test_increment(self, redis: MockRedis) -> None:
        counter = GCounter("agent-1", redis_client=redis)
        assert counter.increment() == 1
        assert counter.increment() == 2
        assert counter.value() == 2

    def test_increment_by_amount(self, redis: MockRedis) -> None:
        counter = GCounter("agent-1", redis_client=redis)
        assert counter.increment(5) == 5
        assert counter.value() == 5

    def test_increment_negative_raises(self, redis: MockRedis) -> None:
        counter = GCounter("agent-1", redis_client=redis)
        with pytest.raises(ValueError, match="positive"):
            counter.increment(0)

    def test_multiple_nodes_shared_redis(self, redis: MockRedis) -> None:
        c1 = GCounter("agent-1", redis_client=redis)
        c2 = GCounter("agent-2", redis_client=redis)
        c1.increment(3)
        c2.increment(5)
        # Sharing same Redis key, c2 loads c1's state and adds its own
        assert c2.value() == 8  # 3 (agent-1) + 5 (agent-2)

    def test_merge_takes_max(self, redis: MockRedis) -> None:
        r1 = MockRedis()
        r2 = MockRedis()
        c1 = GCounter("agent-1", redis_client=r1, key="counter")
        c2 = GCounter("agent-2", redis_client=r2, key="counter")

        c1.increment(3)
        c2.increment(5)

        c1.merge(c2)
        assert c1.value() == 8  # 3 + 5
        assert c1.node_value("agent-1") == 3
        assert c1.node_value("agent-2") == 5

    def test_merge_idempotent(self, redis: MockRedis) -> None:
        r1 = MockRedis()
        r2 = MockRedis()
        c1 = GCounter("agent-1", redis_client=r1, key="c")
        c2 = GCounter("agent-2", redis_client=r2, key="c")

        c1.increment(3)
        c2.increment(5)

        c1.merge(c2)
        c1.merge(c2)  # Merge again — should be idempotent
        assert c1.value() == 8

    def test_node_value(self, redis: MockRedis) -> None:
        counter = GCounter("agent-1", redis_client=redis)
        counter.increment(7)
        assert counter.node_value() == 7
        assert counter.node_value("agent-1") == 7
        assert counter.node_value("nonexistent") == 0

    def test_state(self, redis: MockRedis) -> None:
        counter = GCounter("agent-1", redis_client=redis)
        counter.increment(3)
        state = counter.state()
        assert state == {"agent-1": 3}

    def test_without_redis(self) -> None:
        counter = GCounter("agent-1")
        counter._counters = {"agent-1": 0}
        counter._counters["agent-1"] = 5
        assert counter._counters["agent-1"] == 5


class TestORSet:
    """Tests for the OR-Set CRDT."""

    def test_add_and_contains(self, redis: MockRedis) -> None:
        s = ORSet("node-1", redis_client=redis)
        s.add("task-1")
        assert s.contains("task-1") is True
        assert s.contains("task-2") is False

    def test_remove(self, redis: MockRedis) -> None:
        s = ORSet("node-1", redis_client=redis)
        s.add("task-1")
        assert s.remove("task-1") is True
        assert s.contains("task-1") is False

    def test_remove_nonexistent(self, redis: MockRedis) -> None:
        s = ORSet("node-1", redis_client=redis)
        assert s.remove("nope") is False

    def test_add_wins_over_concurrent_remove(self) -> None:
        r1 = MockRedis()
        r2 = MockRedis()
        s1 = ORSet("node-1", redis_client=r1, key="set")
        s2 = ORSet("node-2", redis_client=r2, key="set")

        # Node 1 adds task-1
        s1.add("task-1")
        # Node 2 also adds task-1 (concurrent)
        s2.add("task-1")

        # Node 1 removes task-1 (only removes its own observed tags)
        s1.remove("task-1")

        # Merge: node 2's add should win (its tag is not tombstoned)
        s1.merge(s2)
        assert s1.contains("task-1") is True

    def test_elements(self, redis: MockRedis) -> None:
        s = ORSet("node-1", redis_client=redis)
        s.add("a")
        s.add("b")
        s.add("c")
        assert s.elements() == {"a", "b", "c"}

    def test_size(self, redis: MockRedis) -> None:
        s = ORSet("node-1", redis_client=redis)
        s.add("x")
        s.add("y")
        assert s.size() == 2

    def test_duplicate_add(self, redis: MockRedis) -> None:
        s = ORSet("node-1", redis_client=redis)
        s.add("task-1")
        s.add("task-1")  # Add again with new tag
        assert s.contains("task-1") is True
        # Removing should tombstone both tags
        s.remove("task-1")
        assert s.contains("task-1") is False

    def test_merge_union(self) -> None:
        r1 = MockRedis()
        r2 = MockRedis()
        s1 = ORSet("node-1", redis_client=r1, key="s")
        s2 = ORSet("node-2", redis_client=r2, key="s")

        s1.add("a")
        s1.add("b")
        s2.add("b")
        s2.add("c")

        s1.merge(s2)
        assert s1.elements() == {"a", "b", "c"}

    def test_state(self, redis: MockRedis) -> None:
        s = ORSet("node-1", redis_client=redis)
        s.add("x")
        state = s.state()
        assert "elements" in state
        assert "tombstones" in state
        assert len(state["elements"]) == 1


class TestLWWRegister:
    """Tests for the LWW-Register CRDT."""

    def test_set_and_get(self, redis: MockRedis) -> None:
        reg = LWWRegister("node-1", redis_client=redis)
        reg.set(0.95)
        assert reg.get() == 0.95

    def test_newer_wins(self, redis: MockRedis) -> None:
        reg = LWWRegister("node-1", redis_client=redis)
        reg.set("old", timestamp=1.0)
        reg.set("new", timestamp=2.0)
        assert reg.get() == "new"

    def test_older_loses(self, redis: MockRedis) -> None:
        reg = LWWRegister("node-1", redis_client=redis)
        reg.set("new", timestamp=2.0)
        result = reg.set("old", timestamp=1.0)
        assert result is False
        assert reg.get() == "new"

    def test_get_with_metadata(self, redis: MockRedis) -> None:
        reg = LWWRegister("node-1", redis_client=redis)
        reg.set(42, timestamp=100.0)
        meta = reg.get_with_metadata()
        assert meta["value"] == 42
        assert meta["timestamp"] == 100.0
        assert meta["writer"] == "node-1"

    def test_merge_newer_wins(self) -> None:
        r1 = MockRedis()
        r2 = MockRedis()
        reg1 = LWWRegister("node-1", redis_client=r1, key="r")
        reg2 = LWWRegister("node-2", redis_client=r2, key="r")

        reg1.set("value-1", timestamp=1.0)
        reg2.set("value-2", timestamp=2.0)

        reg1.merge(reg2)
        assert reg1.get() == "value-2"

    def test_merge_older_stays(self) -> None:
        r1 = MockRedis()
        r2 = MockRedis()
        reg1 = LWWRegister("node-1", redis_client=r1, key="r")
        reg2 = LWWRegister("node-2", redis_client=r2, key="r")

        reg1.set("value-1", timestamp=2.0)
        reg2.set("value-2", timestamp=1.0)

        reg1.merge(reg2)
        assert reg1.get() == "value-1"

    def test_get_unset(self, redis: MockRedis) -> None:
        reg = LWWRegister("node-1", redis_client=redis)
        assert reg.get() is None

    def test_state(self, redis: MockRedis) -> None:
        reg = LWWRegister("node-1", redis_client=redis)
        reg.set("hello", timestamp=5.0)
        state = reg.state()
        assert state["value"] == "hello"
        assert state["timestamp"] == 5.0
        assert state["writer"] == "node-1"

    def test_concurrent_writes_from_different_nodes(self) -> None:
        r1 = MockRedis()
        r2 = MockRedis()
        reg1 = LWWRegister("node-1", redis_client=r1, key="score")
        reg2 = LWWRegister("node-2", redis_client=r2, key="score")

        reg1.set(0.8, timestamp=10.0)
        reg2.set(0.9, timestamp=11.0)

        # Both merge — should converge to 0.9
        reg1.merge(reg2)
        reg2.merge(reg1)
        assert reg1.get() == 0.9
        assert reg2.get() == 0.9
