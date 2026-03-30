"""Tests for the Research Verification Agent."""

from __future__ import annotations

from pathlib import Path

import pytest

from sim_agents.agents.researcher import ResearchFinding
from sim_agents.agents.verifier import VerificationResult, VerifierAgent
from sim_agents.coordination.stigmergy import PheromoneType, StigmergicEnvironment


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    # Create minimal project structure
    src = tmp_path / "src" / "sim_agents"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("")
    (src / "main.py").write_text("# main module")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\ndependencies = [\n"anthropic>=0.1",\n]\n')
    return tmp_path


@pytest.fixture
def env(project_root: Path) -> StigmergicEnvironment:
    return StigmergicEnvironment(project_root)


@pytest.fixture
def verifier(project_root: Path, env: StigmergicEnvironment) -> VerifierAgent:
    return VerifierAgent(project_root, environment=env)


class TestVerificationResult:
    def test_to_dict(self) -> None:
        r = VerificationResult(
            finding_topic="test", verified=True, confidence=0.8,
            checks_passed=["a"], checks_failed=["b"],
        )
        d = r.to_dict()
        assert d["verified"] is True
        assert d["confidence"] == 0.8

    def test_default_values(self) -> None:
        r = VerificationResult(finding_topic="t", verified=False, confidence=0.0)
        assert r.corrections == []
        assert r.checks_passed == []


class TestVerifierAgent:
    def test_get_project_corpus(self, verifier: VerifierAgent) -> None:
        corpus = verifier.get_project_corpus()
        assert len(corpus["python_files"]) >= 1
        assert "pyproject.toml" in corpus["config_files"]

    def test_verify_good_finding(self, verifier: VerifierAgent) -> None:
        finding = ResearchFinding(
            topic="caching optimization",
            summary="Use Redis for API response caching to reduce latency by 50%",
            applicability_score=0.8,
            implementation_sketch="Add Redis cache layer in API handlers",
            sources=["https://redis.io/docs"],
            tags=["performance", "caching"],
        )
        result = verifier.verify_finding(finding)
        assert result.verified is True
        assert result.confidence > 0.5

    def test_verify_poor_finding(self, verifier: VerifierAgent) -> None:
        finding = ResearchFinding(
            topic="bad finding",
            summary="short",
            applicability_score=0.9,
            # Missing: sources, tags, implementation_sketch
        )
        result = verifier.verify_finding(finding)
        assert result.verified is False
        assert len(result.checks_failed) > 0
        assert len(result.corrections) > 0

    def test_verify_writes_pheromone(
        self, verifier: VerifierAgent, project_root: Path
    ) -> None:
        finding = ResearchFinding(
            topic="pheromone test",
            summary="Testing that verification writes a pheromone",
            applicability_score=0.5,
            sources=["test"],
            tags=["test"],
        )
        verifier.verify_finding(finding)
        pheromones = verifier.env.scan_pheromones(PheromoneType.REVIEW)
        assert len(pheromones) >= 1

    def test_verify_all_unverified(
        self, verifier: VerifierAgent, env: StigmergicEnvironment
    ) -> None:
        env.write_pheromone(
            PheromoneType.RESEARCH,
            "finding1.json",
            ResearchFinding(
                topic="topic1", summary="A detailed summary of finding",
                applicability_score=0.6, sources=["src1"], tags=["t1"],
            ).to_dict(),
        )
        env.write_pheromone(
            PheromoneType.RESEARCH,
            "finding2.json",
            ResearchFinding(
                topic="topic2", summary="Another detailed summary here",
                applicability_score=0.7, sources=["src2"], tags=["t2"],
                implementation_sketch="Do X then Y",
            ).to_dict(),
        )

        results = verifier.verify_all_unverified()
        assert len(results) == 2

    def test_verification_history(self, verifier: VerifierAgent) -> None:
        finding = ResearchFinding(
            topic="hist", summary="History test finding content",
            applicability_score=0.5, sources=["s"], tags=["t"],
        )
        verifier.verify_finding(finding)
        history = verifier.get_verification_history()
        assert len(history) == 1

    def test_zero_applicability_fails(self, verifier: VerifierAgent) -> None:
        finding = ResearchFinding(
            topic="zero", summary="Zero applicability test finding",
            applicability_score=0.0,
        )
        result = verifier.verify_finding(finding)
        assert "topic_has_zero_applicability" in result.checks_failed
