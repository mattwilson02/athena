# Epoch 3: The Guardian

> Athena pushes back when you contradict yourself. "I'll stay up till 3am" with a 6am training plan and a sleep value gets flagged before accepting. Strategic advisor, not cheerleader.

**Vision Stage:** Stage 1 (Foundation)
**Appetite:** 3 weeks
**Goal:** Conflict detection, personality enforcement, tradeoff awareness. Athena earns the right to challenge you.
**Status:** Not Started

---

## Press Release

After this epoch, Athena doesn't just record what you say — she holds it against you. Tell her you're staying out late when you have a 6am run and a stated value of discipline? She'll flag the conflict by name before responding. Propose a new project when you're already juggling three? She surfaces the full obligation picture and asks what gives. Her personality sharpens — no more filler, no more cheerleading, no more accepting contradictions without comment. She's not harsh for the sake of it. She's strategically honest because that's what actually helps.

---

## Problem Statement

Right now Athena accepts everything at face value. Say contradictory things across sessions and she won't notice — retrieval pulls relevant nodes but never opposing ones. The personality rules in SOUL.md say "sharp and direct" but Claude defaults to agreeableness. There's no mechanism to detect when a stated intention conflicts with existing goals, values, or commitments. And when someone takes on a new obligation, there's no awareness of what they're already carrying. The result is an assistant that feels smart about connections but intellectually passive — a librarian, not a guardian.

---

## In Scope

### 1. Conflict detection service
**Files:** `backend/services/conflict_service.py` (new), `backend/tests/test_conflict_service.py` (new)
**Spec:** `docs/specs/conflict_detection.md`

New service: `detect_conflicts(message, graph, vector_index, schema)` → list of conflicts.

When the user expresses an intention ("I'm going to", "planning to", "I want to"), search the graph for contradictions:
- **Value violations** — intention contradicts a stated value node
- **Goal contradictions** — intention undermines an active goal
- **Schedule conflicts** — new time commitment overlaps existing ones
- **Habit breaks** — intention breaks an active habit or routine

Each conflict: `{node_id, title, type, conflict_type, explanation, severity}`. Severity is `hard` (direct contradiction) or `soft` (tension worth noting).

Detection uses hybrid retrieval: graph traversal (Self-domain neighbors) + semantic search (find nodes whose content opposes the intention). Not AI-powered — deterministic rules + vector similarity. Fast enough to run on every message.

**What success looks like:** "I'm going to skip the gym this week" with an active `exercise` habit and `health` value → both flagged with specific node names.

### 2. Conflict-aware retrieval
**Files:** `backend/mentor_agent.py`
**Spec:** `docs/specs/conflict_retrieval.md`

Modify `get_context()` to run conflict detection alongside normal retrieval. When conflicts are found, inject a `POTENTIAL CONFLICTS:` section into context — separate from the relevant nodes section. Claude sees both supporting context AND opposing context.

Format:
```
POTENTIAL CONFLICTS:
- [hard] Value "Discipline" (value) — staying out late contradicts your stated commitment to discipline and your 6am training routine
- [soft] Goal "Marathon Training" (goal, status: active) — skipping gym disrupts your 4x/week training schedule
```

Conflicts are injected regardless of whether the conflicting nodes would have been retrieved normally. This ensures the AI always sees contradictions, even for nodes that wouldn't rank highly in standard retrieval.

**What success looks like:** Claude references specific conflicts by node name in its response without being explicitly asked.

### 3. Tradeoff awareness
**Files:** `backend/mentor_agent.py`, `backend/services/conflict_service.py`
**Spec:** `docs/specs/tradeoff_awareness.md`

When the user proposes a new commitment (create a goal, project, habit, or event), inject the full active obligations picture:

```
ACTIVE OBLIGATIONS:
- 3 active goals: Marathon Training (high), Get Promoted (high), Learn Piano (medium)
- 2 active projects: Kitchen Renovation (in_progress), Side Project (planning)
- 4 active habits: Gym 4x/week, Reading 30min/day, Meditation, Journaling
- 1 upcoming event: Italy Trip (2026-04-15)
```

This gives Claude the information to ask "where does this fit?" instead of blindly accepting. The AI is instructed to surface the tradeoff — not to block, but to make the cost visible.

**What success looks like:** "I want to start learning guitar" → Athena responds with "You've got 3 active goals and 4 habits already. What gives to make room for this?"

### 4. Personality enforcement
**Files:** `SOUL.md`, `backend/mentor_agent.py`
**Spec:** `docs/specs/personality_enforcement.md`

SOUL.md gets a `## Conflict Protocol` section with explicit escalation rules. mentor_agent.py gets post-processing checks on Claude's output.

**Conflict Protocol (injected into system prompt):**
1. **Flag** — name the conflicting node(s) before responding to the user's request
2. **Explain** — one sentence on why this is a conflict
3. **Challenge** — ask the user to reconcile, don't just accept
4. **Accept** — if the user insists after being challenged, accept and record

**Anti-cheerleading rules (post-processing):**
- Detect filler phrases in Claude's output: "Great question!", "I'd be happy to", "That's a great idea!", "Absolutely!"
- Strip them or flag for prompt reinforcement
- Track cheerleading rate per session as a quality metric

**What success looks like:** User says something that contradicts their values → Athena names the conflict, explains it, and pushes back before accepting. No filler phrases in the response.

### 5. `contradicts` edge type
**Files:** `vault/_meta/schema.md`, `backend/mentor_agent.py`

Add `contradicts` to the edge type table in schema.md. When conflict detection finds a genuine contradiction, the AI is instructed to propose a `contradicts` link between the new node and the conflicting node. This makes contradictions visible in the graph — not just detected in conversation.

Format spec updated: `"If you detect a contradiction between two nodes, propose a contradicts edge."`

**What success looks like:** Graph shows red `contradicts` edges between genuinely opposing nodes. User can see their contradictions visually.

### 6. Dismissal awareness
**Files:** `backend/mentor_agent.py`, `backend/services/chat_service.py`

Bug fix: the AI sees its own `<graph_updates>` in conversation history but has no visibility into which proposals were accepted vs dismissed. It assumes everything it proposed exists, leading to phantom updates — e.g. proposing an update to a node the user dismissed creating.

Fix: inject dismissed node IDs into the system prompt context so the AI knows what was rejected. When building messages for Claude, check session's `dismissed_updates` list and append a note:

```
DISMISSED PROPOSALS (these nodes do NOT exist — the user rejected them):
- project-guitar-practice (dismissed)
- habit-daily-scales (dismissed)
Do not reference or update these nodes unless the user explicitly brings them up again.
```

This is a prerequisite for conflict detection — can't detect conflicts against phantom nodes.

**What success looks like:** Dismiss a node creation → next message, AI doesn't try to update or reference it.

---

## Out of Scope

- Adaptive mode selection (Mirror/Advisor/Guardian/Dialectic) — E4
- Memory permanence scoring — E4
- Challenge ladder for identity changes — E4
- Commitment tracking / streaks — E5
- State of mind inference — E6
- Proactive messaging / notifications — E4+

---

## Build Order

Ship incrementally:

1. **Dismissal awareness** — fix the phantom node bug first, foundation for everything else
2. **Conflict detection service** — core logic, fully testable in isolation
3. **Conflict-aware retrieval** — integrate into get_context(), Claude starts seeing conflicts
4. **Tradeoff awareness** — obligation surfacing, builds on conflict service
5. **Personality enforcement** — SOUL.md update + post-processing, requires conflicts to enforce against
6. **`contradicts` edge type** — schema + FORMAT_SPEC update, depends on conflict detection working

---

## Acceptance Criteria

1. Dismiss a node creation → AI does not reference or update the phantom node
2. State an intention that contradicts a goal → conflict flagged by node name
3. State an intention that violates a value → value node referenced with explanation
4. Propose a new commitment with 3+ active goals → active obligations surfaced
5. Claude response references conflicts without being asked
6. No cheerleading phrases in conflict responses
7. `contradicts` edges proposed when genuine contradictions detected
8. `detect_conflicts()` returns correct results for programmatic test cases
9. All existing tests pass + new conflict, tradeoff, personality, dismissal tests

---

## Risk

- **Over-flagging makes Athena annoying** — if everything is a "conflict", users stop reading them. Mitigation: only flag Self-domain nodes + active commitments. Severity levels (hard/soft) let the AI calibrate tone. Tune thresholds during validation.
- **Quality depends on graph completeness** — can't detect conflicts with goals that don't exist in the graph. Mitigation: E2 bootstrap ensures core values/goals exist. Conflict detection degrades gracefully (fewer conflicts, not wrong conflicts).
- **Anti-cheerleading is blunt** — string matching for filler phrases may catch legitimate usage. Mitigation: check in context (don't strip "great" from "that's a great risk to flag"). Start with logging, not stripping.
- **Contradiction edges may clutter the graph** — too many soft contradictions create noise. Mitigation: only propose `contradicts` for hard conflicts. Soft tensions are conversational, not structural.
