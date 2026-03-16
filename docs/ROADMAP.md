# Athena — Stage 1 Roadmap

> Pre-planned epoch sequence from current state to Stage 1 completion.
> Mapped on 2026-03-02. See [GAP_ANALYSIS.md](GAP_ANALYSIS.md) for the gap assessment this is built on.

---

## Overview

5 epochs + prep phase. Each epoch is 3 weeks (2 days shape, 12 days build, 2 days validate, 2 days cool-down). ~4 months total.

```
Prep (schema V3 + vault reset + SOUL.md)
  └→ E2: Conversations That Stick (writes + cascade + bootstrap)
       └→ E3: The Guardian (conflict + personality + tradeoffs)
            └→ E4: Weight and Memory (permanence + modes + challenge ladder)
                 └→ E5: Accountability (commitments + streaks + consequences)
                      └→ E6: Reading the Room (state + relationships + fundamentals monitoring)
```

| Phase | Duration | Cumulative | Vision Requirements |
|-------|----------|-----------|---------------------|
| Prep | 4-5 days | Week 1 | Schema, data, personality groundwork |
| E2 | 3 weeks | Week 4 | Cascade (1), Conversations as writes (3), Cold start (9) |
| E3 | 3 weeks | Week 7 | Conflict detection (2), Personality (4), Tradeoffs (7) |
| E4 | 3 weeks | Week 10 | Adaptive modes (5), Memory permanence (6) |
| E5 | 3 weeks | Week 13 | Accountability (8) |
| E6 | 3 weeks | Week 16 | State of mind (10), Relationships (11), Fundamentals monitoring (9) |

---

## Pre-Epoch Prep (4-5 days)

This is the foundation. Every epoch depends on these decisions being right.

### 1. Vault Archive & Reset
- Move all existing vault nodes to `_backup/v2/`
- Clear ChromaDB persistence (`backend/chroma_db/`)
- Clear chat sessions (`backend/chat_sessions/`)
- Rebuild containers with empty state
- Verify: audit returns 0 nodes, graph stats 0/0

### 2. Schema V3 — Full Redesign

Rethink the type system from scratch. Current 27 types grew organically with unclear boundaries.

**Design principles:**
- Every type earns its existence. Overlap = merge or clarify.
- Frontmatter fields must be computed, not aspirational.
- `permanence` level on every type (fundamental | identity | strategic | tactical | ephemeral).
- Status values whitelisted per type.
- Human fundamentals as first-class concept.
- Commitment tracking distinct from tasks.

**Key decisions:**
- Type count: current 27, target ~20-22 with merges
- Does `note` survive? If so, constrained how?
- Goal vs project: merge or sharpen boundary?
- Financial: 3 types or 1?
- Life: memory vs experience vs daily — simplify?

### 3. SOUL.md Rewrite
- `## Conflict Protocol` — 5-step escalation (flag → investigate → escalate → challenge → accept)
- `## Mode Selection` — Mirror/Advisor/Guardian/Dialectic with activation criteria
- `## Bootstrap Protocol` — onboarding conversation for empty graph
- `## Boundaries` — explicit anti-patterns, no cheerleading
- Update `_load_soul()` in `mentor_agent.py` to parse new sections

---

## E2: "Conversations That Stick"

> After this epoch, every plan change in conversation becomes a graph update. When you accept one, connected nodes that reference stale information get cascade proposals too. Knowledge no longer dies with the session.

**Dependencies:** Prep complete (schema V3 + vault reset + SOUL.md)

### In Scope

1. **FORMAT_SPEC rewrite** — mandatory graph updates for any state change. Specific examples: "user says X → propose Y". Fix TARGETING rules so metric updates hit existing nodes, not new memories.
2. **Cascade engine** — `vault_service.cascade_check(node_id, changes)`. After write/update, scan 1-hop neighbors for stale references. Return cascade proposals as additional graph_updates.
3. **Supersession logic** — when creating a replacement, old node gets `status: superseded`. Validation: if a create has `replaces: old-id`, ensure an update for the old node exists in the same batch.
4. **Bootstrap conversation** — empty vault triggers fundamentals scaffold. `get_context()` detects zero nodes → injects bootstrap prompt. First chat creates seed nodes.
5. **GraphUpdateCard UX** — show what's changing: before/after for updates, full content for creates, edge visualization for links.
6. **Vault re-import endpoint** — `POST /api/vault/import` processes archived V2 nodes through the new schema: validates types, assigns permanence, normalises status, dedup-checks, proposes corrections. Batch review.

### Out of Scope
- Conflict detection (E3)
- Adaptive modes (E4)
- Permanence scoring (E4)
- Accountability (E5)

### Key Files
- `backend/mentor_agent.py` — FORMAT_SPEC, bootstrap prompt, write enforcement
- `backend/services/vault_service.py` — cascade_check() method
- `backend/services/chat_service.py` — cascade integration after write/update
- `frontend/src/lib/GraphUpdateCard.svelte` — richer update display
- `backend/routes/vault_routes.py` — import endpoint

### Acceptance Criteria
1. Change plans mid-conversation → update + cascade proposals appear
2. Tell Athena something new → correct type proposed (not "note" catch-all)
3. Start with empty vault → first conversation walks through fundamentals → 6+ nodes created
4. Accept a graph update → cascade proposals fire for affected neighbors
5. GraphUpdateCards show what is being created/changed with detail
6. Import archived nodes → validation + dedup + correction proposals
7. All existing tests pass + new tests for cascade, bootstrap, import

### Risk
- Cascade detection is heuristic (string matching on neighbor content). False positives. Mitigation: cascade proposals are suggestions, not auto-applied.
- Write aggressiveness may produce too many low-quality proposals. Mitigation: dedup check catches duplicates. Tune during validation.

---

## E3: "The Guardian"

> After this epoch, Athena pushes back when you contradict yourself. "I'll stay up till 3am" with a 6am training plan and a sleep value gets flagged before accepting. It's a strategic advisor, not a cheerleader.

**Dependencies:** E2 (cascade + write aggressiveness generate the data that conflicts are detected against)

### In Scope

1. **Conflict retrieval pass** — when user expresses intention ("I'm going to", "I want to", "planning to"), search Self-domain nodes for contradictions. Inject `POTENTIAL CONFLICTS:` header into context.
2. **Conflict service** — `services/conflict_service.py`. `detect_conflicts(intention, graph, vector_index)` → list of `{node_id, title, conflict_type, explanation}`. Types: schedule_conflict, goal_contradiction, value_violation, habit_break.
3. **Personality enforcement** — SOUL.md conflict protocol parsed and injected. Anti-cheerleading rules. Post-processing check: flag responses with filler phrases.
4. **Tradeoff awareness** — new commitment triggers injection of all active goals + commitments into context. Claude sees the full obligation picture.
5. **`contradicts` edge activation** — AI instructed to propose contradiction links when detected.

### Out of Scope
- Adaptive modes (E4)
- Permanence scoring (E4)
- Accountability (E5)
- Full what-if simulation (Stage 2)

### Key Files
- `backend/mentor_agent.py` — conflict retrieval, tradeoff injection, personality checks
- `backend/services/conflict_service.py` (new)
- `SOUL.md` — conflict protocol, boundaries

### Acceptance Criteria
1. Contradict a stated goal → Athena flags it by node name before responding
2. Propose new commitment with 3 active projects → obligations surfaced
3. State intention violating a core value → specific value node referenced
4. No cheerleading on contradictory plans
5. `detect_conflicts()` returns correct results for programmatic test cases
6. All tests pass + conflict detection tests

### Risk
- Over-flagging makes Athena annoying. Mitigation: only flag Self-domain nodes + active commitments.
- Quality depends on graph completeness. Mitigation: fundamentals bootstrap from E2 ensures core values exist.

---

## E4: "Weight and Memory"

> After this epoch, core values weigh more than Tuesday's task in retrieval. Athena adapts its tone to the stakes. Dropping a core value requires earning the change.

**Dependencies:** E3 (conflict detection enables Guardian mode)

### In Scope

1. **Permanence scoring** — add boost to `get_context()`: fundamental +0.20, identity +0.15, strategic +0.10, tactical +0.00, ephemeral -0.05. Derived from `permanence` frontmatter field, falling back to type-based defaults.
2. **Permanence-aware cascade** — modifying identity/fundamental nodes requires extra confirmation. Cascade engine flags: "This would modify a core value."
3. **Adaptive mode selection** — classify message → Mirror/Advisor/Guardian/Dialectic via keyword heuristics + conflict detection results. Inject mode-specific instructions into prompt.
4. **Challenge ladder** — 5-step escalation for identity/fundamental changes. State tracked per session in chat_store: `{node_id, step: 1-5, history: []}`.

### Out of Scope
- Accountability (E5)
- State of mind (E6)
- Relationship intelligence (E6)

### Key Files
- `backend/mentor_agent.py` — permanence scoring, mode classifier
- `backend/services/vault_service.py` — permanence-aware cascade
- `backend/services/chat_service.py` — challenge ladder state
- `backend/chat_store.py` — store challenge state per session

### Acceptance Criteria
1. Simple question → Mirror mode (concise, informational)
2. New commitment → Advisor mode (surfaces tradeoffs)
3. Contradiction → Guardian mode (flags conflict, pushes back)
4. "Should I quit my job?" → Dialectic mode (challenges assumptions)
5. Try to remove a core value → 5-step challenge before allowing
6. Identity nodes consistently in context even for tactical queries
7. All tests pass + mode classification, permanence scoring, challenge ladder tests

### Risk
- Mode classification is heuristic and will misfire. Mitigation: default to Mirror. Guardian overrides when conflict detection fires.
- Permanence boost may crowd out relevant tactical nodes. Mitigation: boost is additive, not multiplicative.

---

## E5: "Accountability"

> After this epoch, Athena tracks commitments and holds you to them. Missed deadlines surface with consequence chains. Habit streaks are calculated. Patterns emerge.

**Dependencies:** E4 (permanence levels define what gets tracked, modes enable appropriate communication)

### In Scope

1. **Commitment tracking** — promise in conversation → commitment node with `deadline`, `made_on`, `context`. Detection via explicit promise patterns ("I will do X by Y").
2. **Habit streak calculation** — compute from linked daily nodes. `accountability_service.calculate_streaks(graph)`.
3. **Proactive alerts** — inject overdue commitments + broken streaks into context header: `PROACTIVE ALERTS:`.
4. **Consequence surfacing** — traverse graph from overdue commitment to find downstream impacts. "This commitment supports your promotion goal."
5. **Pattern recognition** — after 10+ commitments, detect failure patterns. "You tend to drop commitments after social events."

### Out of Scope
- Notifications/push system (Stage 2)
- Calendar integration (Stage 2)
- Energy prediction (E6)

### Key Files
- `backend/services/accountability_service.py` (new)
- `backend/mentor_agent.py` — alert injection, consequence traversal
- `backend/routes/graph_routes.py` — commitment/habit dashboard endpoint

### Acceptance Criteria
1. "I'll do X by Friday" → commitment node created
2. Friday passes → proactive alert in next conversation
3. Habit tracked → streak visible in context
4. Overdue commitment → consequence chain surfaced
5. After 10+ commitments → patterns detected
6. All tests pass + streak, overdue, consequence tests

### Risk
- Promise detection in free text is fuzzy. Mitigation: only capture explicit "I will" patterns. User can dismiss.
- Small datasets produce meaningless patterns. Mitigation: require 10+ data points.

---

## E6: "Reading the Room"

> After this epoch, Athena reads between the lines. Short messages mean stress. It adapts. Relationships become dynamics, not contact cards.

**Dependencies:** E5 (accountability data enables behavioral pattern detection)

### In Scope

1. **State inference** — `services/state_service.py`. Message length, timing, idea density, cancellation rate, repetitive queries → energy/stress/mode assessment.
2. **State-aware mode adjustment** — feed user state into mode classifier. Stressed + Advisor → soften. Burned out → don't pile on.
3. **Relationship tracking** — person mention frequency, influence, growth patterns on person nodes.
4. **Fundamentals monitoring** — alert when a fundamental goes neglected 2+ weeks.

### Out of Scope
- Predictive scheduling (Stage 2)
- Voice (Stage 2)
- Calendar (Stage 2)
- Multi-user (Stage 4)

### Key Files
- `backend/services/state_service.py` (new)
- `backend/mentor_agent.py` — state injection, mode adjustment
- `backend/services/accountability_service.py` — fundamentals check

### Acceptance Criteria
1. 5 terse messages → stress detected, tone adjusts
2. 2am message → sleep schedule flagged
3. Person mentioned 5x → influence tracked on node
4. Fundamental neglected 2 weeks → surfaced in conversation
5. State inference produces correct states for test patterns
6. All tests pass + state inference, fundamentals monitoring tests

### Risk
- State inference from text is noisy. Mitigation: require 3+ signals before adjusting. Probabilistic, not definitive.
- Relationship tracking may feel invasive. Mitigation: frame as observation, not judgment.

---

## What's Parked (Stage 2+)

| Capability | Stage | Notes |
|-----------|-------|-------|
| Calendar integration | 2 | Read/write, time blocks |
| Notification/reminder system | 2 | Push-based nudges |
| Layered daily schedule | 2 | Non-negotiables + exceptions + strategic fill |
| Voice ingestion | 2 | Transcription → structured extraction |
| Full what-if simulation | 2 | Scenario modelling against full graph |
| Energy/rhythm prediction | 2 | Longitudinal pattern analysis |
| Purpose discovery engine | 2 | Emergent + directed + dialectic |
| Telegram restart | 2 | Needs container rebuild + security hardening |
| Multi-agent orchestration | 2 | Split monolithic mentor_agent Claude call into specialist agents (retrieval, conflict detection, synthesis) running in parallel. Enables cheaper models for search (haiku), focused context windows, lower latency. Only worth it when single-call approach hits quality or cost ceilings. |
| Multi-user | 4 | Family/household shared context |
