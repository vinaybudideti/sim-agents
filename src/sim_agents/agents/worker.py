"""Worker/Builder Agent — executes tasks with git worktree isolation.

Each Worker operates on a dedicated git branch (agent/{agent-id}/{task-id})
with its own git worktree, providing complete filesystem isolation.
Workers create pull requests for completed work.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from sim_agents.coordination.stigmergy import PheromoneType, StigmergicEnvironment

logger = structlog.get_logger(__name__)


@dataclass
class WorkResult:
    """Result of a worker executing a task."""

    task_id: int
    agent_id: str
    branch: str
    status: str  # "completed", "failed", "partial"
    files_changed: list[str] = field(default_factory=list)
    commit_hash: str = ""
    error_message: str = ""
    started_at: float = field(default_factory=time.time)
    completed_at: float = 0.0
    pr_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "branch": self.branch,
            "status": self.status,
            "files_changed": self.files_changed,
            "commit_hash": self.commit_hash,
            "error_message": self.error_message,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "pr_url": self.pr_url,
        }


class GitWorktreeManager:
    """Manages git worktrees for isolated agent workspaces."""

    def __init__(self, repo_root: str | Path) -> None:
        self.repo_root = Path(repo_root)
        self._worktrees_dir = self.repo_root / ".worktrees"

    def create_worktree(self, branch_name: str) -> Path:
        """Create a git worktree for isolated work.

        Args:
            branch_name: Name of the branch to work on.

        Returns:
            Path to the worktree directory.

        Raises:
            RuntimeError: If worktree creation fails.
        """
        worktree_path = self._worktrees_dir / branch_name.replace("/", "_")
        worktree_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            # Create branch if it doesn't exist
            self._run_git("branch", branch_name, check=False)

            # Create worktree
            self._run_git(
                "worktree", "add", str(worktree_path), branch_name,
                check=True,
            )
            logger.info("worktree_created", branch=branch_name, path=str(worktree_path))
            return worktree_path

        except subprocess.CalledProcessError as e:
            # Worktree might already exist
            if worktree_path.exists():
                logger.info("worktree_exists", branch=branch_name)
                return worktree_path
            raise RuntimeError(f"Failed to create worktree: {e.stderr}") from e

    def remove_worktree(self, branch_name: str) -> bool:
        """Remove a git worktree.

        Args:
            branch_name: Branch whose worktree to remove.

        Returns:
            True if removed, False if not found.
        """
        worktree_path = self._worktrees_dir / branch_name.replace("/", "_")
        if not worktree_path.exists():
            return False

        try:
            self._run_git("worktree", "remove", str(worktree_path), "--force")
            logger.info("worktree_removed", branch=branch_name)
            return True
        except subprocess.CalledProcessError as e:
            logger.error("worktree_remove_error", error=str(e))
            return False

    def _run_git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        """Run a git command in the repo root.

        Args:
            *args: Git command arguments.
            check: Whether to raise on non-zero exit.

        Returns:
            CompletedProcess result.
        """
        return subprocess.run(
            ["git", *args],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            check=check,
            timeout=30,
        )


class WorkerAgent:
    """Agent that executes assigned tasks with git worktree isolation.

    Each worker gets a dedicated branch and worktree to prevent file-level
    conflicts with other agents. Completed work is committed and PR-ready.
    """

    def __init__(
        self,
        project_root: str | Path,
        agent_id: str = "worker-1",
        environment: StigmergicEnvironment | None = None,
        use_worktrees: bool = False,
    ) -> None:
        self.project_root = Path(project_root)
        self.agent_id = agent_id
        self.env = environment or StigmergicEnvironment(self.project_root)
        self.use_worktrees = use_worktrees
        self._worktree_mgr = GitWorktreeManager(self.project_root) if use_worktrees else None
        self._current_task: dict[str, Any] | None = None
        self._results: list[WorkResult] = []
        logger.info("worker_initialized", agent_id=agent_id)

    def get_branch_name(self, task_id: int) -> str:
        """Generate the branch name for a task.

        Args:
            task_id: The task ID.

        Returns:
            Branch name in format agent/{agent-id}/{task-id}.
        """
        return f"agent/{self.agent_id}/{task_id}"

    def get_assignment(self) -> dict[str, Any] | None:
        """Read the current assignment from the assignments directory.

        Returns:
            Assignment dict if found, None otherwise.
        """
        filepath = self.project_root / "assignments" / f"{self.agent_id}.json"
        if not filepath.exists():
            return None

        try:
            data = json.loads(filepath.read_text())
            assignments = data if isinstance(data, list) else [data]
            # Return first pending assignment
            for a in assignments:
                if a.get("status", "pending") == "pending":
                    return a
            return None
        except (json.JSONDecodeError, OSError) as e:
            logger.error("assignment_read_error", error=str(e))
            return None

    def setup_workspace(self, task_id: int) -> Path:
        """Set up an isolated workspace for the task.

        Creates a git branch and optionally a worktree.

        Args:
            task_id: The task ID.

        Returns:
            Path to the workspace directory.
        """
        branch = self.get_branch_name(task_id)

        if self.use_worktrees and self._worktree_mgr:
            return self._worktree_mgr.create_worktree(branch)

        # Without worktrees, just create the branch
        try:
            subprocess.run(
                ["git", "checkout", "-b", branch],
                cwd=str(self.project_root),
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except (subprocess.SubprocessError, OSError):
            pass  # Branch might already exist

        logger.info("workspace_setup", branch=branch)
        return self.project_root

    def execute_task(self, task: dict[str, Any]) -> WorkResult:
        """Execute a task assignment.

        This is the main execution entry point. In production, this would
        use an LLM to implement the task. Here we provide the framework
        for task execution.

        Args:
            task: Assignment dictionary with task details.

        Returns:
            WorkResult describing the outcome.
        """
        task_id = task.get("task_id", 0)
        branch = self.get_branch_name(task_id)

        result = WorkResult(
            task_id=task_id,
            agent_id=self.agent_id,
            branch=branch,
            status="in_progress",
        )

        self._current_task = task
        logger.info("task_execution_started", task_id=task_id, agent=self.agent_id)

        try:
            # Set up workspace
            workspace = self.setup_workspace(task_id)

            # Execute the task (in production, this calls the LLM)
            files_changed = self._do_work(task, workspace)
            result.files_changed = files_changed

            # Commit changes
            commit_hash = self._commit_changes(task, workspace)
            result.commit_hash = commit_hash

            result.status = "completed"
            result.completed_at = time.time()

            logger.info(
                "task_completed",
                task_id=task_id,
                files=len(files_changed),
                commit=commit_hash[:8] if commit_hash else "none",
            )

        except Exception as e:
            result.status = "failed"
            result.error_message = str(e)
            result.completed_at = time.time()
            logger.error("task_failed", task_id=task_id, error=str(e))

        self._current_task = None
        self._results.append(result)

        # Write result as pheromone
        self.env.write_pheromone(
            PheromoneType.REVIEW,
            f"work-result-{task_id}.json",
            result.to_dict(),
            agent_id=self.agent_id,
        )

        return result

    def _do_work(self, task: dict[str, Any], workspace: Path) -> list[str]:
        """Perform the actual work for a task.

        In production, this would invoke an LLM to write code.
        This implementation creates a placeholder showing the task was processed.

        Args:
            task: Task assignment details.
            workspace: Path to the workspace directory.

        Returns:
            List of files changed.
        """
        # Create a work log entry
        logs_dir = workspace / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)

        task_id = task.get("task_id", 0)
        log_file = logs_dir / f"task-{task_id}-work.json"
        log_data = {
            "task_id": task_id,
            "agent_id": self.agent_id,
            "task_title": task.get("task_title", ""),
            "started_at": time.time(),
            "status": "executed",
        }
        log_file.write_text(json.dumps(log_data, indent=2))

        return [str(log_file.relative_to(workspace))]

    def _commit_changes(self, task: dict[str, Any], workspace: Path) -> str:
        """Commit changes in the workspace.

        Args:
            task: Task details for the commit message.
            workspace: Path to the workspace directory.

        Returns:
            Commit hash, or empty string if commit failed.
        """
        task_id = task.get("task_id", 0)
        title = task.get("task_title", "task")
        message = f"[{self.agent_id}] Complete task {task_id}: {title}"

        try:
            # Stage all changes
            subprocess.run(
                ["git", "add", "-A"],
                cwd=str(workspace),
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )

            # Commit
            result = subprocess.run(
                ["git", "commit", "-m", message, "--allow-empty"],
                cwd=str(workspace),
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )

            # Get commit hash
            hash_result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(workspace),
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
            return hash_result.stdout.strip()

        except subprocess.CalledProcessError as e:
            logger.warning("commit_failed", error=e.stderr)
            return ""

    def get_results(self) -> list[WorkResult]:
        """Get all work results from this agent.

        Returns:
            List of all WorkResult objects.
        """
        return list(self._results)

    def cleanup_workspace(self, task_id: int) -> None:
        """Clean up the workspace for a completed task.

        Args:
            task_id: The task ID to clean up.
        """
        if self.use_worktrees and self._worktree_mgr:
            branch = self.get_branch_name(task_id)
            self._worktree_mgr.remove_worktree(branch)
