# Sprint 1: Node Lifecycle & Graph Management

## Overview

The core chat-to-graph pipeline works: Athena proposes nodes, users accept them one at a time, and the vault grows. But the graph management workflow has critical gaps that create friction as the vault scales. You can't delete nodes, can't create nodes without chatting, can't batch-accept proposals, and wikilinks in chat are dead text. This sprint closes those gaps so the vault stays clean and the UI feels connected.

**Goal:** Complete the node lifecycle (create, read, update, **delete**) and reduce friction in the graph update acceptance flow.

## What Exists

- **Vault CRUD (minus D):** `VaultService.write()` and `VaultService.update()` handle creation and mutation. `_backup/` directory exists for archived nodes. Import flow (`import_proposals`, `import_accept`) already reads from `_backup/`.
- **Node editing:** `NodeDetail.svelte` has full inline editing (title, content, tags, frontmatter) via `updateNode()`.
- **Graph update cards:** `GraphUpdateCard.svelte` handles accept/dismiss/merge for individual proposals. Supports create, update, and link actions with dedup warnings and suggested links.
- **Wikilink rendering:** `format.js` converts `[[wikilinks]]` to `<span class="fmt-wikilink">` — styled but not interactive.
- **Schema-driven types:** All valid types, folders, and frontmatter fields derived from `vault/_meta/schema.md` at boot. `getSchema()` exposes this to the frontend.
- **API layer:** `api.js` has `writeNode()`, `updateNode()`, `getSchema()` — no `deleteNode()`.

## Architectural Decisions

1. **Soft delete via archival.** Node deletion moves the file to `vault/_backup/` rather than removing it. This is consistent with the existing `_backup/` directory and `import_proposals` flow, which already knows how to scan and re-import archived nodes. Hard delete is never exposed.

2. **Graph + vector cleanup on delete.** After archival, call `refresh_fn` (or `rebuild_fn` as fallback) to remove the node from the NetworkX graph, then call `vector_index.delete_one(node_id)` to remove from ChromaDB. The existing `VectorIndex.delete_one()` method already exists.

3. **Dangling edge tolerance.** When a node is deleted, its wikilinks in other files become dangling `[[references]]`. Do NOT walk the vault to clean these up — the vault parser already tolerates dangling links (they just don't produce edges). The `vault/audit` endpoint can surface broken links later.

4. **Lock discipline.** All vault mutations go through `VaultService` under `self.lock` (RLock). The delete method follows the same pattern as `_write_locked` and `_update_locked`.

5. **Frontend patterns.** All new components use Svelte 5 runes (`$state()`, `$props()`, `$effect()`, `$derived()`). No legacy `export let` or `$:` syntax. Colors from `colors.js`. API calls through `api.js` fetch wrappers.

## Tasks

### Task 1: Backend — Node deletion endpoint

**Objective:** Add a `VaultService.delete()` method and a `DELETE /api/vault/node/:id` route that archives a node to `_backup/`.

**Files to modify:**
- `backend/services/vault_service.py` — add `delete()` and `_delete_locked()` methods
- `backend/routes/vault_routes.py` — add `DELETE /api/vault/node/<node_id>` route
- `backend/tests/test_vault_service.py` — add deletion tests
- `backend/tests/test_routes.py` — add route tests

**Requirements:**
- `VaultService.delete(node_id)` must:
  - Validate `node_id` exists in the graph (return 404 if not)
  - Resolve the node's `filepath` from the graph
  - Move the file to `vault/_backup/{original_relative_path}` (preserve subdirectory structure, e.g., `Self/Goals/learn-piano.md` → `_backup/Self/Goals/learn-piano.md`)
  - Create the `_backup` subdirectory if it doesn't exist
  - Call `rebuild_fn()` to remove the node from the graph (no `refresh_fn` for deletes — a full rebuild is safer since edges referencing the deleted node also need clearing)
  - Call `vector_index.delete_one(node_id)` to remove from ChromaDB
  - Return `{"ok": True, "node_id": "...", "archived_to": "...", "stats": {...}}`
- Must hold `self.lock` for the entire operation
- If the source file doesn't exist on disk, return 404
- Follow the `_sanitize_id()` pattern on the input `node_id`

**Patterns to follow:**
- `_write_locked()` / `_update_locked()` structure in `vault_service.py`
- Route pattern in `vault_routes.py` (blueprint, `current_app.config` access)
- `safe_resolve()` from `middleware/security.py` for path validation

**Acceptance criteria:**
- Node file moves to `_backup/` with directory structure preserved
- Node disappears from `GET /api/graph` after deletion
- Node disappears from `GET /api/search` after deletion
- Attempting to delete a non-existent node returns 404
- Previously deleted nodes appear in `POST /api/vault/import` proposals (existing flow)

**Test cases:**
- `test_delete_moves_to_backup` — file exists in `_backup/`, not in original location
- `test_delete_updates_graph` — node no longer in `graph.get_node()`
- `test_delete_nonexistent_returns_404`
- `test_delete_route_returns_ok` — HTTP integration test
- `test_delete_preserves_subdirectory_structure` — backup path mirrors vault path

### Task 2: Frontend — Node deletion in NodeDetail

**Objective:** Add a delete button to `NodeDetail.svelte` with a confirmation step, wired to the new delete endpoint.

**Files to modify:**
- `frontend/src/lib/api.js` — add `deleteNode()` function
- `frontend/src/lib/NodeDetail.svelte` — add delete button and confirmation UI
- `frontend/src/App.svelte` — handle post-deletion state (close panel, refresh nodeMap)

**Requirements:**
- `deleteNode(nodeId)` in `api.js`: `DELETE /api/vault/node/{nodeId}` via `fetchJSON`
- Delete button appears in the `header-actions` div, next to Edit, styled as a subtle danger action (not prominent — edit is more common)
- Clicking delete shows inline confirmation: "Delete [title]?" with Confirm/Cancel buttons (same pattern as session delete in `Sidebar.svelte`)
- On successful deletion:
  - Close the NodeDetail panel (`onClose()`)
  - Call a new `onDelete(nodeId)` callback prop so App.svelte can refresh the node map and graph
- Delete button is hidden during edit mode
- No delete during save (disabled state)

**Patterns to follow:**
- `confirmDeleteId` pattern in `Sidebar.svelte` for inline confirmation
- `action-btn` CSS class from `NodeDetail.svelte` for button styling
- `fetchJSON` wrapper in `api.js`

**Acceptance criteria:**
- Delete button visible on node detail panel (not in edit mode)
- Single click shows confirmation, second click deletes
- Panel closes after deletion
- Graph view updates to remove the deleted node
- Cancel returns to normal state

**API response contract for `DELETE /api/vault/node/:id`:**
```json
{
  "ok": true,
  "node_id": "learn-piano",
  "archived_to": "_backup/Self/Goals/learn-piano.md",
  "stats": {
    "total_nodes": 42,
    "total_edges": 87
  }
}
```

### Task 3: Frontend — Manual node creation

**Objective:** Add a "Create Node" modal that lets users add nodes directly without going through chat. Triggered from the Sidebar.

**Files to create:**
- `frontend/src/lib/CreateNodeModal.svelte` — modal form component

**Files to modify:**
- `frontend/src/lib/Sidebar.svelte` — add "+" button in the Graph stats section header
- `frontend/src/App.svelte` — mount modal, wire callbacks

**Requirements:**
- Modal form fields:
  - **Title** (required, text input)
  - **Type** (required, dropdown populated from `schema.type_list`)
  - **Tags** (optional, comma-separated text input, split on submit)
  - **Content** (optional, textarea)
- Auto-generate `node_id` from title using the same slugification rules as `_sanitize_id()` (lowercase, replace spaces with hyphens, strip non-alphanumeric)
- Show the generated ID below the title field as preview (e.g., "ID: learn-to-cook")
- Domain auto-displays based on selected type (from schema)
- On submit, call `writeNode()` from `api.js` with the appropriate payload
- On success: close modal, refresh nodeMap, optionally navigate to the new node in graph view
- On error: show inline error message
- Close on Escape key or clicking backdrop
- Disable submit button while saving

**Patterns to follow:**
- `SearchModal.svelte` for modal structure (backdrop, close on Escape, portal-style overlay)
- `writeNode()` call pattern from `GraphUpdateCard.svelte` `acceptCreate()` method
- Svelte 5 runes for all state

**Acceptance criteria:**
- "+" button in sidebar Graph section opens the modal
- All 27 types available in dropdown, grouped or sorted by domain
- Created node appears in graph immediately
- Invalid/empty title shows validation feedback
- Modal closes on success or Escape

**Test cases (manual):**
- Create a goal node → appears in graph with correct type color
- Create a node with tags → tags visible in NodeDetail
- Submit with empty title → form shows error, doesn't submit
- Press Escape → modal closes without creating

### Task 4: Frontend — Batch accept graph updates

**Objective:** Add an "Accept All" button that accepts all pending graph update proposals in a single message at once, reducing friction when Athena proposes clusters of 5-10 nodes.

**Files to modify:**
- `frontend/src/lib/ChatView.svelte` — add Accept All button above graph update cards when multiple are pending

**Requirements:**
- "Accept All" button appears above the graph update cards when **2 or more** proposals in a message have `status === 'pending'`
- Clicking "Accept All" sequentially accepts each pending card (not parallel — vault writes must be sequential to avoid lock contention)
- Each card transitions through its normal `pending → writing → accepted` states as it's processed
- If any card fails, stop processing and leave remaining cards as pending (user can retry individually)
- Button shows progress: "Accepting 3/7..." during processing
- Button disappears once all are accepted or only 0-1 remain pending
- Cards with duplicates (`hasDuplicate`) are **skipped** by Accept All — they require explicit user choice (Create New vs Merge)
- Does NOT affect dismissed cards

**Patterns to follow:**
- `GraphUpdateCard.svelte` `accept()` method for the acceptance logic — call it on each card's component instance, or replicate the `acceptCreate`/`acceptUpdate`/`acceptLink` logic
- Svelte 5 runes for state tracking

**Acceptance criteria:**
- Button appears when 2+ non-duplicate proposals are pending
- All non-duplicate pending cards accepted in sequence
- Progress shown during batch accept
- Duplicate cards remain pending for manual resolution
- Button hidden when not applicable

### Task 5: Frontend — Clickable wikilinks

**Objective:** Make `[[wikilinks]]` in chat messages and node detail content clickable, navigating to the referenced node in the graph view.

**Files to modify:**
- `frontend/src/lib/format.js` — render wikilinks as clickable elements with data attributes instead of plain spans
- `frontend/src/lib/ChatView.svelte` — add click handler for wikilink elements (event delegation)
- `frontend/src/lib/NodeDetail.svelte` — add click handler for wikilinks in content

**Requirements:**
- In `format.js`, change the wikilink regex replacement from:
  `<span class="fmt-wikilink">$1</span>`
  to:
  `<a class="fmt-wikilink" data-node-id="$1" href="#">$1</a>`
  (Use `<a>` for accessibility — keyboard navigable, shows pointer cursor)
- The `data-node-id` value should be the raw wikilink text (which is the node ID)
- In `ChatView.svelte`, add a click event handler on the messages container (event delegation) that:
  - Checks if the clicked element has class `fmt-wikilink`
  - Extracts `data-node-id`
  - Calls `onNodeSelect(nodeId)` which navigates to graph view and selects the node
  - Calls `e.preventDefault()` to prevent the `#` navigation
- In `NodeDetail.svelte`, add the same delegated click handler on the `.content` div
- Wikilinks for nodes that don't exist in `nodeMap` should still be clickable (the graph view will just not find them — no special handling needed)

**Patterns to follow:**
- `onNodeSelect` callback already exists on both `ChatView` and `NodeDetail`
- Event delegation pattern (single handler on container, check `e.target`)

**Acceptance criteria:**
- Clicking a `[[wikilink]]` in a chat message switches to graph view and highlights the node
- Clicking a wikilink in node detail content navigates to that node
- Wikilinks show pointer cursor on hover
- Keyboard accessible (Tab to focus, Enter to activate)
- No regression in existing markdown formatting

## API Response Contracts

### `DELETE /api/vault/node/:id`

**Success (200):**
```json
{
  "ok": true,
  "node_id": "learn-piano",
  "archived_to": "_backup/Self/Goals/learn-piano.md",
  "stats": {
    "total_nodes": 42,
    "total_edges": 87
  }
}
```

**Not found (404):**
```json
{
  "error": "Node 'learn-piano' not found"
}
```

**Bad request (400):**
```json
{
  "error": "node_id is empty after sanitization"
}
```

## Implementation Order

1. **Task 1** (Backend: deletion endpoint) — no dependencies, enables Task 2
2. **Task 5** (Clickable wikilinks) — no dependencies, isolated change
3. **Task 2** (Frontend: deletion UI) — depends on Task 1
4. **Task 3** (Frontend: manual node creation) — no dependencies, but benefits from nodeMap refresh patterns established in Task 2
5. **Task 4** (Frontend: batch accept) — no dependencies, but test after Tasks 1-3 are done to verify graph refresh works end-to-end

Tasks 1+5 can be parallelized. Tasks 3+4 can be parallelized after Task 2.

## Definition of Done

- [ ] `python -m pytest` passes with all new tests (backend)
- [ ] `npm run build` succeeds (frontend)
- [ ] Node can be deleted from NodeDetail, disappears from graph, and can be re-imported from `_backup/`
- [ ] Nodes can be created via modal without chatting
- [ ] "Accept All" processes a batch of 5+ proposals without errors
- [ ] Wikilinks in chat navigate to the graph view
- [ ] No regressions in existing chat, graph, or search functionality
