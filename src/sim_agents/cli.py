"""CLI entry point for sim-agents.

Commands:
    sim-agents init        — Scaffold a new SIM project
    sim-agents run         — Run the SIM agent loop
    sim-agents status      — Show current project health
    sim-agents logs        — View agent execution logs
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def cmd_init(args: argparse.Namespace) -> int:
    """Initialize a new SIM project structure.

    Creates all required directories and config files.
    """
    project_dir = Path(args.directory).resolve()
    print(f"Initializing SIM project in: {project_dir}")

    # Create directory structure
    dirs = [
        "src",
        "tests",
        "intel/findings",
        "assignments",
        "reviews",
        "health",
        "logs",
        "notifications",
    ]
    for d in dirs:
        (project_dir / d).mkdir(parents=True, exist_ok=True)
        print(f"  Created {d}/")

    # Create state files if they don't exist
    state_files: dict[str, str] = {
        "PROGRESS.md": (
            "# SIM Agents — Progress Tracker\n\n"
            "## Completed\n\n## In Progress\n\n## Next Steps\n"
        ),
        "FAILURES.md": (
            "# SIM Agents — Failed Approaches\n\n"
            "## Failed Approaches\n_None yet_\n"
        ),
        "DECISIONS.md": (
            "# SIM Agents — Architectural Decisions\n\n"
        ),
    }

    for filename, content in state_files.items():
        filepath = project_dir / filename
        if not filepath.exists():
            filepath.write_text(content)
            print(f"  Created {filename}")
        else:
            print(f"  {filename} already exists, skipping")

    # Create backlog.json if not exists
    backlog_path = project_dir / "backlog.json"
    if not backlog_path.exists():
        backlog_path.write_text(json.dumps({"tasks": [], "next_id": 1}, indent=2))
        print("  Created backlog.json")

    # Create feature_list.json if not exists
    feature_path = project_dir / "feature_list.json"
    if not feature_path.exists():
        feature_path.write_text(json.dumps({
            "features": [],
            "iteration": 0,
            "total_features": 0,
            "passing": 0,
        }, indent=2))
        print("  Created feature_list.json")

    print("\nSIM project initialized successfully!")
    print("Run 'sim-agents run' to start the agent loop.")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Run the SIM agent loop."""
    project_dir = Path(args.directory).resolve()

    if not (project_dir / "backlog.json").exists():
        print("Error: Not a SIM project. Run 'sim-agents init' first.")
        return 1

    print(f"Starting SIM agent loop in: {project_dir}")
    print(f"  Cycles: {args.cycles}")
    print(f"  Budget: ${args.budget}")
    print(f"  Workers: {args.workers}")
    print()

    try:
        from sim_agents.orchestrator import SIMOrchestrator

        orchestrator = SIMOrchestrator(
            project_root=project_dir,
            num_workers=args.workers,
        )

        results = orchestrator.run(
            max_cycles=args.cycles,
            budget=args.budget,
        )

        # Print summary
        print("\n" + "=" * 50)
        print("SIM Run Summary")
        print("=" * 50)
        for r in results:
            print(
                f"  Cycle {r.cycle_number}: "
                f"created={r.tasks_created} "
                f"assigned={r.tasks_assigned} "
                f"approved={r.tasks_approved} "
                f"rejected={r.tasks_rejected} "
                f"health={r.health_status} "
                f"({r.duration_seconds:.1f}s)"
            )

        total_approved = sum(r.tasks_approved for r in results)
        total_rejected = sum(r.tasks_rejected for r in results)
        print(f"\nTotal: {total_approved} approved, {total_rejected} rejected")

        status = orchestrator.get_status()
        print(f"Health: {status['health_status']}")
        print(f"Budget remaining: ${status['budget_remaining']:.2f}")

    except Exception as e:
        print(f"Error: {e}")
        return 1

    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Show current project health status."""
    project_dir = Path(args.directory).resolve()

    if not (project_dir / "backlog.json").exists():
        print("Error: Not a SIM project. Run 'sim-agents init' first.")
        return 1

    print(f"SIM Project Status: {project_dir}")
    print("=" * 50)

    # Read backlog
    backlog_path = project_dir / "backlog.json"
    try:
        backlog = json.loads(backlog_path.read_text())
        tasks = backlog.get("tasks", [])
        open_tasks = [t for t in tasks if t.get("status") == "open"]
        completed = [t for t in tasks if t.get("status") == "completed"]
        print(f"\nBacklog: {len(tasks)} total, {len(open_tasks)} open, {len(completed)} completed")
    except (json.JSONDecodeError, OSError):
        print("\nBacklog: Unable to read")

    # Read feature list
    feature_path = project_dir / "feature_list.json"
    if feature_path.exists():
        try:
            features = json.loads(feature_path.read_text())
            passing = features.get("passing", 0)
            total = features.get("total_features", 0)
            print(f"Features: {passing}/{total} passing")
        except (json.JSONDecodeError, OSError):
            pass

    # Read health report
    health_path = project_dir / "health" / "runtime-report.json"
    if health_path.exists():
        try:
            report = json.loads(health_path.read_text())
            print(f"\nHealth: {report.get('overall_status', 'unknown')}")
            for metric in report.get("metrics", []):
                status_icon = "OK" if metric["status"] == "healthy" else "!!"
                print(f"  [{status_icon}] {metric['name']}: {metric['value']}")
        except (json.JSONDecodeError, OSError):
            pass
    else:
        print("\nHealth: No report yet (run 'sim-agents run' first)")

    # Check environment directories
    print("\nEnvironment:")
    for dirname in ["intel/findings", "assignments", "reviews", "health", "logs"]:
        dirpath = project_dir / dirname
        if dirpath.exists():
            count = len(list(dirpath.glob("*.json")))
            print(f"  {dirname}: {count} artifacts")

    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    """View agent execution logs."""
    project_dir = Path(args.directory).resolve()
    logs_dir = project_dir / "logs"

    if not logs_dir.exists():
        print("No logs directory found.")
        return 1

    log_files = sorted(logs_dir.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)

    if not log_files:
        print("No log files found.")
        return 0

    limit = args.limit
    print(f"Recent logs (showing {min(limit, len(log_files))} of {len(log_files)}):")
    print("-" * 50)

    for log_file in log_files[:limit]:
        try:
            data = json.loads(log_file.read_text())
            agent = data.get("agent_id", data.get("agent", "unknown"))
            status = data.get("status", "unknown")
            task_id = data.get("task_id", "?")
            print(f"  [{agent}] Task {task_id}: {status} — {log_file.name}")
        except (json.JSONDecodeError, OSError):
            print(f"  [error] Could not read {log_file.name}")

    return 0


def create_parser() -> argparse.ArgumentParser:
    """Create the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="sim-agents",
        description="SIM (Stigmergic-Immune Morphogenetic) multi-agent development system",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # init
    init_parser = subparsers.add_parser("init", help="Initialize a new SIM project")
    init_parser.add_argument(
        "-d", "--directory", default=".", help="Project directory (default: current)"
    )

    # run
    run_parser = subparsers.add_parser("run", help="Run the SIM agent loop")
    run_parser.add_argument(
        "-d", "--directory", default=".", help="Project directory"
    )
    run_parser.add_argument(
        "--cycles", "-c", type=int, default=1, help="Number of cycles (default: 1)"
    )
    run_parser.add_argument(
        "--budget", "-b", type=float, default=100.0, help="Budget in dollars (default: 100)"
    )
    run_parser.add_argument(
        "--workers", "-w", type=int, default=2, help="Number of workers (default: 2)"
    )

    # status
    status_parser = subparsers.add_parser("status", help="Show project health status")
    status_parser.add_argument(
        "-d", "--directory", default=".", help="Project directory"
    )

    # logs
    logs_parser = subparsers.add_parser("logs", help="View agent execution logs")
    logs_parser.add_argument(
        "-d", "--directory", default=".", help="Project directory"
    )
    logs_parser.add_argument(
        "--limit", "-n", type=int, default=20, help="Number of log entries (default: 20)"
    )

    return parser


def main() -> int:
    """Main CLI entry point."""
    parser = create_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    commands = {
        "init": cmd_init,
        "run": cmd_run,
        "status": cmd_status,
        "logs": cmd_logs,
    }

    handler = commands.get(args.command)
    if handler:
        return handler(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
