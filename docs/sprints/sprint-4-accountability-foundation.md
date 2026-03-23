# Sprint 4: Accountability Foundation

> **Epoch:** E5 — Accountability
> **Branch:** `sprint/4`
> **Goal:** Athena tracks commitments, calculates habit streaks, and surfaces overdue obligations proactively. Missed deadlines come with consequence chains.

---

## Overview

E4 gave Athena permanence scoring, adaptive modes, and the challenge ladder — she now weighs identity over tactics and makes you earn changes to who you are. But she's still reactive. She waits for you to bring things up. If you promised to do something by Friday and it's now Tuesday, she doesn't mention it. If your gym streak broke two weeks ago, she doesn't notice.

This sprint adds the accountability layer:

1. **Accountability service** — a new `accountability_service.py` that computes habit streaks from daily nodes, finds overdue commitments, and traces consequence chains through the graph.
2. **Commitment detection** — the FORMAT_SPEC teaches Claude to tag graph updates with commitment metadata (`deadline`, `made_on`, `commitment_context`) when the user makes an explicit promise.
3. **Proactive alerts** — a `PROACTIVE ALERTS:` header injected into the system prompt context before Claude sees the message, surfacing overdue commitments and broken habit streaks so Athena raises them naturally.
4. **Consequence surfacing** — when a commitment is overdue, traverse the graph to find downstream nodes it supports/blocks, so Athena can say "this commitment supports your promotion goal" rather than just "this is overdue."

**Not in this sprint:** Pattern recognition across 10+ commitments (requires more data accumulation), notification/push system (Stage 2), calendar integration (Stage 2).

---

## What Exists

| Component | State | Relevant to This Sprint |
|-----------|-------|------------------------|
| `mentor_agent.py` `get_context()` | Hybrid retrieval with temporal header, overdue sweep, tiered context assembly | Proactive alerts injected alongside temporal header |
| `_get_overdue_nodes()` in `mentor_agent.py` | Finds active nodes past their due/deadline/scheduled_for | Reused by accountability service for commitment-specific overdue detection |
| `mentor_agent.py` `_build_conflict_note()` | Formats conflicts + obligations for system prompt injection | Proactive alerts follow the same injection pattern |
| `chat_service.py` flow | detect_conflicts → classify_mode → chat_stream → post-process | Accountability check inserted before mentor call |
| `conflict_service.py` obligation surfacing | Surfaces active goals/projects/habits when new commitment detected | Complementary — obligations are forward-looking, accountability is backward-looking |
| `vault_service.py` `cascade_check()` | 1-hop graph traversal + semantic cascade | Consequence surfacing reuses graph traversal patterns |
| `schema.md` habit type | `frequency: daily\|weekly\|monthly`, `status: active\|lapsed\|building`, `streak: 0` | Streak field exists but is never computed |
| `schema.md` daily type | `date`, `mood`, `energy` | Daily nodes linked to habits via `## Related` edges are the source for streak calculation |
| `schema.md` goal/task/project types | `deadline`/`due`, `status`, `priority` | Commitment metadata enriches these existing fields |
| `_GRAPH_INSTRUCTIONS` in `mentor_agent.py` | FORMAT_SPEC for Claude's graph updates | Commitment detection rules added here |
| Vault habit files (e.g. `strength-training.md`) | 10+ active habits with frequency, edges to goals/dailies | Real data for streak calculation |
| Vault daily files (e.g. `friday-mar-20-gym-planning.md`) | 10+ daily nodes with dates, edges to habits | Real data — daily nodes mention habits via `## Related` edges |

---

## Architectural Decisions

### 1. Accountability service is a standalone module, not part of mentor_agent

Unlike mode classification (which was ~50 lines of keyword matching), accountability involves graph traversal, date arithmetic, and streak calculation — enough complexity to warrant its own file. It follows the same pattern as `conflict_service.py`: a pure function that takes graph + vector_index and returns structured results.

**Why:** `mentor_agent.py` is already 1,357 lines. Adding 200+ lines of streak calculation and consequence traversal would make it unwieldy. The conflict service set the precedent for extracting domain logic into focused modules.

### 2. Streaks are computed on-demand, not persisted

`calculate_streaks()` traverses the graph each time rather than maintaining a running counter. It reads daily nodes linked to each habit, checks their dates against the habit's frequency, and returns the current streak count.

**Why:** The graph is the source of truth. A persisted counter can desync (missed writes, manual edits, imports). On-demand calculation is always accurate. The graph is small enough (<500 nodes) that traversal is instant. If performance becomes an issue later, caching can be added without changing the interface.

### 3. Commitment metadata uses existing frontmatter fields, not a new node type

The roadmap says "promise → commitment node." But adding a new node type is heavy (schema change, parser update, folder creation, FORMAT_SPEC rules). Instead, commitments are **regular goal/task/project nodes** enriched with two optional frontmatter fields: `committed_on` (ISO date when the promise was made) and `commitment_context` (what the user said). The `deadline` field already exists on goals/tasks/projects.

**Why:** A commitment is a goal or task with a deadline and explicit promise context. Creating a separate `commitment` type would fragment the graph — "finish the PR by Friday" is a task whether or not you promised it. The distinction is metadata (when did you commit, in what context), not type. This also avoids schema.md changes in this sprint.

### 4. Proactive alerts are injected into the system prompt, not sent as separate messages

Alerts appear as a `PROACTIVE ALERTS:` section in the system prompt, after the temporal facts header and before the CONTEXT block. Claude reads them and naturally weaves them into its response when relevant.

**Why:** Same principle as conflict injection (E3) and mode injection (E4). The AI decides how to surface the information — sometimes it's the opening line, sometimes a gentle aside, sometimes it's held for a better moment. Forcing separate messages or notifications is Stage 2 territory.

### 5. Consequence chain traversal is bounded at 2 hops

When an overdue commitment is found, traverse up to 2 hops through `supported_by`, `part_of`, `blocked_by`, and `relates_to` edges to find impacted nodes. Only include nodes in the alert that are active (not completed/cancelled).

**Why:** Unbounded traversal in a connected graph produces noise. 2 hops captures direct impacts ("this task supports your promotion goal") without spiraling into tenuous connections. Same depth limit as `get_context()` Tier 3.

---

## Tasks

### Task 1: Accountability Service — Streak Calculation

**Objective:** Create `accountability_service.py` with a function that computes current habit streaks by analyzing daily nodes linked to each habit.

**Files to create:**
- `backend/services/accountability_service.py`

**Files to modify:** (none)

**Requirements:**

```
calculate_streaks(graph) -> list[dict]
```

Returns a list of habit streak reports:
```python
{
    "habit_id": "strength-training",
    "habit_title": "Strength Training 3x/week",
    "frequency": "3x/week",       # from frontmatter
    "status": "active",
    "current_streak": 3,          # consecutive periods met
    "last_completed": "2026-03-20",  # date of most recent linked daily
    "days_since_last": 1,         # days since last_completed
    "streak_status": "on_track" | "at_risk" | "broken"
}
```

**Streak calculation logic:**

1. Find all nodes where `type == "habit"` and `status` is active/building
2. For each habit, find linked daily nodes via graph edges (any edge type — habits link to dailies via `relates_to` and `## Related` sections)
3. Sort linked dailies by `date` field, most recent first
4. Determine streak based on frequency:
   - `daily` — count consecutive days with a linked daily (gap of >1 day breaks the streak)
   - `weekly` — count consecutive weeks with at least one linked daily (gap of >7 days from last daily breaks it)
   - `monthly` — count consecutive months with at least one linked daily
   - Non-standard frequencies (e.g. `3x/week`) — treat as `weekly` (at least one linked daily per 7-day window)
5. Determine `streak_status`:
   - `on_track` — last completed within the expected frequency window (daily: today or yesterday; weekly: within 7 days; monthly: within 30 days)
   - `at_risk` — last completed is approaching the window boundary (daily: 1 day ago; weekly: 5-7 days ago; monthly: 25-30 days ago)
   - `broken` — last completed exceeds the frequency window

**Edge cases:**
- Habit with no linked dailies → `current_streak: 0`, `streak_status: "broken"`, `last_completed: None`
- Habit with `status: lapsed` → skip (only calculate for active/building)
- Daily node with no `date` field → skip that daily
- Multiple dailies on the same date → count as one occurrence

**Pattern to follow:** Same pure-function-over-graph pattern as `detect_conflicts()` in `conflict_service.py`. Takes `graph` (VaultGraph), returns structured dicts. No side effects.

**Acceptance criteria:**
- Active habits with recent linked dailies show correct streak counts
- `daily` frequency habits break streak after 1 missed day
- `weekly` frequency habits break streak after 7+ days gap
- Habits with no linked dailies return streak 0
- Lapsed habits are excluded
- Returns results sorted by urgency (broken first, then at_risk, then on_track)

**Test cases** (`backend/tests/test_accountability_service.py`, new file):
- `test_daily_streak_consecutive` — 3 consecutive daily nodes → streak 3
- `test_daily_streak_broken_by_gap` — days 1,2,4 (gap at 3) → streak 1 (only day 4)
- `test_weekly_streak` — dailies at day 0, day 8, day 15 → streak 3 (each within a 7-day window)
- `test_weekly_streak_broken` — last daily 10 days ago → streak_status "broken"
- `test_no_linked_dailies` — habit with zero dailies → streak 0, status broken
- `test_lapsed_habit_excluded` — lapsed habits not in results
- `test_nonstandard_frequency_treated_as_weekly` — "3x/week" treated like weekly
- `test_multiple_dailies_same_date` — two dailies on same date → count as one
- `test_results_sorted_by_urgency` — broken habits appear before on_track

---

### Task 2: Accountability Service — Overdue Commitments and Consequence Chains

**Objective:** Add functions to find overdue commitments and trace their downstream consequences through the graph.

**Files to modify:**
- `backend/services/accountability_service.py` — add `find_overdue_commitments()` and `trace_consequences()`

**Requirements:**

```
find_overdue_commitments(graph, today: date) -> list[dict]
```

Returns overdue nodes enriched with commitment context and consequences:
```python
{
    "node_id": "finish-pr-review",
    "title": "Finish PR Review",
    "type": "task",
    "priority": "high",
    "due": "2026-03-18",
    "days_overdue": 3,
    "committed_on": "2026-03-15",      # from frontmatter, if present
    "commitment_context": "Told Sarah I'd review by Tuesday",  # from frontmatter, if present
    "consequences": [                   # downstream impact chain
        {"node_id": "promotion-goal", "title": "Get Promoted", "type": "goal", "relationship": "supported_by"},
        {"node_id": "project-alpha", "title": "Project Alpha", "type": "project", "relationship": "part_of"}
    ]
}
```

**Detection logic:**

1. Find all nodes with `due` or `deadline` field where:
   - The date is in the past (before `today`)
   - The `status` is active/pending/todo/in_progress/planning/blocked (not completed/done/cancelled/abandoned/superseded)
2. Calculate `days_overdue = (today - due_date).days`
3. Read `committed_on` and `commitment_context` from frontmatter (optional fields — absent for non-commitment nodes)
4. Call `trace_consequences(graph, node_id)` for each overdue node

```
trace_consequences(graph, node_id: str, max_hops: int = 2) -> list[dict]
```

Traverse outward from the overdue node through these edge types: `supported_by`, `part_of`, `blocked_by`, `relates_to`. At each hop, check if the connected node is active. Return active nodes with their relationship to the overdue node.

**Traversal rules:**
- Follow edges in both directions (the overdue task may `part_of` a project, or a goal may be `supported_by` the overdue task)
- Only include active nodes (status not in completed/done/cancelled/abandoned/superseded)
- Deduplicate by node_id
- Cap at `max_hops` (default 2)
- Sort consequences by permanence level (identity first, then strategic, then tactical) — use `_get_permanence()` from `mentor_agent.py`

**Edge cases:**
- Node with no due/deadline → skip
- Due date that can't be parsed → skip
- Node with no outward edges → empty consequences list
- Circular edges → track visited set to avoid infinite loops

**Pattern to follow:** `_get_overdue_nodes()` already exists in `mentor_agent.py` for basic overdue detection. This function enriches the results with commitment context and consequence chains. `trace_consequences()` follows the same graph traversal pattern as `cascade_check()` in `vault_service.py`.

**Acceptance criteria:**
- Overdue tasks/goals/projects with past deadlines are found
- Completed/cancelled nodes are not flagged as overdue
- Consequence chain includes supporting goals and parent projects
- Consequences are sorted by permanence (identity goals outrank tactical tasks)
- `committed_on` and `commitment_context` are included when present in frontmatter
- Traversal doesn't exceed 2 hops

**Test cases** (add to `backend/tests/test_accountability_service.py`):
- `test_overdue_task_found` — task with due yesterday, status todo → returned
- `test_completed_task_not_overdue` — task with due yesterday, status done → not returned
- `test_days_overdue_calculated` — due 3 days ago → days_overdue = 3
- `test_consequence_chain_1_hop` — overdue task part_of project → project in consequences
- `test_consequence_chain_2_hop` — overdue task → project → goal → goal in consequences
- `test_consequence_chain_skips_completed` — completed goal not in consequences
- `test_consequence_chain_max_hops` — 3-hop chain with max_hops=2 → only 2 hops returned
- `test_no_due_date_skipped` — node without due/deadline field not returned
- `test_commitment_metadata_included` — node with committed_on field → included in result
- `test_consequences_sorted_by_permanence` — goal (strategic) appears before task (tactical)

---

### Task 3: Proactive Alert Injection

**Objective:** Inject a `PROACTIVE ALERTS:` section into the system prompt so Athena naturally surfaces overdue commitments and broken streaks.

**Files to modify:**
- `backend/mentor_agent.py` — add `_build_proactive_alerts()` method, call it in `chat_stream()` and `chat()`
- `backend/services/chat_service.py` — call accountability service before mentor, pass alerts downstream

**Requirements:**

**In `chat_service.py`:**

After conflict detection and mode classification, call the accountability service:

```python
from services.accountability_service import calculate_streaks, find_overdue_commitments

streaks = calculate_streaks(self.graph)
overdue = find_overdue_commitments(self.graph, date.today())
```

Package results into an `alerts` dict and pass to `mentor.chat_stream()`:

```python
alerts = {
    "broken_streaks": [s for s in streaks if s["streak_status"] == "broken"],
    "at_risk_streaks": [s for s in streaks if s["streak_status"] == "at_risk"],
    "overdue_commitments": overdue,
}
```

Pass `alerts=alerts` to `chat_stream()` and `chat()` alongside existing `conflicts`, `mode`, `challenges`.

**In `mentor_agent.py`:**

Add `_build_proactive_alerts(alerts: dict) -> str` that formats alerts for the system prompt:

```
PROACTIVE ALERTS — raise these naturally when relevant. Don't lead with all of them at once.
Pick the most relevant 1-2 based on what the user is talking about.

BROKEN STREAKS:
- "Strength Training 3x/week" — last completed 12 days ago, streak broken. Was at 8 before breaking.
- "Evening Reading" — last completed 5 days ago, streak broken.

AT RISK:
- "Morning Deep Work" — last completed 6 days ago, at risk of breaking (weekly frequency).

OVERDUE COMMITMENTS:
- "Finish PR Review" (task, high priority) — 3 days overdue (due Mar 18). Committed Mar 15: "Told Sarah I'd review by Tuesday."
  → Consequences: supports "Get Promoted" (goal), part of "Project Alpha" (project)
- "Submit Tax Return" (task, high priority) — 1 day overdue (due Mar 20).
```

**System prompt placement:**

Call `_build_proactive_alerts()` in `chat_stream()` and `chat()` and append it to the system prompt **after** identity + instructions + context and **before** conflict/mode/challenge notes. This positioning ensures alerts are visible but don't override conflict/mode instructions.

The alert text includes behavioral guidance: "raise these naturally when relevant. Don't lead with all of them at once. Pick the most relevant 1-2 based on what the user is talking about." This prevents Athena from dumping all alerts every message.

**Suppression logic:**

If there are no broken streaks, no at-risk streaks, and no overdue commitments, `_build_proactive_alerts()` returns an empty string (no section injected). Don't inject noise.

**In `chat()` and `chat_stream()`:**

Add `alerts: dict | None = None` parameter with same pattern as `conflicts` and `mode`. Default to empty dict.

**Edge cases:**
- If accountability service raises an exception, catch it, log it, and proceed with empty alerts (same defensive pattern as conflict detection)
- If there are >10 alerts total, cap at 5 broken streaks + 3 overdue commitments (most urgent first) to avoid prompt bloat

**Pattern to follow:** Same injection pattern as `_build_conflict_note()` and `_build_mode_note()`. Same defensive error handling as `_detect_conflicts()` in `chat_service.py`.

**Acceptance criteria:**
- Broken streaks appear in the system prompt as PROACTIVE ALERTS
- Overdue commitments appear with consequence chains
- Alerts are capped to avoid prompt bloat (max 5 streaks + 3 overdue)
- Empty alerts produce no system prompt section
- Accountability service errors don't break chat flow
- Alerts include behavioral guidance ("raise naturally when relevant")

**Test cases** (add to `backend/tests/test_mentor_agent.py`):
- `test_proactive_alerts_broken_streaks` — broken streak appears in formatted output
- `test_proactive_alerts_overdue_with_consequences` — overdue commitment with consequences formatted correctly
- `test_proactive_alerts_empty_no_output` — no alerts → empty string
- `test_proactive_alerts_capped` — >10 alerts → only most urgent included
- `test_system_prompt_includes_alerts` — system prompt contains "PROACTIVE ALERTS" when alerts exist
- `test_alerts_not_in_prompt_when_empty` — system prompt has no alerts section when all streaks are on_track

**Test cases** (add to `backend/tests/test_routes.py` or `backend/tests/test_chat_service.py`):
- `test_accountability_error_doesnt_break_chat` — mock accountability to raise, chat still works with empty alerts

---

### Task 4: Commitment Detection in FORMAT_SPEC

**Objective:** Teach Claude to recognize explicit promises and tag the resulting graph updates with commitment metadata.

**Files to modify:**
- `backend/mentor_agent.py` — add commitment detection rules to `_GRAPH_INSTRUCTIONS`
- `backend/services/chat_service.py` — add `_enrich_commitment_metadata()` post-processing step

**Requirements:**

**FORMAT_SPEC addition to `_GRAPH_INSTRUCTIONS`:**

Add a new section `── 8. COMMITMENTS ──` after the existing `── 7. FORMAT ──` section:

```
── 8. COMMITMENTS ──
When the user makes an explicit promise ("I'll do X by Friday", "I commit to X", "I promise to Y",
"I'll have it done by Z"), tag the graph update with commitment metadata:

  "frontmatter": {
    "committed_on": "2026-03-21",        // today's date
    "commitment_context": "Promised Sarah I'd review by Tuesday"  // brief context of the promise
  }

Only tag explicit promises with clear deadlines. "I should probably do X sometime" is NOT a commitment.
"I want to start running" is NOT a commitment. "I'll run 3 times this week" IS a commitment.

If the node already exists, use action: "update" with frontmatter changes to add committed_on and commitment_context.
If it's a new task/goal, include committed_on and commitment_context in the create frontmatter.
```

**Post-processing in `chat_service.py`:**

Add `_enrich_commitment_metadata(updates: list[dict]) -> list[dict]` that validates commitment fields on graph updates:

1. For each update with `committed_on` in frontmatter:
   - Validate `committed_on` is a valid ISO date string
   - If invalid, remove it (don't let malformed dates through)
   - Ensure `commitment_context` exists (if missing, set to empty string)
2. For each update with a `deadline`/`due` field and `committed_on`:
   - No additional processing needed — the pairing of committed_on + deadline is what makes it a tracked commitment

Call this step after `_annotate_permanence_warnings()` in both `send_message()` and `stream_message()`.

**Pattern to follow:** Same FORMAT_SPEC extension pattern used for cascade rules (`── 5. CASCADE ──`) and edge rules (`── 6. EDGES ──`). Same post-processing enrichment pattern as `_dedup_check()` and `_annotate_permanence_warnings()`.

**Acceptance criteria:**
- "I'll finish the report by Friday" produces a graph update with `committed_on` and `commitment_context`
- Vague intentions ("I should try running") do NOT get commitment metadata
- Invalid `committed_on` dates are stripped in post-processing
- Existing graph update format is unchanged for non-commitment updates
- FORMAT_SPEC section is clear enough that Claude tags commitments correctly

**Test cases** (add to `backend/tests/test_routes.py` or new `backend/tests/test_commitment_detection.py`):
- `test_enrich_commitment_valid_date` — update with valid committed_on passes through
- `test_enrich_commitment_invalid_date_stripped` — update with malformed committed_on has it removed
- `test_enrich_commitment_context_default` — update with committed_on but no context gets empty string
- `test_enrich_no_commitment_fields_unchanged` — update without commitment fields unchanged
- `test_format_spec_contains_commitment_section` — _GRAPH_INSTRUCTIONS contains "COMMITMENTS"

---

### Task 5: Accountability Dashboard Endpoint

**Objective:** Add a REST endpoint that returns the current accountability state — streaks, overdue commitments, and consequence chains — for frontend consumption.

**Files to modify:**
- `backend/routes/graph_routes.py` — add `/api/accountability` endpoint

**Requirements:**

```
GET /api/accountability
```

Response:
```json
{
  "streaks": [
    {
      "habit_id": "strength-training",
      "habit_title": "Strength Training 3x/week",
      "frequency": "3x/week",
      "status": "active",
      "current_streak": 3,
      "last_completed": "2026-03-20",
      "days_since_last": 1,
      "streak_status": "on_track"
    }
  ],
  "overdue": [
    {
      "node_id": "finish-pr-review",
      "title": "Finish PR Review",
      "type": "task",
      "priority": "high",
      "due": "2026-03-18",
      "days_overdue": 3,
      "committed_on": "2026-03-15",
      "commitment_context": "Told Sarah I'd review by Tuesday",
      "consequences": [
        {"node_id": "promotion-goal", "title": "Get Promoted", "type": "goal", "relationship": "supported_by"}
      ]
    }
  ],
  "summary": {
    "total_habits": 10,
    "on_track": 7,
    "at_risk": 2,
    "broken": 1,
    "overdue_count": 2,
    "oldest_overdue_days": 5
  }
}
```

The endpoint calls `calculate_streaks()` and `find_overdue_commitments()` from the accountability service. It also computes a `summary` object for quick consumption.

**Error handling:** If the accountability service raises an exception, return a 500 with `{"error": "Failed to compute accountability state"}`. Log the exception.

**Pattern to follow:** Same endpoint pattern as `/api/graph/stats` in `graph_routes.py`. Pure read operation, no side effects. The graph and today's date are the only inputs.

**Acceptance criteria:**
- `GET /api/accountability` returns streaks, overdue, and summary
- Response includes all active habits with streak data
- Response includes all overdue commitments with consequence chains
- Summary counts match the detail arrays
- Endpoint handles errors gracefully with 500 response

**Test cases** (add to `backend/tests/test_routes.py`):
- `test_accountability_endpoint_returns_streaks` — mock graph with habits → streaks in response
- `test_accountability_endpoint_returns_overdue` — mock graph with overdue task → overdue in response
- `test_accountability_endpoint_summary_counts` — summary.on_track + at_risk + broken = total_habits
- `test_accountability_endpoint_empty_graph` — empty graph → empty arrays, zero counts
- `test_accountability_endpoint_error_handling` — accountability service raises → 500 response

---

## API Response Contracts

### New endpoint: `GET /api/accountability`

```json
{
  "streaks": [
    {
      "habit_id": "string",
      "habit_title": "string",
      "frequency": "string",
      "status": "string",
      "current_streak": "number",
      "last_completed": "string | null",
      "days_since_last": "number | null",
      "streak_status": "on_track | at_risk | broken"
    }
  ],
  "overdue": [
    {
      "node_id": "string",
      "title": "string",
      "type": "string",
      "priority": "string",
      "due": "string",
      "days_overdue": "number",
      "committed_on": "string | null",
      "commitment_context": "string | null",
      "consequences": [
        {
          "node_id": "string",
          "title": "string",
          "type": "string",
          "relationship": "string"
        }
      ]
    }
  ],
  "summary": {
    "total_habits": "number",
    "on_track": "number",
    "at_risk": "number",
    "broken": "number",
    "overdue_count": "number",
    "oldest_overdue_days": "number"
  }
}
```

### Chat API response: unchanged

The chat response shape is unchanged. The only internal difference is that the system prompt now includes a `PROACTIVE ALERTS:` section when there are broken streaks or overdue commitments. The `graph_updates` array may now include `committed_on` and `commitment_context` in the `frontmatter` of commitment-tagged updates.

---

## Implementation Order

```
Task 1: Accountability Service — Streak Calculation
  └→ Task 2: Accountability Service — Overdue Commitments + Consequences (depends on Task 1 for shared module)
      └→ Task 3: Proactive Alert Injection (depends on Tasks 1 + 2 for data)
  Task 4: Commitment Detection in FORMAT_SPEC (independent — FORMAT_SPEC changes + post-processing)
  └→ Task 5: Accountability Dashboard Endpoint (depends on Tasks 1 + 2 for service functions)
```

Tasks 1 and 4 can be built in parallel. Task 2 depends on Task 1 (same file). Task 3 depends on Tasks 1 + 2. Task 5 depends on Tasks 1 + 2.

Recommended sequence: **1 → 2 → 4 → 3 → 5** (build the service, then the detection, then wire them together, then expose via API).

---

## New Files

| File | Purpose |
|------|---------|
| `backend/services/accountability_service.py` | Streak calculation, overdue commitments, consequence chains |
| `backend/tests/test_accountability_service.py` | Accountability service unit tests |

**Total new files: 2** (well within the 15-file limit)

---

## Definition of Done

1. All existing tests pass (`python -m pytest` from `backend/`)
2. `npm run build` succeeds in `frontend/`
3. New test files pass with all cases green
4. Active habits have streak counts calculated from linked daily nodes
5. Overdue tasks/goals/projects are detected with correct days_overdue
6. Consequence chains trace from overdue commitments to supporting goals (2 hops max)
7. System prompt includes `PROACTIVE ALERTS:` section when streaks are broken or commitments overdue
8. Alerts are surfaced naturally by Claude (not dumped as a list every message)
9. "I'll do X by Friday" produces graph updates with `committed_on` and `commitment_context`
10. `GET /api/accountability` returns streaks, overdue, and summary
11. Accountability service errors don't break the chat flow
12. No new dependencies added to `requirements.txt`
13. No regression in conflict detection, mode classification, permanence scoring, or challenge ladder
