"""Task Assigner Agent — Vickrey auction allocation with distributed locking.

Reads backlog.json, performs Vickrey auction-style allocation where each agent's
fitness for a task is scored based on capabilities. Writes assignments to
assignments/{agent-id}.json. Uses distributed locking to prevent two agents
from claiming the same task.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from sim_agents.agents.task_creator import BacklogItem, TaskCreatorAgent
from sim_agents.coordination.locking import LockManager
from sim_agents.coordination.stigmergy import PheromoneType, StigmergicEnvironment

logger = structlog.get_logger(__name__)


@dataclass
class AgentProfile:
    """Profile describing an agent's capabilities and current state."""

    agent_id: str
    skills: list[str] = field(default_factory=list)
    current_load: int = 0
    max_load: int = 3
    performance_history: dict[str, float] = field(default_factory=dict)

    def fitness_for_task(self, task: BacklogItem) -> float:
        """Calculate this agent's fitness score for a task.

        Based on skill match, current load, and past performance on similar tasks.

        Args:
            task: The task to evaluate fitness for.

        Returns:
            Fitness score (higher = better fit). 0 if at max load.
        """
        if self.current_load >= self.max_load:
            return 0.0

        # Skill match score (0-1)
        if task.required_skills:
            matched = sum(1 for s in task.required_skills if s in self.skills)
            skill_score = matched / len(task.required_skills)
        else:
            skill_score = 0.5  # No specific skills required

        # Load factor (prefer less loaded agents)
        load_factor = 1.0 - (self.current_load / self.max_load)

        # Performance history bonus
        perf_bonus = 0.0
        for skill in task.required_skills:
            if skill in self.performance_history:
                perf_bonus += self.performance_history[skill]
        if task.required_skills:
            perf_bonus /= len(task.required_skills)

        return round(skill_score * 40 + load_factor * 30 + perf_bonus * 30, 2)


@dataclass
class Assignment:
    """A task assignment to an agent."""

    task_id: int
    agent_id: str
    task_title: str
    task_description: str
    assigned_at: float = field(default_factory=time.time)
    status: str = "pending"
    bid_score: float = 0.0
    second_price: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "task_title": self.task_title,
            "task_description": self.task_description,
            "assigned_at": self.assigned_at,
            "status": self.status,
            "bid_score": self.bid_score,
            "second_price": self.second_price,
        }


class TaskAssignerAgent:
    """Agent that assigns tasks using Vickrey auction mechanics.

    In a Vickrey auction, the highest bidder wins but pays the second-highest
    price. This ensures truthful capability reporting — agents cannot game
    the system by overclaiming abilities.
    """

    def __init__(
        self,
        project_root: str | Path,
        environment: StigmergicEnvironment | None = None,
        lock_manager: LockManager | None = None,
        agent_id: str = "task-assigner",
    ) -> None:
        self.project_root = Path(project_root)
        self.agent_id = agent_id
        self.env = environment or StigmergicEnvironment(self.project_root)
        self.lock_manager = lock_manager or LockManager()
        self._agents: dict[str, AgentProfile] = {}
        self._assignments_dir = self.project_root / "assignments"
        self._assignments_dir.mkdir(parents=True, exist_ok=True)
        logger.info("task_assigner_initialized", agent_id=agent_id)

    def register_agent(self, profile: AgentProfile) -> None:
        """Register an agent as available for task assignment.

        Args:
            profile: The agent's capability profile.
        """
        self._agents[profile.agent_id] = profile
        logger.info("agent_registered", agent_id=profile.agent_id, skills=profile.skills)

    def unregister_agent(self, agent_id: str) -> None:
        """Remove an agent from the available pool.

        Args:
            agent_id: ID of the agent to unregister.
        """
        self._agents.pop(agent_id, None)
        logger.info("agent_unregistered", agent_id=agent_id)

    def get_available_agents(self) -> list[AgentProfile]:
        """Get all agents that can accept new tasks.

        Returns:
            List of agents with available capacity.
        """
        return [
            a for a in self._agents.values()
            if a.current_load < a.max_load
        ]

    def vickrey_auction(self, task: BacklogItem) -> Assignment | None:
        """Run a Vickrey auction for a task.

        Each available agent bids their fitness score. The highest bidder wins
        and pays the second-highest price (for truthful reporting incentive).

        Args:
            task: The task to auction.

        Returns:
            Assignment if successful, None if no suitable agent found.
        """
        available = self.get_available_agents()
        if not available:
            logger.warning("no_available_agents", task_id=task.id)
            return None

        # Collect bids
        bids: list[tuple[AgentProfile, float]] = []
        for agent in available:
            score = agent.fitness_for_task(task)
            if score > 0:
                bids.append((agent, score))

        if not bids:
            logger.warning("no_suitable_agents", task_id=task.id)
            return None

        # Sort by score descending
        bids.sort(key=lambda b: b[1], reverse=True)

        winner, winning_bid = bids[0]
        second_price = bids[1][1] if len(bids) > 1 else 0.0

        assignment = Assignment(
            task_id=task.id,
            agent_id=winner.agent_id,
            task_title=task.title,
            task_description=task.description,
            bid_score=winning_bid,
            second_price=second_price,
        )

        logger.info(
            "vickrey_auction_result",
            task_id=task.id,
            winner=winner.agent_id,
            bid=winning_bid,
            second_price=second_price,
        )
        return assignment

    def assign_task(self, task: BacklogItem) -> Assignment | None:
        """Assign a task through Vickrey auction with distributed locking.

        Acquires a lock on the task before assignment to prevent double-claiming.

        Args:
            task: The task to assign.

        Returns:
            Assignment if successful, None if failed.
        """
        # Run auction
        assignment = self.vickrey_auction(task)
        if not assignment:
            return None

        # Try to acquire lock
        lock = self.lock_manager.acquire_task_lock(
            str(task.id),
            assignment.agent_id,
            timeout_seconds=5,
        )
        if not lock:
            logger.warning(
                "task_lock_failed",
                task_id=task.id,
                agent_id=assignment.agent_id,
            )
            return None

        # Write pheromone for stigmergic coordination (uses envelope format)
        self.env.write_pheromone(
            PheromoneType.ASSIGNMENT,
            f"pheromone-{assignment.agent_id}-{assignment.task_id}.json",
            assignment.to_dict(),
            agent_id=self.agent_id,
        )

        # Write structured assignment list to assignments/{agent-id}.json
        self._write_assignment(assignment)

        # Update agent load
        agent = self._agents.get(assignment.agent_id)
        if agent:
            agent.current_load += 1

        return assignment

    def _write_assignment(self, assignment: Assignment) -> None:
        """Write assignment to the assignments directory.

        Args:
            assignment: The assignment to write.
        """
        filepath = self._assignments_dir / f"{assignment.agent_id}.json"

        # Load existing assignments for this agent
        existing: list[dict[str, Any]] = []
        if filepath.exists():
            try:
                data = json.loads(filepath.read_text())
                existing = data if isinstance(data, list) else [data]
            except (json.JSONDecodeError, OSError):
                existing = []

        existing.append(assignment.to_dict())
        filepath.write_text(json.dumps(existing, indent=2, default=str))

    def assign_all_open_tasks(
        self, task_creator: TaskCreatorAgent
    ) -> list[Assignment]:
        """Assign all open tasks from the backlog.

        Args:
            task_creator: TaskCreatorAgent to get open tasks from.

        Returns:
            List of successful assignments.
        """
        open_tasks = task_creator.get_open_tasks()
        assignments: list[Assignment] = []

        for task in open_tasks:
            assignment = self.assign_task(task)
            if assignment:
                task_creator.update_task_status(task.id, "in_progress")
                assignments.append(assignment)

        logger.info(
            "batch_assignment_complete",
            total_tasks=len(open_tasks),
            assigned=len(assignments),
        )
        return assignments

    def get_agent_assignments(self, agent_id: str) -> list[dict[str, Any]]:
        """Get all assignments for a specific agent.

        Args:
            agent_id: The agent to query assignments for.

        Returns:
            List of assignment dicts.
        """
        filepath = self._assignments_dir / f"{agent_id}.json"
        if not filepath.exists():
            return []
        try:
            data = json.loads(filepath.read_text())
            return data if isinstance(data, list) else [data]
        except (json.JSONDecodeError, OSError):
            return []
