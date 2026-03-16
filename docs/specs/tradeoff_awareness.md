# Tradeoff Awareness

> E3 Item 4 — Surface the full obligation picture when the user proposes a new commitment.

## Problem

Athena detects conflicts (contradictions with existing values/goals/habits), but doesn't surface the broader picture when someone takes on something *new*. Say "I want to start learning guitar" and she'll create the goal — but she won't tell you that you already have 3 active goals, 4 daily habits, and 2 in-progress projects. The user has no sense of their current load, and neither does the AI. Commitments accumulate invisibly until something breaks.

This isn't conflict detection — there's no contradiction. It's capacity awareness. The user isn't opposing a goal; they're adding to an already full plate. The AI needs to see the plate.

## Solution

When the conflict service detects that a message contains a **new commitment signal** (creating a goal, project, habit, or event), build an obligation summary from the graph and inject it into the system prompt alongside any conflicts. This gives Claude the data to ask "what gives?" instead of blindly accepting.

Not AI-powered — deterministic graph query. Runs as part of the existing `detect_conflicts()` pipeline.

## API

Extends the existing `detect_conflicts()` return value. When obligation load is high enough to warrant surfacing, a special `commitment_overload` conflict is returned:

```python
{
    "node_id": "__obligations__",  # synthetic — not a real node
    "title": "Active Obligations",
    "type": "meta",
    "conflict_type": "commitment_overload",
    "severity": "soft",
    "explanation": "You already have 3 active goals, 2 projects, and 4 habits. Where does this fit?",
    "obligations": {
        "goals": [{"id": "marathon", "title": "Marathon Training", "priority": "high"}, ...],
        "projects": [{"id": "kitchen-reno", "title": "Kitchen Renovation", "status": "in_progress"}, ...],
        "habits": [{"id": "gym", "title": "Gym 4x/week", "frequency": "daily"}, ...],
        "events_upcoming": [{"id": "italy-trip", "title": "Italy Trip", "date": "2026-04-15"}, ...],
    }
}
```

## Detection

### When to fire

Obligation summary fires when **both** conditions are met:

1. **New commitment signal** — the message expresses intent to start/create something:
   - "I want to start...", "I'm going to begin...", "thinking about picking up..."
   - "I want to learn...", "planning a new..."
   - Spending signals that imply ongoing commitment: "subscribe", "sign up"
   - NOT negation signals (those are conflict detection's job)

2. **Load threshold exceeded** — the user already has enough active obligations to warrant surfacing:
   - 3+ active goals, OR
   - 2+ in-progress projects, OR
   - 5+ active habits, OR
   - Total active commitments (goals + projects + habits) >= 6

If the threshold isn't met, don't fire — small graphs don't need load warnings.

### New commitment signals

```python
_NEW_COMMITMENT_SIGNALS = [
    "start", "begin", "pick up", "take on", "add",
    "learn", "try", "launch", "kick off",
    "sign up", "enroll", "commit to",
    "new goal", "new project", "new habit",
]
```

These combine with the existing intention signals from conflict detection. A message needs both an intention signal ("I want to", "planning to") AND a new commitment signal to trigger obligation surfacing.

### Gathering obligations

Query the graph for active commitments:

```python
def _gather_obligations(graph) -> dict:
    """Count and list active obligations by type."""
    obligations = {"goals": [], "projects": [], "habits": [], "events_upcoming": []}

    for node in graph.get_nodes_by_type("goal"):
        status = node.get("status", "active")
        if status not in _RESOLVED_STATUSES:
            obligations["goals"].append({
                "id": node["id"],
                "title": node.get("title", node["id"]),
                "priority": node.get("priority", "medium"),
            })

    for node in graph.get_nodes_by_type("project"):
        status = node.get("status", "active")
        if status not in _RESOLVED_STATUSES:
            obligations["projects"].append({
                "id": node["id"],
                "title": node.get("title", node["id"]),
                "status": node.get("status", "active"),
            })

    for node in graph.get_nodes_by_type("habit"):
        status = node.get("status", "active")
        if status not in _RESOLVED_STATUSES:
            obligations["habits"].append({
                "id": node["id"],
                "title": node.get("title", node["id"]),
                "frequency": node.get("frequency", "weekly"),
            })

    # Only upcoming events (next 30 days)
    today = date.today()
    for node in graph.get_nodes_by_type("event"):
        status = node.get("status", "active")
        if status in _RESOLVED_STATUSES:
            continue
        event_date = node.get("date", "")
        if event_date:
            try:
                dt = datetime.fromisoformat(str(event_date).split("T")[0]).date()
                if today <= dt <= today + timedelta(days=30):
                    obligations["events_upcoming"].append({
                        "id": node["id"],
                        "title": node.get("title", node["id"]),
                        "date": str(event_date),
                    })
            except (ValueError, TypeError):
                pass

    return obligations
```

### Threshold check

```python
def _is_overloaded(obligations: dict) -> bool:
    """Check if active obligations exceed the surfacing threshold."""
    goals = len(obligations["goals"])
    projects = len(obligations["projects"])
    habits = len(obligations["habits"])
    total = goals + projects + habits

    return goals >= 3 or projects >= 2 or habits >= 5 or total >= 6
```

## System prompt injection

The `_build_conflict_note()` in mentor_agent.py already handles conflict injection. For commitment_overload, the obligations dict is formatted as a readable summary:

```
ACTIVE OBLIGATIONS (the user is proposing a new commitment — surface this):
- 3 active goals: Marathon Training (high), Get Promoted (high), Learn Piano (medium)
- 2 active projects: Kitchen Renovation (in_progress), Athena Development (in_progress)
- 4 active habits: Gym 4x/week (daily), Reading (daily), Meditation (daily), Journaling (weekly)
- 1 upcoming event: Italy Trip (Apr 15)
Ask the user what they're willing to deprioritize or drop to make room for this new commitment.
```

This is built in `_build_conflict_note()` by detecting the `commitment_overload` type and formatting the `obligations` dict.

## Files Changed

| File | Change |
|------|--------|
| `backend/services/conflict_service.py` | Add `_NEW_COMMITMENT_SIGNALS`, `_gather_obligations()`, `_is_overloaded()`, integrate into `detect_conflicts()` |
| `backend/mentor_agent.py` | Update `_build_conflict_note()` to format obligation summaries for commitment_overload |
| `backend/tests/test_conflict_service.py` | New tests for obligation gathering, threshold, and commitment_overload detection |

## Edge Cases

- **Empty/small graphs** — threshold not met, no obligation surfacing. Degrades gracefully.
- **Only negation, no new commitment** — "I want to quit guitar" doesn't trigger obligations (that's conflict detection). Only new commitments fire.
- **User already knows** — the AI sees the full picture and can decide how much to emphasize. The obligation data is injected; the AI chooses the tone.
- **Events beyond 30 days** — not included in obligation count. Only near-term events contribute to load.
- **Resolved/abandoned commitments** — filtered out by `_RESOLVED_STATUSES`, same as conflict detection.

## Acceptance Criteria

1. "I want to start learning guitar" with 3+ active goals → `commitment_overload` returned with obligation summary
2. "I want to start a new habit" with 5+ habits → fires
3. "I want to quit the gym" → does NOT fire (negation, not new commitment)
4. "What are my goals?" → does NOT fire (no intention signal)
5. Vault with < 6 total commitments → does NOT fire (below threshold)
6. Obligation summary includes goals, projects, habits with titles and priorities
7. Claude references the obligation picture in its response without being asked
8. All existing conflict detection tests still pass
