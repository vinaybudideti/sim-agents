"""Tests for the CLI entry point."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from sim_agents.cli import cmd_init, cmd_logs, cmd_status, create_parser, main


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    return tmp_path


class TestCreateParser:
    def test_parser_creation(self) -> None:
        parser = create_parser()
        assert parser is not None

    def test_parse_init(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["init", "-d", "/tmp/test"])
        assert args.command == "init"
        assert args.directory == "/tmp/test"

    def test_parse_run(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["run", "--cycles", "5", "--budget", "50"])
        assert args.command == "run"
        assert args.cycles == 5
        assert args.budget == 50.0

    def test_parse_run_defaults(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["run"])
        assert args.cycles == 1
        assert args.budget == 100.0
        assert args.workers == 2

    def test_parse_status(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["status"])
        assert args.command == "status"

    def test_parse_logs(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["logs", "--limit", "50"])
        assert args.command == "logs"
        assert args.limit == 50

    def test_parse_logs_default_limit(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["logs"])
        assert args.limit == 20


class TestCmdInit:
    def test_init_creates_directories(self, project_root: Path) -> None:
        parser = create_parser()
        args = parser.parse_args(["init", "-d", str(project_root)])
        result = cmd_init(args)
        assert result == 0

        assert (project_root / "intel" / "findings").is_dir()
        assert (project_root / "assignments").is_dir()
        assert (project_root / "reviews").is_dir()
        assert (project_root / "health").is_dir()
        assert (project_root / "logs").is_dir()

    def test_init_creates_state_files(self, project_root: Path) -> None:
        parser = create_parser()
        args = parser.parse_args(["init", "-d", str(project_root)])
        cmd_init(args)

        assert (project_root / "PROGRESS.md").exists()
        assert (project_root / "FAILURES.md").exists()
        assert (project_root / "DECISIONS.md").exists()
        assert (project_root / "backlog.json").exists()
        assert (project_root / "feature_list.json").exists()

    def test_init_preserves_existing_files(self, project_root: Path) -> None:
        (project_root / "PROGRESS.md").write_text("# My Progress")
        parser = create_parser()
        args = parser.parse_args(["init", "-d", str(project_root)])
        cmd_init(args)
        content = (project_root / "PROGRESS.md").read_text()
        assert content == "# My Progress"

    def test_init_backlog_is_valid_json(self, project_root: Path) -> None:
        parser = create_parser()
        args = parser.parse_args(["init", "-d", str(project_root)])
        cmd_init(args)
        data = json.loads((project_root / "backlog.json").read_text())
        assert "tasks" in data
        assert "next_id" in data


class TestCmdStatus:
    def test_status_no_project(self, project_root: Path) -> None:
        parser = create_parser()
        args = parser.parse_args(["status", "-d", str(project_root)])
        result = cmd_status(args)
        assert result == 1  # Not a SIM project

    def test_status_with_project(self, project_root: Path) -> None:
        # Init first
        parser = create_parser()
        cmd_init(parser.parse_args(["init", "-d", str(project_root)]))

        args = parser.parse_args(["status", "-d", str(project_root)])
        result = cmd_status(args)
        assert result == 0


class TestCmdLogs:
    def test_logs_no_directory(self, project_root: Path) -> None:
        parser = create_parser()
        args = parser.parse_args(["logs", "-d", str(project_root)])
        result = cmd_logs(args)
        assert result == 1

    def test_logs_empty(self, project_root: Path) -> None:
        (project_root / "logs").mkdir()
        parser = create_parser()
        args = parser.parse_args(["logs", "-d", str(project_root)])
        result = cmd_logs(args)
        assert result == 0

    def test_logs_with_entries(self, project_root: Path) -> None:
        logs_dir = project_root / "logs"
        logs_dir.mkdir()
        (logs_dir / "task-1.json").write_text(json.dumps({
            "agent_id": "worker-1", "task_id": 1, "status": "completed",
        }))
        parser = create_parser()
        args = parser.parse_args(["logs", "-d", str(project_root)])
        result = cmd_logs(args)
        assert result == 0


class TestMain:
    def test_no_command(self) -> None:
        with patch("sys.argv", ["sim-agents"]):
            result = main()
            assert result == 0

    def test_init_command(self, project_root: Path) -> None:
        with patch("sys.argv", ["sim-agents", "init", "-d", str(project_root)]):
            result = main()
            assert result == 0
