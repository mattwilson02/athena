# Athena — Stage 1 Gap Analysis

> Where are we vs where the vision says we should be? Mapped on 2026-03-02 after E1 completion.

---

## Current State vs Stage 1 Vision

| # | Requirement | Vision Says | Current State | Gap Location | Severity |
|---|------------|-------------|---------------|-------------|----------|
| 1 | **Cascade updates** | One change ripples through connected nodes | 0% — no cascade logic exists | New service + vault_service | CRITICAL |
| 2 | **Conflict detection** | Flags contradictions with goals/values/habits | 0% — no conflict logic | New service + retrieval | CRITICAL |
| 3 | **Conversations as writes** | Plan changes in chat become graph updates | 30% — prompt says to, AI doesn't | FORMAT_SPEC rewrite | CRITICAL |
| 4 | **Personality enforcement** | Sharp, direct, strategic mentor | 20% — SOUL.md loaded but ignored | SOUL.md + prompting | HIGH |
| 5 | **Adaptive modes** | Mirror/Advisor/Guardian/Dialectic | 0% — all responses same tone | Mode classifier in mentor_agent | HIGH |
| 6 | **Memory permanence** | Identity > Strategic > Tactical weighting | 0% — all nodes equal | Schema field + scoring | HIGH |
| 7 | **Tradeoff engine** | Maps cost of new commitment vs existing | 0% — no cost analysis | Retrieval enrichment | HIGH |
| 8 | **Accountability** | Commitment tracking, streaks, intervention | 15% — fields exist, no logic | New service | HIGH |
| 9 | **Human fundamentals** | Movement/sleep/nutrition/connection/purpose/finance scaffold | 0% — no bootstrap | Schema + onboarding prompt | MEDIUM |
| 10 | **State of mind reading** | Infer stress/energy from messages | 0% | New service | MEDIUM |
| 11 | **Relationship intelligence** | Dynamics, growth, influence mapping | 0% — people nodes are contact cards | Schema + analysis | MEDIUM |

### What's Done (E1)

| Capability | Status |
|-----------|--------|
| Status-aware retrieval scoring | 100% |
| Pre-computed temporal context | 95% |
| Concurrent write safety | 100% |
| Incremental vector indexing | 100% |
| Vault audit endpoint | 100% |
| Thread-safe vault writes | 100% |

---

## Architecture Health

| Component | File | Health | Stage 1 Changes Needed |
|-----------|------|--------|----------------------|
| App factory | `server.py` | 95% — clean | Register new services only |
| Graph engine | `vault_graph.py` | 90% — solid | None |
| Vector search | `vector_search.py` | 75% — works | Metadata filtering, date indexing |
| **AI reasoning** | **`mentor_agent.py`** | **60% — fragile** | **FORMAT_SPEC, scoring, mode selection, context enrichment** |
| Vault I/O | `vault_service.py` | 90% — solid | Add cascade methods |
| Chat orchestration | `chat_service.py` | 90% — solid | Integrate cascade + conflict |
| Routes | `routes/*.py` | 90% — clean | Add endpoints as features land |
| Auth & security | `middleware/` | 90% — good | None |
| Tests | `tests/` | 85% — good units | Need integration tests |

### mentor_agent.py — The Core Problem

This is the intelligence layer and it's the weakest component at 970 lines. Issues:

- **Scoring is fragile** — domain boost is binary, centrality caps too early, temporal boost is all-or-nothing, session boost is buried. Hub nodes dominate context.
- **Personality is advice, not enforced** — SOUL.md says "sharp, direct" but Claude cheerleads. Instructions are suggestions, not constraints.
- **FORMAT_SPEC fights itself** — TARGETING says "prefer specific nodes over goals" which causes AI to create new memories instead of updating existing goals. CASCADE rule exists in text but has no implementation.
- **Conversations aren't write operations** — AI is asked to propose updates but doesn't enforce them. Knowledge dies with the session.
- **No conflict awareness** — retrieval doesn't look for contradicting nodes. The context includes relevant nodes but not opposing ones.

---

## Schema Gaps

| Gap | Impact | Blocks |
|-----|--------|--------|
| No `permanence` field | Can't weight identity > tactical in retrieval | E4 (Memory permanence) |
| No human fundamentals scaffold | No bootstrap for new users, no species-level monitoring | E2 (Bootstrap), E6 (Monitoring) |
| Habit `streak` field exists but never computed | Can't track consistency | E5 (Accountability) |
| Status value chaos (20+ undeclared) | No validation, any string accepted | All epochs |
| Goal vs project boundary unclear | Duplicate nodes, confused AI | Schema V3 |
| `note` as catch-all | Hides what's actually stored | Schema V3 |
| No commitment type | Can't track promises distinctly from tasks | E5 (Accountability) |
| Duplicate nodes in vault | `weight-cut-goal` + `cut-to-83kg`, `financial-freedom-goal` + `financial-freedom-target` | Data quality |

### Vault Data Quality

The ~105 existing nodes were created under broken rules:
- AI created wrong types (memories instead of goal updates)
- Frontmatter fields are inconsistent (custom fields not in schema)
- Status values diverge from schema definitions
- Duplicate/near-duplicate nodes exist for the same concept
- DATE RULES weren't in the prompt for much of the data creation period

**Decision:** Archive to `_backup/v2/`, start fresh, re-import later through a strict validation pipeline.

---

## What's Solid (Don't Touch)

- App factory pattern (`server.py`)
- NetworkX graph wrapper (`vault_graph.py`)
- Vault parser (`vault_parser.py`)
- Chat session persistence (`chat_store.py`)
- Authentication & security (`middleware/`)
- Docker three-container architecture
- Svelte 5 frontend structure
- Markdown + YAML frontmatter vault format
- Schema-driven architecture principle
