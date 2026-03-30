"""Project Agent — health monitoring, runtime verification, failure ticket creation.

Runs the project in sandboxed environment, monitors health metrics,
writes reports to health/runtime-report.json, creates high-priority
tickets for failures as stigmergic danger signals.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from sim_agents.coordination.stigmergy import PheromoneType, StigmergicEnvironment

logger = structlog.get_logger(__name__)


@dataclass
class HealthMetric:
    """A single health metric measurement."""

    name: str
    value: float
    threshold: float
    status: str  # "healthy", "warning", "critical"
    measured_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "threshold": self.threshold,
            "status": self.status,
            "measured_at": self.measured_at,
        }


@dataclass
class RuntimeReport:
    """Complete runtime health report."""

    overall_status: str  # "healthy", "degraded", "failing"
    metrics: list[HealthMetric] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    packages_ok: bool = True
    tests_passing: bool = True
    report_time: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_status": self.overall_status,
            "metrics": [m.to_dict() for m in self.metrics],
            "errors": self.errors,
            "warnings": self.warnings,
            "packages_ok": self.packages_ok,
            "tests_passing": self.tests_passing,
            "report_time": self.report_time,
        }


class ProjectRunnerAgent:
    """Agent that monitors project health and creates failure tickets.

    Verifies packages resolve, checks for runtime errors, runs tests,
    and writes health reports. Creates high-priority tickets when
    failures are detected.
    """

    def __init__(
        self,
        project_root: str | Path,
        environment: StigmergicEnvironment | None = None,
        agent_id: str = "project-runner",
    ) -> None:
        self.project_root = Path(project_root)
        self.agent_id = agent_id
        self.env = environment or StigmergicEnvironment(self.project_root)
        self._report_path = self.project_root / "health" / "runtime-report.json"
        self._reports: list[RuntimeReport] = []

        # Homeostatic set points
        self.set_points = {
            "test_pass_rate": 0.95,
            "code_coverage": 0.80,
            "error_rate": 0.01,
        }
        logger.info("project_runner_initialized", agent_id=agent_id)

    def check_packages(self) -> HealthMetric:
        """Verify all packages are installed and resolve correctly.

        Returns:
            HealthMetric for package status.
        """
        try:
            result = subprocess.run(
                ["pip", "check"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            ok = result.returncode == 0
            return HealthMetric(
                name="packages",
                value=1.0 if ok else 0.0,
                threshold=1.0,
                status="healthy" if ok else "critical",
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            return HealthMetric(
                name="packages",
                value=0.0,
                threshold=1.0,
                status="critical",
            )

    def check_tests(self) -> HealthMetric:
        """Run the test suite and measure pass rate.

        Returns:
            HealthMetric for test results.
        """
        try:
            result = subprocess.run(
                ["python", "-m", "pytest", "--tb=no", "-q"],
                cwd=str(self.project_root),
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            output = result.stdout
            # Parse pytest output for pass/fail counts
            passed = 0
            failed = 0
            for line in output.split("\n"):
                if "passed" in line:
                    parts = line.split()
                    for i, p in enumerate(parts):
                        if p == "passed" and i > 0:
                            try:
                                passed = int(parts[i - 1])
                            except ValueError:
                                pass
                        if p == "failed" and i > 0:
                            try:
                                failed = int(parts[i - 1])
                            except ValueError:
                                pass

            total = passed + failed
            rate = passed / total if total > 0 else 1.0

            status = "healthy" if rate >= self.set_points["test_pass_rate"] else "critical"
            return HealthMetric(
                name="test_pass_rate",
                value=round(rate, 3),
                threshold=self.set_points["test_pass_rate"],
                status=status,
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            return HealthMetric(
                name="test_pass_rate",
                value=0.0,
                threshold=self.set_points["test_pass_rate"],
                status="critical",
            )

    def check_imports(self) -> HealthMetric:
        """Verify the main package can be imported.

        Returns:
            HealthMetric for import status.
        """
        try:
            result = subprocess.run(
                ["python", "-c", "import sim_agents; print(sim_agents.__version__)"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            ok = result.returncode == 0
            return HealthMetric(
                name="import_check",
                value=1.0 if ok else 0.0,
                threshold=1.0,
                status="healthy" if ok else "critical",
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            return HealthMetric(
                name="import_check",
                value=0.0,
                threshold=1.0,
                status="critical",
            )

    def run_health_check(self) -> RuntimeReport:
        """Run a comprehensive health check.

        Returns:
            RuntimeReport with all metrics.
        """
        logger.info("health_check_started")

        metrics: list[HealthMetric] = []
        errors: list[dict[str, Any]] = []
        warnings: list[str] = []

        # Check packages
        pkg_metric = self.check_packages()
        metrics.append(pkg_metric)
        if pkg_metric.status != "healthy":
            errors.append({"component": "packages", "message": "Package check failed"})

        # Check imports
        import_metric = self.check_imports()
        metrics.append(import_metric)
        if import_metric.status != "healthy":
            errors.append({"component": "imports", "message": "Package import failed"})

        # Check tests
        test_metric = self.check_tests()
        metrics.append(test_metric)
        if test_metric.status != "healthy":
            if test_metric.value < 0.5:
                errors.append({
                    "component": "tests",
                    "message": f"Test pass rate critical: {test_metric.value}",
                })
            else:
                warnings.append(f"Test pass rate below target: {test_metric.value}")

        # Determine overall status
        if any(m.status == "critical" for m in metrics):
            overall = "failing"
        elif any(m.status == "warning" for m in metrics):
            overall = "degraded"
        else:
            overall = "healthy"

        report = RuntimeReport(
            overall_status=overall,
            metrics=metrics,
            errors=errors,
            warnings=warnings,
            packages_ok=pkg_metric.status == "healthy",
            tests_passing=test_metric.status == "healthy",
        )

        # Write health pheromone (separate file from saved report)
        self.env.write_pheromone(
            PheromoneType.HEALTH,
            "pheromone-runtime-report.json",
            report.to_dict(),
            agent_id=self.agent_id,
        )

        # Save report (plain dict, not pheromone envelope)
        self._save_report(report)
        self._reports.append(report)

        # Create tickets for failures
        if errors:
            self._create_failure_tickets(errors)

        logger.info(
            "health_check_completed",
            status=overall,
            metrics=len(metrics),
            errors=len(errors),
        )
        return report

    def _save_report(self, report: RuntimeReport) -> None:
        """Save report to health/runtime-report.json."""
        self._report_path.parent.mkdir(parents=True, exist_ok=True)
        self._report_path.write_text(
            json.dumps(report.to_dict(), indent=2, default=str)
        )

    def _create_failure_tickets(self, errors: list[dict[str, Any]]) -> None:
        """Create high-priority tickets for detected failures.

        Writes failure tickets as pheromones to attract agent attention.

        Args:
            errors: List of error dicts.
        """
        for error in errors:
            self.env.write_pheromone(
                PheromoneType.HEALTH,
                f"ticket-{error['component']}.json",
                {
                    "type": "failure_ticket",
                    "component": error["component"],
                    "message": error["message"],
                    "priority": "high",
                    "status": "failing",
                    "score": 0.0,
                    "created_by": self.agent_id,
                },
                agent_id=self.agent_id,
            )

    def get_reports(self) -> list[RuntimeReport]:
        """Get all health reports from this session."""
        return list(self._reports)

    def is_project_healthy(self) -> bool:
        """Quick check if the project is currently healthy.

        Returns:
            True if the last report shows healthy status.
        """
        if not self._reports:
            return True  # No data yet
        return self._reports[-1].overall_status == "healthy"
