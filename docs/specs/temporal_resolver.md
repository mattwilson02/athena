# Temporal Resolver Fixes

> E3 Item 7 — Fix three blind spots in `_get_nodes_in_date_range()` that silently drop nodes from retrieval.

## Problem

`_get_nodes_in_date_range()` (line 635 of `mentor_agent.py`) has three bugs that make nodes invisible when they shouldn't be.

### Problem A: Inactive status filter is too aggressive

Lines 639-641 filter out all nodes whose status is in `_INACTIVE_STATUSES` (`completed`, `done`, `cancelled`, `archived`, `abandoned`, `superseded`) before checking dates. A task completed today is exactly what a retrospective query wants — "what did I do today?" should surface it. The status filter kills it before the date check runs.

The scorer already handles this. `_STATUS_PENALTIES` (line 48) applies `-0.15` for `completed`/`done` and `-0.25` for `cancelled`/`archived`/`abandoned`/`superseded`. That's the right mechanism — deprioritize, don't delete.

### Problem B: Overdue tasks fall outside forward-looking date ranges

`_resolve_temporal_query()` (line 575) maps "what do I need to do" to a forward-looking range (e.g. today onward via domain keywords). A task with `deadline: 2026-03-15` queried on March 16 falls outside this range. The task is incomplete and urgent, but invisible because its date is in the past.

### Problem C: No overdue concept in retrieval

There is no mechanism to surface active nodes whose deadline/due date has passed. These are the most urgent items in the graph, but `_get_nodes_in_date_range()` only matches nodes within the resolved date range. An overdue task doesn't fall in any forward-looking range, so it's never returned.

## Solution

Two changes, both in `mentor_agent.py`. No new files. No query intent classification.

### Change 1: Remove the status filter from `_get_nodes_in_date_range()`

Delete the `_INACTIVE_STATUSES` check (lines 639-641). Let every node with a matching date through. The scorer's `_STATUS_PENALTIES` already deprioritizes completed/cancelled nodes — that's the correct layer for this. The date range function's job is to find nodes with matching dates, not to judge whether they're worth showing.

This fixes Problem A directly: completed tasks dated today will be returned and scored like any other temporal match.

### Change 2: Add an overdue sweep in `get_context()`

After the existing temporal resolution (line 690-694), add a second pass: scan for active nodes with a `due` or `deadline` date before today. These are overdue items. Inject them into `temporal_node_ids` so they flow through the existing scoring and assembly pipeline.

```python
# After line 694 (existing temporal resolution)
overdue_nodes = _get_overdue_nodes(self.graph, today)
temporal_node_ids |= {n["id"] for n in overdue_nodes}
```

New function:

```python
def _get_overdue_nodes(graph, today: date) -> list[dict]:
    """Find active nodes with due/deadline in the past."""
    _ACTIVE_STATUSES = {"active", "pending", "todo", "in_progress", "planning", "blocked", ""}
    overdue = []
    for node in graph.get_all_nodes():
        status = str(node.get("status", "") or "").lower()
        if status not in _ACTIVE_STATUSES:
            continue
        for field in ("due", "deadline"):
            val = node.get(field)
            if not val:
                continue
            try:
                if isinstance(val, (date, datetime)):
                    node_date = val if isinstance(val, date) else val.date()
                else:
                    node_date = datetime.fromisoformat(str(val).split("T")[0]).date()
            except (ValueError, TypeError):
                continue
            if node_date < today:
                overdue.append(node)
                break
    return overdue
```

Key decisions:

- **Only `due` and `deadline` fields, not `date`.** A `date` field in the past just means the node happened in the past (e.g. an experience, a daily log). That's not overdue. Only `due`/`deadline` carry the semantics of "should have been done by now."
- **Whitelist active statuses rather than blacklist inactive ones.** This is the opposite of `_INACTIVE_STATUSES`. We want only genuinely incomplete nodes — if someone adds a new status like `on_hold`, it won't accidentally surface as overdue.
- **Overdue nodes always inject, regardless of whether `_resolve_temporal_query()` found a date range.** A query like "what should I focus on" has no temporal phrase but absolutely needs to see overdue items. The overdue sweep runs unconditionally.

This fixes Problems B and C: overdue tasks are surfaced via `temporal_node_ids` and get the `0.3` temporal boost in scoring (line 744), plus injection into results if not already present (lines 759-770).

## Implementation Detail

### `_get_nodes_in_date_range()` — remove status filter

Before:
```python
def _get_nodes_in_date_range(graph, start: date, end: date) -> list[dict]:
    """Scan all nodes for matching date/due/deadline fields within range."""
    matches = []
    for node in graph.get_all_nodes():
        status = str(node.get("status", "") or "").lower()
        if status in _INACTIVE_STATUSES:
            continue
        for field in ("date", "due", "deadline"):
```

After:
```python
def _get_nodes_in_date_range(graph, start: date, end: date) -> list[dict]:
    """Scan all nodes for matching date/due/deadline fields within range."""
    matches = []
    for node in graph.get_all_nodes():
        for field in ("date", "due", "deadline"):
```

`_INACTIVE_STATUSES` can be removed entirely if nothing else references it. Check before deleting.

### `get_context()` — add overdue injection

Insert after line 694 (`logger.debug(...)` for temporal resolution), before Step 1 (domain classification):

```python
# Overdue sweep — active nodes past their deadline always surface
overdue_nodes = _get_overdue_nodes(self.graph, today)
temporal_node_ids |= {n["id"] for n in overdue_nodes}
if overdue_nodes:
    logger.debug(f"Overdue nodes: {len(overdue_nodes)}")
```

This works even when `date_range` is `None` because `temporal_node_ids` is initialized as an empty set on line 689.

### `_get_overdue_nodes()` — new function

Place after `_get_nodes_in_date_range()` (after line 656). See implementation above.

## Files Changed

| File | Change |
|------|--------|
| `backend/mentor_agent.py` | Remove status filter from `_get_nodes_in_date_range()`, add `scheduled_for` to date field loops, add `_get_overdue_nodes()`, inject overdue nodes in `get_context()`, apply status penalties to temporal injection score |

No new files. No changes to other services.

## Edge Cases

- **Hundreds of overdue nodes:** Unlikely in practice — most vaults have <10 overdue items. If it becomes a problem, cap at 10 sorted by recency of deadline. Not worth adding now.
- **Node with `due` in the past and status `completed`:** `_get_overdue_nodes()` skips it (status not in active whitelist). `_get_nodes_in_date_range()` will return it if the date falls in the query range, and the scorer will apply the `-0.15` penalty. Correct behavior.
- **Node with `date` in the past and status `completed`:** Same — returned by `_get_nodes_in_date_range()` if date matches, deprioritized by scorer. "What did I do today?" with a completed task dated today now works.
- **Node with both `due` and `date` fields:** `_get_nodes_in_date_range()` matches on the first field that falls in range. `_get_overdue_nodes()` checks `due` and `deadline` only. No double-counting because both use `temporal_node_ids` (a set).
- **No overdue nodes exist:** `_get_overdue_nodes()` returns empty list. No effect on pipeline.
- **Overdue node already in semantic search results:** Gets `temporal_boost = 0.3` added to its score (line 744). Not injected twice — the injection block (lines 759-770) checks `result_ids`.
- **`_INACTIVE_STATUSES` removal side effects:** Grep for references before deleting. If only used in `_get_nodes_in_date_range()`, safe to remove.

## Additional Issues (from audit)

Beyond the three core problems, an audit of the temporal system found further issues. These are documented here for awareness — some should be folded into this fix, others are separate work.

### Should fix in this pass

**D. `scheduled_for` field ignored in temporal retrieval**
`_get_nodes_in_date_range()` only checks `("date", "due", "deadline")` but the schema defines `scheduled_for` (used by tasks like the ferry booking) and `renewal_date` (subscriptions). Fix: add both fields to the loop in `_get_nodes_in_date_range()` and `_get_overdue_nodes()` (only `scheduled_for` for overdue — a renewal date isn't "overdue" in the same sense). Also add to the pre-computed temporal facts header (line 824) which has the same hardcoded field list.

**H. Temporal injection bypasses status penalties**
Temporal nodes are injected with a fixed score of `0.3` (line 765), bypassing `_STATUS_PENALTIES`. Semantic results get penalized for completed/cancelled status but temporal results don't. Fix: apply `_STATUS_PENALTIES` to the `0.3` base score when injecting temporal nodes.

### Should fix separately (not blockers)

**E. Common temporal phrases unsupported**
`_resolve_temporal_query()` has no handlers for "last week", "last month", "next month", "coming up", or "last N days". These silently return None and fall through to semantic search only. Worth adding but doesn't block the core fix.

**F. Day-name queries skip today**
If today is Monday and user asks "what's on monday", `days_ahead == 0` is forced to 7 — resolving to next Monday. Should resolve to today.

**G. Day names in node titles trigger false temporal matches**
Substring check `if day_name in q` matches any occurrence. "Tell me about monday-motivation-habit" incorrectly triggers date logic. Needs word-boundary matching.

**I. Temporal query false positives from substring matching**
Words like "next" and "last" in regular queries ("when's the next run") can incorrectly trigger temporal phrase logic. Same word-boundary fix as G.

## Acceptance Criteria

1. **Completed task today is visible:** Create a task with `date: today`, `status: completed`. Query "what did I do today?" — task appears in context.
2. **Overdue task surfaces for forward queries:** Create a task with `deadline: yesterday`, `status: pending`. Query "what do I need to do?" — task appears in context.
3. **Overdue task surfaces without temporal phrase:** Same overdue task. Query "what should I focus on?" (no date phrase) — task still appears via overdue sweep.
4. **Completed overdue task does not surface as overdue:** Task with `deadline: yesterday`, `status: completed`. Should not appear in overdue results.
5. **Old `date`-field nodes don't surface as overdue:** A daily log with `date: 2025-01-01` should not appear in overdue sweep (only `due`/`deadline` trigger overdue).
6. **Cancelled/archived nodes with today's date are deprioritized, not hidden:** Cancelled task dated today appears in "what happened today?" results but ranks lower than active nodes.
7. **`scheduled_for` is queryable:** Task with `scheduled_for: tomorrow` surfaces for "what's coming up tomorrow?".
8. **Temporal injection respects status penalties:** Completed node injected via temporal path has lower score than active node injected the same way.
9. **No regressions:** All existing retrieval tests pass. Temporal resolution for "today", "tomorrow", "this week", etc. still works.
