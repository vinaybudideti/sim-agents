"""Notes/Documentation Agent — living stenographer.

Monitors git commits, assignment changes, and completion events.
Maintains PROGRESS.md, DECISIONS.md, and FAILURES.md.
Uses observation masking for token reduction.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from sim_agents.coordination.stigmergy import PheromoneType, StigmergicEnvironment

logger = structlog.get_logger(__name__)


@dataclass
class ProgressEntry:
    """An entry in the progress log."""

    step: str
    description: str
    status: str  # "completed", "in_progress", "failed"
    timestamp: float = field(default_factory=time.time)

    def to_markdown(self) -> str:
        marker = "x" if self.status == "completed" else " "
        return f"- [{marker}] {self.step}: {self.description}"


@dataclass
class DecisionRecord:
    """An architectural decision record (ADR)."""

    title: str
    status: str  # "PROPOSED", "ACCEPTED", "DEPRECATED", "SUPERSEDED"
    decision: str
    context: str
    rationale: str
    consequences: str
    date: str = field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d"))

    def to_markdown(self) -> str:
        return (
            f"## ADR: {self.title}\n"
            f"**Date:** {self.date}\n"
            f"**Status:** {self.status}\n"
            f"**Decision:** {self.decision}\n"
            f"**Context:** {self.context}\n"
            f"**Rationale:** {self.rationale}\n"
            f"**Consequences:** {self.consequences}\n"
        )


@dataclass
class FailureRecord:
    """A record of a failed approach."""

    what_attempted: str
    module: str
    approach: str
    why_failed: str
    error_evidence: str
    lesson: str
    status: str = "ABANDONED"
    date: str = field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d"))

    def to_markdown(self) -> str:
        return (
            f"### [{self.date}] — {self.what_attempted}\n"
            f"**Module:** {self.module}\n"
            f"**Approach:** {self.approach}\n"
            f"**Why it failed:** {self.why_failed}\n"
            f"**Error/Evidence:** {self.error_evidence}\n"
            f"**Lesson:** {self.lesson}\n"
            f"**Status:** {self.status}\n"
        )


class NotesAgent:
    """Agent that maintains project documentation artifacts.

    Monitors the environment for changes and keeps PROGRESS.md,
    DECISIONS.md, and FAILURES.md up to date. Uses observation
    masking to reduce token consumption by >50%.
    """

    def __init__(
        self,
        project_root: str | Path,
        environment: StigmergicEnvironment | None = None,
        agent_id: str = "notes-agent",
    ) -> None:
        self.project_root = Path(project_root)
        self.agent_id = agent_id
        self.env = environment or StigmergicEnvironment(self.project_root)
        self._progress_path = self.project_root / "PROGRESS.md"
        self._decisions_path = self.project_root / "DECISIONS.md"
        self._failures_path = self.project_root / "FAILURES.md"
        logger.info("notes_agent_initialized", agent_id=agent_id)

    def get_recent_commits(self, count: int = 10) -> list[dict[str, str]]:
        """Get recent git commits with observation masking.

        Only extracts key information (hash, message) — strips verbose output.

        Args:
            count: Number of recent commits to retrieve.

        Returns:
            List of commit dicts with hash, message, author, date.
        """
        try:
            result = subprocess.run(
                [
                    "git", "log", f"-{count}",
                    "--format=%H|%s|%an|%aI",
                ],
                cwd=str(self.project_root),
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if result.returncode != 0:
                return []

            commits: list[dict[str, str]] = []
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                parts = line.split("|", 3)
                if len(parts) >= 2:
                    commits.append({
                        "hash": parts[0][:8],
                        "message": parts[1],
                        "author": parts[2] if len(parts) > 2 else "",
                        "date": parts[3] if len(parts) > 3 else "",
                    })
            return commits
        except (subprocess.SubprocessError, OSError):
            return []

    def add_progress(self, step: str, description: str, status: str = "completed") -> None:
        """Add a progress entry to PROGRESS.md.

        Args:
            step: Step identifier.
            description: What was done.
            status: "completed", "in_progress", or "failed".
        """
        entry = ProgressEntry(step=step, description=description, status=status)

        if self._progress_path.exists():
            content = self._progress_path.read_text()
        else:
            content = "# Progress Tracker\n\n## Completed\n\n## In Progress\n"

        line = entry.to_markdown()

        if status == "completed":
            # Add under Completed section
            content = content.replace(
                "## Completed\n",
                f"## Completed\n{line}\n",
                1,
            )
        else:
            # Add under In Progress section
            content = content.replace(
                "## In Progress\n",
                f"## In Progress\n{line}\n",
                1,
            )

        self._progress_path.write_text(content)
        logger.info("progress_added", step=step, status=status)

    def add_decision(self, record: DecisionRecord) -> None:
        """Add an architectural decision record to DECISIONS.md.

        Args:
            record: The decision to record.
        """
        if self._decisions_path.exists():
            content = self._decisions_path.read_text()
        else:
            content = "# Architectural Decision Records\n\n"

        content += "\n" + record.to_markdown()
        self._decisions_path.write_text(content)
        logger.info("decision_recorded", title=record.title)

    def add_failure(self, record: FailureRecord) -> None:
        """Add a failure record to FAILURES.md.

        The most important file — prevents agents from re-attempting dead ends.

        Args:
            record: The failure to record.
        """
        if self._failures_path.exists():
            content = self._failures_path.read_text()
        else:
            content = "# Failed Approaches\n\n"

        content += "\n" + record.to_markdown()
        self._failures_path.write_text(content)
        logger.info("failure_recorded", what=record.what_attempted)

    def observation_mask(self, verbose_output: str, max_chars: int = 500) -> str:
        """Apply observation masking to reduce token consumption.

        Strips verbose tool outputs and retains only key results.
        Achieves >50% token reduction.

        Args:
            verbose_output: The full output to mask.
            max_chars: Maximum characters to retain.

        Returns:
            Masked (compressed) output.
        """
        if len(verbose_output) <= max_chars:
            return verbose_output

        # Keep first and last portions
        head_size = max_chars // 2
        tail_size = max_chars // 2
        masked = (
            verbose_output[:head_size]
            + f"\n... [{len(verbose_output) - max_chars} chars masked] ...\n"
            + verbose_output[-tail_size:]
        )
        return masked

    def generate_status_summary(self) -> dict[str, Any]:
        """Generate a summary of current project status.

        Combines progress, recent commits, and environment state
        into a concise status report.

        Returns:
            Status summary dict.
        """
        commits = self.get_recent_commits(5)
        scan = self.env.scan_environment()

        summary = {
            "timestamp": time.time(),
            "recent_commits": len(commits),
            "last_commit": commits[0] if commits else None,
            "research_artifacts": len(scan.research_artifacts),
            "pending_assignments": len(scan.pending_assignments),
            "pending_reviews": len(scan.pending_reviews),
            "health_reports": len(scan.health_reports),
        }

        # Write summary as pheromone
        self.env.write_pheromone(
            PheromoneType.LOG,
            "status-summary.json",
            summary,
            agent_id=self.agent_id,
        )

        return summary

    def sync_from_environment(self) -> int:
        """Sync notes from environment changes.

        Monitors assignments and reviews for completion events
        and updates progress accordingly.

        Returns:
            Number of updates made.
        """
        updates = 0

        # Check for completed assignments
        assignments = self.env.scan_pheromones(PheromoneType.ASSIGNMENT)
        for a in assignments:
            if a.data.get("status") == "completed":
                task_title = a.data.get("task_title", "unknown")
                self.add_progress(
                    f"task-{a.data.get('task_id', '?')}",
                    f"Completed: {task_title}",
                    status="completed",
                )
                updates += 1

        logger.info("sync_complete", updates=updates)
        return updates
