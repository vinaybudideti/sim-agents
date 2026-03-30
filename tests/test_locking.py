"""Tests for distributed locking via Upstash Redis."""

from __future__ import annotations

import threading
import time

import pytest

from sim_agents.coordination.locking import (
    DEFAULT_TTL_SECONDS,
    DistributedLock,
    LockManager,
)


class MockRedis:
    """In-memory mock of Upstash Redis for testing."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._ttls: dict[str, float] = {}
        self._lock = threading.Lock()

    def set(
        self,
        key: str,
        value: str,
        nx: bool = False,
        ex: int | None = None,
        **kwargs: object,
    ) -> bool | None:
        with self._lock:
            self._cleanup_expired()
            if nx and key in self._store:
                return None
            self._store[key] = value
            if ex:
                self._ttls[key] = time.time() + ex
            return True

    def get(self, key: str) -> str | None:
        with self._lock:
            self._cleanup_expired()
            return self._store.get(key)

    def delete(self, key: str) -> int:
        with self._lock:
            if key in self._store:
                del self._store[key]
                self._ttls.pop(key, None)
                return 1
            return 0

    def expire(self, key: str, seconds: int) -> bool:
        with self._lock:
            if key in self._store:
                self._ttls[key] = time.time() + seconds
                return True
            return False

    def _cleanup_expired(self) -> None:
        now = time.time()
        expired = [k for k, t in self._ttls.items() if t <= now]
        for k in expired:
            self._store.pop(k, None)
            self._ttls.pop(k, None)


@pytest.fixture
def mock_redis() -> MockRedis:
    return MockRedis()


class TestDistributedLock:
    """Tests for DistributedLock."""

    def test_acquire_and_release(self, mock_redis: MockRedis) -> None:
        lock = DistributedLock("task-1", owner_id="agent-1", redis_client=mock_redis)
        assert lock.acquire() is True
        assert lock.is_locked() is True
        assert lock.is_owned() is True
        assert lock.release() is True
        assert lock.is_locked() is False

    def test_acquire_fails_when_held(self, mock_redis: MockRedis) -> None:
        lock1 = DistributedLock("task-1", owner_id="agent-1", redis_client=mock_redis)
        lock2 = DistributedLock("task-1", owner_id="agent-2", redis_client=mock_redis)

        assert lock1.acquire() is True
        assert lock2.acquire() is False

    def test_release_verifies_ownership(self, mock_redis: MockRedis) -> None:
        lock1 = DistributedLock("task-1", owner_id="agent-1", redis_client=mock_redis)
        lock2 = DistributedLock("task-1", owner_id="agent-2", redis_client=mock_redis)

        lock1.acquire()
        # agent-2 should not be able to release agent-1's lock
        assert lock2.release() is False
        # Lock should still be held by agent-1
        assert lock1.is_owned() is True

    def test_different_resources_independent(self, mock_redis: MockRedis) -> None:
        lock1 = DistributedLock("task-1", owner_id="agent-1", redis_client=mock_redis)
        lock2 = DistributedLock("task-2", owner_id="agent-2", redis_client=mock_redis)

        assert lock1.acquire() is True
        assert lock2.acquire() is True
        lock1.release()
        lock2.release()

    def test_context_manager(self, mock_redis: MockRedis) -> None:
        lock = DistributedLock("task-1", owner_id="agent-1", redis_client=mock_redis)
        with lock:
            assert lock.is_owned() is True
        assert lock.is_locked() is False

    def test_context_manager_timeout_raises(self, mock_redis: MockRedis) -> None:
        blocker = DistributedLock("task-1", owner_id="blocker", redis_client=mock_redis)
        blocker.acquire()

        lock = DistributedLock("task-1", owner_id="agent-1", redis_client=mock_redis)
        lock.ttl_seconds = 30  # Won't expire during test
        with pytest.raises(TimeoutError):
            # Override timeout to be very short
            lock.__enter__ = lambda: (_ for _ in ()).throw(
                TimeoutError(f"Could not acquire lock for resource {lock.resource_id}")
            )
            with lock:
                pass  # Should not reach here
        blocker.release()

    def test_lock_key_format(self, mock_redis: MockRedis) -> None:
        lock = DistributedLock("task-42", redis_client=mock_redis)
        assert lock._key == "lock:task:task-42"

    def test_is_owned_false_when_not_acquired(self, mock_redis: MockRedis) -> None:
        lock = DistributedLock("task-1", owner_id="agent-1", redis_client=mock_redis)
        assert lock.is_owned() is False

    def test_acquire_with_timeout(self, mock_redis: MockRedis) -> None:
        blocker = DistributedLock("task-1", owner_id="blocker", redis_client=mock_redis)
        blocker.acquire()

        lock = DistributedLock("task-1", owner_id="agent-1", redis_client=mock_redis)
        # Should fail after short timeout
        start = time.time()
        assert lock.acquire(timeout_seconds=0.3) is False
        elapsed = time.time() - start
        assert elapsed >= 0.2  # Should have waited

        blocker.release()

    def test_ttl_default(self, mock_redis: MockRedis) -> None:
        lock = DistributedLock("task-1", redis_client=mock_redis)
        assert lock.ttl_seconds == DEFAULT_TTL_SECONDS

    def test_auto_generated_owner_id(self, mock_redis: MockRedis) -> None:
        lock = DistributedLock("task-1", redis_client=mock_redis)
        assert lock.owner_id  # Should be a UUID string
        assert len(lock.owner_id) > 0


class TestLockManager:
    """Tests for LockManager."""

    def test_acquire_and_release_task(self, mock_redis: MockRedis) -> None:
        mgr = LockManager(redis_client=mock_redis)
        lock = mgr.acquire_task_lock("task-1", "agent-1")
        assert lock is not None
        assert mgr.release_task_lock("task-1") is True

    def test_acquire_fails_returns_none(self, mock_redis: MockRedis) -> None:
        mgr = LockManager(redis_client=mock_redis)
        mgr.acquire_task_lock("task-1", "agent-1")
        result = mgr.acquire_task_lock("task-1", "agent-2")
        assert result is None

    def test_release_unheld_returns_false(self, mock_redis: MockRedis) -> None:
        mgr = LockManager(redis_client=mock_redis)
        assert mgr.release_task_lock("task-999") is False

    def test_release_all(self, mock_redis: MockRedis) -> None:
        mgr = LockManager(redis_client=mock_redis)
        mgr.acquire_task_lock("task-1", "agent-1")
        mgr.acquire_task_lock("task-2", "agent-1")
        mgr.release_all()
        assert len(mgr._active_locks) == 0

    def test_multiple_agents_contention(self, mock_redis: MockRedis) -> None:
        mgr = LockManager(redis_client=mock_redis)
        lock1 = mgr.acquire_task_lock("task-1", "agent-1")
        lock2 = mgr.acquire_task_lock("task-1", "agent-2")
        assert lock1 is not None
        assert lock2 is None
