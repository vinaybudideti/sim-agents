# SIM Agent System — Project Constitution

## Project Location
This project is located at: /Users/vinaykumarreddy/Documents/sim-agents/project1/sim-agents/

## Identity
This is **sim-agents**, a Python package implementing the SIM (Stigmergic-Immune Morphogenetic) architecture for perpetual autonomous multi-agent software development.

## CRITICAL: Read Before Doing ANYTHING
1. ALWAYS read `information.pdf` in this directory first — it contains the complete architecture spec
2. ALWAYS read `PROGRESS.md` before starting any work
3. ALWAYS read `FAILURES.md` before attempting any implementation
4. ALWAYS read `DECISIONS.md` for architectural context
5. ALWAYS read `backlog.json` for current task priorities

## Architecture Overview (from information.pdf)
- **3 Biological Layers**: Stigmergic Coordination, Immune Error Detection, Morphogenetic Self-Organization
- **10 Agent Roles**: Task Creator, Task Assigner, Notes Agent (Orchestrator tier) + Research, Research Verification, Worker/Builder (2-4), Checker/QA, Project Agent (Specialist tier)
- **Implementation**: Option A — Claude Agent SDK + Redis + Git
- **Agent Framework**: CrewAI for orchestration
- **Cost Optimization**: agentfuse-runtime for all LLM calls
- **Collision Prevention**: Git worktrees, Redis distributed locks (Upstash), CRDTs, event sourcing

## Credentials & Secrets
- ALL credentials are stored as environment variables
- Access Redis: `os.environ["UPSTASH_REDIS_REST_URL"]` and `os.environ["UPSTASH_REDIS_REST_TOKEN"]`
- NEVER hardcode any credentials in code
- NEVER commit .env files or any file containing secrets
- NEVER print or log credential values

## File Conventions
```
src/sim_agents/           → Main package source code
src/sim_agents/agents/    → All 10 agent definitions
src/sim_agents/coordination/ → Stigmergy, locking, state (CRDTs)
src/sim_agents/memory/    → Progress, failures, decisions management
src/sim_agents/safety/    → Budget, drift detection, rollback
src/sim_agents/notifications/ → Slack, Telegram human-in-the-loop
tests/                    → All test files (mirror src structure)
intel/findings/           → Research artifacts (stigmergic layer)
assignments/              → Task assignments per agent
reviews/                  → QA review results
health/                   → Project health reports
notifications/            → Human intervention requests
logs/                     → Agent execution logs
```

## Core State Files
- `PROGRESS.md` → What's done, in-progress, next steps
- `FAILURES.md` → Failed approaches and WHY (most important file — prevents re-attempting dead ends)
- `DECISIONS.md` → Architectural decisions with rationale (ADR format)
- `backlog.json` → Task queue with priority scores (JSON, not Markdown)
- `feature_list.json` → Feature tracking with boolean pass/fail flags

## Git Workflow
- Each agent works on branch: `agent/{agent-id}/{task-id}`
- PRs target main, must pass QA before merge
- Commit messages format: `[{agent-role}] {description}`
- Commit after EVERY completed module/subtask
- Never push directly to main

## Quality Gates
- All tests must pass (`pytest`)
- No lint errors (`ruff check .`)
- Type checking passes (`mypy src/`)
- Coverage must not decrease below 80%
- `FAILURES.md` must be checked before any implementation
- Every module must have corresponding tests

## Development Rules
1. After every completed module → commit with clear message
2. After every completed step → update PROGRESS.md
3. If something fails → document in FAILURES.md with WHY
4. Before starting any task → read PROGRESS.md and FAILURES.md
5. Never stop between steps → read PROGRESS.md and continue to next
6. Write tests BEFORE or ALONGSIDE every module
7. Run tests after each module to verify everything passes
8. When writing sim-agents package code, use agentfuse-runtime for LLM API calls within the agents. This does NOT apply to the current Claude Code build session. If agentfuse-runtime fails to import, fall back to direct anthropic SDK calls and add a TODO.
9. Production-grade quality — zero tolerance for errors

## Model Routing (Cost Optimization)
- Use Sonnet-class models for routine tasks (workers, research, docs)
- Use Opus-class models ONLY for architecture decisions and complex reasoning
- Target: 60% cost reduction through smart routing

## Context Management
- Strip verbose tool outputs, keep only key results
- Use grep/head/tail for targeted reads, never dump entire files
- If context is getting large, commit work, update PROGRESS.md
- Each session starts fresh by reading state files

## Homeostatic Targets
| Variable | Set Point |
|----------|-----------|
| Test pass rate | >95% |
| Code coverage | >80% |
| Build time | <5 minutes |
| Error rate | <1% |
