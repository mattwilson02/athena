# Spec: Supersession Logic

**Epoch:** E2 — Conversations That Stick
**Item:** 4 of 6
**Files:** `backend/mentor_agent.py`, `backend/services/chat_service.py`
**Status:** Specced

---

## Problem

When the user changes plans ("Actually I'm doing a half marathon instead of the ultra"), Athena should:
1. Create the new thing (half marathon)
2. Update the old thing (ultra) with `status: superseded` + `superseded_by: half-marathon`

Currently, the GRAPH_INSTRUCTIONS mention supersession on one line but:
- Don't tell the AI to set `superseded_by` on the old node
- Don't validate that both create + update appear in the same batch
- Don't show a negative example of what bad supersession looks like

## Approach

Two changes:
1. **Prompt strengthening** — expand the supersession trigger in GRAPH_INSTRUCTIONS with explicit field instructions and a negative example
2. **Batch validation** — in `_dedup_check` or a new post-processing step, detect when a create + update pair looks like supersession and ensure `superseded_by` is set

## Detailed Design

### 1. Prompt changes (mentor_agent.py)

Update the trigger in section 1 (WHEN TO WRITE):

```
- "Actually, doing Y instead of X" → CREATE Y + UPDATE X: status → superseded, superseded_by → Y's node_id
```

Add a supersession-specific negative example in section 4 (COMMON MISTAKES):

```
WRONG: User says "Actually I'm doing a half marathon instead of the ultra" → create half-marathon only
RIGHT: → create half-marathon + update ultra: {status: "superseded", superseded_by: "half-marathon"}

WRONG: User changes from plan A to plan B → update plan A content to describe plan B
RIGHT: → create plan B + update plan A with superseded status + link between them
```

Add `superseded_by` to the FORMAT section's frontmatter documentation:

```
"superseded_by": "Optional: node_id of the replacement (set automatically on supersession)"
```

### 2. Batch validation (chat_service.py)

After `_dedup_check`, add a `_validate_supersession` post-processing step:

```python
def _validate_supersession(self, updates: list[dict]) -> list[dict]:
    """If a batch contains a create + an update with status: superseded,
    ensure the update has superseded_by pointing to the new node."""

    creates = {u["node_id"]: u for u in updates if u.get("action") == "create"}

    for update in updates:
        if update.get("action") != "update":
            continue
        changes = update.get("changes", {})
        fm = changes.get("frontmatter", {})
        if fm.get("status") != "superseded":
            continue

        # Has superseded_by already? Skip.
        if fm.get("superseded_by"):
            continue

        # Find the matching create in this batch
        # Heuristic: if there's exactly one create, it's probably the replacement
        if len(creates) == 1:
            replacement_id = list(creates.keys())[0]
            fm["superseded_by"] = replacement_id
        # If multiple creates, check if any have an edge pointing to the superseded node
        else:
            for cid, create in creates.items():
                edges = create.get("edges", [])
                if any(e.get("target") == update.get("node_id") for e in edges):
                    fm["superseded_by"] = cid
                    break

    return updates
```

This runs in the chat service pipeline, after the AI generates updates but before they're sent to the frontend.

### 3. Cascade integration

The cascade engine already handles `status: superseded` as a terminal status (it's not in the set, but we should add it). When a node is superseded, cascade should propose updates to linked nodes just like cancellation.

Add "superseded" to `_INACTIVE_STATUSES` in mentor_agent.py and to `status_terminal` check in vault_service.py cascade_check.

## What Does NOT Change

- Vault file format — `superseded_by` is just another frontmatter field
- Graph update JSON schema — no structural changes
- Frontend — superseded nodes display like any other status update
- NodeDetail — already shows all frontmatter fields including `superseded_by`

## Testing

1. "Actually I'm doing a half marathon instead of the ultra" → create half-marathon + update ultra with `status: superseded, superseded_by: half-marathon`
2. "I'm switching from Python to Rust for the project" → create new project + supersede old one
3. Both cards appear in the same batch (not just the create)
4. Accepting the supersession update → cascade fires for linked tasks

## Risks

- **AI forgets superseded_by** — mitigated by batch validation filling it in
- **False positives** — batch validation only activates when status is explicitly "superseded", low risk of false matches
- **Multiple creates** — heuristic uses edge targeting to disambiguate, falls back to no-op if unclear
