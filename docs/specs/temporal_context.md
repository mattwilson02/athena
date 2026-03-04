# Spec: Pre-Computed Temporal Context

**Epoch:** E1 Ground Truth — Item 4
**Files:** `backend/mentor_agent.py`

---

## What

Inject computed date-relative facts into node context strings so Claude reads "in 69 days" instead of computing it (and getting it wrong).

---

## Problem

Claude was asked "how long until the ultra?" and said "72 days" when the actual answer was ~69. LLMs are bad at date arithmetic. Fix: compute the facts in Python and inject them.

---

## Implementation

Add `_format_date_context(node)` that:
1. Finds all date-bearing fields (`date`, `due`, `deadline`, `created`, `updated`)
2. For each, computes the delta from today
3. Returns a human-readable string like `Date: 2026-05-09 (in 70 days)` or `Created: 2026-02-25 (3 days ago)`

Call it from `_node_context_full()` and `_node_context_summary()` to enrich the context string.

Also inject `Today is {weekday}, {month} {day}, {year}.` into the system prompt header.

---

## Tests

1. Future date → "in N days"
2. Past date → "N days ago"
3. Today → "today"
4. Yesterday → "yesterday (1 day ago)"
5. No date fields → empty string
