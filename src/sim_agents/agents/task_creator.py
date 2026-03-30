"""Task Creator Agent — scans environment and maintains backlog.json with priority scores.

The Task Creator continuously scans the project environment — reading research
artifacts, analyzing code health metrics, and monitoring the project roadmap.
It maintains backlog.json with priority scores computed from urgency gradient,
dependency depth, and estimated complexity.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from sim_agents.coordination.stigmergy import PheromoneType, StigmergicEnvironment

logger = structlog.get_logger(__name__)


@dataclass
class BacklogItem:
    """A single task in the backlog."""

    id: int
    title: str
    description: str
    priority_score: float
    urgency: float
    dependency_depth: float
    complexity: float
    required_skills: list[str] = field(default_factory=list)
    estimated_turns: int = 10
    dependencies: list[int] = field(default_factory=list)
    status: str = "open"
    assigned_to: str | None = None
    created_at: float = field(default_factory=time.time)
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "priority_score": self.priority_score,
            "urgency": self.urgency,
            "dependency_depth": self.dependency_depth,
            "complexity": self.complexity,
            "required_skills": self.required_skills,
            "estimated_turns": self.estimated_turns,
            "dependencies": self.dependencies,
            "status": self.status,
            "assigned_to": self.assigned_to,
            "created_at": self.created_at,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BacklogItem:
        """Create a BacklogItem from a dictionary."""
        return cls(
            id=data["id"],
            title=data["title"],
            description=data.get("description", ""),
            priority_score=data.get("priority_score", 0),
            urgency=data.get("urgency", 0),
            dependency_depth=data.get("dependency_depth", 0),
            complexity=data.get("complexity", 0),
            required_skills=data.get("required_skills", []),
            estimated_turns=data.get("estimated_turns", 10),
            dependencies=data.get("dependencies", []),
            status=data.get("status", "open"),
            assigned_to=data.get("assigned_to"),
            created_at=data.get("created_at", time.time()),
            source=data.get("source", ""),
        )


def compute_priority_score(
    urgency: float,
    dependency_depth: float,
    complexity: float,
    weights: dict[str, float] | None = None,
) -> float:
    """Compute priority score from urgency, dependency depth, and complexity.

    Higher urgency and dependency_depth increase priority.
    Higher complexity decreases priority (prefer simpler tasks first).

    Args:
        urgency: How urgent the task is (0-10).
        dependency_depth: How many tasks depend on this (0-10).
        complexity: Estimated complexity (0-10).
        weights: Optional custom weights.

    Returns:
        Priority score (higher = more important).
    """
    w = weights or {"urgency": 4.0, "dependency_depth": 4.0, "complexity": 2.0}
    score = (
        w["urgency"] * urgency
        + w["dependency_depth"] * dependency_depth
        + w["complexity"] * (10 - complexity)  # Invert: simpler = higher priority
    )
    return round(score, 2)


class TaskCreatorAgent:
    """Agent that scans the environment and maintains the task backlog.

    Reads research artifacts, code health, and project state to generate
    and prioritize tasks in backlog.json.
    """

    def __init__(
        self,
        project_root: str | Path,
        environment: StigmergicEnvironment | None = None,
        agent_id: str = "task-creator",
    ) -> None:
        self.project_root = Path(project_root)
        self.agent_id = agent_id
        self.env = environment or StigmergicEnvironment(self.project_root)
        self._backlog_path = self.project_root / "backlog.json"
        logger.info("task_creator_initialized", agent_id=agent_id)

    def load_backlog(self) -> dict[str, Any]:
        """Load the current backlog from backlog.json.

        Returns:
            Backlog data with tasks list and next_id.
        """
        if not self._backlog_path.exists():
            return {"tasks": [], "next_id": 1}
        try:
            data = json.loads(self._backlog_path.read_text())
            return data
        except (json.JSONDecodeError, OSError) as e:
            logger.error("backlog_load_error", error=str(e))
            return {"tasks": [], "next_id": 1}

    def save_backlog(self, backlog: dict[str, Any]) -> None:
        """Save the backlog to backlog.json.

        Args:
            backlog: Backlog data to save.
        """
        self._backlog_path.write_text(json.dumps(backlog, indent=2, default=str))
        logger.info("backlog_saved", tasks=len(backlog.get("tasks", [])))

    def create_task(
        self,
        title: str,
        description: str,
        urgency: float = 5.0,
        dependency_depth: float = 0.0,
        complexity: float = 5.0,
        required_skills: list[str] | None = None,
        estimated_turns: int = 10,
        dependencies: list[int] | None = None,
        source: str = "",
    ) -> BacklogItem:
        """Create a new task and add it to the backlog.

        Args:
            title: Task title.
            description: Task description.
            urgency: Urgency gradient (0-10).
            dependency_depth: How many tasks depend on this (0-10).
            complexity: Estimated complexity (0-10).
            required_skills: Skills needed.
            estimated_turns: Estimated agent turns to complete.
            dependencies: IDs of tasks this depends on.
            source: Where this task originated from.

        Returns:
            The created BacklogItem.
        """
        backlog = self.load_backlog()
        task_id = backlog.get("next_id", 1)

        priority = compute_priority_score(urgency, dependency_depth, complexity)

        item = BacklogItem(
            id=task_id,
            title=title,
            description=description,
            priority_score=priority,
            urgency=urgency,
            dependency_depth=dependency_depth,
            complexity=complexity,
            required_skills=required_skills or [],
            estimated_turns=estimated_turns,
            dependencies=dependencies or [],
            source=source,
        )

        backlog["tasks"].append(item.to_dict())
        backlog["next_id"] = task_id + 1
        self.save_backlog(backlog)

        logger.info(
            "task_created",
            task_id=task_id,
            title=title,
            priority=priority,
        )
        return item

    def scan_and_create_tasks(self) -> list[BacklogItem]:
        """Scan the environment for new findings and create tasks from them.

        Reads research artifacts from intel/findings/ and creates corresponding
        tasks if they haven't been processed yet.

        Returns:
            List of newly created BacklogItems.
        """
        scan = self.env.scan_environment()
        new_tasks: list[BacklogItem] = []
        backlog = self.load_backlog()
        existing_sources = {
            t.get("source", "") for t in backlog.get("tasks", [])
        }

        for artifact in scan.research_artifacts:
            source_path = artifact.get("path", "")
            if source_path in existing_sources:
                continue

            topic = artifact.get("topic", "unknown")
            applicability = artifact.get("applicability", 0.5)

            task = self.create_task(
                title=f"Implement finding: {topic}",
                description=f"Based on research artifact: {source_path}",
                urgency=applicability * 10,
                complexity=5.0,
                source=source_path,
            )
            new_tasks.append(task)

        if new_tasks:
            logger.info("tasks_from_scan", count=len(new_tasks))
        return new_tasks

    def reprioritize(self) -> None:
        """Recalculate priority scores for all open tasks.

        Updates priority scores based on current urgency, dependency depth,
        and complexity values. Re-sorts the backlog.
        """
        backlog = self.load_backlog()
        for task in backlog.get("tasks", []):
            if task.get("status") == "open":
                task["priority_score"] = compute_priority_score(
                    task.get("urgency", 0),
                    task.get("dependency_depth", 0),
                    task.get("complexity", 0),
                )
        # Sort by priority (highest first)
        backlog["tasks"].sort(
            key=lambda t: t.get("priority_score", 0), reverse=True
        )
        self.save_backlog(backlog)
        logger.info("backlog_reprioritized")

    def get_open_tasks(self) -> list[BacklogItem]:
        """Get all open tasks sorted by priority.

        Returns:
            List of open BacklogItems sorted by priority (highest first).
        """
        backlog = self.load_backlog()
        open_tasks = [
            BacklogItem.from_dict(t)
            for t in backlog.get("tasks", [])
            if t.get("status") == "open"
        ]
        open_tasks.sort(key=lambda t: t.priority_score, reverse=True)
        return open_tasks

    def update_task_status(self, task_id: int, status: str) -> bool:
        """Update the status of a task.

        Args:
            task_id: ID of the task to update.
            status: New status (open, in_progress, completed, failed).

        Returns:
            True if task was found and updated, False otherwise.
        """
        backlog = self.load_backlog()
        for task in backlog.get("tasks", []):
            if task["id"] == task_id:
                task["status"] = status
                self.save_backlog(backlog)
                logger.info("task_status_updated", task_id=task_id, status=status)
                return True
        return False

    def get_health_derived_tasks(self) -> list[BacklogItem]:
        """Create tasks from health report danger signals.

        Scans health reports for issues and creates high-priority tasks.

        Returns:
            List of newly created tasks from health issues.
        """
        health_pheromones = self.env.scan_pheromones(PheromoneType.HEALTH)
        new_tasks: list[BacklogItem] = []
        backlog = self.load_backlog()
        existing_sources = {
            t.get("source", "") for t in backlog.get("tasks", [])
        }

        for pheromone in health_pheromones:
            source = pheromone.path
            if source in existing_sources:
                continue

            data = pheromone.data
            if data.get("status") == "failing" or data.get("score", 1.0) < 0.5:
                task = self.create_task(
                    title=f"Fix health issue: {data.get('component', 'unknown')}",
                    description=f"Health report indicates failure: {source}",
                    urgency=9.0,
                    dependency_depth=8.0,
                    complexity=6.0,
                    source=source,
                )
                new_tasks.append(task)

        return new_tasks
