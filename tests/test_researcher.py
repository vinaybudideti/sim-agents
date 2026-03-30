"""Tests for the Research Agent."""

from __future__ import annotations

from pathlib import Path

import pytest

from sim_agents.agents.researcher import ResearchAgent, ResearchFinding
from sim_agents.coordination.stigmergy import PheromoneType, StigmergicEnvironment


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def env(project_root: Path) -> StigmergicEnvironment:
    return StigmergicEnvironment(project_root)


@pytest.fixture
def researcher(project_root: Path, env: StigmergicEnvironment) -> ResearchAgent:
    return ResearchAgent(project_root, environment=env)


class TestResearchFinding:
    def test_to_dict(self) -> None:
        f = ResearchFinding(
            topic="caching", summary="Redis caching improves perf",
            applicability_score=0.8, agent_id="r1",
        )
        d = f.to_dict()
        assert d["topic"] == "caching"
        assert d["applicability_score"] == 0.8
        assert d["verified"] is False

    def test_from_dict(self) -> None:
        data = {
            "topic": "testing",
            "summary": "Property-based testing",
            "applicability_score": 0.7,
            "verified": True,
        }
        f = ResearchFinding.from_dict(data)
        assert f.topic == "testing"
        assert f.verified is True

    def test_roundtrip(self) -> None:
        original = ResearchFinding(
            topic="optimization", summary="Use connection pooling",
            applicability_score=0.9, tags=["performance"],
        )
        restored = ResearchFinding.from_dict(original.to_dict())
        assert restored.topic == original.topic
        assert restored.tags == original.tags


class TestResearchAgent:
    def test_create_finding(self, researcher: ResearchAgent) -> None:
        finding = researcher.create_finding(
            topic="caching strategies",
            summary="Redis caching for API responses",
            applicability_score=0.85,
        )
        assert finding.topic == "caching strategies"
        assert finding.applicability_score == 0.85
        assert finding.agent_id == "researcher"

    def test_create_finding_writes_pheromone(
        self, researcher: ResearchAgent, project_root: Path
    ) -> None:
        researcher.create_finding("test-topic", "Test summary", 0.5)
        pheromones = researcher.env.scan_pheromones(PheromoneType.RESEARCH)
        assert len(pheromones) >= 1

    def test_generate_filename(self, researcher: ResearchAgent) -> None:
        filename = researcher._generate_filename("Caching Strategies")
        assert filename.endswith(".json")
        assert "caching-strategies" in filename

    def test_get_findings(self, researcher: ResearchAgent) -> None:
        researcher.create_finding("t1", "s1", 0.5)
        researcher.create_finding("t2", "s2", 0.7)
        assert len(researcher.get_findings()) == 2

    def test_get_verified_only(self, researcher: ResearchAgent) -> None:
        researcher.create_finding("t1", "s1", 0.5)
        f2 = researcher.create_finding("t2", "s2", 0.7)
        f2.verified = True
        findings = researcher.get_findings(verified_only=True)
        assert len(findings) == 1
        assert findings[0].topic == "t2"

    def test_get_findings_from_environment(
        self, researcher: ResearchAgent, env: StigmergicEnvironment
    ) -> None:
        researcher.create_finding("env-topic", "From env", 0.6)
        env_findings = researcher.get_findings_from_environment()
        assert len(env_findings) >= 1

    def test_scan_project_missing_tests(
        self, researcher: ResearchAgent, project_root: Path
    ) -> None:
        src_dir = project_root / "src"
        src_dir.mkdir(exist_ok=True)
        (src_dir / "module.py").write_text("# code")
        tests_dir = project_root / "tests"
        tests_dir.mkdir(exist_ok=True)

        opps = researcher.scan_project_for_improvements()
        missing_tests = [o for o in opps if o["type"] == "missing_test"]
        assert len(missing_tests) >= 1

    def test_scan_project_health_issues(
        self, researcher: ResearchAgent, env: StigmergicEnvironment
    ) -> None:
        env.write_pheromone(
            PheromoneType.HEALTH,
            "low-score.json",
            {"component": "api", "score": 0.4},
        )
        opps = researcher.scan_project_for_improvements()
        health = [o for o in opps if o["type"] == "health_issue"]
        assert len(health) == 1


class TestModelRouting:
    def test_simple_task_uses_haiku(self, researcher: ResearchAgent) -> None:
        assert researcher.select_model_tier(0.1) == "haiku"

    def test_moderate_task_uses_sonnet(self, researcher: ResearchAgent) -> None:
        assert researcher.select_model_tier(0.5) == "sonnet"

    def test_complex_task_uses_opus(self, researcher: ResearchAgent) -> None:
        assert researcher.select_model_tier(0.9) == "opus"

    def test_boundary_low(self, researcher: ResearchAgent) -> None:
        assert researcher.select_model_tier(0.3) == "sonnet"

    def test_boundary_high(self, researcher: ResearchAgent) -> None:
        assert researcher.select_model_tier(0.7) == "opus"
