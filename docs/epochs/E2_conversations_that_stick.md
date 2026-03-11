# Epoch 2: Conversations That Stick

> Every plan change in conversation becomes a graph update. Connected nodes stay in sync. Knowledge no longer dies with the session.

**Vision Stage:** Stage 1 (Foundation)
**Appetite:** 3 weeks
**Goal:** Conversations are write operations. Graph updates cascade to neighbors. New users bootstrap from zero.
**Status:** Complete

---

## Press Release

After this epoch, when you tell Athena your plans changed, she proposes the right graph updates — not a "note", but the actual node type. When you accept an update, Athena checks connected nodes for stale references and proposes cascade updates too. Start with an empty vault and the first conversation walks you through your fundamentals — values, goals, fears — building the skeleton of your graph. GraphUpdateCards show exactly what's being created or changed, so you can make informed accept/dismiss decisions.

---

## Problem Statement

Right now, knowledge dies with the chat session. Athena acknowledges plan changes in conversation but doesn't reliably propose graph updates for them. When she does propose updates, the type is often wrong ("note" catch-all). Accepting an update doesn't trigger checks on connected nodes — so changing a project deadline leaves downstream tasks pointing at the old date. New users face an empty graph with no guidance. And GraphUpdateCards show so little detail that users can't tell what they're accepting.

---

## In Scope

### 1. GraphUpdateCard UX
**Files:** `frontend/src/lib/GraphUpdateCard.svelte`, `frontend/src/lib/ChatView.svelte`

Current cards show action + node ID + type. Not enough to make an informed decision.

**Create cards show:**
- Title, type, full content preview
- Tags as pills
- Proposed edges with target node names (not just IDs)
- Frontmatter fields (status, priority, date, etc.)

**Update cards show:**
- Node title + type
- What's changing: before → after for frontmatter fields
- Content diff or append preview
- New edges being added
- New tags being added

**Link cards show:**
- Source → target with both titles
- Edge type as label

All cards collapsible (expanded by default for pending, collapsed for accepted/dismissed).

**What success looks like:** User reads a card and knows exactly what will happen if they accept it.

### 2. FORMAT_SPEC rewrite
**Files:** `backend/mentor_agent.py`

Rewrite the FORMAT_SPEC and prompt instructions so that:
- Any state change in conversation (reschedule, cancel, new plan, new commitment) MUST produce graph updates
- Type selection is precise — specific examples mapping conversational phrases to types
- Updates target existing nodes, not create duplicates
- The "note" type is last resort with explicit gate: "If you catch yourself typing note, stop and reconsider"

The current FORMAT_SPEC already has many of these rules but they're not effective enough. Sharpen the wording, add more examples, reduce ambiguity.

**What success looks like:** Tell Athena "I cancelled the trip" → update card for the trip node with `status: cancelled`, not a new "note" about cancellation.

### 3. Cascade engine
**Files:** `backend/services/vault_service.py`, `backend/services/chat_service.py`, `backend/vector_search.py`

After a graph update is accepted (write or update), find affected nodes using **hybrid cascade** — graph traversal + semantic search:

**Graph cascade (1-hop neighbors):**
- Traverse direct edges from the updated node
- Check each neighbor's content/frontmatter for stale references to the changed data
- High confidence — these are explicitly connected

**Semantic cascade (unlinked but related):**
- Run a semantic search using the updated node's title + change summary
- Filter out nodes already found via graph traversal
- Lower confidence — flag as "possibly affected" with a weaker signal
- Also propose a link edge to fix the missing connection

This catches the case where nodes that *should* be connected weren't linked at creation time. e.g. cancel "Italy Trip" → semantic search finds "Book Rome flights" even though they were never linked.

**Interface:**
- `vault_service.cascade_check(node_id, changes, vector_index)` → list of cascade proposals
- Each proposal: `{node_id, title, type, reason, suggested_changes, confidence: "graph"|"semantic"}`
- Reason is human-readable: "This task references [Trip] which was just cancelled" or "This node mentions Italy Trip but isn't linked — may be affected"
- Graph-sourced proposals shown with full confidence, semantic proposals shown with a softer "possibly affected" indicator

Cascade proposals are returned to the frontend as additional graph update cards. They are suggestions — the user still accepts or dismisses each one.

**What success looks like:** Cancel a project → cascade proposals appear for linked tasks AND semantically related but unlinked nodes, with the option to fix the missing link too.

### 4. Supersession logic
**Files:** `backend/services/vault_service.py`, `backend/mentor_agent.py`

When a new node replaces an old one (new plan supersedes old plan):
- Old node gets `status: superseded` + `superseded_by: new-node-id`
- AI instructed to use `action: update` on old node in same batch as `action: create` for new node
- Validation: if FORMAT_SPEC mentions "replaces", ensure both create + update are in the batch

**What success looks like:** "Actually I'm doing a half marathon instead of the ultra" → create half-marathon + update ultra with `status: superseded`.

### 5. Bootstrap conversation
**Files:** `backend/mentor_agent.py`, `backend/services/chat_service.py`

When the vault has zero nodes (or below a threshold), inject a bootstrap prompt:
- `get_context()` detects zero/few nodes → returns bootstrap context instead of "(No relevant nodes)"
- Bootstrap prompt guides Athena to ask about fundamentals: core values, top goals, key fears, important people, active projects
- First conversation should produce 6-10 seed nodes minimum
- After bootstrap, normal retrieval takes over

**What success looks like:** Your dad clones the project, opens chat, and Athena walks him through building his graph from scratch.

### 7. 3D Graph View
**Files:** `frontend/src/lib/GraphView.svelte` (rewrite), new `frontend/src/lib/graph/` directory
**Spec:** `docs/specs/3d_graph_view.md`

Replace the 2D canvas graph with a 3D Threlte (Three.js + Svelte) visualization. Cosmic/constellation aesthetic — dark background with star-field, glowing color-coded nodes, thin translucent edges, orbit controls. Labels hidden by default, shown on hover/zoom. Domains cluster naturally via 3D force layout. Fly-to camera animation on node select. InstancedMesh + LineSegments for performance.

**What success looks like:** Open the graph tab and see your knowledge as a navigable 3D space — clusters visible, nothing overlapping, feels like exploring a star map.

### 6. Vault re-import endpoint
**Files:** `backend/routes/vault_routes.py`, `backend/services/vault_service.py`

`POST /api/vault/import` processes archived nodes (from `_backup/`) through current schema:
- Validates types against schema, proposes corrections for invalid types
- Assigns permanence levels (when schema V3 adds them)
- Normalises status values
- Dedup-checks against existing vault nodes
- Returns batch of proposals for user review (not auto-imported)

**What success looks like:** Import 100 archived nodes → get a batch review screen showing which are valid, which need fixes, which are duplicates.

---

## Out of Scope

- Conflict detection (E3)
- Personality enforcement / anti-cheerleading (E3)
- Adaptive modes (E4)
- Permanence scoring in retrieval (E4)
- Accountability / commitment tracking (E5)
- Voice interface — speech-to-text input, text-to-speech responses (E4+)
- Proactive messaging / notifications — scheduled check-ins, reminders, nudges (E4+)
- Schema V3 full redesign (prep phase — do minimal changes needed for E2)
- Full vault archive/reset (prep phase — work with existing data)

---

## Build Order

Ship incrementally. Each item builds on the previous:

1. **GraphUpdateCard UX** — immediate value, no backend changes, unblocks user testing
2. **FORMAT_SPEC rewrite** — improves AI output quality, makes cards more useful
3. **Cascade engine** — requires reliable graph updates to cascade from
4. **Supersession logic** — extends cascade with replacement semantics
5. **Bootstrap conversation** — standalone, can be built in parallel with 3-4
6. **Vault re-import** — depends on validation logic, build last
7. **3D Graph View** — frontend-only, no backend changes, can be built in parallel with 5-6

---

## Acceptance Criteria

1. GraphUpdateCards show full detail — content, tags, edges, before/after for updates
2. Tell Athena plans changed → correct update proposed (not "note")
3. Accept a graph update → cascade proposals appear for affected neighbors
4. Replace a plan → old node superseded, new node created, both in same batch
5. Empty vault → first conversation walks fundamentals → 6+ seed nodes
6. Import archived nodes → validation + dedup + correction proposals
7. All existing tests pass + new tests for cascade, bootstrap, import
8. 3D graph view — navigable, clusters visible, labels on hover, fly-to on select, no overlap

---

## Risk

- **Cascade false positives** — heuristic matching on neighbor content will sometimes flag unrelated nodes. Mitigation: cascade proposals are suggestions, user dismisses false positives. Tune during validation.
- **Write aggressiveness over-proposes** — too many low-quality updates per message. Mitigation: dedup check catches duplicates. Monitor proposal count and quality during validation.
- **GraphUpdateCard complexity** — showing too much detail may overwhelm. Mitigation: collapsible sections, progressive disclosure.
- **Bootstrap feels scripted** — walking through fundamentals could feel like a form. Mitigation: let Athena lead conversationally, not as a checklist.
