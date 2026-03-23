# Sprint 8: Daily Planning Engine

> **Epoch:** Stage 2 Preparation — Scheduling Infrastructure
> **Branch:** `sprint/8`
> **Goal:** Athena knows what your day looks like before you ask. Plans are captured, tracked, and compared against reality — so Athena learns the gap between what you say and what you do.

---

## Overview

Stage 1 is complete. Athena has conflict detection, permanence scoring, accountability tracking, state inference, fundamentals monitoring, relationship intelligence, and query-aware retrieval. She knows what you've committed to, what's overdue, and how stressed you are.

But she can't answer the most basic daily question: "What should I focus on today?" There's no structured view of the day. No aggregation of events, due tasks, habit targets, and overdue commitments into a single actionable briefing. And critically — no way to capture what the user *plans* to do and compare it to what they *actually* did.

The product spec calls this out explicitly:

> Forward-looking (plans) and backward-looking (reflections) serve different functions. Plans create expectations. Reflections validate or invalidate them. The gap between "I planned to do X" and "I actually did Y" is where Athena learns the most about you.

> This becomes predictive: "You tend to overcommit on Mondays. Last 6 Mondays you planned 8 things and completed 4. Want me to trim this to 5?"

This sprint adds the daily planning engine:

1. **Daily briefing service** — a new `planning_service.py` that aggregates today's events, due tasks, habit targets, overdue commitments, and relevant alerts into a structured briefing. Pure read — assembles existing data into a single view.
2. **Plan capture** — FORMAT_SPEC teaches Claude to record the user's stated plan for the day as structured data on a `daily` node. When the user says "today I'm going to X, Y, Z", those intentions become a trackable plan.
3. **Plan-reality comparison** — at end-of-day or next morning, compares the captured plan against actual outcomes (completed tasks, linked daily activities). Surfaces the gap and detects patterns over time.
4. **Plan-aware context injection** — today's briefing and active plan are injected into the system prompt so Athena can reference what's planned without the user re-explaining.
5. **Briefing API and frontend** — `GET /api/briefing` endpoint and a `DailyPlanView.svelte` component that shows the structured daily view.

**Not in this sprint:** Calendar integration (external APIs), time-blocking UI, notification/push system, multi-day planning, energy-based scheduling, weekly review.

---

## What Exists

| Component | State | Relevant to This Sprint |
|-----------|-------|------------------------|
| `accountability_service.py` `calculate_streaks()` | Returns habit streak status (on_track/at_risk/broken) | Feeds into briefing — which habits are due today |
| `accountability_service.py` `find_overdue_commitments()` | Overdue tasks/goals/projects with consequence chains | Overdue items appear in briefing |
| `accountability_service.py` `check_fundamentals()` | Monitors 6 fundamentals for neglect | Fundamentals status in briefing summary |
| `_build_proactive_alerts()` in `mentor_agent.py` | Formats streaks, overdue, fundamentals, relationships for system prompt | Plan-aware injection follows same pattern, sits alongside alerts |
| `_GRAPH_INSTRUCTIONS` in `mentor_agent.py` | FORMAT_SPEC sections 1-8 (when/what/action/mistakes/cascade/edges/format/commitments) | Plan capture rules added as section 9 |
| `chat_service.py` flow | state → alerts → conflicts → mode → chat_stream | Briefing computed alongside alerts, passed to mentor |
| `schema.md` `daily` type | `date`, `mood`, `energy` frontmatter; Domain: Life; Folder: Life/Daily | Plan data added to daily nodes via frontmatter |
| `schema.md` `event` type | `date`, `location`, `people`, `status: upcoming\|attended\|cancelled` | Events with today's date appear in briefing |
| `schema.md` `task` type | `status`, `priority`, `due`, `project` | Tasks due today appear in briefing |
| `schema.md` `habit` type | `frequency`, `status`, `streak` | Active habits with frequency inform "what's due today" |
| `vault_graph.py` `get_nodes_by_type()` | Filters graph nodes by type | Used by briefing to find today's events, due tasks |
| `vault_service.py` `update()` | Patches frontmatter, content, tags, edges | Used to write plan data to daily nodes |
| `graph_routes.py` `GET /api/accountability` | Returns streaks, overdue, fundamentals, relationships_summary | Pattern for briefing endpoint |
| `_node_context_compact()` in `mentor_agent.py` | Compact node format (~150-200 chars) | Briefing items use similar compact formatting |
| `_enrich_commitment_metadata()` in `chat_service.py` | Post-processing step for graph updates | Plan capture post-processing follows same pattern |
| `AccountabilityView.svelte` | Dashboard for streaks, overdue, fundamentals | Pattern for DailyPlanView |
| `api.js` `getAccountability()` | Fetch wrapper for accountability endpoint | Pattern for getBriefing() |

---

## Architectural Decisions

### 1. The briefing is computed on-demand, not cached or scheduled

`compile_briefing()` reads the graph, accountability data, and session history each time it's called. No cron job, no cached state. The briefing endpoint returns a fresh snapshot.

**Why:** Same principle as `calculate_streaks()` (AD#2 in Sprint 4) — the graph is the source of truth, and on-demand computation is always accurate. The graph is small enough (<500 nodes) that aggregation is instant. Caching adds staleness risk for negligible performance gain. When push-based briefings are needed (Stage 2 notifications), they'll call this same function on a trigger — the computation logic doesn't change.

### 2. Plans are stored as structured frontmatter on daily nodes, not a new node type

When the user states their plan for the day ("today I'm going to gym, review the PR, and read"), Claude creates or updates a `daily` node with a `planned` list in frontmatter. Each planned item is a string description, optionally linking to an existing node ID.

**Why:** The daily node is the natural container for "what was planned for this day" — it already captures what happened (mood, energy, linked activities via `## Related` edges). Adding plan data alongside keeps the full day picture in one place. A separate `plan` node type would fragment the data model and require cross-referencing. Schema changes are minimal — `planned` is a new optional list field on the `daily` type.

### 3. Plan-reality comparison uses graph edges and status changes, not free-text matching

A planned item is "completed" if: (a) it references a node ID that has been updated to a completed status during the day, OR (b) the daily node's `## Related` section links to the referenced node, OR (c) the item is explicitly marked completed in a subsequent graph update. Free-text matching ("did the user mention they went to the gym?") is not used.

**Why:** Structured comparison is reliable. Free-text matching across conversation history is fuzzy and expensive. The graph already captures completions (task status changes, daily→habit links). The plan stores node IDs where possible — comparing IDs is exact. Items without node IDs (ad-hoc plans like "grocery shopping") are tracked by explicit update only.

### 4. Plan-reality patterns are computed over the last 14 days, not persisted

`analyze_plan_patterns()` reads the last 14 daily nodes with plan data and computes completion rates, day-of-week patterns, and common drops. No persistent pattern store.

**Why:** 14 days of daily nodes is a tiny dataset to scan. Persisting patterns adds a stale data problem — patterns change as behavior changes. Computing fresh ensures accuracy. When the dataset grows large enough to warrant persistence (months of data), that's a future optimization. The product spec says "after 10+ commitments" for pattern recognition — 14 days gives us enough data for initial patterns.

### 5. The briefing is injected into the system prompt as a separate section, not merged into proactive alerts

Today's briefing appears as a `TODAY'S BRIEFING:` section in the system prompt, positioned between dismissed notes and state notes. It's distinct from proactive alerts — the briefing is the day's structure, alerts are exceptions that need attention.

**Why:** Mixing briefing items with alerts would blur the distinction between "what's planned" (informational) and "what's wrong" (action-needed). The briefing provides baseline context; alerts call out deviations. Claude needs both but should treat them differently — the briefing is reference material, alerts demand response.

---

## Tasks

### Task 1: Daily Briefing Service

**Objective:** Create `planning_service.py` with a function that compiles today's agenda from existing graph data into a structured briefing.

**Files to create:**
- `backend/services/planning_service.py`

**Requirements:**

```
compile_briefing(graph: VaultGraph, accountability_data: dict, today: date) -> dict
```

Takes the graph, pre-computed accountability data (from `calculate_streaks()` + `find_overdue_commitments()` + `check_fundamentals()`), and today's date. Returns:

```python
{
    "date": "2026-03-23",
    "day_of_week": "Monday",
    "events": [
        {
            "node_id": "dentist-appointment",
            "title": "Dentist Appointment",
            "time": "14:00",            # from date field if datetime, else None
            "location": "City Dental",
            "people": ["sarah"],
            "status": "upcoming",
        }
    ],
    "due_tasks": [
        {
            "node_id": "finish-pr-review",
            "title": "Finish PR Review",
            "priority": "high",
            "project": "project-alpha",  # parent project ID if any
            "days_overdue": 0,           # 0 = due today, >0 = overdue
        }
    ],
    "habit_targets": [
        {
            "habit_id": "strength-training",
            "title": "Strength Training",
            "frequency": "3x/week",
            "streak_status": "on_track",
            "current_streak": 8,
            "last_completed": "2026-03-21",
            "due_today": True,           # based on frequency and last completion
        }
    ],
    "active_plan": {                     # from today's daily node, if exists
        "daily_id": "monday-mar-23",
        "planned": [
            {"description": "Gym session", "linked_node": "strength-training", "completed": False},
            {"description": "Review PR for Sarah", "linked_node": "finish-pr-review", "completed": False},
            {"description": "Read 30 pages", "linked_node": "evening-reading", "completed": True},
        ],
        "completion_rate": 0.33,
    },
    "overdue_summary": {
        "count": 2,
        "top_items": [
            {"node_id": "tax-return", "title": "Submit Tax Return", "days_overdue": 3, "priority": "high"}
        ],
    },
    "fundamentals_status": {
        "neglected": ["movement"],       # fundamentals neglected >14 days
        "at_risk": [],                   # fundamentals approaching neglect
    },
    "yesterday_review": {               # plan-reality from yesterday, if available
        "planned_count": 5,
        "completed_count": 3,
        "completion_rate": 0.6,
        "missed": ["Evening Reading", "Meditation"],
    },
}
```

**Data sources and logic:**

1. **Events** — find all nodes where `type == "event"` and `date` matches today. Parse datetime if available for time ordering. Include `location` and `people` from frontmatter.

2. **Due tasks** — find all nodes where `type == "task"` and `due` is today or earlier AND `status` is not `done`/`cancelled`. Merge with `find_overdue_commitments()` results for consequence data. Sort by priority (high → medium → low), then days_overdue descending.

3. **Habit targets** — from `calculate_streaks()` results, determine which habits are "due today" based on frequency:
   - `daily` habits → always due
   - `weekly` habits → due if `days_since_last >= 5` (leaving 2-day buffer) OR if not completed this week
   - Non-standard frequencies (e.g., `3x/week`) → due if `days_since_last >= 2`
   - Sort: at_risk/broken streaks first, then by streak length descending

4. **Active plan** — find today's `daily` node (match by `date` field). Read `planned` frontmatter if present. Check completion status by looking up each `linked_node`'s current status and checking for edges from the daily node.

5. **Overdue summary** — condensed from accountability data: count + top 3 most urgent.

6. **Fundamentals status** — from `check_fundamentals()`: which fundamentals are neglected or approaching neglect.

7. **Yesterday review** — find yesterday's daily node, read its plan, compute completion rate. Only populated if yesterday's daily has a `planned` field.

**Edge cases:**
- No events today → `events: []`
- No daily node for today → `active_plan: None`
- No daily node for yesterday → `yesterday_review: None`
- Task with no `due` field → not included in due_tasks
- Event with datetime (e.g., `2026-03-23T14:00`) → extract time. Event with date only → `time: None`
- Multiple daily nodes for today (shouldn't happen, but defensive) → use the one with the most content

**Pattern to follow:** Same pure-function-over-data pattern as `calculate_streaks()` and `find_overdue_commitments()`. Takes data, returns structured dict. No side effects. No API calls.

**Acceptance criteria:**
- Briefing includes all events for today with times when available
- Due tasks include both today's tasks and overdue tasks, sorted by priority
- Habit targets correctly identify which habits are due based on frequency
- Active plan populated from today's daily node when it exists
- Yesterday's review populated when yesterday's daily has a plan
- Empty graph produces a valid briefing with empty arrays and null optionals

**Test cases** (`backend/tests/test_planning_service.py`, new file):
- `test_briefing_events_for_today` — event with today's date appears in events
- `test_briefing_events_exclude_other_dates` — event for tomorrow not included
- `test_briefing_event_time_extraction` — datetime field → time extracted
- `test_briefing_due_tasks_today` — task due today with status todo → in due_tasks
- `test_briefing_due_tasks_excludes_done` — completed task not in due_tasks
- `test_briefing_overdue_tasks_included` — task due yesterday → in due_tasks with days_overdue=1
- `test_briefing_tasks_sorted_by_priority` — high before medium before low
- `test_briefing_habit_targets_daily` — daily habit → always due
- `test_briefing_habit_targets_weekly_due` — weekly habit, last completed 6 days ago → due
- `test_briefing_habit_targets_weekly_not_due` — weekly habit, completed yesterday → not due
- `test_briefing_active_plan_from_daily` — daily node with planned field → active_plan populated
- `test_briefing_no_daily_node` — no daily node for today → active_plan is None
- `test_briefing_yesterday_review` — yesterday's daily with plan → yesterday_review populated
- `test_briefing_empty_graph` — empty graph → valid briefing with empty arrays

---

### Task 2: Plan Capture in FORMAT_SPEC and Schema

**Objective:** Teach Claude to record daily plans as structured data on daily nodes, and extend the schema to support plan fields.

**Files to modify:**
- `backend/mentor_agent.py` — add section `── 9. DAILY PLANS ──` to `_GRAPH_INSTRUCTIONS`
- `vault/_meta/schema.md` — add `planned` field to `daily` type frontmatter
- `backend/services/chat_service.py` — add `_process_plan_updates()` post-processing step

**Requirements:**

**Schema change — add `planned` to `daily` type:**

```yaml
date:                   # The day this entry is for. ISO date.
mood:                   # Optional. great | good | neutral | bad | terrible
energy:                 # Optional. high | medium | low
planned: []             # Optional. List of planned activities for this day.
```

Each item in `planned` is a dict:
```yaml
planned:
  - description: "Gym session"
    linked_node: strength-training    # Optional. Node ID of related habit/task/goal.
    completed: false                  # Updated when activity is done.
  - description: "Review PR for Sarah"
    linked_node: finish-pr-review
    completed: false
```

**FORMAT_SPEC addition:**

Add after `── 8. COMMITMENTS ──`:

```
── 9. DAILY PLANS ──
When the user describes what they plan to do today ("today I'm going to...", "my plan for today is...",
"I need to X, Y, and Z today", "for today I want to..."), capture it as a daily node update:

  action: "update" (if today's daily exists) or "create" (if not)
  type: "daily"
  frontmatter:
    date: "{today's date}"
    planned:
      - description: "Gym session"
        linked_node: "strength-training"   // Link to existing habit/task if one matches
        completed: false
      - description: "Review PR"
        linked_node: "finish-pr-review"    // Link to existing task if one matches
        completed: false

Link planned items to existing nodes when there's a clear match:
- "go to the gym" → link to the user's gym/training habit
- "finish the PR review" → link to an existing task node
- "grocery shopping" → no link (ad-hoc activity, just description)

When the user reports completing a planned item ("done with the gym", "finished the PR review"),
update the daily node's planned list to set completed: true for that item.

Do NOT create a daily node just because the user says "today I..." in passing context.
Only capture when the user is explicitly describing their plan or agenda for the day.
```

**Post-processing in `chat_service.py`:**

Add `_process_plan_updates(updates: list[dict]) -> list[dict]` that validates plan data on daily node updates:

1. For each update targeting a `daily` type with `planned` in frontmatter:
   - Validate each planned item has a `description` (non-empty string)
   - Validate `linked_node` is a string or None (if invalid, set to None)
   - Default `completed` to `false` if missing
   - Strip duplicate items (same description, case-insensitive)
2. For updates that set `completed: true` on a planned item:
   - If the linked_node exists and has `status: done`/`completed`, accept
   - If the linked_node doesn't exist, still accept (trust Claude's judgment from conversation context)

Call this after `_enrich_commitment_metadata()` in both `send_message()` and `stream_message()`.

**Edge cases:**
- User mentions "today" but isn't making a plan → Claude should NOT create a plan (the FORMAT_SPEC instruction handles this via "only capture when explicitly describing plan/agenda")
- User adds to an existing plan ("also, I need to call the dentist") → Claude should update the existing daily node, appending to the planned list rather than replacing it
- User reports partial completion → update specific items, not the whole list
- Plan with no linkable nodes → all items have `linked_node: null`, which is fine

**Pattern to follow:** Same FORMAT_SPEC extension pattern as `── 8. COMMITMENTS ──`. Same post-processing pattern as `_enrich_commitment_metadata()`.

**Acceptance criteria:**
- "Today I'm going to hit the gym, review Sarah's PR, and read for 30 minutes" → daily node created/updated with 3 planned items
- Planned items link to existing habit/task nodes where a match exists
- "Done with the gym" → corresponding planned item marked completed
- Vague "today" references don't trigger plan capture
- Schema includes `planned` field on daily type

**Test cases** (add to `backend/tests/test_planning_service.py`):
- `test_process_plan_valid` — planned list with description + linked_node → passes through
- `test_process_plan_missing_description` — item without description → removed
- `test_process_plan_completed_default_false` — item without completed field → defaults to false
- `test_process_plan_deduplication` — two items with same description → deduped to one
- `test_process_plan_invalid_linked_node` — non-string linked_node → set to None
- `test_format_spec_contains_daily_plans` — `_GRAPH_INSTRUCTIONS` contains "DAILY PLANS"

---

### Task 3: Plan-Reality Comparison and Pattern Detection

**Objective:** Compare daily plans against actual outcomes and detect patterns in plan completion over time.

**Files to modify:**
- `backend/services/planning_service.py` — add `compare_plan_reality()` and `analyze_plan_patterns()` functions

**Requirements:**

```
compare_plan_reality(graph: VaultGraph, daily_node_id: str) -> dict | None
```

Takes a daily node ID and computes the plan-reality comparison:

```python
{
    "daily_id": "monday-mar-23",
    "date": "2026-03-23",
    "planned_count": 5,
    "completed_count": 3,
    "completion_rate": 0.6,
    "items": [
        {
            "description": "Gym session",
            "linked_node": "strength-training",
            "planned": True,
            "completed": True,
            "source": "plan_marked",       # how completion was determined
        },
        {
            "description": "Review PR for Sarah",
            "linked_node": "finish-pr-review",
            "planned": True,
            "completed": False,
            "source": None,
        },
    ],
    "unplanned_completions": [             # things done but not in the plan
        {
            "node_id": "call-dentist",
            "title": "Call Dentist",
            "type": "task",
            "source": "status_change",
        }
    ],
}
```

**Completion detection logic (priority order):**

1. `plan_marked` — the planned item has `completed: true` in frontmatter (explicitly marked by Claude)
2. `status_change` — the `linked_node` exists and has `status` in (`done`, `completed`, `attended`) AND was updated on the plan date (check node's last-modified or updated timestamp)
3. `daily_linked` — the daily node has a `## Related` edge to the `linked_node` (indicating activity)

If any of these match, the item is completed. If none match AND the item has no `linked_node`, rely only on `plan_marked`.

**Unplanned completions:** Find tasks/events that were completed on the plan date but weren't in the planned list. Scan nodes where `status` changed to done/completed/attended AND the change date matches the daily's date. These represent "things you did but didn't plan" — useful signal for understanding how realistic plans are.

```
analyze_plan_patterns(graph: VaultGraph, lookback_days: int = 14) -> dict
```

Reads daily nodes from the last `lookback_days` that have `planned` data, computes patterns:

```python
{
    "days_with_plans": 10,               # how many days had plans
    "avg_planned_items": 4.5,
    "avg_completion_rate": 0.65,
    "completion_by_day_of_week": {
        "Monday": 0.5,
        "Tuesday": 0.7,
        "Wednesday": 0.8,
        # ...
    },
    "most_dropped_categories": [         # types of items most often incomplete
        {"category": "reading", "drop_rate": 0.6},
        {"category": "exercise", "drop_rate": 0.3},
    ],
    "overcommit_days": 3,                # days where planned > 6 items
    "avg_unplanned_completions": 1.2,    # things done but not planned per day
    "planning_insight": "You tend to overcommit on Mondays (avg 6 items, 50% completion). Consider planning 3-4 items instead.",
}
```

**Pattern logic:**

1. Collect all daily nodes from last N days with `planned` in frontmatter
2. For each, run `compare_plan_reality()` to get completion data
3. Aggregate: avg planned items, avg completion rate, by day-of-week
4. Identify overcommit days (planned > 6 items with < 60% completion)
5. Track which categories (matched by keyword in description — "gym"/"run" → exercise, "read" → reading, "work"/"code"/"dev" → work) are most often dropped
6. Generate a `planning_insight` string: one actionable observation. E.g., "You average 65% completion. Your best day is Wednesday (80%). Consider planning fewer items on Mondays." Only generate if `days_with_plans >= 5` (need enough data). Otherwise `planning_insight: None`.

**Edge cases:**
- Daily node with no `planned` field → `compare_plan_reality()` returns None
- All items completed → completion_rate 1.0, empty missed list
- No daily nodes in lookback window → `analyze_plan_patterns()` returns default dict with zeros
- Linked node deleted since plan was made → treat as incomplete (can't verify)

**Pattern to follow:** Same pure-function pattern as `calculate_streaks()`. Same keyword-matching approach as `_FUNDAMENTAL_KEYWORDS` for category detection.

**Acceptance criteria:**
- Plan with 5 items, 3 completed → completion_rate 0.6
- Item marked completed in frontmatter → detected via `plan_marked`
- Linked task with status done → detected via `status_change`
- Unplanned completions detected and listed separately
- 14-day analysis produces per-day completion rates and overcommit detection
- Planning insight generated when 5+ days of plan data exist

**Test cases** (add to `backend/tests/test_planning_service.py`):
- `test_compare_plan_all_completed` — 3/3 items completed → completion_rate 1.0
- `test_compare_plan_partial` — 2/4 completed → completion_rate 0.5
- `test_compare_plan_marked_source` — item with completed: true → source "plan_marked"
- `test_compare_plan_status_change_source` — linked node status done → source "status_change"
- `test_compare_plan_daily_linked_source` — daily Related edge to linked node → source "daily_linked"
- `test_compare_plan_no_linked_node` — item without linked_node, not marked → incomplete
- `test_compare_plan_unplanned_completions` — task done today but not in plan → in unplanned list
- `test_compare_plan_no_plan` — daily without planned → returns None
- `test_patterns_avg_completion` — 3 days with rates 0.5, 0.7, 0.9 → avg 0.7
- `test_patterns_by_day_of_week` — Monday plans avg lower completion → reflected
- `test_patterns_overcommit_detection` — day with 7 items, 40% completion → counted as overcommit
- `test_patterns_insufficient_data` — fewer than 5 days → planning_insight is None
- `test_patterns_no_plan_data` — no dailies with plans → valid default dict

---

### Task 4: Plan-Aware Context Injection

**Objective:** Inject today's briefing and active plan into the system prompt so Athena references the day's structure naturally.

**Files to modify:**
- `backend/mentor_agent.py` — add `_build_briefing_note()` method, inject into `chat()` and `chat_stream()`
- `backend/services/chat_service.py` — compute briefing alongside alerts, pass downstream

**Requirements:**

**In `chat_service.py`:**

After computing alerts (streaks, overdue, fundamentals, relationships), compute the briefing:

```python
from services.planning_service import compile_briefing, analyze_plan_patterns

briefing = compile_briefing(self.graph, {
    "streaks": streaks,
    "overdue": overdue,
    "fundamentals": fundamentals,
}, date.today())

plan_patterns = analyze_plan_patterns(self.graph)
```

Pass `briefing` and `plan_patterns` to `mentor.chat_stream()` and `mentor.chat()` as a new `briefing` parameter.

**In `mentor_agent.py`:**

Add `_build_briefing_note(briefing: dict | None, patterns: dict | None) -> str`:

```
TODAY'S BRIEFING — {day_of_week}, {date}
Reference this when the user asks about their day, what's next, or what to focus on.

SCHEDULED:
- 14:00 Dentist Appointment (upcoming) @ City Dental
- Evening: Dinner with Ethan (upcoming)

DUE TODAY:
- [high] Finish PR Review (part of Project Alpha)
- [medium] Update resume

HABIT TARGETS (due today based on frequency):
- Strength Training (3x/week, streak: 8, on_track)
- Evening Reading (daily, streak: 3, on_track)

OVERDUE (carry-forward):
- Submit Tax Return — 3 days overdue (high priority)

ACTIVE PLAN:
The user planned: Gym session, Review PR for Sarah, Read 30 pages
Completed so far: Read 30 pages (1/3)

YESTERDAY'S REVIEW:
Planned 5, completed 3 (60%). Missed: Evening Reading, Meditation.

PLANNING PATTERNS:
You tend to overcommit on Mondays (avg 6 items, 50% completion). Consider suggesting 3-4 items.
```

**System prompt placement:**

Inject between dismissed notes and state notes. Updated injection order:

```
system += self._build_dismissed_note(dismissed_ids or [])
system += self._build_briefing_note(briefing, plan_patterns)   # NEW — before state
system += self._build_state_note(state or {})
system += self._build_proactive_alerts(alerts or {}, state=state)
system += self._build_conflict_note(conflicts or [])
system += self._build_mode_note(mode)
system += self._build_challenge_note(challenges or {})
```

The briefing comes early because it's reference context — Claude should see what the day looks like before seeing stress state or alerts. This helps Claude calibrate ("the user has a packed day AND is stressed" vs "light day, stressed about something else").

**Suppression rules:**
- If briefing is empty (no events, no due tasks, no habits, no plan, no overdue) → return empty string
- If user's state is elevated stress with high confidence → condense briefing to events + top 2 due items only (don't overwhelm)
- Planning patterns only injected if `planning_insight` is not None

**In `chat()` and `chat_stream()`:**

Add `briefing: dict | None = None` and `plan_patterns: dict | None = None` parameters with same pattern as `alerts` and `state`.

**Edge cases:**
- If `compile_briefing()` raises → catch, log, proceed with empty briefing (same defensive pattern)
- If no events and no due tasks → still inject habit targets and overdue if present
- Weekend vs weekday → no behavioral difference (habits and commitments don't take weekends off unless the user says so)

**Pattern to follow:** Same `_build_X_note()` injection pattern as `_build_state_note()` and `_build_proactive_alerts()`. Same defensive try/except in `chat_service.py`.

**Acceptance criteria:**
- System prompt includes `TODAY'S BRIEFING:` when the day has relevant items
- Events, due tasks, habit targets, and plan status all appear in briefing note
- Planning patterns appear when sufficient data exists
- Briefing suppressed when day is empty
- Briefing condensed when user stress is elevated
- Briefing computation errors don't break chat flow

**Test cases** (add to `backend/tests/test_mentor_agent.py`):
- `test_briefing_note_with_events` — event today → appears in formatted output under SCHEDULED
- `test_briefing_note_with_due_tasks` — task due today → appears under DUE TODAY
- `test_briefing_note_with_habit_targets` — habit due today → appears under HABIT TARGETS
- `test_briefing_note_with_active_plan` — daily node with plan → appears under ACTIVE PLAN
- `test_briefing_note_yesterday_review` — yesterday had a plan → review section populated
- `test_briefing_note_empty_day` — no events, tasks, habits → empty string returned
- `test_briefing_note_with_patterns` — planning insight exists → appears in output
- `test_briefing_note_stressed_condensed` — elevated stress → only events + top 2 due items
- `test_system_prompt_includes_briefing` — system prompt contains "TODAY'S BRIEFING" when briefing exists

**Test cases** (add to `backend/tests/test_routes.py` or `test_chat_service.py`):
- `test_briefing_error_doesnt_break_chat` — mock planning_service to raise → chat proceeds

---

### Task 5: Briefing API Endpoint and Frontend DailyPlanView

**Objective:** Add a REST endpoint that returns the structured briefing and a frontend component that displays the daily plan view.

**Files to modify:**
- `backend/routes/graph_routes.py` — add `GET /api/briefing` endpoint
- `frontend/src/lib/DailyPlanView.svelte` (new file) — daily plan view component
- `frontend/src/lib/api.js` — add `getBriefing()` fetch wrapper
- `frontend/src/App.svelte` — add DailyPlanView to view switching
- `frontend/src/lib/Sidebar.svelte` — add "Today" link to sidebar navigation

**Requirements:**

**Backend endpoint:**

```
GET /api/briefing?date=2026-03-23
```

Optional `date` query parameter (ISO date string). Defaults to today.

Response — the full output of `compile_briefing()` plus plan-reality comparison and patterns:

```json
{
  "briefing": {
    "date": "2026-03-23",
    "day_of_week": "Monday",
    "events": [...],
    "due_tasks": [...],
    "habit_targets": [...],
    "active_plan": {...},
    "overdue_summary": {...},
    "fundamentals_status": {...},
    "yesterday_review": {...}
  },
  "plan_reality": {
    "planned_count": 5,
    "completed_count": 3,
    "completion_rate": 0.6,
    "items": [...],
    "unplanned_completions": [...]
  },
  "patterns": {
    "days_with_plans": 10,
    "avg_planned_items": 4.5,
    "avg_completion_rate": 0.65,
    "completion_by_day_of_week": {...},
    "overcommit_days": 3,
    "planning_insight": "..."
  }
}
```

If `date` is not today, `plan_reality` reflects the historical comparison for that date. `patterns` always reflects the last 14 days regardless of the requested date.

**Error handling:** If planning service raises, return 500 with `{"error": "Failed to compile briefing"}`. If `date` param is invalid, return 400 with `{"error": "Invalid date format. Use ISO date (YYYY-MM-DD)."}`.

**Frontend `DailyPlanView.svelte`:**

A structured daily view with sections:

1. **Header** — day/date, completion ring (plan progress as a circular indicator, or simple "3/5 completed" if no plan)
2. **Schedule section** — today's events listed chronologically with times, locations, people
3. **Plan section** — if a plan exists, show checklist of planned items with completion status. Items linked to nodes are clickable (emit event to show NodeDetail). If no plan, show prompt: "No plan captured yet. Tell Athena what you're focusing on today."
4. **Due section** — tasks due today and overdue carry-forwards, with priority badges
5. **Habits section** — habits due today with streak indicators
6. **Yesterday section** — collapsible, shows yesterday's plan completion rate and missed items
7. **Patterns section** — collapsible, shows planning insight and day-of-week chart (simple text-based for now, not a chart library)

**Styling:**
- Follow the same dark theme as `AccountabilityView.svelte` and `RelationshipsView.svelte`
- Use existing CSS custom properties from `app.css`
- Completion indicators: green for completed, amber for in-progress/pending, red for overdue
- No external dependencies

**`api.js` addition:**

```javascript
export async function getBriefing(date = null) {
    const params = date ? `?date=${date}` : '';
    return fetchJSON(`/api/briefing${params}`);
}
```

**`App.svelte` integration:**

Add `DailyPlanView` import and view switching case (`currentView === 'today'`). Pass `onNodeSelect` handler for clickable node links.

**`Sidebar.svelte` integration:**

Add a "Today" navigation item alongside the existing "Accountability" and "Relationships" items. Use a calendar/day icon or similar unicode glyph. Highlight when active.

**Pattern to follow:** Same endpoint pattern as `GET /api/accountability`. Same component structure as `AccountabilityView.svelte` (loading state, error handling, data fetching via `$effect`). Same sidebar integration as existing view items.

**Acceptance criteria:**
- `GET /api/briefing` returns structured briefing with all sections
- `GET /api/briefing?date=2026-03-20` returns historical briefing for that date
- Invalid date returns 400
- Frontend shows events, plan, due tasks, habits in organized sections
- Sidebar has "Today" link that switches to DailyPlanView
- Linked nodes in plan items are clickable
- No plan → shows prompt to tell Athena about today's plan
- Component handles loading/error states gracefully

**Test cases** (add to `backend/tests/test_routes.py`):
- `test_briefing_endpoint_returns_data` — GET `/api/briefing` → 200 with briefing key
- `test_briefing_endpoint_custom_date` — GET `/api/briefing?date=2026-03-20` → briefing for that date
- `test_briefing_endpoint_invalid_date` — GET `/api/briefing?date=notadate` → 400
- `test_briefing_endpoint_includes_patterns` — response includes patterns key
- `test_briefing_endpoint_includes_plan_reality` — response includes plan_reality key
- `test_briefing_endpoint_error_handling` — planning service raises → 500

---

## API Response Contracts

### New endpoint: `GET /api/briefing`

**Query parameters:**
- `date` (optional) — ISO date string (YYYY-MM-DD). Defaults to today.

**Response:**
```json
{
  "briefing": {
    "date": "string (ISO date)",
    "day_of_week": "string",
    "events": [
      {
        "node_id": "string",
        "title": "string",
        "time": "string | null",
        "location": "string | null",
        "people": ["string"],
        "status": "string"
      }
    ],
    "due_tasks": [
      {
        "node_id": "string",
        "title": "string",
        "priority": "string",
        "project": "string | null",
        "days_overdue": "number"
      }
    ],
    "habit_targets": [
      {
        "habit_id": "string",
        "title": "string",
        "frequency": "string",
        "streak_status": "string",
        "current_streak": "number",
        "last_completed": "string | null",
        "due_today": "boolean"
      }
    ],
    "active_plan": {
      "daily_id": "string",
      "planned": [
        {
          "description": "string",
          "linked_node": "string | null",
          "completed": "boolean"
        }
      ],
      "completion_rate": "number (0.0-1.0)"
    } | null,
    "overdue_summary": {
      "count": "number",
      "top_items": [
        {
          "node_id": "string",
          "title": "string",
          "days_overdue": "number",
          "priority": "string"
        }
      ]
    },
    "fundamentals_status": {
      "neglected": ["string"],
      "at_risk": ["string"]
    },
    "yesterday_review": {
      "planned_count": "number",
      "completed_count": "number",
      "completion_rate": "number",
      "missed": ["string"]
    } | null
  },
  "plan_reality": {
    "planned_count": "number",
    "completed_count": "number",
    "completion_rate": "number",
    "items": [
      {
        "description": "string",
        "linked_node": "string | null",
        "planned": "boolean",
        "completed": "boolean",
        "source": "string | null"
      }
    ],
    "unplanned_completions": [
      {
        "node_id": "string",
        "title": "string",
        "type": "string",
        "source": "string"
      }
    ]
  } | null,
  "patterns": {
    "days_with_plans": "number",
    "avg_planned_items": "number",
    "avg_completion_rate": "number",
    "completion_by_day_of_week": {"string": "number"},
    "most_dropped_categories": [{"category": "string", "drop_rate": "number"}],
    "overcommit_days": "number",
    "avg_unplanned_completions": "number",
    "planning_insight": "string | null"
  }
}
```

### Chat API response: unchanged

No changes to the chat response shape. The system prompt now includes a `TODAY'S BRIEFING:` section when relevant data exists. Graph updates for daily nodes may include `planned` in frontmatter.

---

## Implementation Order

```
Task 1: Daily Briefing Service (independent — new file, no dependencies)
  └→ Task 3: Plan-Reality Comparison + Patterns (depends on Task 1 for shared module)
Task 2: Plan Capture in FORMAT_SPEC + Schema (independent — FORMAT_SPEC + schema changes)
  └→ Task 4: Plan-Aware Context Injection (depends on Tasks 1, 2, 3 for data)
      └→ Task 5: Briefing API + Frontend (depends on Tasks 1, 3, 4 for full pipeline)
```

Tasks 1 and 2 can be built in parallel. Task 3 depends on Task 1 (same file). Task 4 depends on Tasks 1, 2, and 3. Task 5 depends on Tasks 1, 3, and 4.

Recommended sequence: **1 → 2 → 3 → 4 → 5** (build the service, add plan capture, add comparison, wire into chat flow, then expose via API and frontend).

---

## New Files

| File | Purpose |
|------|---------|
| `backend/services/planning_service.py` | Daily briefing compilation, plan-reality comparison, pattern detection |
| `backend/tests/test_planning_service.py` | Planning service unit tests |
| `frontend/src/lib/DailyPlanView.svelte` | Frontend daily plan view component |

**Total new files: 3** (well within the 15-file limit)

---

## Definition of Done

1. All existing tests pass (`python -m pytest` from `backend/`)
2. `npm run build` succeeds in `frontend/`
3. New test files pass with all cases green
4. `GET /api/briefing` returns structured daily briefing with events, tasks, habits, and plan data
5. "Today I'm going to gym, review PR, and read" → daily node updated with 3 planned items
6. Planned items link to existing habit/task nodes where a match exists
7. Completing a planned item → `completed: true` in daily node frontmatter
8. Plan-reality comparison correctly identifies completed vs missed items
9. 14-day pattern analysis produces completion rates and planning insights
10. System prompt includes `TODAY'S BRIEFING:` with today's agenda and plan status
11. Frontend DailyPlanView shows events, plan checklist, due tasks, and habits
12. "Today" link in sidebar switches to daily plan view
13. Planning service errors don't break chat flow
14. No regression in conflict detection, mode classification, permanence scoring, challenge ladder, accountability, state inference, query-aware retrieval, or relationship intelligence
15. No new dependencies added to `requirements.txt` or `package.json`
