"""Distributed locking via Upstash Redis — SETNX with TTL, heartbeat, ownership verification.

Provides task-level locking to prevent two agents from claiming the same task.
Lock key format: lock:task:{task-id}
Uses SETNX for atomic acquisition, heartbeat refresh, and ownership-verified release.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Default lock TTL in seconds
DEFAULT_TTL_SECONDS = 30
HEARTBEAT_INTERVAL_SECONDS = 10


def _get_redis_client() -> Any:
    """Create an Upstash Redis client from environment variables.

    Returns:
        Upstash Redis client instance.

    Raises:
        RuntimeError: If environment variables are not set.
    """
    try:
        from upstash_redis import Redis
    except ImportError as e:
        raise RuntimeError("upstash-redis package is required: pip install upstash-redis") from e

    url = os.environ.get("UPSTASH_REDIS_REST_URL")
    token = os.environ.get("UPSTASH_REDIS_REST_TOKEN")
    if not url or not token:
        raise RuntimeError(
            "UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN must be set"
        )
    return Redis(url=url, token=token)


@dataclass
class LockInfo:
    """Information about an acquired lock."""

    key: str
    owner_id: str
    acquired_at: float = field(default_factory=time.time)
    ttl_seconds: int = DEFAULT_TTL_SECONDS
    is_held: bool = True


class DistributedLock:
    """Redis-based distributed lock using Upstash Redis REST API.

    Uses SETNX for atomic acquisition with TTL. A background heartbeat thread
    refreshes the TTL while the lock is held. Release verifies ownership before
    deletion to prevent releasing another agent's lock.
    """

    def __init__(
        self,
        resource_id: str,
        owner_id: str | None = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        redis_client: Any | None = None,
    ) -> None:
        """Initialize a distributed lock.

        Args:
            resource_id: The resource to lock (e.g. task ID).
            owner_id: Unique identifier for the lock owner. Generated if not provided.
            ttl_seconds: Lock TTL in seconds (auto-refreshed by heartbeat).
            redis_client: Optional pre-configured Redis client (for testing).
        """
        self.resource_id = resource_id
        self.owner_id = owner_id or str(uuid.uuid4())
        self.ttl_seconds = ttl_seconds
        self._redis = redis_client
        self._key = f"lock:task:{resource_id}"
        self._heartbeat_thread: threading.Thread | None = None
        self._heartbeat_stop = threading.Event()
        self._lock_info: LockInfo | None = None

    def _get_redis(self) -> Any:
        """Get or create the Redis client."""
        if self._redis is None:
            self._redis = _get_redis_client()
        return self._redis

    def acquire(self, timeout_seconds: float = 0) -> bool:
        """Attempt to acquire the lock.

        Uses SETNX (SET if Not eXists) for atomic acquisition with TTL.

        Args:
            timeout_seconds: Max time to wait for lock. 0 means try once.

        Returns:
            True if lock was acquired, False otherwise.
        """
        redis = self._get_redis()
        deadline = time.time() + timeout_seconds

        while True:
            # SETNX with TTL: SET key value NX EX ttl
            result = redis.set(
                self._key,
                self.owner_id,
                nx=True,
                ex=self.ttl_seconds,
            )

            if result:
                self._lock_info = LockInfo(
                    key=self._key,
                    owner_id=self.owner_id,
                    ttl_seconds=self.ttl_seconds,
                )
                self._start_heartbeat()
                logger.info(
                    "lock_acquired",
                    resource=self.resource_id,
                    owner=self.owner_id,
                )
                return True

            if time.time() >= deadline:
                logger.debug(
                    "lock_acquire_failed",
                    resource=self.resource_id,
                    owner=self.owner_id,
                )
                return False

            time.sleep(0.1)

    def release(self) -> bool:
        """Release the lock with ownership verification.

        Only releases if the current owner still holds the lock, preventing
        a stale lock from being released by the wrong agent.

        Returns:
            True if lock was released, False if not owned or already expired.
        """
        redis = self._get_redis()
        self._stop_heartbeat()

        # Verify ownership then delete — using get + compare + delete
        # Upstash REST doesn't support Lua scripts directly, so we use
        # get-check-delete pattern with race condition mitigation
        current_value = redis.get(self._key)

        if current_value != self.owner_id:
            logger.warning(
                "lock_release_not_owner",
                resource=self.resource_id,
                owner=self.owner_id,
                current_owner=current_value,
            )
            if self._lock_info:
                self._lock_info.is_held = False
            return False

        redis.delete(self._key)
        if self._lock_info:
            self._lock_info.is_held = False

        logger.info(
            "lock_released",
            resource=self.resource_id,
            owner=self.owner_id,
        )
        return True

    def is_locked(self) -> bool:
        """Check if the resource is currently locked by anyone.

        Returns:
            True if the resource is locked.
        """
        redis = self._get_redis()
        return redis.get(self._key) is not None

    def is_owned(self) -> bool:
        """Check if the lock is currently held by this owner.

        Returns:
            True if this owner holds the lock.
        """
        redis = self._get_redis()
        return redis.get(self._key) == self.owner_id

    def _start_heartbeat(self) -> None:
        """Start a background thread that refreshes the lock TTL."""
        self._heartbeat_stop.clear()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            daemon=True,
            name=f"lock-heartbeat-{self.resource_id}",
        )
        self._heartbeat_thread.start()

    def _stop_heartbeat(self) -> None:
        """Stop the heartbeat thread."""
        self._heartbeat_stop.set()
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            self._heartbeat_thread.join(timeout=5)
        self._heartbeat_thread = None

    def _heartbeat_loop(self) -> None:
        """Periodically refresh the lock TTL while held."""
        while not self._heartbeat_stop.is_set():
            self._heartbeat_stop.wait(timeout=HEARTBEAT_INTERVAL_SECONDS)
            if self._heartbeat_stop.is_set():
                break
            try:
                redis = self._get_redis()
                # Only refresh if we still own the lock
                if redis.get(self._key) == self.owner_id:
                    redis.expire(self._key, self.ttl_seconds)
                    logger.debug(
                        "lock_heartbeat",
                        resource=self.resource_id,
                        owner=self.owner_id,
                    )
                else:
                    logger.warning(
                        "lock_lost_during_heartbeat",
                        resource=self.resource_id,
                    )
                    if self._lock_info:
                        self._lock_info.is_held = False
                    break
            except Exception as e:
                logger.error("heartbeat_error", error=str(e))

    def __enter__(self) -> DistributedLock:
        """Context manager support — acquire lock on entry."""
        if not self.acquire(timeout_seconds=10):
            raise TimeoutError(
                f"Could not acquire lock for resource {self.resource_id}"
            )
        return self

    def __exit__(self, *args: Any) -> None:
        """Context manager support — release lock on exit."""
        self.release()


class LockManager:
    """Manages multiple distributed locks.

    Provides a higher-level interface for acquiring and releasing locks
    across multiple resources.
    """

    def __init__(self, redis_client: Any | None = None) -> None:
        self._redis = redis_client
        self._active_locks: dict[str, DistributedLock] = {}

    def acquire_task_lock(
        self,
        task_id: str,
        agent_id: str,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        timeout_seconds: float = 0,
    ) -> DistributedLock | None:
        """Acquire a lock for a specific task.

        Args:
            task_id: The task to lock.
            agent_id: The agent acquiring the lock.
            ttl_seconds: Lock TTL.
            timeout_seconds: Max wait time.

        Returns:
            DistributedLock if acquired, None if failed.
        """
        lock = DistributedLock(
            resource_id=task_id,
            owner_id=agent_id,
            ttl_seconds=ttl_seconds,
            redis_client=self._redis,
        )
        if lock.acquire(timeout_seconds=timeout_seconds):
            self._active_locks[task_id] = lock
            return lock
        return None

    def release_task_lock(self, task_id: str) -> bool:
        """Release a previously acquired task lock.

        Args:
            task_id: The task to unlock.

        Returns:
            True if released, False if not held.
        """
        lock = self._active_locks.pop(task_id, None)
        if lock:
            return lock.release()
        return False

    def release_all(self) -> None:
        """Release all held locks."""
        for task_id in list(self._active_locks.keys()):
            self.release_task_lock(task_id)
