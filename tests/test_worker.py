"""Tests for the Worker/Builder Agent."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from sim_agents.agents.worker import GitWorktreeManager, WorkerAgent, WorkResult
from sim_agents.coordination.stigmergy import StigmergicEnvironment


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a temporary git repository."""
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(tmp_path), capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(tmp_path), capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "commit.gpgsign", "false"],
        cwd=str(tmp_path), capture_output=True, check=True,
    )
    # Create initial commit
    readme = tmp_path / "README.md"
    readme.write_text("# Test")
    subprocess.run(["git", "add", "-A"], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=str(tmp_path), capture_output=True, check=True,
    )
    return tmp_path


@pytest.fixture
def env(git_repo: Path) -> StigmergicEnvironment:
    return StigmergicEnvironment(git_repo)


@pytest.fixture
def worker(git_repo: Path, env: StigmergicEnvironment) -> WorkerAgent:
    return WorkerAgent(git_repo, agent_id="worker-1", environment=env)


class TestWorkResult:
    def test_to_dict(self) -> None:
        r = WorkResult(
            task_id=1, agent_id="w1", branch="agent/w1/1", status="completed",
            files_changed=["file.py"], commit_hash="abc123",
        )
        d = r.to_dict()
        assert d["task_id"] == 1
        assert d["status"] == "completed"
        assert d["files_changed"] == ["file.py"]

    def test_default_values(self) -> None:
        r = WorkResult(task_id=1, agent_id="w1", branch="b", status="pending")
        assert r.files_changed == []
        assert r.error_message == ""
        assert r.pr_url == ""


class TestWorkerAgent:
    def test_get_branch_name(self, worker: WorkerAgent) -> None:
        assert worker.get_branch_name(42) == "agent/worker-1/42"

    def test_execute_task(self, worker: WorkerAgent) -> None:
        task = {
            "task_id": 1,
            "task_title": "Build feature X",
            "task_description": "Implement feature X",
        }
        result = worker.execute_task(task)
        assert result.status == "completed"
        assert result.task_id == 1
        assert result.agent_id == "worker-1"
        assert len(result.files_changed) > 0
        assert result.completed_at > 0

    def test_execute_task_creates_log(
        self, worker: WorkerAgent, git_repo: Path
    ) -> None:
        task = {"task_id": 5, "task_title": "Test task"}
        worker.execute_task(task)

        log_file = git_repo / "logs" / "task-5-work.json"
        assert log_file.exists()
        data = json.loads(log_file.read_text())
        assert data["task_id"] == 5

    def test_execute_task_commits(self, worker: WorkerAgent, git_repo: Path) -> None:
        task = {"task_id": 1, "task_title": "Test"}
        result = worker.execute_task(task)
        assert result.commit_hash != ""

    def test_get_results(self, worker: WorkerAgent) -> None:
        worker.execute_task({"task_id": 1, "task_title": "T1"})
        worker.execute_task({"task_id": 2, "task_title": "T2"})
        results = worker.get_results()
        assert len(results) == 2

    def test_get_assignment_none(self, worker: WorkerAgent) -> None:
        assert worker.get_assignment() is None

    def test_get_assignment_from_file(
        self, worker: WorkerAgent, git_repo: Path
    ) -> None:
        assignments_dir = git_repo / "assignments"
        assignments_dir.mkdir(exist_ok=True)
        filepath = assignments_dir / "worker-1.json"
        filepath.write_text(json.dumps([{
            "task_id": 1,
            "task_title": "Test",
            "status": "pending",
        }]))

        assignment = worker.get_assignment()
        assert assignment is not None
        assert assignment["task_id"] == 1

    def test_execute_writes_review_pheromone(
        self, worker: WorkerAgent, git_repo: Path
    ) -> None:
        task = {"task_id": 3, "task_title": "Review test"}
        worker.execute_task(task)

        review_file = git_repo / "reviews" / "work-result-3.json"
        assert review_file.exists()

    def test_branch_format(self, worker: WorkerAgent) -> None:
        branch = worker.get_branch_name(99)
        assert branch.startswith("agent/")
        assert "worker-1" in branch
        assert "99" in branch


class TestGitWorktreeManager:
    def test_init(self, git_repo: Path) -> None:
        mgr = GitWorktreeManager(git_repo)
        assert mgr.repo_root == git_repo

    def test_run_git(self, git_repo: Path) -> None:
        mgr = GitWorktreeManager(git_repo)
        result = mgr._run_git("status")
        assert result.returncode == 0
