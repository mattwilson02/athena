# Conflict Detection Service

> E3 Item 2 — Detect when user intentions contradict existing goals, values, habits, commitments, finances, or schedule.

## Problem

Athena accepts everything at face value. Say "I'm going to skip the gym this week" when you have an active `exercise` habit and a `health` value — she won't notice the contradiction. Mention a new purchase when you're over budget — no pushback. Propose a trip the same week as a deadline — silence. Retrieval pulls relevant nodes but never *opposing* ones. The AI has no signal to challenge.

## Solution

A new `conflict_service.py` that detects contradictions between a user's stated intention and existing graph nodes across all domains. Runs on every message. Returns conflicts for injection into the AI's context.

Not AI-powered — deterministic graph queries + vector similarity. No API calls. Fast enough to run synchronously on every message.

## API

```python
def detect_conflicts(
    message: str,
    graph: VaultGraph,
    vector_index: VectorIndex,
    schema: dict,
) -> list[dict]:
    """Detect conflicts between a user message and existing graph nodes.

    Returns:
        List of conflict dicts:
        {
            "node_id": "exercise-habit",
            "title": "Exercise 4x/week",
            "type": "habit",
            "conflict_type": "habit_break",
            "explanation": "Skipping gym contradicts your active habit of exercising 4x/week",
            "severity": "hard",
        }
    """
```

## Conflict Types

### Identity & Self conflicts

| Type | Trigger | Source types | Severity |
|------|---------|-------------|----------|
| `value_violation` | Intention opposes a stated value | `value` | `hard` for core/important, `soft` for aspirational |
| `goal_contradiction` | Intention undermines an active goal | `goal` | `hard` for high priority, `soft` for medium/low |
| `habit_break` | Intention breaks an active habit/routine | `habit` | `hard` for daily, `soft` for weekly/monthly |
| `belief_contradiction` | Statement contradicts a held belief | `belief` | `soft` (beliefs are meant to be challenged) |
| `fear_avoidance` | Intention feeds or enables a fear the user is trying to overcome | `fear` (status: managed/active) | `soft` |

### Planning & time conflicts

| Type | Trigger | Source types | Severity |
|------|---------|-------------|----------|
| `schedule_conflict` | New time commitment overlaps existing event/deadline | `event`, `task`, `project` | `hard` if dates overlap, `soft` if same week |
| `priority_inversion` | Working on low-priority item when high-priority is overdue/urgent | `task`, `goal`, `project` | `soft` |
| `commitment_overload` | Taking on new commitment when active load is already high | `goal`, `project`, `habit` | `soft` — more of a tradeoff signal (see item 3) |

### Financial conflicts

| Type | Trigger | Source types | Severity |
|------|---------|-------------|----------|
| `budget_breach` | Proposed spending would exceed budget | `budget`, `expense` | `hard` if over budget, `soft` if approaching limit |
| `financial_contradiction` | Spending contradicts a stated financial goal (e.g. "save money" goal + luxury purchase) | `goal`, `budget` | `soft` |

### Relationship & context conflicts

| Type | Trigger | Source types | Severity |
|------|---------|-------------|----------|
| `commitment_to_person` | Breaking a commitment that involves someone else | `event`, `task` with people edges | `hard` |

## Detection Pipeline

Four-stage pipeline. Each stage is independent and produces candidates that are merged at the end.

### Stage 1: Intention signal detection

Quick filter — check if the message contains signals that warrant conflict checking:

**Action intentions:**
- Explicit: "I'm going to", "I want to", "planning to", "I'll", "I've decided to", "thinking about"
- Negative: "skip", "quit", "stop", "cancel", "drop", "give up", "not going to", "won't"
- Change: "instead", "actually", "changed my mind", "scrap", "forget about"

**Spending intentions:**
- "buy", "spend", "order", "subscribe", "book", "pay for", "splurge"

**Time intentions:**
- "this weekend", "tonight", "tomorrow", "next week", date mentions

If no intention signals found, return early with empty list. Most informational messages ("what's in my graph?", "tell me about X") don't need conflict checking.

### Stage 2: Gather conflict candidates

Query the graph for nodes that could conflict, grouped by check type:

```python
# Self-domain: active values, goals, habits, beliefs, fears
self_types = ("value", "goal", "habit", "belief", "fear")
self_candidates = []
for node_type in self_types:
    for node in graph.get_nodes_by_type(node_type):
        status = node.get("status", "active")
        if status in _RESOLVED_STATUSES:
            continue
        self_candidates.append(node)

# Planning: active events, tasks, projects with deadlines
planning_types = ("event", "task", "project")
planning_candidates = []
for node_type in planning_types:
    for node in graph.get_nodes_by_type(node_type):
        status = node.get("status", "active")
        if status in _RESOLVED_STATUSES:
            continue
        planning_candidates.append(node)

# Finance: active budgets
finance_candidates = []
for node in graph.get_nodes_by_type("budget"):
    finance_candidates.append(node)
```

Typically 30-50 active nodes across all these types. Fast to iterate.

### Stage 3: Semantic similarity matching

Use vector search to find nodes semantically related to the user's message:

```python
results = vector_index.search(message, n=20)

# Types eligible for conflict checking
conflict_types = {"value", "goal", "habit", "belief", "fear", "event", "task",
                  "project", "budget", "expense", "subscription"}
relevant_results = [r for r in results if r["type"] in conflict_types]
```

ChromaDB returns L2 distances — lower is more similar. Thresholds:
- `< 0.5`: strong match — likely relevant to what the user is saying
- `0.5 - 0.8`: moderate match — worth checking
- `> 0.8`: weak — skip

Merge with graph candidates, deduplicate by node_id.

### Stage 4: Conflict classification

For each candidate, classify whether the message actually conflicts:

```python
def _classify_conflict(message_lower: str, node: dict, has_negation: bool,
                       has_spending: bool) -> dict | None:
    """Determine if message conflicts with node. Returns conflict dict or None."""
    node_type = node.get("type")
    title = node.get("title", "")

    # Topic matching — does the message reference this node's subject?
    topic_words = _extract_topic_words(title, node.get("content", ""))
    topic_match = any(w in message_lower for w in topic_words if len(w) > 3)

    if not topic_match and node_type not in ("budget",):
        return None  # no connection between message and this node

    # --- Identity conflicts ---

    if node_type == "value" and has_negation:
        priority = node.get("priority", "important")
        return {
            "conflict_type": "value_violation",
            "severity": "hard" if priority in ("core", "important") else "soft",
            "explanation": f"This contradicts your stated value of {title}",
        }

    if node_type == "goal" and has_negation:
        priority = node.get("priority", "medium")
        return {
            "conflict_type": "goal_contradiction",
            "severity": "hard" if priority == "high" else "soft",
            "explanation": f"This undermines your active goal: {title}",
        }

    if node_type == "habit" and has_negation:
        frequency = node.get("frequency", "weekly")
        return {
            "conflict_type": "habit_break",
            "severity": "hard" if frequency == "daily" else "soft",
            "explanation": f"This breaks your active habit: {title} ({frequency})",
        }

    if node_type == "belief" and has_negation:
        return {
            "conflict_type": "belief_contradiction",
            "severity": "soft",
            "explanation": f"This contradicts your belief: {title}",
        }

    if node_type == "fear":
        # Check if intention enables/feeds a fear (e.g. "I'll avoid the presentation"
        # when they have a fear of public speaking they're trying to overcome)
        status = node.get("status", "active")
        if status in ("active", "managed") and _is_avoidance_pattern(message_lower, node):
            return {
                "conflict_type": "fear_avoidance",
                "severity": "soft",
                "explanation": f"This may be reinforcing your fear: {title}",
            }

    # --- Planning conflicts ---

    if node_type == "event" and _has_date_overlap(message_lower, node):
        has_people = bool(node.get("people", []))
        return {
            "conflict_type": "commitment_to_person" if has_people else "schedule_conflict",
            "severity": "hard" if has_people else "soft",
            "explanation": f"You have \"{title}\" scheduled around the same time",
        }

    if node_type in ("task", "project"):
        priority = node.get("priority", "medium")
        due = node.get("due") or node.get("deadline")
        if priority == "high" and due and _is_overdue(due):
            return {
                "conflict_type": "priority_inversion",
                "severity": "soft",
                "explanation": f"Your high-priority {node_type} \"{title}\" is overdue (due {due})",
            }

    # --- Financial conflicts ---

    if node_type == "budget" and has_spending:
        return {
            "conflict_type": "budget_breach",
            "severity": "soft",
            "explanation": f"Check this against your {title} budget",
        }

    if node_type == "goal" and has_spending:
        # Financial goals (save money, reduce spending) vs spending
        content_lower = (node.get("content", "") + " " + title).lower()
        if any(w in content_lower for w in ("save", "budget", "spend less", "frugal", "cut costs")):
            return {
                "conflict_type": "financial_contradiction",
                "severity": "soft",
                "explanation": f"This spending may conflict with your goal: {title}",
            }

    return None
```

### Merging and ranking

Combine all detected conflicts, deduplicate by node_id, sort by severity (`hard` first), and cap at 5.

## Files Changed

| File | Change |
|------|--------|
| `backend/services/conflict_service.py` | New file — `detect_conflicts()`, classification helpers |
| `backend/tests/test_conflict_service.py` | New file — tests for each conflict type, edge cases |

## Helper Functions

```python
_RESOLVED_STATUSES = {
    "completed", "done", "achieved", "overcome",
    "abandoned", "superseded", "cancelled", "attended",
}

_STOP_WORDS = {"the", "a", "an", "my", "to", "and", "or", "i", "is", "it", "of", "in", "for"}

def _extract_topic_words(title: str, content: str) -> set[str]:
    """Extract meaningful words from a node's title and first sentence of content."""
    text = (title + " " + content.split("\n")[0] if content else title).lower()
    return set(text.split()) - _STOP_WORDS

def _is_avoidance_pattern(message_lower: str, fear_node: dict) -> bool:
    """Check if message shows avoidance of a fear topic."""
    avoidance_words = {"avoid", "skip", "not going to", "can't face", "too scared",
                       "not ready", "put off", "postpone"}
    topic_words = _extract_topic_words(fear_node.get("title", ""), fear_node.get("content", ""))
    topic_match = any(w in message_lower for w in topic_words if len(w) > 3)
    has_avoidance = any(a in message_lower for a in avoidance_words)
    return topic_match and has_avoidance

def _has_date_overlap(message_lower: str, event_node: dict) -> bool:
    """Check if message mentions a time that overlaps with an event's date."""
    # Simple: check if event date keywords appear in message
    # e.g. event on "2026-04-15" and message says "april 15" or "next tuesday"
    # Full date parsing is complex — start with keyword matching on month/day
    event_date = event_node.get("date", "")
    if not event_date:
        return False
    # Parse month name from event date for rough matching
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(str(event_date).split("T")[0])
        month_name = dt.strftime("%B").lower()
        day = str(dt.day)
        return month_name in message_lower or f"{month_name} {day}" in message_lower
    except (ValueError, TypeError):
        return False

def _is_overdue(due_str: str) -> bool:
    """Check if a due date is in the past."""
    try:
        from datetime import date, datetime
        due = datetime.fromisoformat(str(due_str).split("T")[0]).date()
        return due < date.today()
    except (ValueError, TypeError):
        return False
```

## Edge Cases

- **No intention signals in message:** Return empty list. No false positives on "what are my goals?"
- **No active nodes exist:** Return empty. Degrades gracefully for new/empty vaults.
- **Multiple conflicts for same message:** Cap at 5, prioritize `hard` over `soft`.
- **Vague messages:** "I don't feel like it" won't match any specific node topic. Better to miss a vague conflict than flag a false positive.
- **Resolved nodes:** Completed/achieved/abandoned/cancelled nodes are skipped.
- **Fear with status "overcome":** Skipped — only active/managed fears generate avoidance conflicts.
- **Budget nodes with no amount:** Still flag as `soft` — the AI can check the details.
- **Date overlap is approximate:** We use month/day keyword matching, not full calendar arithmetic. False negatives are acceptable; false positives are not.

## What This Does NOT Do

- **No AI calls** — purely deterministic. Semantic similarity via ChromaDB embeddings, not Claude.
- **No conflict resolution** — just detection. The AI decides how to respond to conflicts.
- **No cross-session memory** — conflicts are detected per-message, not accumulated across sessions.
- **No intent classification** — we don't try to understand *why* the user is saying something.
- **No calendar arithmetic** — date overlap is keyword-based, not proper interval checking. Full calendar integration is Stage 2.

## Acceptance Criteria

1. "I'm going to skip gym" with active exercise habit → `habit_break` conflict returned
2. "I'll stay up late" with discipline value → `value_violation` returned
3. "I'm cancelling the marathon" with active marathon goal → `goal_contradiction` returned
4. "I'll avoid the presentation" with public-speaking fear → `fear_avoidance` returned
5. "I want to buy a new laptop" with active save-money goal → `financial_contradiction` returned
6. "I want to buy a new laptop" with monthly budget node → `budget_breach` returned
7. "I'll skip the dinner with Jake" with event involving Jake → `commitment_to_person` returned
8. New commitment with overdue high-priority task → `priority_inversion` returned
9. "What are my goals?" → no conflicts (no intention signal)
10. Completed/achieved/cancelled nodes don't generate conflicts
11. Max 5 conflicts returned per message, `hard` sorted first
12. All tests pass
