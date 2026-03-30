"""Tests for the Notes/Documentation Agent."""

from __future__ import annotations

from pathlib import Path

import pytest

from sim_agents.agents.notes import (
    DecisionRecord,
    FailureRecord,
    NotesAgent,
    ProgressEntry,
)
from sim_agents.coordination.stigmergy import PheromoneType, StigmergicEnvironment


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def env(project_root: Path) -> StigmergicEnvironment:
    return StigmergicEnvironment(project_root)


@pytest.fixture
def notes(project_root: Path, env: StigmergicEnvironment) -> NotesAgent:
    return NotesAgent(project_root, environment=env)


class TestProgressEntry:
    def test_completed_markdown(self) -> None:
        entry = ProgressEntry("Step 1", "Built module X", "completed")
        md = entry.to_markdown()
        assert "[x]" in md
        assert "Step 1" in md

    def test_in_progress_markdown(self) -> None:
        entry = ProgressEntry("Step 2", "Building Y", "in_progress")
        md = entry.to_markdown()
        assert "[ ]" in md


class TestDecisionRecord:
    def test_to_markdown(self) -> None:
        adr = DecisionRecord(
            title="Use Redis",
            status="ACCEPTED",
            decision="Use Upstash Redis for distributed locking",
            context="Need distributed locking across agents",
            rationale="HTTP-based, works everywhere",
            consequences="Must use upstash-redis package",
        )
        md = adr.to_markdown()
        assert "Use Redis" in md
        assert "ACCEPTED" in md
        assert "upstash-redis" in md


class TestFailureRecord:
    def test_to_markdown(self) -> None:
        failure = FailureRecord(
            what_attempted="Direct Redis TCP",
            module="coordination/locking.py",
            approach="Used standard redis package",
            why_failed="Cloud sandbox blocks TCP ports",
            error_evidence="ConnectionRefusedError",
            lesson="Use Upstash REST-based Redis",
        )
        md = failure.to_markdown()
        assert "Direct Redis TCP" in md
        assert "ConnectionRefusedError" in md
        assert "ABANDONED" in md


class TestNotesAgent:
    def test_add_progress(self, notes: NotesAgent, project_root: Path) -> None:
        notes.add_progress("Step 1", "Built coordination layer")
        content = (project_root / "PROGRESS.md").read_text()
        assert "Step 1" in content
        assert "[x]" in content

    def test_add_progress_in_progress(self, notes: NotesAgent, project_root: Path) -> None:
        notes.add_progress("Step 2", "Building agents", "in_progress")
        content = (project_root / "PROGRESS.md").read_text()
        assert "[ ]" in content

    def test_add_decision(self, notes: NotesAgent, project_root: Path) -> None:
        adr = DecisionRecord(
            title="Use CrewAI",
            status="ACCEPTED",
            decision="Use CrewAI for orchestration",
            context="Need agent framework",
            rationale="Best for role-based agents",
            consequences="Adds CrewAI dependency",
        )
        notes.add_decision(adr)
        content = (project_root / "DECISIONS.md").read_text()
        assert "Use CrewAI" in content

    def test_add_failure(self, notes: NotesAgent, project_root: Path) -> None:
        failure = FailureRecord(
            what_attempted="LangChain agents",
            module="agents/",
            approach="Used LangChain agent framework",
            why_failed="Too much overhead for simple tasks",
            error_evidence="Token cost 3x higher",
            lesson="Use CrewAI for simpler agent definitions",
        )
        notes.add_failure(failure)
        content = (project_root / "FAILURES.md").read_text()
        assert "LangChain agents" in content

    def test_observation_masking(self, notes: NotesAgent) -> None:
        long_output = "x" * 2000
        masked = notes.observation_mask(long_output, max_chars=500)
        assert len(masked) < len(long_output)
        assert "masked" in masked

    def test_observation_masking_short_passthrough(self, notes: NotesAgent) -> None:
        short = "hello world"
        assert notes.observation_mask(short) == short

    def test_generate_status_summary(self, notes: NotesAgent) -> None:
        summary = notes.generate_status_summary()
        assert "timestamp" in summary
        assert "recent_commits" in summary

    def test_status_summary_writes_pheromone(
        self, notes: NotesAgent, project_root: Path
    ) -> None:
        notes.generate_status_summary()
        pheromones = notes.env.scan_pheromones(PheromoneType.LOG)
        assert len(pheromones) >= 1

    def test_sync_from_environment(
        self, notes: NotesAgent, env: StigmergicEnvironment
    ) -> None:
        env.write_pheromone(
            PheromoneType.ASSIGNMENT,
            "completed-task.json",
            {"status": "completed", "task_id": 1, "task_title": "Done task"},
        )
        updates = notes.sync_from_environment()
        assert updates == 1

    def test_multiple_progress_entries(self, notes: NotesAgent, project_root: Path) -> None:
        notes.add_progress("Step 1", "First step")
        notes.add_progress("Step 2", "Second step")
        content = (project_root / "PROGRESS.md").read_text()
        assert "Step 1" in content
        assert "Step 2" in content

    def test_get_recent_commits_no_repo(self, notes: NotesAgent) -> None:
        # tmp_path is not a git repo, should return empty
        commits = notes.get_recent_commits()
        assert commits == []
