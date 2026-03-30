"""Tests for the QA Checker Agent."""

from __future__ import annotations

from pathlib import Path

import pytest

from sim_agents.agents.qa_checker import (
    DangerAssessment,
    DangerSignal,
    QACheckerAgent,
    ReviewPass,
    ReviewResult,
    ReviewVerdict,
    TestStrategy,
)
from sim_agents.coordination.stigmergy import PheromoneType, StigmergicEnvironment


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def env(project_root: Path) -> StigmergicEnvironment:
    return StigmergicEnvironment(project_root)


@pytest.fixture
def qa(project_root: Path, env: StigmergicEnvironment) -> QACheckerAgent:
    return QACheckerAgent(project_root, environment=env)


class TestDangerSignals:
    def test_detect_new_code(self, qa: QACheckerAgent, env: StigmergicEnvironment) -> None:
        env.write_pheromone(
            PheromoneType.REVIEW,
            "work-result-1.json",
            {"status": "completed", "task_id": 1},
        )
        signals = qa.detect_danger_signals()
        assert any(s.signal_type == DangerSignal.NEW_CODE for s in signals)

    def test_detect_failing_tests(self, qa: QACheckerAgent, env: StigmergicEnvironment) -> None:
        env.write_pheromone(
            PheromoneType.HEALTH,
            "health-report.json",
            {"test_pass_rate": 0.85},
        )
        signals = qa.detect_danger_signals()
        assert any(s.signal_type == DangerSignal.FAILING_TESTS for s in signals)

    def test_detect_coverage_drop(self, qa: QACheckerAgent, env: StigmergicEnvironment) -> None:
        env.write_pheromone(
            PheromoneType.HEALTH,
            "coverage.json",
            {"coverage": 0.65},
        )
        signals = qa.detect_danger_signals()
        assert any(s.signal_type == DangerSignal.COVERAGE_DROP for s in signals)

    def test_no_signals_when_healthy(self, qa: QACheckerAgent) -> None:
        signals = qa.detect_danger_signals()
        assert len(signals) == 0


class TestReviewPasses:
    def test_static_pass(self, qa: QACheckerAgent) -> None:
        result = qa.static_analysis_pass()
        assert isinstance(result, ReviewPass)
        assert result.pass_type == "static"

    def test_behavioral_pass(self, qa: QACheckerAgent) -> None:
        result = qa.behavioral_pass()
        assert isinstance(result, ReviewPass)
        assert result.pass_type == "behavioral"

    def test_semantic_pass_no_changes(self, qa: QACheckerAgent) -> None:
        result = qa.semantic_pass({"files_changed": []})
        assert result.pass_type == "semantic"
        assert not result.passed  # No changes = issue

    def test_semantic_pass_with_changes(self, qa: QACheckerAgent) -> None:
        result = qa.semantic_pass({"files_changed": ["file.py"]})
        assert result.passed is True


class TestReviewWork:
    def test_review_produces_result(self, qa: QACheckerAgent) -> None:
        result = qa.review_work(1)
        assert isinstance(result, ReviewResult)
        assert result.task_id == 1
        assert len(result.passes) == 3

    def test_review_writes_pheromone(
        self, qa: QACheckerAgent, project_root: Path
    ) -> None:
        qa.review_work(5)
        review_file = project_root / "reviews" / "review-5.json"
        assert review_file.exists()

    def test_review_history(self, qa: QACheckerAgent) -> None:
        qa.review_work(1)
        qa.review_work(2)
        history = qa.get_review_history()
        assert len(history) == 2

    def test_review_verdict_types(self) -> None:
        assert ReviewVerdict.APPROVED.value == "approved"
        assert ReviewVerdict.REJECTED.value == "rejected"
        assert ReviewVerdict.NEEDS_REVISION.value == "needs_revision"


class TestClonalSelection:
    def test_strategy_update_reinforcement(self) -> None:
        strategy = TestStrategy("test", "Test strategy", effectiveness=0.5)
        strategy.update_effectiveness(found_real_bug=True)
        assert strategy.effectiveness > 0.5
        assert strategy.bugs_found == 1

    def test_strategy_update_attenuation(self) -> None:
        strategy = TestStrategy("test", "Test strategy", effectiveness=0.5)
        strategy.update_effectiveness(found_real_bug=False)
        assert strategy.effectiveness < 0.5

    def test_strategy_effectiveness_bounded(self) -> None:
        strategy = TestStrategy("test", "Test", effectiveness=0.95)
        for _ in range(20):
            strategy.update_effectiveness(found_real_bug=True)
        assert strategy.effectiveness <= 1.0

    def test_strategy_min_effectiveness(self) -> None:
        strategy = TestStrategy("test", "Test", effectiveness=0.15)
        for _ in range(100):
            strategy.update_effectiveness(found_real_bug=False)
        assert strategy.effectiveness >= 0.1

    def test_get_strategies_sorted(self, qa: QACheckerAgent) -> None:
        strategies = qa.get_strategies()
        for i in range(len(strategies) - 1):
            assert strategies[i].effectiveness >= strategies[i + 1].effectiveness

    def test_false_positive_rate(self) -> None:
        strategy = TestStrategy("test", "Test")
        strategy.update_effectiveness(found_real_bug=True)
        strategy.update_effectiveness(found_real_bug=False)
        strategy.update_effectiveness(found_real_bug=False)
        assert strategy.false_positive_rate > 0


class TestReviewResult:
    def test_to_dict(self) -> None:
        result = ReviewResult(
            task_id=1,
            agent_id="qa-1",
            verdict=ReviewVerdict.APPROVED,
            passes=[ReviewPass("static", True)],
        )
        d = result.to_dict()
        assert d["verdict"] == "approved"
        assert len(d["passes"]) == 1
        assert d["passes"][0]["pass_type"] == "static"

    def test_danger_assessment_in_result(self) -> None:
        result = ReviewResult(
            task_id=1,
            agent_id="qa-1",
            verdict=ReviewVerdict.REJECTED,
            danger_signals=[
                DangerAssessment(DangerSignal.NEW_CODE, 0.5, "src/x.py", "new code"),
            ],
        )
        d = result.to_dict()
        assert len(d["danger_signals"]) == 1
        assert d["danger_signals"][0]["signal_type"] == "new_code"
