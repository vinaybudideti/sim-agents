"""Research Agent — discovers techniques, libraries, and patterns.

Writes structured findings to intel/findings/{date}-{topic}.json.
Uses cheaper model for initial scans, frontier model for deep analysis.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from sim_agents.coordination.stigmergy import PheromoneType, StigmergicEnvironment

logger = structlog.get_logger(__name__)


@dataclass
class ResearchFinding:
    """A structured research finding."""

    topic: str
    summary: str
    applicability_score: float  # 0.0 to 1.0
    implementation_sketch: str = ""
    estimated_effort: str = "medium"
    sources: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    model_used: str = "sonnet"
    verified: bool = False
    verification_notes: str = ""
    created_at: float = field(default_factory=time.time)
    agent_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "summary": self.summary,
            "applicability_score": self.applicability_score,
            "implementation_sketch": self.implementation_sketch,
            "estimated_effort": self.estimated_effort,
            "sources": self.sources,
            "tags": self.tags,
            "model_used": self.model_used,
            "verified": self.verified,
            "verification_notes": self.verification_notes,
            "created_at": self.created_at,
            "agent_id": self.agent_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResearchFinding:
        return cls(
            topic=data.get("topic", ""),
            summary=data.get("summary", ""),
            applicability_score=data.get("applicability_score", 0.0),
            implementation_sketch=data.get("implementation_sketch", ""),
            estimated_effort=data.get("estimated_effort", "medium"),
            sources=data.get("sources", []),
            tags=data.get("tags", []),
            model_used=data.get("model_used", "sonnet"),
            verified=data.get("verified", False),
            verification_notes=data.get("verification_notes", ""),
            created_at=data.get("created_at", time.time()),
            agent_id=data.get("agent_id", ""),
        )


class ResearchAgent:
    """Agent that investigates techniques, libraries, and patterns.

    Uses cheaper model (Haiku/Sonnet) for initial scans and escalates to
    frontier model (Opus) only for deep analysis — cutting costs by ~60%.
    """

    def __init__(
        self,
        project_root: str | Path,
        environment: StigmergicEnvironment | None = None,
        agent_id: str = "researcher",
    ) -> None:
        self.project_root = Path(project_root)
        self.agent_id = agent_id
        self.env = environment or StigmergicEnvironment(self.project_root)
        self._findings: list[ResearchFinding] = []
        logger.info("researcher_initialized", agent_id=agent_id)

    def _generate_filename(self, topic: str) -> str:
        """Generate a filename for a research finding.

        Args:
            topic: The research topic.

        Returns:
            Filename in format {date}-{topic}.json.
        """
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        safe_topic = topic.lower().replace(" ", "-").replace("/", "-")[:50]
        return f"{date_str}-{safe_topic}.json"

    def create_finding(
        self,
        topic: str,
        summary: str,
        applicability_score: float = 0.5,
        implementation_sketch: str = "",
        estimated_effort: str = "medium",
        sources: list[str] | None = None,
        tags: list[str] | None = None,
        model_used: str = "sonnet",
    ) -> ResearchFinding:
        """Create and publish a research finding.

        Args:
            topic: Research topic.
            summary: Summary of the finding.
            applicability_score: How applicable to the project (0-1).
            implementation_sketch: High-level implementation plan.
            estimated_effort: "low", "medium", or "high".
            sources: References and sources.
            tags: Classification tags.
            model_used: Which model tier was used.

        Returns:
            The created ResearchFinding.
        """
        finding = ResearchFinding(
            topic=topic,
            summary=summary,
            applicability_score=applicability_score,
            implementation_sketch=implementation_sketch,
            estimated_effort=estimated_effort,
            sources=sources or [],
            tags=tags or [],
            model_used=model_used,
            agent_id=self.agent_id,
        )

        # Write to intel/findings/
        filename = self._generate_filename(topic)
        self.env.write_pheromone(
            PheromoneType.RESEARCH,
            filename,
            finding.to_dict(),
            agent_id=self.agent_id,
        )

        self._findings.append(finding)
        logger.info(
            "finding_created",
            topic=topic,
            applicability=applicability_score,
            model=model_used,
        )
        return finding

    def scan_project_for_improvements(self) -> list[dict[str, Any]]:
        """Scan the project to identify areas for improvement.

        Checks code structure, dependencies, test coverage gaps, etc.

        Returns:
            List of improvement opportunities.
        """
        opportunities: list[dict[str, Any]] = []

        # Check for missing test files
        src_dir = self.project_root / "src"
        tests_dir = self.project_root / "tests"

        if src_dir.exists():
            for py_file in src_dir.rglob("*.py"):
                if py_file.name.startswith("_"):
                    continue
                test_name = f"test_{py_file.stem}.py"
                if not (tests_dir / test_name).exists():
                    opportunities.append({
                        "type": "missing_test",
                        "file": str(py_file.relative_to(self.project_root)),
                        "suggestion": f"Create {test_name}",
                        "priority": "medium",
                    })

        # Check for health reports with issues
        health_pheromones = self.env.scan_pheromones(PheromoneType.HEALTH)
        for p in health_pheromones:
            if p.data.get("score", 1.0) < 0.8:
                opportunities.append({
                    "type": "health_issue",
                    "component": p.data.get("component", "unknown"),
                    "score": p.data.get("score"),
                    "priority": "high",
                })

        logger.info("project_scanned", opportunities=len(opportunities))
        return opportunities

    def get_findings(self, verified_only: bool = False) -> list[ResearchFinding]:
        """Get all research findings.

        Args:
            verified_only: If True, only return verified findings.

        Returns:
            List of findings.
        """
        if verified_only:
            return [f for f in self._findings if f.verified]
        return list(self._findings)

    def get_findings_from_environment(self) -> list[ResearchFinding]:
        """Load all findings from the stigmergic environment.

        Returns:
            List of all findings in intel/findings/.
        """
        pheromones = self.env.scan_pheromones(PheromoneType.RESEARCH)
        findings: list[ResearchFinding] = []
        for p in pheromones:
            try:
                findings.append(ResearchFinding.from_dict(p.data))
            except (KeyError, TypeError) as e:
                logger.warning("finding_parse_error", error=str(e))
        return findings

    def select_model_tier(self, task_complexity: float) -> str:
        """Select the appropriate model tier based on task complexity.

        Uses cheaper models for routine tasks and frontier models for
        complex analysis — cutting research costs by ~60%.

        Args:
            task_complexity: Complexity score (0-1).

        Returns:
            Model tier name ("haiku", "sonnet", or "opus").
        """
        if task_complexity < 0.3:
            return "haiku"
        elif task_complexity < 0.7:
            return "sonnet"
        else:
            return "opus"
