"""Stigmergic coordination layer — digital pheromone reading/writing and environment scanning.

Agents coordinate indirectly by reading and modifying shared environment artifacts
(intel/findings/, assignments/, reviews/, health/) rather than direct messaging.
Filesystem monitoring via watchdog detects environment changes in real time.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

import structlog
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

logger = structlog.get_logger(__name__)


class PheromoneType(Enum):
    """Types of digital pheromones in the stigmergic environment."""

    RESEARCH = "intel/findings"
    ASSIGNMENT = "assignments"
    REVIEW = "reviews"
    HEALTH = "health"
    LOG = "logs"
    NOTIFICATION = "notifications"


@dataclass
class Pheromone:
    """A digital pheromone — a structured artifact left in the shared environment."""

    pheromone_type: PheromoneType
    path: str
    data: dict[str, Any]
    timestamp: float = field(default_factory=time.time)
    agent_id: str = ""
    strength: float = 1.0

    def decay(self, rate: float = 0.01) -> None:
        """Reduce pheromone strength over time (evaporation)."""
        elapsed = time.time() - self.timestamp
        self.strength = max(0.0, self.strength - (rate * elapsed))


@dataclass
class EnvironmentScan:
    """Result of scanning the stigmergic environment."""

    research_artifacts: list[dict[str, Any]]
    pending_assignments: list[dict[str, Any]]
    pending_reviews: list[dict[str, Any]]
    health_reports: list[dict[str, Any]]
    scan_timestamp: float = field(default_factory=time.time)


class StigmergicEnvironment:
    """Manages the shared environment for stigmergic agent coordination.

    Agents read and write digital pheromones (structured JSON artifacts) to
    designated directories. The environment mediates all coordination — agents
    never communicate directly.
    """

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root)
        self._observers: list[Observer] = []
        self._event_handlers: dict[PheromoneType, list[Callable[[Pheromone], None]]] = {}
        self._ensure_directories()
        logger.info("stigmergic_environment_initialized", root=str(self.project_root))

    def _ensure_directories(self) -> None:
        """Create all pheromone directories if they don't exist."""
        for ptype in PheromoneType:
            directory = self.project_root / ptype.value
            directory.mkdir(parents=True, exist_ok=True)

    def get_pheromone_path(self, ptype: PheromoneType) -> Path:
        """Get the filesystem path for a pheromone type."""
        return self.project_root / ptype.value

    def write_pheromone(
        self,
        ptype: PheromoneType,
        filename: str,
        data: dict[str, Any],
        agent_id: str = "",
    ) -> Pheromone:
        """Write a digital pheromone (artifact) to the environment.

        Args:
            ptype: The type of pheromone to write.
            filename: Name of the artifact file (should end in .json).
            data: Structured data to write.
            agent_id: ID of the agent writing the pheromone.

        Returns:
            The written Pheromone object.
        """
        directory = self.get_pheromone_path(ptype)
        filepath = directory / filename
        timestamp = time.time()

        artifact = {
            "agent_id": agent_id,
            "timestamp": timestamp,
            "type": ptype.value,
            "data": data,
        }

        filepath.write_text(json.dumps(artifact, indent=2, default=str))
        logger.info(
            "pheromone_written",
            type=ptype.value,
            file=filename,
            agent=agent_id,
        )

        pheromone = Pheromone(
            pheromone_type=ptype,
            path=str(filepath),
            data=data,
            timestamp=timestamp,
            agent_id=agent_id,
        )

        # Notify subscribers
        for handler in self._event_handlers.get(ptype, []):
            try:
                handler(pheromone)
            except Exception as e:
                logger.error("pheromone_handler_error", error=str(e), type=ptype.value)

        return pheromone

    def read_pheromone(self, ptype: PheromoneType, filename: str) -> Pheromone | None:
        """Read a single pheromone artifact from the environment.

        Args:
            ptype: The type of pheromone to read.
            filename: Name of the artifact file.

        Returns:
            Pheromone object if found, None otherwise.
        """
        filepath = self.get_pheromone_path(ptype) / filename
        if not filepath.exists():
            return None

        try:
            artifact = json.loads(filepath.read_text())
            return Pheromone(
                pheromone_type=ptype,
                path=str(filepath),
                data=artifact.get("data", {}),
                timestamp=artifact.get("timestamp", 0.0),
                agent_id=artifact.get("agent_id", ""),
            )
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("pheromone_read_error", file=str(filepath), error=str(e))
            return None

    def scan_pheromones(self, ptype: PheromoneType) -> list[Pheromone]:
        """Scan all pheromones of a given type.

        Args:
            ptype: The type of pheromones to scan.

        Returns:
            List of all pheromones found, sorted by timestamp (newest first).
        """
        directory = self.get_pheromone_path(ptype)
        pheromones: list[Pheromone] = []

        for filepath in directory.glob("*.json"):
            try:
                artifact = json.loads(filepath.read_text())
                pheromones.append(
                    Pheromone(
                        pheromone_type=ptype,
                        path=str(filepath),
                        data=artifact.get("data", {}),
                        timestamp=artifact.get("timestamp", 0.0),
                        agent_id=artifact.get("agent_id", ""),
                    )
                )
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("pheromone_scan_error", file=str(filepath), error=str(e))

        pheromones.sort(key=lambda p: p.timestamp, reverse=True)
        return pheromones

    def scan_environment(self) -> EnvironmentScan:
        """Perform a full scan of the stigmergic environment.

        Returns:
            EnvironmentScan with all current artifacts across all pheromone types.
        """
        research = self.scan_pheromones(PheromoneType.RESEARCH)
        assignments = self.scan_pheromones(PheromoneType.ASSIGNMENT)
        reviews = self.scan_pheromones(PheromoneType.REVIEW)
        health = self.scan_pheromones(PheromoneType.HEALTH)

        scan = EnvironmentScan(
            research_artifacts=[{"path": p.path, **p.data} for p in research],
            pending_assignments=[{"path": p.path, **p.data} for p in assignments],
            pending_reviews=[{"path": p.path, **p.data} for p in reviews],
            health_reports=[{"path": p.path, **p.data} for p in health],
        )

        logger.info(
            "environment_scanned",
            research=len(scan.research_artifacts),
            assignments=len(scan.pending_assignments),
            reviews=len(scan.pending_reviews),
            health=len(scan.health_reports),
        )
        return scan

    def subscribe(
        self, ptype: PheromoneType, handler: Callable[[Pheromone], None]
    ) -> None:
        """Subscribe to pheromone events of a given type.

        Args:
            ptype: The pheromone type to subscribe to.
            handler: Callback function invoked when a pheromone of this type is written.
        """
        if ptype not in self._event_handlers:
            self._event_handlers[ptype] = []
        self._event_handlers[ptype].append(handler)
        logger.info("pheromone_subscription_added", type=ptype.value)

    def start_filesystem_watch(self) -> None:
        """Start watching pheromone directories for external changes via watchdog."""
        for ptype in PheromoneType:
            directory = self.get_pheromone_path(ptype)
            handler = _PheromoneFileHandler(self, ptype)
            observer = Observer()
            observer.schedule(handler, str(directory), recursive=False)
            observer.daemon = True
            observer.start()
            self._observers.append(observer)
        logger.info("filesystem_watch_started")

    def stop_filesystem_watch(self) -> None:
        """Stop all filesystem watchers."""
        for observer in self._observers:
            observer.stop()
        for observer in self._observers:
            observer.join(timeout=5)
        self._observers.clear()
        logger.info("filesystem_watch_stopped")

    def remove_pheromone(self, ptype: PheromoneType, filename: str) -> bool:
        """Remove a pheromone artifact from the environment.

        Args:
            ptype: The pheromone type.
            filename: Name of the artifact file.

        Returns:
            True if removed, False if not found.
        """
        filepath = self.get_pheromone_path(ptype) / filename
        if filepath.exists():
            filepath.unlink()
            logger.info("pheromone_removed", type=ptype.value, file=filename)
            return True
        return False


class _PheromoneFileHandler(FileSystemEventHandler):
    """Watchdog handler that converts filesystem events to pheromone events."""

    def __init__(self, env: StigmergicEnvironment, ptype: PheromoneType) -> None:
        self.env = env
        self.ptype = ptype

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory or not str(event.src_path).endswith(".json"):
            return
        filename = os.path.basename(str(event.src_path))
        pheromone = self.env.read_pheromone(self.ptype, filename)
        if pheromone:
            for handler in self.env._event_handlers.get(self.ptype, []):
                try:
                    handler(pheromone)
                except Exception as e:
                    logger.error(
                        "filesystem_handler_error",
                        error=str(e),
                        file=filename,
                    )

    def on_modified(self, event: FileSystemEvent) -> None:
        self.on_created(event)
