# Spec: Cascade Engine

**Epoch:** E2 — Conversations That Stick
**Item:** 3 of 6
**Files:** `backend/services/vault_service.py`, `backend/services/chat_service.py`, `backend/vector_search.py`, `backend/routes/vault_routes.py`, `frontend/src/lib/GraphUpdateCard.svelte`, `frontend/src/lib/api.js`
**Status:** Specced

---

## Problem

When a user accepts a graph update (create, update, or link), Athena writes the change and moves on. But graph changes often have ripple effects:

1. **Cancel a project** → linked tasks are now stale (still status: active)
2. **Move an event date** → a task "Book flights for [event]" has a deadline that's now wrong
3. **Create a new node** → an existing unlinked node mentions the same topic but was never connected

Currently, the user has to notice these inconsistencies themselves and manually fix them. The graph silently goes stale.

## Approach

After a graph update is accepted and written to vault, run a **cascade check** that finds affected nodes using two strategies:

1. **Graph cascade** — traverse 1-hop neighbors of the changed node, check for stale references
2. **Semantic cascade** — search by title + change summary, find unlinked-but-related nodes

Return cascade proposals as additional graph update cards that the user can accept or dismiss, just like regular graph updates.

## Detailed Design

### New method: `VaultService.cascade_check()`

```python
def cascade_check(self, node_id: str, changes: dict) -> list[dict]:
    """Find nodes affected by a change. Returns cascade proposals."""
```

**Input:**
- `node_id` — the node that was just created/updated
- `changes` — what changed (for creates: the full node data; for updates: the changes dict)

**Output:** list of cascade proposals, each shaped like a graph update:
```python
{
    "action": "update",          # or "link"
    "node_id": "book-rome-flights",
    "title": "Book Rome Flights",
    "type": "task",
    "changes": {"frontmatter": {"status": "cancelled"}},  # for updates
    "reason": "Linked to 'Italy Trip' which was just cancelled",
    "confidence": "graph",       # or "semantic"
}
```

### Step 1: Graph cascade (1-hop neighbors)

```
Get all direct neighbors of node_id (both directions)
For each neighbor:
  - If the changed node's status changed to cancelled/completed:
    - If neighbor is a task with status active/pending → propose status update
    - If neighbor is an event in the future → flag as "may need review"
  - If the changed node's date/deadline changed:
    - If neighbor is a task referencing the old date → flag date review
  - If this was a create:
    - Skip (no stale references possible from graph neighbors)
```

Graph cascade proposals have `confidence: "graph"` — these are high-confidence because they're explicitly connected.

### Step 2: Semantic cascade (unlinked but related)

```
Build search query from: node title + change summary (e.g. "Italy Trip cancelled")
Run vector_index.search(query, n=8)
Filter out:
  - The node itself
  - Nodes already found via graph cascade
  - Nodes already linked (1-hop neighbors)
For each remaining result with score < 0.6:
  - Propose a "link" action connecting the result to the changed node
  - ALSO apply the same staleness checks as graph cascade (Step 1):
    - If the changed node's status → cancelled/completed and the result
      still references the topic as active → propose update + link
    - If the changed node's date changed and the result references
      the old date → propose date review + link
  - If no staleness detected, propose link only
```

This means semantic cascade can return **two proposals for one node** — a link and an update — bundled as a single card with both actions. The user sees: "Found 'Book Flights for Ethan Visit' — not linked, and still marked active. Propose: link + set status to cancelled."

Semantic cascade proposals have `confidence: "semantic"` — lower confidence, shown with softer UI treatment.

### Step 3: Limit and rank

- Max 5 cascade proposals total (3 graph + 2 semantic, adjustable)
- Graph proposals first, then semantic
- If no proposals, return empty list (no UI noise)

### Integration: vault_routes.py

Both `/api/vault/write` and `/api/vault/update` already return results. Add `cascade_proposals` to the response:

```python
@vault_bp.route("/api/vault/write", methods=["POST"])
def vault_write():
    result = vault_service.write(data)
    if result.get("ok"):
        proposals = vault_service.cascade_check(
            data.get("node_id"), data
        )
        if proposals:
            result["cascade_proposals"] = proposals
    return jsonify(result)
```

Same pattern for `/api/vault/update`.

This keeps it in the existing request/response cycle — no new endpoints needed.

### Integration: Frontend (GraphUpdateCard.svelte)

After `acceptCreate()` or `acceptUpdate()` succeeds, check the response for `cascade_proposals`. If present, emit them upward so ChatView can render them as new GraphUpdateCards.

```javascript
// In acceptCreate():
const result = await writeNode({...});
if (result.cascade_proposals?.length) {
    onCascade(result.cascade_proposals);
}
```

New prop: `onCascade = () => {}` — callback that passes cascade proposals up to ChatView.

### Integration: ChatView.svelte

When `onCascade` fires, append the cascade proposals to the message's `graph_updates` array. They render as normal GraphUpdateCards but with a visual indicator for confidence level:

- `confidence: "graph"` — normal card styling, "Cascade" badge
- `confidence: "semantic"` — softer border/background, "Suggested" badge

### Integration: Telegram (chat_service.py)

In `_confirm_pending()`, after writing to vault, run cascade check. If proposals exist, queue them as additional pending updates:

```python
if self.vault_service:
    proposals = self.vault_service.cascade_check(node_id, update)
    if proposals:
        existing = self.chat_store.get_pending_updates(session_id)
        self.chat_store.set_pending_updates(session_id, existing + proposals)
```

This way Telegram users see cascade proposals as follow-up confirm/dismiss prompts.

## What Does NOT Change

- `_parse_graph_updates()` — cascade proposals use the same shape as regular updates
- Graph update JSON schema — identical structure, just with extra `reason` and `confidence` fields
- `writeNode()` / `updateNode()` API functions — same calls, just read extra fields from response
- Chat streaming — cascade is triggered on accept, not during the AI response

## Status Change Inference

The trickiest part is deciding *what* to propose for graph-cascaded neighbors. Keep it simple with a rule table:

| Parent change | Neighbor type | Neighbor status | Proposal |
|---|---|---|---|
| status → cancelled | task | active/pending | status → cancelled |
| status → cancelled | event | any | flag for review |
| status → completed | task (part_of) | active/pending | flag for review |
| date/deadline changed | task | any | flag date review |
| deleted/archived | any linked | any | flag orphan risk |

"Flag for review" means: propose an update with `changes: {}` and a `reason` explaining what happened. The user decides what to do. Don't over-automate — just surface the ripple.

## Testing

Manual testing:
1. Create a project with 2 linked tasks → cancel the project → cascade proposes cancelling the tasks
2. Create a node about "Rome" → cascade finds existing "Italy Trip" via semantic search → proposes link
3. Update a node's deadline → cascade flags linked tasks for date review
4. Accept a create with no related nodes → no cascade proposals (no noise)
5. Telegram: confirm a create → cascade proposals appear as next pending items

## Risks

- **Over-proposing** — every accept spawns 3 cascade cards = annoying. Mitigation: max 5 proposals, high thresholds, no cascade-of-cascades (cascade proposals themselves don't trigger further cascades).
- **Latency** — cascade adds a vector search + graph traversal to every accept. Mitigation: both are fast (ChromaDB local, NetworkX in-memory). If slow, make cascade async and return proposals in a follow-up SSE event.
- **Stale proposals** — user accepts cascade proposal A, which changes the validity of proposal B. Mitigation: each proposal is independently valid. Worst case, a proposal fails on write (node already updated) — handle gracefully.
