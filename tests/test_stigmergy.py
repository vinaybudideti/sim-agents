"""Tests for stigmergic coordination layer."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from sim_agents.coordination.stigmergy import (
    EnvironmentScan,
    Pheromone,
    PheromoneType,
    StigmergicEnvironment,
)


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """Create a temporary project root."""
    return tmp_path


@pytest.fixture
def env(project_root: Path) -> StigmergicEnvironment:
    """Create a StigmergicEnvironment instance."""
    return StigmergicEnvironment(project_root)


class TestPheromone:
    """Tests for the Pheromone dataclass."""

    def test_pheromone_creation(self) -> None:
        p = Pheromone(
            pheromone_type=PheromoneType.RESEARCH,
            path="/test/path.json",
            data={"key": "value"},
            agent_id="agent-1",
        )
        assert p.pheromone_type == PheromoneType.RESEARCH
        assert p.data == {"key": "value"}
        assert p.agent_id == "agent-1"
        assert p.strength == 1.0

    def test_pheromone_decay(self) -> None:
        p = Pheromone(
            pheromone_type=PheromoneType.RESEARCH,
            path="/test",
            data={},
            timestamp=time.time() - 100,
        )
        p.decay(rate=0.01)
        assert p.strength < 1.0

    def test_pheromone_decay_never_negative(self) -> None:
        p = Pheromone(
            pheromone_type=PheromoneType.RESEARCH,
            path="/test",
            data={},
            timestamp=time.time() - 10000,
        )
        p.decay(rate=0.01)
        assert p.strength >= 0.0


class TestStigmergicEnvironment:
    """Tests for the StigmergicEnvironment."""

    def test_directories_created(self, env: StigmergicEnvironment, project_root: Path) -> None:
        for ptype in PheromoneType:
            assert (project_root / ptype.value).is_dir()

    def test_write_pheromone(self, env: StigmergicEnvironment, project_root: Path) -> None:
        pheromone = env.write_pheromone(
            PheromoneType.RESEARCH,
            "test-finding.json",
            {"topic": "testing", "applicability": 0.9},
            agent_id="researcher-1",
        )
        assert pheromone.pheromone_type == PheromoneType.RESEARCH
        assert pheromone.data["topic"] == "testing"

        filepath = project_root / "intel" / "findings" / "test-finding.json"
        assert filepath.exists()
        content = json.loads(filepath.read_text())
        assert content["agent_id"] == "researcher-1"
        assert content["data"]["topic"] == "testing"

    def test_read_pheromone(self, env: StigmergicEnvironment) -> None:
        env.write_pheromone(
            PheromoneType.ASSIGNMENT,
            "worker-1.json",
            {"task_id": 42, "status": "pending"},
            agent_id="assigner-1",
        )
        result = env.read_pheromone(PheromoneType.ASSIGNMENT, "worker-1.json")
        assert result is not None
        assert result.data["task_id"] == 42

    def test_read_nonexistent_pheromone(self, env: StigmergicEnvironment) -> None:
        result = env.read_pheromone(PheromoneType.RESEARCH, "nonexistent.json")
        assert result is None

    def test_scan_pheromones(self, env: StigmergicEnvironment) -> None:
        env.write_pheromone(PheromoneType.REVIEW, "pr-1.json", {"verdict": "approved"})
        env.write_pheromone(PheromoneType.REVIEW, "pr-2.json", {"verdict": "rejected"})

        results = env.scan_pheromones(PheromoneType.REVIEW)
        assert len(results) == 2
        # Should be sorted newest first
        assert results[0].timestamp >= results[1].timestamp

    def test_scan_empty_directory(self, env: StigmergicEnvironment) -> None:
        results = env.scan_pheromones(PheromoneType.HEALTH)
        assert results == []

    def test_scan_environment(self, env: StigmergicEnvironment) -> None:
        env.write_pheromone(PheromoneType.RESEARCH, "f1.json", {"topic": "a"})
        env.write_pheromone(PheromoneType.ASSIGNMENT, "a1.json", {"task": 1})
        env.write_pheromone(PheromoneType.REVIEW, "r1.json", {"ok": True})
        env.write_pheromone(PheromoneType.HEALTH, "h1.json", {"score": 0.95})

        scan = env.scan_environment()
        assert isinstance(scan, EnvironmentScan)
        assert len(scan.research_artifacts) == 1
        assert len(scan.pending_assignments) == 1
        assert len(scan.pending_reviews) == 1
        assert len(scan.health_reports) == 1

    def test_remove_pheromone(self, env: StigmergicEnvironment) -> None:
        env.write_pheromone(PheromoneType.RESEARCH, "to-remove.json", {"temp": True})
        assert env.remove_pheromone(PheromoneType.RESEARCH, "to-remove.json") is True
        assert env.read_pheromone(PheromoneType.RESEARCH, "to-remove.json") is None

    def test_remove_nonexistent(self, env: StigmergicEnvironment) -> None:
        assert env.remove_pheromone(PheromoneType.RESEARCH, "nope.json") is False

    def test_subscribe_and_notify(self, env: StigmergicEnvironment) -> None:
        received: list[Pheromone] = []
        env.subscribe(PheromoneType.RESEARCH, lambda p: received.append(p))

        env.write_pheromone(PheromoneType.RESEARCH, "sub-test.json", {"notified": True})
        assert len(received) == 1
        assert received[0].data["notified"] is True

    def test_subscribe_wrong_type_no_notify(self, env: StigmergicEnvironment) -> None:
        received: list[Pheromone] = []
        env.subscribe(PheromoneType.HEALTH, lambda p: received.append(p))

        env.write_pheromone(PheromoneType.RESEARCH, "other.json", {"data": 1})
        assert len(received) == 0

    def test_get_pheromone_path(self, env: StigmergicEnvironment, project_root: Path) -> None:
        path = env.get_pheromone_path(PheromoneType.RESEARCH)
        assert path == project_root / "intel" / "findings"

    def test_filesystem_watch_start_stop(self, env: StigmergicEnvironment) -> None:
        env.start_filesystem_watch()
        assert len(env._observers) == len(PheromoneType)
        env.stop_filesystem_watch()
        assert len(env._observers) == 0

    def test_corrupted_json_handled(
        self, env: StigmergicEnvironment, project_root: Path
    ) -> None:
        filepath = project_root / "intel" / "findings" / "bad.json"
        filepath.write_text("not valid json {{{")
        result = env.read_pheromone(PheromoneType.RESEARCH, "bad.json")
        assert result is None

    def test_corrupted_json_in_scan(
        self, env: StigmergicEnvironment, project_root: Path
    ) -> None:
        env.write_pheromone(PheromoneType.RESEARCH, "good.json", {"ok": True})
        bad_file = project_root / "intel" / "findings" / "bad.json"
        bad_file.write_text("corrupted")
        results = env.scan_pheromones(PheromoneType.RESEARCH)
        assert len(results) == 1  # Only the good one
