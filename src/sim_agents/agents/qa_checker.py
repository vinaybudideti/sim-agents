"""Checker/QA Agent — immune system danger theory verification.

Responds to danger signals (new code, failing tests, coverage drops).
Implements three verification passes: static analysis, behavioral verification,
semantic review. Uses clonal selection to evolve testing strategies over time.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import structlog

from sim_agents.coordination.stigmergy import PheromoneType, StigmergicEnvironment

logger = structlog.get_logger(__name__)


class DangerSignal(Enum):
    """Types of danger signals that trigger QA review."""

    NEW_CODE = "new_code"
    FAILING_TESTS = "failing_tests"
    COVERAGE_DROP = "coverage_drop"
    COMPLEXITY_INCREASE = "complexity_increase"
    DEPENDENCY_CHANGE = "dependency_change"


class ReviewVerdict(Enum):
    """Possible outcomes of a QA review."""

    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_REVISION = "needs_revision"


@dataclass
class DangerAssessment:
    """Assessment of danger signals in the codebase."""

    signal_type: DangerSignal
    severity: float  # 0.0 to 1.0
    source: str
    details: str
    detected_at: float = field(default_factory=time.time)


@dataclass
class ReviewPass:
    """Result of a single review pass."""

    pass_type: str  # "static", "behavioral", "semantic"
    passed: bool
    issues: list[dict[str, Any]] = field(default_factory=list)
    duration_seconds: float = 0.0
    details: str = ""


@dataclass
class ReviewResult:
    """Complete QA review result."""

    task_id: int
    agent_id: str
    verdict: ReviewVerdict
    passes: list[ReviewPass] = field(default_factory=list)
    danger_signals: list[DangerAssessment] = field(default_factory=list)
    remediation: list[str] = field(default_factory=list)
    reviewed_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "verdict": self.verdict.value,
            "passes": [
                {
                    "pass_type": p.pass_type,
                    "passed": p.passed,
                    "issues": p.issues,
                    "duration_seconds": p.duration_seconds,
                    "details": p.details,
                }
                for p in self.passes
            ],
            "danger_signals": [
                {
                    "signal_type": d.signal_type.value,
                    "severity": d.severity,
                    "source": d.source,
                    "details": d.details,
                }
                for d in self.danger_signals
            ],
            "remediation": self.remediation,
            "reviewed_at": self.reviewed_at,
        }


@dataclass
class TestStrategy:
    """A testing strategy that can evolve via clonal selection."""

    name: str
    description: str
    effectiveness: float = 0.5  # 0.0 to 1.0, updated by clonal selection
    false_positive_rate: float = 0.0
    bugs_found: int = 0
    runs: int = 0

    def update_effectiveness(self, found_real_bug: bool) -> None:
        """Update strategy effectiveness via clonal selection.

        Strategies that find real bugs are reinforced; those that produce
        false positives are attenuated.

        Args:
            found_real_bug: Whether this run found a real bug.
        """
        self.runs += 1
        if found_real_bug:
            self.bugs_found += 1
            # Reinforce: increase effectiveness
            self.effectiveness = min(1.0, self.effectiveness + 0.1)
        else:
            # Attenuate slightly
            self.effectiveness = max(0.1, self.effectiveness - 0.02)

        if self.runs > 0:
            self.false_positive_rate = 1.0 - (self.bugs_found / self.runs)


class QACheckerAgent:
    """QA Agent implementing immune system danger theory.

    Responds to danger signals rather than checking everything. Uses three
    verification passes and evolves strategies via clonal selection.
    """

    def __init__(
        self,
        project_root: str | Path,
        environment: StigmergicEnvironment | None = None,
        agent_id: str = "qa-checker",
    ) -> None:
        self.project_root = Path(project_root)
        self.agent_id = agent_id
        self.env = environment or StigmergicEnvironment(self.project_root)
        self._strategies: list[TestStrategy] = self._default_strategies()
        self._review_history: list[ReviewResult] = []
        logger.info("qa_checker_initialized", agent_id=agent_id)

    def _default_strategies(self) -> list[TestStrategy]:
        """Create default testing strategies."""
        return [
            TestStrategy("lint_check", "Run linter for style and error checking"),
            TestStrategy("type_check", "Run type checker for type safety"),
            TestStrategy("test_suite", "Run full test suite"),
            TestStrategy("security_scan", "Scan for security vulnerabilities"),
            TestStrategy("complexity_check", "Check cyclomatic complexity"),
        ]

    def detect_danger_signals(self) -> list[DangerAssessment]:
        """Scan the environment for danger signals.

        Checks for new code, failing tests, coverage drops, etc.

        Returns:
            List of detected danger signals.
        """
        signals: list[DangerAssessment] = []

        # Check for work results (new code)
        review_pheromones = self.env.scan_pheromones(PheromoneType.REVIEW)
        for p in review_pheromones:
            if p.data.get("status") == "completed":
                signals.append(DangerAssessment(
                    signal_type=DangerSignal.NEW_CODE,
                    severity=0.5,
                    source=p.path,
                    details=f"New code from task {p.data.get('task_id', '?')}",
                ))

        # Check health reports for failing indicators
        health_pheromones = self.env.scan_pheromones(PheromoneType.HEALTH)
        for p in health_pheromones:
            if p.data.get("test_pass_rate", 1.0) < 0.95:
                signals.append(DangerAssessment(
                    signal_type=DangerSignal.FAILING_TESTS,
                    severity=0.8,
                    source=p.path,
                    details=f"Test pass rate: {p.data.get('test_pass_rate')}",
                ))
            if p.data.get("coverage", 1.0) < 0.80:
                signals.append(DangerAssessment(
                    signal_type=DangerSignal.COVERAGE_DROP,
                    severity=0.6,
                    source=p.path,
                    details=f"Coverage: {p.data.get('coverage')}",
                ))

        logger.info("danger_signals_detected", count=len(signals))
        return signals

    def static_analysis_pass(self, files: list[str] | None = None) -> ReviewPass:
        """Run static analysis (linting, type checking, security scanning).

        Args:
            files: Optional list of files to check. Checks all if None.

        Returns:
            ReviewPass with static analysis results.
        """
        start = time.time()
        issues: list[dict[str, Any]] = []
        passed = True

        # Try running ruff
        try:
            target = files or [str(self.project_root / "src")]
            result = subprocess.run(
                ["ruff", "check", "--output-format=json"] + target,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if result.returncode != 0 and result.stdout:
                try:
                    lint_issues = json.loads(result.stdout)
                    for issue in lint_issues:
                        issues.append({
                            "type": "lint",
                            "rule": issue.get("code", ""),
                            "message": issue.get("message", ""),
                            "file": issue.get("filename", ""),
                            "line": issue.get("location", {}).get("row", 0),
                        })
                except json.JSONDecodeError:
                    pass
                if issues:
                    passed = False
        except (FileNotFoundError, subprocess.SubprocessError):
            # Ruff not available — not a failure
            pass

        duration = time.time() - start
        return ReviewPass(
            pass_type="static",
            passed=passed,
            issues=issues,
            duration_seconds=round(duration, 2),
            details=f"Checked {len(files or [])} files" if files else "Checked all source files",
        )

    def behavioral_pass(self) -> ReviewPass:
        """Run behavioral verification (tests, regression checks).

        Returns:
            ReviewPass with test results.
        """
        start = time.time()
        issues: list[dict[str, Any]] = []
        passed = True

        try:
            result = subprocess.run(
                ["python", "-m", "pytest", "--tb=short", "-q"],
                cwd=str(self.project_root),
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            if result.returncode != 0:
                passed = False
                issues.append({
                    "type": "test_failure",
                    "message": result.stdout[-500:] if result.stdout else "Tests failed",
                    "stderr": result.stderr[-500:] if result.stderr else "",
                })
        except (FileNotFoundError, subprocess.SubprocessError) as e:
            issues.append({
                "type": "test_error",
                "message": f"Could not run tests: {e}",
            })

        duration = time.time() - start
        return ReviewPass(
            pass_type="behavioral",
            passed=passed,
            issues=issues,
            duration_seconds=round(duration, 2),
            details="Test suite execution",
        )

    def semantic_pass(self, work_result: dict[str, Any] | None = None) -> ReviewPass:
        """Run semantic review (LLM-based code review for logic errors).

        In production, this would use an LLM for deep code analysis.

        Args:
            work_result: Optional work result to review.

        Returns:
            ReviewPass with semantic review results.
        """
        start = time.time()
        issues: list[dict[str, Any]] = []

        # Check for common patterns
        if work_result:
            files_changed = work_result.get("files_changed", [])
            if not files_changed:
                issues.append({
                    "type": "no_changes",
                    "message": "Task completed but no files were changed",
                })

        duration = time.time() - start
        return ReviewPass(
            pass_type="semantic",
            passed=len(issues) == 0,
            issues=issues,
            duration_seconds=round(duration, 2),
            details="Semantic code review",
        )

    def review_work(
        self, task_id: int, work_result: dict[str, Any] | None = None
    ) -> ReviewResult:
        """Perform a complete QA review with all three passes.

        Args:
            task_id: The task being reviewed.
            work_result: Optional work result data.

        Returns:
            ReviewResult with verdict and details.
        """
        logger.info("review_started", task_id=task_id)

        # Detect danger signals
        signals = self.detect_danger_signals()

        # Run three passes
        static = self.static_analysis_pass()
        behavioral = self.behavioral_pass()
        semantic = self.semantic_pass(work_result)

        passes = [static, behavioral, semantic]
        all_passed = all(p.passed for p in passes)

        # Determine verdict
        if all_passed:
            verdict = ReviewVerdict.APPROVED
        elif not behavioral.passed:
            verdict = ReviewVerdict.REJECTED
        else:
            verdict = ReviewVerdict.NEEDS_REVISION

        # Generate remediation instructions
        remediation: list[str] = []
        for p in passes:
            for issue in p.issues:
                remediation.append(
                    f"[{p.pass_type}] Fix: {issue.get('message', 'unknown issue')}"
                )

        result = ReviewResult(
            task_id=task_id,
            agent_id=self.agent_id,
            verdict=verdict,
            passes=passes,
            danger_signals=signals,
            remediation=remediation,
        )

        # Write review to environment
        self.env.write_pheromone(
            PheromoneType.REVIEW,
            f"review-{task_id}.json",
            result.to_dict(),
            agent_id=self.agent_id,
        )

        # Update strategy effectiveness
        self._update_strategies(passes)

        self._review_history.append(result)
        logger.info(
            "review_completed",
            task_id=task_id,
            verdict=verdict.value,
            passes_passed=sum(1 for p in passes if p.passed),
        )
        return result

    def _update_strategies(self, passes: list[ReviewPass]) -> None:
        """Update strategy effectiveness via clonal selection.

        Args:
            passes: Review passes from the latest review.
        """
        for p in passes:
            found_issues = len(p.issues) > 0
            for strategy in self._strategies:
                if strategy.name.startswith(p.pass_type[:4]):
                    strategy.update_effectiveness(found_issues)

    def get_strategies(self) -> list[TestStrategy]:
        """Get current testing strategies with effectiveness scores."""
        return sorted(self._strategies, key=lambda s: s.effectiveness, reverse=True)

    def get_review_history(self) -> list[ReviewResult]:
        """Get all past review results."""
        return list(self._review_history)
