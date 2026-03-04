# Spec: Status-Aware Retrieval Scoring

**Epoch:** E1 Ground Truth — Item 3
**Files:** `backend/mentor_agent.py`

---

## What

Deprioritize completed, cancelled, and archived nodes in retrieval scoring so that active, actionable context surfaces first.

---

## Discovery Findings

**Current scoring in `get_context()`:**
- `semantic_score` — base score from ChromaDB distance
- `domain_boost` — +0.2/+0.1 for matching domain
- `recency_boost` — up to +0.15 for time-sensitive types
- `centrality_boost` — up to +0.08 (logarithmic)
- `temporal_boost` — +0.3 for date-range matches
- `session_boost` — +0.05 per conversation topic match

No status-awareness exists. A completed task from 3 months ago scores the same as an active one.

**Status values in the vault:**
Scanning nodes reveals these statuses: `active`, `planned`, `completed`, `parked`, `blocked`, `quitting`, `confirmed`, `contingency`.

---

## Implementation

Add a `status_penalty` in the scoring loop, applied after all boosts:

```python
# Status penalty — deprioritize resolved nodes
status = result.get("status", "") or ""
status_lower = str(status).lower()
status_penalty = 0.0
if status_lower in ("completed", "done"):
    status_penalty = -0.15
elif status_lower in ("cancelled", "archived", "abandoned"):
    status_penalty = -0.25
elif status_lower == "parked":
    status_penalty = -0.10
```

Add `status_penalty` to the `total` calculation.

Also need to fetch the node's status — the `result` dict from ChromaDB search may not include `status`. Fall back to `graph.get_node()` which has full frontmatter.

---

## Tests

In `backend/tests/test_mentor_agent.py`:

1. **Completed node scores lower** — same semantic score, completed vs active → completed ranks lower
2. **Cancelled node scores even lower** — penalty is larger than completed
3. **Active node no penalty** — status=active gets 0.0 penalty
4. **No status field no penalty** — missing status treated as active
