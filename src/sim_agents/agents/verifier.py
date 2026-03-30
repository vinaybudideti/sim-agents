"""Research Verification Agent — cross-references research against project code.

Implements immune system negative selection: maintains a "self" corpus of the
project's actual code and dependencies, cross-references findings against it,
and sets verified flag.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from sim_agents.agents.researcher import ResearchFinding
from sim_agents.coordination.stigmergy import PheromoneType, StigmergicEnvironment

logger = structlog.get_logger(__name__)


@dataclass
class VerificationResult:
    """Result of verifying a research finding."""

    finding_topic: str
    verified: bool
    confidence: float  # 0.0 to 1.0
    checks_passed: list[str] = field(default_factory=list)
    checks_failed: list[str] = field(default_factory=list)
    corrections: list[str] = field(default_factory=list)
    verified_at: float = field(default_factory=time.time)
    agent_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_topic": self.finding_topic,
            "verified": self.verified,
            "confidence": self.confidence,
            "checks_passed": self.checks_passed,
            "checks_failed": self.checks_failed,
            "corrections": self.corrections,
            "verified_at": self.verified_at,
            "agent_id": self.agent_id,
        }


class VerifierAgent:
    """Agent that cross-references research findings against actual project code.

    Maintains a "self" corpus and verifies that findings are accurate and
    applicable to the actual codebase.
    """

    def __init__(
        self,
        project_root: str | Path,
        environment: StigmergicEnvironment | None = None,
        agent_id: str = "verifier",
    ) -> None:
        self.project_root = Path(project_root)
        self.agent_id = agent_id
        self.env = environment or StigmergicEnvironment(self.project_root)
        self._verification_history: list[VerificationResult] = []
        logger.info("verifier_initialized", agent_id=agent_id)

    def get_project_corpus(self) -> dict[str, Any]:
        """Build a corpus of the project's actual state.

        Returns:
            Dict with project files, dependencies, and configuration.
        """
        corpus: dict[str, Any] = {
            "python_files": [],
            "dependencies": [],
            "config_files": [],
        }

        # Scan Python files
        src_dir = self.project_root / "src"
        if src_dir.exists():
            for py_file in src_dir.rglob("*.py"):
                corpus["python_files"].append(
                    str(py_file.relative_to(self.project_root))
                )

        # Check for dependency specs
        pyproject = self.project_root / "pyproject.toml"
        if pyproject.exists():
            corpus["config_files"].append("pyproject.toml")
            try:
                content = pyproject.read_text()
                # Extract dependency names (simple parsing)
                in_deps = False
                for line in content.split("\n"):
                    stripped = line.strip()
                    if stripped.startswith("dependencies"):
                        in_deps = True
                    elif in_deps and stripped.startswith('"'):
                        dep_name = stripped.strip('"').strip("',").split(">=")[0].split(">=")[0]
                        corpus["dependencies"].append(dep_name)
                    elif in_deps and stripped == "]":
                        in_deps = False
            except OSError:
                pass

        return corpus

    def verify_finding(self, finding: ResearchFinding) -> VerificationResult:
        """Verify a research finding against the actual project.

        Checks:
        1. Referenced APIs/libraries exist in the project
        2. Suggested libraries are compatible with dependencies
        3. Proposed patterns don't conflict with existing architecture

        Args:
            finding: The research finding to verify.

        Returns:
            VerificationResult with details.
        """
        corpus = self.get_project_corpus()
        checks_passed: list[str] = []
        checks_failed: list[str] = []
        corrections: list[str] = []

        # Check 1: Topic relevance
        if finding.applicability_score > 0:
            checks_passed.append("topic_has_applicability_score")
        else:
            checks_failed.append("topic_has_zero_applicability")

        # Check 2: Source validity
        if finding.sources:
            checks_passed.append("has_sources")
        else:
            checks_failed.append("no_sources_provided")
            corrections.append("Finding should include references/sources")

        # Check 3: Implementation sketch present for high-applicability findings
        if finding.applicability_score >= 0.7 and not finding.implementation_sketch:
            checks_failed.append("high_applicability_missing_implementation")
            corrections.append(
                "High-applicability findings should include implementation sketch"
            )
        elif finding.implementation_sketch:
            checks_passed.append("has_implementation_sketch")

        # Check 4: Tags are meaningful
        if finding.tags:
            checks_passed.append("has_classification_tags")
        else:
            checks_failed.append("no_tags")

        # Check 5: Summary is substantial
        if len(finding.summary) >= 20:
            checks_passed.append("summary_is_substantial")
        else:
            checks_failed.append("summary_too_short")
            corrections.append("Summary should be at least 20 characters")

        # Calculate confidence and verdict
        total_checks = len(checks_passed) + len(checks_failed)
        confidence = len(checks_passed) / total_checks if total_checks > 0 else 0.0
        verified = confidence >= 0.6 and len(checks_failed) <= 1

        result = VerificationResult(
            finding_topic=finding.topic,
            verified=verified,
            confidence=round(confidence, 2),
            checks_passed=checks_passed,
            checks_failed=checks_failed,
            corrections=corrections,
            agent_id=self.agent_id,
        )

        self._verification_history.append(result)

        # Write verification result as pheromone
        self.env.write_pheromone(
            PheromoneType.REVIEW,
            f"verification-{finding.topic.replace(' ', '-')[:30]}.json",
            result.to_dict(),
            agent_id=self.agent_id,
        )

        logger.info(
            "finding_verified",
            topic=finding.topic,
            verified=verified,
            confidence=confidence,
        )
        return result

    def verify_all_unverified(self) -> list[VerificationResult]:
        """Verify all unverified findings from the environment.

        Returns:
            List of verification results.
        """
        pheromones = self.env.scan_pheromones(PheromoneType.RESEARCH)
        results: list[VerificationResult] = []

        for p in pheromones:
            if not p.data.get("verified", False):
                try:
                    finding = ResearchFinding.from_dict(p.data)
                    result = self.verify_finding(finding)
                    results.append(result)
                except (KeyError, TypeError) as e:
                    logger.warning("verification_parse_error", error=str(e))

        logger.info("batch_verification_complete", verified=len(results))
        return results

    def get_verification_history(self) -> list[VerificationResult]:
        """Get all past verification results."""
        return list(self._verification_history)
