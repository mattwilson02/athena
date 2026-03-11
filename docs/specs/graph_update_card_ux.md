# Spec: GraphUpdateCard UX

> E2 Item 1. Make graph update cards show enough detail to make informed accept/dismiss decisions.

**Files:** `frontend/src/lib/GraphUpdateCard.svelte`
**Backend changes:** None — all data already exists in the update objects from the AI response.

---

## Problem

Current cards show minimal information:
- **Create:** Title + 120-char content truncation + edge target IDs (not titles)
- **Update:** "Update: {node_id}" + one-line summary like "Set status, priority" — no values shown
- **Link:** Raw IDs: "source-id → target-id (type)"

Users can't tell what they're accepting. An update card that says "Set status" doesn't tell you *what* status is being set to. A create card truncates content so aggressively you can't judge quality. Edge pills show IDs not human names.

---

## Data Available

The AI response already includes everything we need. No backend changes required.

### Create update object
```json
{
  "action": "create",
  "node_id": "lake-district-ultra",
  "type": "event",
  "title": "Lake District Ultra",
  "content": "50-mile ultra marathon through the Lake District fells...",
  "tags": ["running", "endurance"],
  "frontmatter": { "status": "planned", "date": "2026-05-09", "priority": "high" },
  "edges": [
    { "target": "marathon-training", "type": "supported_by" },
    { "target": "get-fit", "type": "part_of" }
  ]
}
```

### Update update object
```json
{
  "action": "update",
  "node_id": "italy-trip",
  "changes": {
    "title": "New Title (only if renaming)",
    "frontmatter": { "status": "cancelled" },
    "append_content": "Cancelled due to schedule conflict.",
    "add_tags": ["cancelled"],
    "add_edges": [{ "target": "other-node", "type": "relates_to" }]
  }
}
```

### Link update object
```json
{
  "action": "link",
  "source": "marathon-training",
  "target": "get-fit",
  "type": "supported_by"
}
```

**What we don't have:** The "before" value for update fields. We'd need a backend call (`GET /api/node/:id`) to get the current state. Worth doing for a proper before → after display.

---

## Design

### Create cards

```
┌─ NEW ─ [event] ─────────────────────────────────┐
│                                                   │
│  Lake District Ultra                              │
│                                                   │
│  50-mile ultra marathon through the Lake          │
│  District fells. Starting from Keswick,           │
│  following the Cumbria Way...                     │
│                                                   │
│  ┌──────────┬──────────┬───────────┐              │
│  │ status   │ date     │ priority  │              │
│  │ planned  │ 2026-05-09│ high     │              │
│  └──────────┴──────────┴───────────┘              │
│                                                   │
│  Tags: [running] [endurance]                      │
│                                                   │
│  Edges:                                           │
│  supported_by → Marathon Training                 │
│  part_of → Get Fit                                │
│                                                   │
│  [Accept]  [Dismiss]                              │
└───────────────────────────────────────────────────┘
```

- Title prominent, type badge in header
- Full content shown (not truncated to 120 chars) — collapse with "Show more" if over ~200 chars
- Frontmatter fields in a compact grid (only non-boilerplate: skip id, type, title, created, updated)
- Tags as pills
- Edges show edge type + target **title** (not ID) — resolve from edge target ID

### Update cards

```
┌─ UPDATE ─ [update] ──────────────────────────────┐
│                                                   │
│  Italy Trip                                       │
│                                                   │
│  Changes:                                         │
│  status: active → cancelled                       │
│                                                   │
│  Append:                                          │
│  "Cancelled due to schedule conflict."            │
│                                                   │
│  +tags: [cancelled]                               │
│  +edges: relates_to → Work Project                │
│                                                   │
│  [Accept]  [Dismiss]                              │
└───────────────────────────────────────────────────┘
```

- Title shows the node's **human title** (not node_id) — requires resolving from graph
- Each frontmatter change as `key: old → new` (fetch current value via API) or `key: → new` if we can't fetch
- `append_content` shown in full (not truncated)
- `content` (full replacement) shown with warning: "Replaces existing content"
- New tags as `+tag` pills
- New edges with resolved target titles

### Link cards

```
┌─ LINK ─ [link] ──────────────────────────────────┐
│                                                   │
│  Marathon Training ──supported_by──→ Get Fit      │
│                                                   │
│  [Accept]  [Dismiss]                              │
└───────────────────────────────────────────────────┘
```

- Source and target show **human titles** (not IDs)
- Edge type as label between them

### Collapsed (accepted) cards

Same as current — one-line summary. No change needed.

---

## Implementation

### 1. Resolve node titles from IDs

The card needs to resolve node IDs to human titles for:
- Update card: `update.node_id` → node title
- Edge targets: `edge.target` → target title
- Link cards: `source` and `target` → titles

**Approach:** Pass graph data (already loaded in App.svelte) down to GraphUpdateCard as a prop. Build a lookup map `id → title` from the graph nodes array.

```
GraphUpdateCard receives: { update, sessionId, nodeMap, onAccepted, onDismissed }
```

Where `nodeMap` is `Record<string, { title: string, type: string, ... }>` built from graph data.

### 2. Fetch current node state for update before/after

For update cards, fetch the current node to show before → after:

```js
let currentNode = $state(null);

$effect(() => {
  if (update.action === 'update' && update.node_id) {
    fetchNode(update.node_id).then(n => currentNode = n).catch(() => {});
  }
});
```

If the fetch fails or is slow, fall back to showing just the new value (no "before").

### 3. Content expansion

For create cards with long content (>200 chars):
- Show first 200 chars + "Show more" toggle
- Expanded state shows full content
- Default: expanded for pending, collapsed for accepted

### 4. Frontmatter grid

Filter out boilerplate fields that are always set automatically:
```js
const HIDDEN_FM_KEYS = ['id', 'type', 'title', 'created', 'updated'];
```

Show remaining fields in a compact row of key-value pairs.

### 5. Changes to ChatView.svelte

Pass `nodeMap` to GraphUpdateCard:

```svelte
<GraphUpdateCard {update} {sessionId} {nodeMap} />
```

Build `nodeMap` from the graph data already fetched in App.svelte. Pass it through to ChatView as a prop.

---

## Prop chain

```
App.svelte (has graphData from getGraph())
  → builds nodeMap = Object.fromEntries(graphData.nodes.map(n => [n.id, n]))
  → passes nodeMap to ChatView
    → ChatView passes nodeMap to GraphUpdateCard
```

---

## Edge cases

- **Node not in graph yet:** A create card's edge targets may reference nodes that don't exist yet (if they're being created in the same batch). Fall back to showing the ID.
- **Rapid accepts:** After accepting a create, the nodeMap won't include the new node until graph is reloaded. This is fine — other cards in the same batch can show IDs as fallback.
- **Empty content:** Some creates have no content (just frontmatter). Skip the content section entirely.
- **Empty frontmatter:** Some creates have no custom frontmatter. Skip the grid.
- **Fetch failure for current node:** Show update values without "before" state. Still useful.

---

## What success looks like

1. Create cards show: title, full content (expandable), tags, frontmatter grid, edges with human titles
2. Update cards show: node title (not ID), field-by-field changes with values, append preview, new tags/edges
3. Link cards show: source title → target title with edge type
4. User reads a card and knows exactly what accepting it will do
