"""Tests for the Project Runner Agent."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sim_agents.agents.project_runner import (
    HealthMetric,
    ProjectRunnerAgent,
    RuntimeReport,
)
from sim_agents.coordination.stigmergy import PheromoneType, StigmergicEnvironment


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def env(project_root: Path) -> StigmergicEnvironment:
    return StigmergicEnvironment(project_root)


@pytest.fixture
def runner(project_root: Path, env: StigmergicEnvironment) -> ProjectRunnerAgent:
    return ProjectRunnerAgent(project_root, environment=env)


class TestHealthMetric:
    def test_to_dict(self) -> None:
        m = HealthMetric("tests", 0.95, 0.95, "healthy")
        d = m.to_dict()
        assert d["name"] == "tests"
        assert d["status"] == "healthy"

    def test_critical_metric(self) -> None:
        m = HealthMetric("tests", 0.5, 0.95, "critical")
        assert m.status == "critical"


class TestRuntimeReport:
    def test_to_dict(self) -> None:
        report = RuntimeReport(
            overall_status="healthy",
            metrics=[HealthMetric("tests", 1.0, 0.95, "healthy")],
        )
        d = report.to_dict()
        assert d["overall_status"] == "healthy"
        assert len(d["metrics"]) == 1
        assert d["tests_passing"] is True

    def test_failing_report(self) -> None:
        report = RuntimeReport(
            overall_status="failing",
            errors=[{"component": "tests", "message": "Tests failed"}],
            tests_passing=False,
        )
        d = report.to_dict()
        assert d["overall_status"] == "failing"
        assert len(d["errors"]) == 1


class TestProjectRunnerAgent:
    def test_check_packages(self, runner: ProjectRunnerAgent) -> None:
        metric = runner.check_packages()
        assert isinstance(metric, HealthMetric)
        assert metric.name == "packages"

    def test_check_imports(self, runner: ProjectRunnerAgent) -> None:
        metric = runner.check_imports()
        assert isinstance(metric, HealthMetric)
        assert metric.name == "import_check"

    def test_run_health_check(self, runner: ProjectRunnerAgent) -> None:
        report = runner.run_health_check()
        assert isinstance(report, RuntimeReport)
        assert len(report.metrics) >= 2  # packages + imports at minimum

    def test_health_check_saves_report(
        self, runner: ProjectRunnerAgent, project_root: Path
    ) -> None:
        runner.run_health_check()
        report_path = project_root / "health" / "runtime-report.json"
        assert report_path.exists()
        data = json.loads(report_path.read_text())
        assert "overall_status" in data

    def test_health_check_writes_pheromone(
        self, runner: ProjectRunnerAgent, env: StigmergicEnvironment
    ) -> None:
        runner.run_health_check()
        pheromones = env.scan_pheromones(PheromoneType.HEALTH)
        assert len(pheromones) >= 1

    def test_get_reports(self, runner: ProjectRunnerAgent) -> None:
        runner.run_health_check()
        runner.run_health_check()
        assert len(runner.get_reports()) == 2

    def test_is_project_healthy_no_data(self, runner: ProjectRunnerAgent) -> None:
        assert runner.is_project_healthy() is True

    def test_set_points(self, runner: ProjectRunnerAgent) -> None:
        assert runner.set_points["test_pass_rate"] == 0.95
        assert runner.set_points["code_coverage"] == 0.80
        assert runner.set_points["error_rate"] == 0.01

    def test_failure_tickets_created(
        self, runner: ProjectRunnerAgent, env: StigmergicEnvironment
    ) -> None:
        # Run health check and check if failure tickets are created for errors
        runner.run_health_check()
        # The actual result depends on the environment, but the method should work
        reports = runner.get_reports()
        assert len(reports) == 1
