# Epoch 1: Ground Truth

> Zero stale nodes. The graph is accurate, current, and trustworthy.

**Vision Stage:** Stage 1 (Foundation)
**Appetite:** 3 weeks (March 1 – March 21, 2026)
**Goal:** No stale nodes in the vault. Every node reflects reality.
**Status:** Build complete. All 6 items implemented. 217 tests passing. Audit returns zero issues.

---

## Press Release

After this epoch, every node in Athena's vault is accurate and current. Stale tasks with past dates are resolved — completed, cancelled, or rescheduled. Orphan nodes are connected or removed. When you ask about your plans, you only see what's actually active. When you ask "how many days until the ultra," you get the right number. The graph is a reliable model of your life, not a graveyard of outdated intentions.

---

## Problem Statement

The graph is Athena's memory. Stale nodes are corrupted memories. Right now, the vault has:
- Tasks with past dates still marked `active` or `planned`
- Cancelled/superseded plans ranking equally with current ones in retrieval
- Orphan nodes disconnected from the graph
- Broken wikilinks pointing to non-existent nodes
- Claude fumbling dates and arithmetic because context doesn't pre-compute them
- Concurrent write crashes when accepting updates quickly

You can't build cascade intelligence on a graph full of stale data. Fix the data first.

---

## In Scope

### 1. Vault audit endpoint
**Files:** `backend/routes/vault_routes.py`, new `backend/services/audit_service.py`

`POST /api/vault/audit` scans every node for known issues:
- **Stale statuses** — tasks/events with date in the past + status still `active`/`planned`
- **Orphan nodes** — zero edges, disconnected from graph
- **Broken wikilinks** — `[[references]]` to non-existent nodes
- **Type mismatches** — nodes in wrong folder for their type

Returns structured report: count by issue type, list of affected node IDs.

**What success looks like:** Hit the endpoint, get a JSON report showing exactly which nodes need attention and why.

### 2. Vault data triage — fix every stale node
**Files:** Vault markdown files

This is the core deliverable. Run the audit, then fix every issue:
- **Stale statuses** — review each one. Mark completed if done, cancelled if abandoned, update date if rescheduled.
- **Orphan nodes** — connect to relevant nodes or remove if genuinely useless.
- **Broken wikilinks** — fix target or remove the link.
- Use Athena-assisted batch review where possible — feed clusters to the AI, review proposals in batches.
- Process in waves: Planning nodes first (most volatile), then People, then Self.

**What success looks like:** `POST /api/vault/audit` returns zero stale statuses, zero orphans, zero broken links.

### 3. Status-aware retrieval scoring
**Files:** `backend/mentor_agent.py`

Add status-based scoring modifier in `get_context()`:
- `active` / `planned` / `in_progress` → no penalty (default)
- `completed` / `done` → -0.15 penalty
- `cancelled` / `superseded` / `descoped` → -0.25 penalty

Status read from node frontmatter. No status field = no penalty.

**What success looks like:** Ask about goals → active goals rank higher than completed/cancelled ones. Stale tasks don't waste context budget.

### 4. Pre-computed temporal context
**Files:** `backend/mentor_agent.py`

When temporal resolution finds a date range and matching nodes, inject computed facts into context so Claude doesn't need to do arithmetic:
- "TODAY is Saturday, 28 February 2026"
- "This weekend: Saturday 28 Feb – Sunday 1 Mar"
- "Lake District Ultra — May 9, 2026 (in 69 days)"

Claude gets the answer pre-computed. No arithmetic failures.

**What success looks like:** Ask "how many days until the ultra" → correct number. Ask "what day is it" → correct every time.

### 5. Concurrent write safety
**Files:** `backend/services/vault_service.py`

Prevent crashes from rapid graph update accepts:
- Threading lock around vault write + rebuild operations
- Only one rebuild at a time — subsequent writes queue behind it
- Consider debounced rebuild: write file immediately, coalesce rebuilds

**What success looks like:** Accept 10 graph updates in 2 seconds → no crash, no data corruption.

### 6. Update docs and status
- Update TODO_NEXT.md — mark completed items, add discoveries
- Update STATUS.md — reflect current state
- Update CLAUDE.md if architecture changed

---

## Out of Scope

- **Cascade updates** — Epoch 2. Fix the data first, then build cascade on clean foundations.
- **Conflict detection** — Epoch 2. Needs accurate graph to detect real conflicts.
- **Personality enforcement (SOUL.md changes)** — Epoch 2. Needs conflict detection to work.
- **UI improvements** (activity feed, node click, graph update descriptions) — Epoch 2 or 3.
- **Telegram restart** — Parked until needed.
- **What-if engine, tradeoff engine, adaptive modes** — Stage 1 completion, not this epoch.

---

## Stretch (cut if time runs short)

- **Status-aware ChromaDB metadata filtering** — exclude cancelled nodes at search time, not just scoring
- **Automated stale status resolution** — script that bulk-marks past-date tasks as `completed` with user confirmation

---

## Spec

### Pre-computed temporal context (detail)

In `get_context()`, after temporal resolution and before context assembly:

```python
# Build temporal context header
temporal_header_parts = []
today = date.today()
temporal_header_parts.append(f"TODAY: {today.strftime('%A, %d %B %Y')}")

if date_range:
    start, end = date_range
    if start == end:
        temporal_header_parts.append(f"Query date: {start.strftime('%A %d %B %Y')}")
    else:
        temporal_header_parts.append(
            f"Query range: {start.strftime('%A %d %b')} – {end.strftime('%A %d %b %Y')}"
        )

# For each temporal node, compute distance from today
for node in temporal_matches:
    node_date = ...  # parse from frontmatter
    delta = (node_date - today).days
    if delta == 0:
        distance_str = "today"
    elif delta == 1:
        distance_str = "tomorrow"
    elif delta == -1:
        distance_str = "yesterday"
    elif delta > 0:
        distance_str = f"in {delta} days"
    else:
        distance_str = f"{abs(delta)} days ago"
    temporal_header_parts.append(
        f"• {node['title']} — {node_date.strftime('%A %d %b')} ({distance_str})"
    )
```

Inject `temporal_header` at the TOP of the context string, before node details. This gives Claude pre-computed facts it can reference directly.

### Status-aware scoring (detail)

```python
_STATUS_PENALTIES = {
    "completed": -0.15,
    "done": -0.15,
    "cancelled": -0.25,
    "superseded": -0.25,
    "descoped": -0.25,
}

# In scoring loop:
node = self.graph.get_node(result["id"])
if node:
    status = node.get("status", "")
    status_penalty = _STATUS_PENALTIES.get(status, 0.0)
    total += status_penalty
```

### Concurrent write safety (detail)

```python
import threading

class VaultService:
    def __init__(self, ...):
        ...
        self._write_lock = threading.Lock()

    def write_node(self, ...):
        with self._write_lock:
            # file write + rebuild
            ...
```

Consider debounced rebuild: write file immediately (fast), but coalesce the expensive rebuild with a 500ms timer. Multiple writes within 500ms trigger only one rebuild.

### Vault audit (detail)

```python
def audit_vault(graph, schema) -> dict:
    issues = {
        "stale_status": [],    # past date + active status
        "orphans": [],         # zero edges
        "broken_links": [],    # wikilinks to missing nodes
        "type_mismatch": [],   # node in wrong folder
    }
    today = date.today()
    all_ids = set(graph.get_all_node_ids())

    for node in graph.get_all_nodes():
        # Stale status check
        node_date = parse_date(node.get("date") or node.get("due") or node.get("deadline"))
        status = node.get("status", "")
        if node_date and node_date < today and status in ("active", "planned"):
            issues["stale_status"].append(node["id"])

        # Orphan check
        if graph.get_degree(node["id"]) == 0:
            issues["orphans"].append(node["id"])

        # Broken wikilinks
        for edge in graph.get_edges_from(node["id"]):
            if edge["target"] not in all_ids:
                issues["broken_links"].append({
                    "source": node["id"],
                    "target": edge["target"],
                })

    return {
        "total_nodes": len(all_ids),
        "issues": issues,
        "summary": {k: len(v) for k, v in issues.items()},
    }
```

---

## Verification

1. **Zero stale nodes:** `POST /api/vault/audit` returns zero issues across all categories
2. **Tests pass:** `cd backend && python3 -m pytest tests/ -v` — all green
3. **Date accuracy:** Ask "what day is it" 5 times → correct every time
4. **Date math:** Ask "how many days until [event]" → correct number
5. **Status scoring:** Ask about goals → active goals rank higher than completed/cancelled
6. **Write safety:** Accept 10 updates rapidly → no crash, all applied
7. **Behavioral regression:** Normal chat still feels good, graph updates still land
8. **Graph quality audit:** Sample 10 random nodes — all accurate, connections complete

---

## Risk

- **Pre-computed context increases token usage** — temporal header adds ~200-400 chars. Acceptable given 3000 token budget.
- **Status scoring might over-penalize** — a completed goal is still relevant when discussing progress. Tune penalties during validation.
- **Write lock could cause slowness** — if rebuilds take 2+ seconds, queued writes feel laggy. Monitor and consider debounce.
