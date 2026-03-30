# SIM Agents — Architectural Decision Records

## ADR-001: Implementation Framework Choice
**Date:** 2026-03-29
**Status:** ACCEPTED
**Decision:** Use Option A from SIM architecture spec — Claude Agent SDK + Redis + Git
**Context:** Three implementation options exist (Option A: Claude Agent SDK, Option B: LangGraph + Kafka, Option C: CrewAI Flows). Option A is recommended for Claude-native teams.
**Rationale:** Direct Claude Agent SDK integration provides best agent runtime with built-in file ops, shell access, and subagent orchestration. CrewAI used for high-level agent orchestration on top.
**Consequences:** Requires Anthropic API access, Redis for distributed locking, Git for file isolation.

## ADR-002: Redis Provider
**Date:** 2026-03-29
**Status:** ACCEPTED
**Decision:** Use Upstash Redis (REST-based, cloud-hosted) instead of local Redis
**Context:** Project runs in cloud sandbox and local MacBook — local Redis won't persist in cloud.
**Rationale:** Upstash provides REST API over HTTPS, works everywhere without TCP ports. Free tier sufficient for development.
**Consequences:** Use `upstash-redis` Python package, not standard `redis` package. Access via environment variables.

## ADR-003: Cost Optimization
**Date:** 2026-03-29
**Status:** ACCEPTED
**Decision:** Use agentfuse-runtime (pip install agentfuse-runtime) for all LLM API calls
**Context:** Multi-agent system can burn tokens fast — need budget enforcement and cost tracking.
**Rationale:** AgentFuse provides semantic caching, budget enforcement, and multi-provider routing with zero infrastructure.
**Consequences:** All LLM calls must go through AgentFuse, not direct API calls.

## ADR-004: Package Structure
**Date:** 2026-03-29
**Status:** ACCEPTED
**Decision:** Build as installable Python package with CLI (sim-agents init / sim-agents run)
**Context:** Need a clean, distributable package that others can install and use.
**Rationale:** Standard Python packaging with pyproject.toml, src layout, entry points for CLI.
**Consequences:** Package name on PyPI: sim-agents. Import as: from sim_agents import ...

---

## Template for New Decisions

```
## ADR-XXX: [Title]
**Date:** [date]
**Status:** PROPOSED / ACCEPTED / DEPRECATED / SUPERSEDED
**Decision:** [what was decided]
**Context:** [what prompted the decision]
**Rationale:** [why this option was chosen]
**Consequences:** [what this means going forward]
```
