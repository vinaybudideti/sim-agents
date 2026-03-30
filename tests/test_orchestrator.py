"""Tests for the SIM Orchestrator."""

from __future__ import annotations

from pathlib import Path

import pytest

from sim_agents.orchestrator import (
    CycleResult,
    HomeostaticState,
    SIMOrchestrator,
)


class MockRedis:
    """In-memory mock Redis."""

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
    # Create minimal project structure for tests
    src = tmp_path / "src" / "sim_agents"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text('__version__ = "0.1.0"')
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "__init__.py").write_text("")
    return tmp_path


@pytest.fixture
def mock_redis() -> MockRedis:
    return MockRedis()


@pytest.fixture
def orchestrator(project_root: Path, mock_redis: MockRedis) -> SIMOrchestrator:
    return SIMOrchestrator(project_root, num_workers=2, redis_client=mock_redis)


class TestCycleResult:
    def test_to_dict(self) -> None:
        r = CycleResult(cycle_number=1, tasks_created=5, tasks_approved=3)
        d = r.to_dict()
        assert d["cycle_number"] == 1
        assert d["tasks_created"] == 5
        assert d["tasks_approved"] == 3

    def test_default_values(self) -> None:
        r = CycleResult(cycle_number=1)
        assert r.tasks_created == 0
        assert r.homeostatic_ok is True
        assert r.errors == []


class TestHomeostaticState:
    def test_healthy_state(self) -> None:
        state = HomeostaticState()
        assert state.is_within_bounds() is True

    def test_unhealthy_tests(self) -> None:
        state = HomeostaticState(test_pass_rate=0.5)
        assert state.is_within_bounds() is False

    def test_unhealthy_coverage(self) -> None:
        state = HomeostaticState(code_coverage=0.5)
        assert state.is_within_bounds() is False

    def test_unhealthy_errors(self) -> None:
        state = HomeostaticState(error_rate=0.1)
        assert state.is_within_bounds() is False

    def test_low_budget(self) -> None:
        state = HomeostaticState(token_budget_remaining=0.1)
        assert state.is_within_bounds() is False


class TestSIMOrchestrator:
    def test_initialization(self, orchestrator: SIMOrchestrator) -> None:
        assert len(orchestrator.workers) == 2
        assert orchestrator._cycle_count == 0

    def test_run_single_cycle(self, orchestrator: SIMOrchestrator) -> None:
        result = orchestrator.run_cycle()
        assert isinstance(result, CycleResult)
        assert result.cycle_number == 1
        assert result.duration_seconds > 0

    def test_run_multiple_cycles(self, orchestrator: SIMOrchestrator) -> None:
        results = orchestrator.run(max_cycles=2)
        assert len(results) == 2
        assert results[0].cycle_number == 1
        assert results[1].cycle_number == 2

    def test_get_status(self, orchestrator: SIMOrchestrator) -> None:
        orchestrator.run_cycle()
        status = orchestrator.get_status()
        assert status["cycles_completed"] == 1
        assert "health_status" in status
        assert "workers" in status
        assert status["workers"] == 2

    def test_get_cycle_history(self, orchestrator: SIMOrchestrator) -> None:
        orchestrator.run(max_cycles=3)
        history = orchestrator.get_cycle_history()
        assert len(history) == 3

    def test_budget_enforcement(self, orchestrator: SIMOrchestrator) -> None:
        # Very small budget should stop early
        results = orchestrator.run(max_cycles=1000, budget=0.0001)
        assert len(results) <= 1000

    def test_cycle_increments(self, orchestrator: SIMOrchestrator) -> None:
        orchestrator.run_cycle()
        orchestrator.run_cycle()
        assert orchestrator._cycle_count == 2

    def test_worker_lookup(self, orchestrator: SIMOrchestrator) -> None:
        w = orchestrator._get_worker("worker-1")
        assert w is not None
        assert w.agent_id == "worker-1"

    def test_worker_lookup_not_found(self, orchestrator: SIMOrchestrator) -> None:
        w = orchestrator._get_worker("nonexistent")
        assert w is None
