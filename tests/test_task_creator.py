"""Tests for the Task Creator Agent."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sim_agents.agents.task_creator import (
    BacklogItem,
    TaskCreatorAgent,
    compute_priority_score,
)
from sim_agents.coordination.stigmergy import PheromoneType, StigmergicEnvironment


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def env(project_root: Path) -> StigmergicEnvironment:
    return StigmergicEnvironment(project_root)


@pytest.fixture
def agent(project_root: Path, env: StigmergicEnvironment) -> TaskCreatorAgent:
    return TaskCreatorAgent(project_root, environment=env)


class TestComputePriorityScore:
    def test_basic_score(self) -> None:
        score = compute_priority_score(urgency=10, dependency_depth=10, complexity=0)
        assert score > 0

    def test_higher_urgency_higher_score(self) -> None:
        low = compute_priority_score(urgency=1, dependency_depth=5, complexity=5)
        high = compute_priority_score(urgency=9, dependency_depth=5, complexity=5)
        assert high > low

    def test_higher_complexity_lower_score(self) -> None:
        simple = compute_priority_score(urgency=5, dependency_depth=5, complexity=1)
        complex_ = compute_priority_score(urgency=5, dependency_depth=5, complexity=9)
        assert simple > complex_

    def test_higher_dependency_depth_higher_score(self) -> None:
        low_dep = compute_priority_score(urgency=5, dependency_depth=1, complexity=5)
        high_dep = compute_priority_score(urgency=5, dependency_depth=9, complexity=5)
        assert high_dep > low_dep


class TestBacklogItem:
    def test_to_dict(self) -> None:
        item = BacklogItem(
            id=1, title="Test", description="Desc",
            priority_score=50.0, urgency=5.0,
            dependency_depth=3.0, complexity=4.0,
        )
        d = item.to_dict()
        assert d["id"] == 1
        assert d["title"] == "Test"
        assert d["status"] == "open"

    def test_from_dict(self) -> None:
        data = {
            "id": 1, "title": "Test", "description": "Desc",
            "priority_score": 50.0, "urgency": 5.0,
            "dependency_depth": 3.0, "complexity": 4.0,
        }
        item = BacklogItem.from_dict(data)
        assert item.id == 1
        assert item.title == "Test"

    def test_roundtrip(self) -> None:
        item = BacklogItem(
            id=42, title="Roundtrip", description="Test",
            priority_score=75.0, urgency=8.0,
            dependency_depth=5.0, complexity=3.0,
            required_skills=["python"],
        )
        restored = BacklogItem.from_dict(item.to_dict())
        assert restored.id == item.id
        assert restored.title == item.title
        assert restored.required_skills == ["python"]


class TestTaskCreatorAgent:
    def test_create_task(self, agent: TaskCreatorAgent) -> None:
        item = agent.create_task(
            title="Build module X",
            description="Implement module X",
            urgency=8.0,
            complexity=4.0,
        )
        assert item.id == 1
        assert item.title == "Build module X"
        assert item.priority_score > 0

    def test_create_multiple_tasks_increments_id(self, agent: TaskCreatorAgent) -> None:
        t1 = agent.create_task("Task 1", "First", urgency=5.0)
        t2 = agent.create_task("Task 2", "Second", urgency=5.0)
        assert t2.id == t1.id + 1

    def test_load_save_backlog(self, agent: TaskCreatorAgent) -> None:
        agent.create_task("Test", "Description", urgency=5.0)
        backlog = agent.load_backlog()
        assert len(backlog["tasks"]) == 1
        assert backlog["next_id"] == 2

    def test_load_nonexistent_backlog(self, project_root: Path) -> None:
        backlog_path = project_root / "backlog.json"
        if backlog_path.exists():
            backlog_path.unlink()
        a = TaskCreatorAgent(project_root)
        backlog = a.load_backlog()
        assert backlog == {"tasks": [], "next_id": 1}

    def test_get_open_tasks(self, agent: TaskCreatorAgent) -> None:
        agent.create_task("Open 1", "Desc", urgency=3.0)
        agent.create_task("Open 2", "Desc", urgency=8.0)
        open_tasks = agent.get_open_tasks()
        assert len(open_tasks) == 2
        # Should be sorted by priority, highest first
        assert open_tasks[0].urgency >= open_tasks[1].urgency

    def test_update_task_status(self, agent: TaskCreatorAgent) -> None:
        item = agent.create_task("Status test", "Desc", urgency=5.0)
        assert agent.update_task_status(item.id, "completed") is True
        open_tasks = agent.get_open_tasks()
        assert len(open_tasks) == 0

    def test_update_nonexistent_task(self, agent: TaskCreatorAgent) -> None:
        assert agent.update_task_status(999, "completed") is False

    def test_reprioritize(self, agent: TaskCreatorAgent) -> None:
        agent.create_task("Low", "Desc", urgency=1.0, complexity=9.0)
        agent.create_task("High", "Desc", urgency=9.0, complexity=1.0)
        agent.reprioritize()
        backlog = agent.load_backlog()
        tasks = backlog["tasks"]
        assert tasks[0]["title"] == "High"

    def test_scan_and_create_tasks(
        self, agent: TaskCreatorAgent, env: StigmergicEnvironment
    ) -> None:
        env.write_pheromone(
            PheromoneType.RESEARCH,
            "2026-03-30-optimization.json",
            {"topic": "optimization", "applicability": 0.8},
            agent_id="researcher-1",
        )
        new_tasks = agent.scan_and_create_tasks()
        assert len(new_tasks) == 1
        assert "optimization" in new_tasks[0].title

    def test_scan_no_duplicates(
        self, agent: TaskCreatorAgent, env: StigmergicEnvironment
    ) -> None:
        env.write_pheromone(
            PheromoneType.RESEARCH,
            "finding.json",
            {"topic": "caching", "applicability": 0.7},
        )
        first = agent.scan_and_create_tasks()
        second = agent.scan_and_create_tasks()
        assert len(first) == 1
        assert len(second) == 0  # Should not create duplicates

    def test_health_derived_tasks(
        self, agent: TaskCreatorAgent, env: StigmergicEnvironment
    ) -> None:
        env.write_pheromone(
            PheromoneType.HEALTH,
            "report.json",
            {"component": "api-server", "status": "failing", "score": 0.3},
        )
        tasks = agent.get_health_derived_tasks()
        assert len(tasks) == 1
        assert "api-server" in tasks[0].title

    def test_health_no_tasks_when_healthy(
        self, agent: TaskCreatorAgent, env: StigmergicEnvironment
    ) -> None:
        env.write_pheromone(
            PheromoneType.HEALTH,
            "ok.json",
            {"component": "db", "status": "ok", "score": 0.95},
        )
        tasks = agent.get_health_derived_tasks()
        assert len(tasks) == 0
