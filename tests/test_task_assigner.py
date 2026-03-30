"""Tests for the Task Assigner Agent."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from sim_agents.agents.task_assigner import (
    AgentProfile,
    Assignment,
    TaskAssignerAgent,
)
from sim_agents.agents.task_creator import BacklogItem, TaskCreatorAgent
from sim_agents.coordination.locking import LockManager
from sim_agents.coordination.stigmergy import StigmergicEnvironment


class MockRedis:
    """In-memory mock Redis for locking tests."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def set(self, key: str, value: str, nx: bool = False, ex: int | None = None, **kw: object) -> bool | None:
        if nx and key in self._store:
            return None
        self._store[key] = value
        return True

    def get(self, key: str) -> str | None:
        return self._store.get(key)

    def delete(self, key: str) -> int:
        return 1 if self._store.pop(key, None) is not None else 0

    def expire(self, key: str, seconds: int) -> bool:
        return key in self._store


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def mock_redis() -> MockRedis:
    return MockRedis()


@pytest.fixture
def env(project_root: Path) -> StigmergicEnvironment:
    return StigmergicEnvironment(project_root)


@pytest.fixture
def lock_mgr(mock_redis: MockRedis) -> LockManager:
    return LockManager(redis_client=mock_redis)


@pytest.fixture
def assigner(
    project_root: Path, env: StigmergicEnvironment, lock_mgr: LockManager
) -> TaskAssignerAgent:
    return TaskAssignerAgent(project_root, environment=env, lock_manager=lock_mgr)


def _make_task(
    task_id: int = 1,
    title: str = "Test Task",
    skills: list[str] | None = None,
    urgency: float = 5.0,
) -> BacklogItem:
    return BacklogItem(
        id=task_id,
        title=title,
        description="Test description",
        priority_score=50.0,
        urgency=urgency,
        dependency_depth=5.0,
        complexity=5.0,
        required_skills=skills or ["python"],
    )


class TestAgentProfile:
    def test_fitness_with_matching_skills(self) -> None:
        profile = AgentProfile(agent_id="w1", skills=["python", "testing"])
        task = _make_task(skills=["python"])
        score = profile.fitness_for_task(task)
        assert score > 0

    def test_fitness_no_matching_skills(self) -> None:
        profile = AgentProfile(agent_id="w1", skills=["java"])
        task = _make_task(skills=["python"])
        score = profile.fitness_for_task(task)
        # Still > 0 due to load factor, but skill component is 0
        assert score >= 0

    def test_fitness_at_max_load(self) -> None:
        profile = AgentProfile(agent_id="w1", skills=["python"], current_load=3, max_load=3)
        task = _make_task()
        assert profile.fitness_for_task(task) == 0.0

    def test_fitness_no_required_skills(self) -> None:
        profile = AgentProfile(agent_id="w1", skills=["python"])
        task = _make_task(skills=[])
        score = profile.fitness_for_task(task)
        assert score > 0

    def test_fitness_prefers_less_loaded(self) -> None:
        light = AgentProfile(agent_id="w1", skills=["python"], current_load=0)
        heavy = AgentProfile(agent_id="w2", skills=["python"], current_load=2)
        task = _make_task()
        assert light.fitness_for_task(task) > heavy.fitness_for_task(task)


class TestVickreyAuction:
    def test_auction_selects_best_fit(self, assigner: TaskAssignerAgent) -> None:
        assigner.register_agent(AgentProfile("w1", skills=["python", "testing"]))
        assigner.register_agent(AgentProfile("w2", skills=["java"]))
        task = _make_task(skills=["python", "testing"])

        result = assigner.vickrey_auction(task)
        assert result is not None
        assert result.agent_id == "w1"

    def test_auction_records_second_price(self, assigner: TaskAssignerAgent) -> None:
        assigner.register_agent(AgentProfile("w1", skills=["python"]))
        assigner.register_agent(AgentProfile("w2", skills=["python"]))
        task = _make_task()

        result = assigner.vickrey_auction(task)
        assert result is not None
        assert result.second_price > 0

    def test_auction_no_agents(self, assigner: TaskAssignerAgent) -> None:
        task = _make_task()
        result = assigner.vickrey_auction(task)
        assert result is None

    def test_auction_single_agent(self, assigner: TaskAssignerAgent) -> None:
        assigner.register_agent(AgentProfile("w1", skills=["python"]))
        task = _make_task()
        result = assigner.vickrey_auction(task)
        assert result is not None
        assert result.second_price == 0.0  # No second bidder


class TestTaskAssignment:
    def test_assign_task(self, assigner: TaskAssignerAgent) -> None:
        assigner.register_agent(AgentProfile("w1", skills=["python"]))
        task = _make_task()
        result = assigner.assign_task(task)
        assert result is not None
        assert result.agent_id == "w1"

    def test_assign_writes_to_file(
        self, assigner: TaskAssignerAgent, project_root: Path
    ) -> None:
        assigner.register_agent(AgentProfile("w1", skills=["python"]))
        task = _make_task()
        assigner.assign_task(task)

        filepath = project_root / "assignments" / "w1.json"
        assert filepath.exists()
        data = json.loads(filepath.read_text())
        assert len(data) == 1
        assert data[0]["task_id"] == 1

    def test_assign_updates_agent_load(self, assigner: TaskAssignerAgent) -> None:
        assigner.register_agent(AgentProfile("w1", skills=["python"]))
        task = _make_task()
        assigner.assign_task(task)
        assert assigner._agents["w1"].current_load == 1

    def test_assign_prevents_double_claim(
        self, assigner: TaskAssignerAgent, mock_redis: MockRedis
    ) -> None:
        assigner.register_agent(AgentProfile("w1", skills=["python"]))
        assigner.register_agent(AgentProfile("w2", skills=["python"]))

        task = _make_task()
        # First assignment succeeds
        r1 = assigner.assign_task(task)
        assert r1 is not None

        # Same task can't be assigned again (lock held)
        r2 = assigner.assign_task(task)
        assert r2 is None

    def test_get_agent_assignments(self, assigner: TaskAssignerAgent) -> None:
        assigner.register_agent(AgentProfile("w1", skills=["python"]))
        assigner.assign_task(_make_task(task_id=1))
        assigner.assign_task(_make_task(task_id=2, title="Task 2"))

        assignments = assigner.get_agent_assignments("w1")
        assert len(assignments) == 2

    def test_get_empty_assignments(self, assigner: TaskAssignerAgent) -> None:
        assert assigner.get_agent_assignments("nonexistent") == []

    def test_register_unregister(self, assigner: TaskAssignerAgent) -> None:
        assigner.register_agent(AgentProfile("w1", skills=["python"]))
        assert len(assigner.get_available_agents()) == 1
        assigner.unregister_agent("w1")
        assert len(assigner.get_available_agents()) == 0

    def test_assign_all_open_tasks(
        self, assigner: TaskAssignerAgent, project_root: Path, env: StigmergicEnvironment
    ) -> None:
        tc = TaskCreatorAgent(project_root, environment=env)
        tc.create_task("Task A", "Desc", urgency=8.0, required_skills=["python"])
        tc.create_task("Task B", "Desc", urgency=6.0, required_skills=["python"])

        assigner.register_agent(AgentProfile("w1", skills=["python"]))
        assigner.register_agent(AgentProfile("w2", skills=["python"]))

        results = assigner.assign_all_open_tasks(tc)
        assert len(results) == 2


class TestAssignment:
    def test_to_dict(self) -> None:
        a = Assignment(
            task_id=1, agent_id="w1",
            task_title="Test", task_description="Desc",
            bid_score=75.0, second_price=60.0,
        )
        d = a.to_dict()
        assert d["task_id"] == 1
        assert d["bid_score"] == 75.0
        assert d["second_price"] == 60.0
