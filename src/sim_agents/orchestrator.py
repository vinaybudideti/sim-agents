"""SIM Orchestrator — wires all 10 agents through the SIM loop.

Full stigmergic coordination flow:
Task Creator > Assigner > Workers > QA > Project Agent > Homeostatic check > loop
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from sim_agents.agents.notes import NotesAgent
from sim_agents.agents.project_runner import ProjectRunnerAgent
from sim_agents.agents.qa_checker import QACheckerAgent, ReviewVerdict
from sim_agents.agents.researcher import ResearchAgent
from sim_agents.agents.task_assigner import AgentProfile, TaskAssignerAgent
from sim_agents.agents.task_creator import TaskCreatorAgent
from sim_agents.agents.verifier import VerifierAgent
from sim_agents.agents.worker import WorkerAgent
from sim_agents.coordination.locking import LockManager
from sim_agents.coordination.stigmergy import StigmergicEnvironment

logger = structlog.get_logger(__name__)


@dataclass
class CycleResult:
    """Result of a single SIM orchestration cycle."""

    cycle_number: int
    tasks_created: int = 0
    tasks_assigned: int = 0
    tasks_completed: int = 0
    tasks_approved: int = 0
    tasks_rejected: int = 0
    health_status: str = "unknown"
    homeostatic_ok: bool = True
    duration_seconds: float = 0.0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_number": self.cycle_number,
            "tasks_created": self.tasks_created,
            "tasks_assigned": self.tasks_assigned,
            "tasks_completed": self.tasks_completed,
            "tasks_approved": self.tasks_approved,
            "tasks_rejected": self.tasks_rejected,
            "health_status": self.health_status,
            "homeostatic_ok": self.homeostatic_ok,
            "duration_seconds": self.duration_seconds,
            "errors": self.errors,
        }


@dataclass
class HomeostaticState:
    """Current state of homeostatic variables."""

    test_pass_rate: float = 1.0
    code_coverage: float = 1.0
    error_rate: float = 0.0
    token_budget_remaining: float = 1.0
    agent_throughput: float = 1.0

    def is_within_bounds(self) -> bool:
        """Check if all variables are within homeostatic set points."""
        return (
            self.test_pass_rate >= 0.95
            and self.code_coverage >= 0.80
            and self.error_rate <= 0.01
            and self.token_budget_remaining >= 0.20
        )


class SIMOrchestrator:
    """Orchestrates all 10 SIM agents through the coordination loop.

    The orchestrator doesn't command agents directly — it triggers
    stigmergic cycles where agents read the shared environment,
    do their work, and write back to the environment.
    """

    def __init__(
        self,
        project_root: str | Path,
        num_workers: int = 2,
        redis_client: Any | None = None,
    ) -> None:
        self.project_root = Path(project_root)
        self.num_workers = num_workers

        # Shared environment and coordination
        self.env = StigmergicEnvironment(self.project_root)
        self.lock_manager = LockManager(redis_client=redis_client)

        # Orchestrator tier
        self.task_creator = TaskCreatorAgent(
            self.project_root, environment=self.env, agent_id="task-creator"
        )
        self.task_assigner = TaskAssignerAgent(
            self.project_root,
            environment=self.env,
            lock_manager=self.lock_manager,
            agent_id="task-assigner",
        )
        self.notes_agent = NotesAgent(
            self.project_root, environment=self.env, agent_id="notes-agent"
        )

        # Specialist tier
        self.workers: list[WorkerAgent] = []
        for i in range(num_workers):
            worker = WorkerAgent(
                self.project_root,
                agent_id=f"worker-{i + 1}",
                environment=self.env,
            )
            self.workers.append(worker)
            self.task_assigner.register_agent(
                AgentProfile(
                    agent_id=f"worker-{i + 1}",
                    skills=["python", "testing", "documentation"],
                    max_load=3,
                )
            )

        self.qa_checker = QACheckerAgent(
            self.project_root, environment=self.env, agent_id="qa-checker"
        )
        self.researcher = ResearchAgent(
            self.project_root, environment=self.env, agent_id="researcher"
        )
        self.verifier = VerifierAgent(
            self.project_root, environment=self.env, agent_id="verifier"
        )
        self.project_runner = ProjectRunnerAgent(
            self.project_root, environment=self.env, agent_id="project-runner"
        )

        # State
        self._cycle_count = 0
        self._cycle_history: list[CycleResult] = []
        self._homeostatic = HomeostaticState()
        self._total_budget: float = 100.0
        self._spent_budget: float = 0.0

        logger.info(
            "orchestrator_initialized",
            workers=num_workers,
            root=str(self.project_root),
        )

    def run_cycle(self) -> CycleResult:
        """Run a single SIM orchestration cycle.

        Flow: Task Creator > Assigner > Workers > QA > Project Agent > Homeostatic

        Returns:
            CycleResult with cycle metrics.
        """
        self._cycle_count += 1
        start = time.time()
        result = CycleResult(cycle_number=self._cycle_count)

        logger.info("cycle_started", cycle=self._cycle_count)

        try:
            # Phase 1: Task Creation — scan environment for new tasks
            new_tasks = self.task_creator.scan_and_create_tasks()
            health_tasks = self.task_creator.get_health_derived_tasks()
            result.tasks_created = len(new_tasks) + len(health_tasks)

            # Phase 2: Task Assignment — Vickrey auction
            assignments = self.task_assigner.assign_all_open_tasks(self.task_creator)
            result.tasks_assigned = len(assignments)

            # Phase 3: Worker Execution
            for assignment in assignments:
                worker = self._get_worker(assignment.agent_id)
                if worker:
                    work_result = worker.execute_task(assignment.to_dict())
                    if work_result.status == "completed":
                        result.tasks_completed += 1

                        # Phase 4: QA Review
                        review = self.qa_checker.review_work(
                            assignment.task_id,
                            work_result.to_dict(),
                        )
                        if review.verdict == ReviewVerdict.APPROVED:
                            result.tasks_approved += 1
                            self.task_creator.update_task_status(
                                assignment.task_id, "completed"
                            )
                        else:
                            result.tasks_rejected += 1
                            self.task_creator.update_task_status(
                                assignment.task_id, "needs_revision"
                            )

            # Phase 5: Research & Verification
            self.verifier.verify_all_unverified()

            # Phase 6: Project Health Check
            health_report = self.project_runner.run_health_check()
            result.health_status = health_report.overall_status

            # Phase 7: Homeostatic Check
            self._update_homeostatic(health_report)
            result.homeostatic_ok = self._homeostatic.is_within_bounds()

            # Phase 8: Notes — update documentation
            self.notes_agent.generate_status_summary()

        except Exception as e:
            result.errors.append(str(e))
            logger.error("cycle_error", cycle=self._cycle_count, error=str(e))

        result.duration_seconds = round(time.time() - start, 2)
        self._cycle_history.append(result)

        # Release locks from completed tasks
        self.lock_manager.release_all()

        logger.info(
            "cycle_completed",
            cycle=self._cycle_count,
            created=result.tasks_created,
            assigned=result.tasks_assigned,
            completed=result.tasks_completed,
            approved=result.tasks_approved,
            rejected=result.tasks_rejected,
            health=result.health_status,
            duration=result.duration_seconds,
        )
        return result

    def run(self, max_cycles: int = 1, budget: float = 100.0) -> list[CycleResult]:
        """Run the SIM loop for multiple cycles.

        Args:
            max_cycles: Maximum number of cycles to run.
            budget: Total budget for the run.

        Returns:
            List of CycleResults from all cycles.
        """
        self._total_budget = budget
        self._spent_budget = 0.0
        results: list[CycleResult] = []

        logger.info("sim_run_started", max_cycles=max_cycles, budget=budget)

        for i in range(max_cycles):
            # Check budget
            if self._spent_budget >= self._total_budget:
                logger.warning("budget_exhausted", spent=self._spent_budget)
                break

            result = self.run_cycle()
            results.append(result)

            # Simulate budget consumption
            self._spent_budget += result.duration_seconds * 0.1

            # If homeostatic check fails, enter recovery mode
            if not result.homeostatic_ok:
                logger.warning("homeostatic_violation", cycle=i + 1)

        logger.info(
            "sim_run_completed",
            cycles=len(results),
            total_approved=sum(r.tasks_approved for r in results),
        )
        return results

    def _get_worker(self, agent_id: str) -> WorkerAgent | None:
        """Get a worker by agent ID."""
        for w in self.workers:
            if w.agent_id == agent_id:
                return w
        return None

    def _update_homeostatic(self, health_report: Any) -> None:
        """Update homeostatic state from health report."""
        for metric in health_report.metrics:
            if metric.name == "test_pass_rate":
                self._homeostatic.test_pass_rate = metric.value
        self._homeostatic.token_budget_remaining = max(
            0.0, 1.0 - (self._spent_budget / max(self._total_budget, 1.0))
        )

    def get_status(self) -> dict[str, Any]:
        """Get current orchestrator status.

        Returns:
            Status dict with cycle history and health info.
        """
        return {
            "cycles_completed": self._cycle_count,
            "total_tasks_approved": sum(
                r.tasks_approved for r in self._cycle_history
            ),
            "total_tasks_rejected": sum(
                r.tasks_rejected for r in self._cycle_history
            ),
            "health_status": (
                self._cycle_history[-1].health_status
                if self._cycle_history
                else "unknown"
            ),
            "homeostatic_ok": self._homeostatic.is_within_bounds(),
            "budget_remaining": round(
                self._total_budget - self._spent_budget, 2
            ),
            "workers": len(self.workers),
        }

    def get_cycle_history(self) -> list[CycleResult]:
        """Get all cycle results."""
        return list(self._cycle_history)
