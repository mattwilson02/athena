# Athena V4 — Streaming, Search, Editing, Smarter Retrieval

> V4 makes Athena feel alive: responses stream in real-time, you can search and edit nodes without leaving the app, retrieval is session-aware, and the backend is properly structured.

V3 shipped: bug fixes (frontmatter, dedup, type validation), 3 new types (movie, quote, pill), and a full UI overhaul. V4 addresses the gaps that remained — the 2-5s synchronous wait, no way to find nodes, no in-app editing, context-blind retrieval, and a 900-line monolith backend.

---

## 1. Backend Cleanup (Route/Service Split)

### Problem
`server.py` was 895 lines — routes, business logic, and helpers all in one file.

### Solution
Split into Flask blueprints + service classes.

```
backend/
  routes/
    __init__.py
    chat_routes.py         # /api/chat/*, /api/chat/stream
    graph_routes.py        # /api/graph/*, /api/node/*, /api/search, /api/schema, /api/activity
    vault_routes.py        # /api/vault/*
    insights_routes.py     # /api/insights
  services/
    __init__.py
    vault_service.py       # File I/O, cross-referencing, repair
    chat_service.py        # Message orchestration (streaming + non-streaming)
  server.py                # ~90 lines: create_app(), register blueprints, boot
```

### Key Decisions
- **App factory pattern**: `create_app()` initialises all components and stores them on `app.config` for blueprint access via `current_app.config`.
- **Thin routes**: Each endpoint is 5-10 lines. All business logic lives in services or existing modules (mentor_agent, vault_graph, etc.).
- **Zero functional changes**: Every endpoint produces identical responses to V3.

---

## 2. Streaming Responses

### Problem
Chat was synchronous — 2-5 second "Thinking..." wait with no feedback.

### Solution
Server-Sent Events (SSE) streaming from Claude API through Flask to the browser.

### Backend

**`mentor_agent.py` — `chat_stream()` generator:**
- Uses `client.messages.stream()` context manager (Anthropic SDK)
- Yields `("text", {"content": token})` for each streamed text chunk
- Buffers `<graph_updates>` block — detects opening tag, stops streaming visible text
- After stream completes: parses graph_updates, validates types, yields `("done", {...})`

**`routes/chat_routes.py` — `POST /api/chat/stream`:**
- Flask `Response(generate(), mimetype='text/event-stream')`
- SSE format: `data: {"type": "text|done|error", ...}\n\n`
- Headers: `Cache-Control: no-cache`, `X-Accel-Buffering: no`
- Original `POST /api/chat` kept for backward compatibility

**Critical fix:** Flask request context is NOT available inside generator functions. Service references must be captured before entering the generator.

### Frontend

**`api.js` — `streamMessage(sessionId, message, { onText, onDone, onError })`:**
- Uses `fetch` with `ReadableStream` (not `EventSource` — needs POST)
- Parses `data:` lines from chunked response
- Returns `AbortController` for cancellation

**`ChatView.svelte`:**
- Pushes placeholder assistant message with `isStreaming: true`
- `onText(token)` appends to message content incrementally
- `onDone(event)` finalises with graph_updates and relevant_nodes
- Blinking cursor during streaming replaces "Thinking..." indicator

---

## 3. Cmd+K Search Modal

### Problem
No way to find existing nodes without scrolling the graph.

### Backend Enhancement
`GET /api/search?q=X` now combines two strategies:
1. **Title match**: Case-insensitive substring scan across all nodes → `match_type: "title"`, score 0
2. **Semantic match**: ChromaDB vector search → `match_type: "semantic"`
3. **Merge**: Title matches first (cap 5), then semantic (deduped), return top 15

### Frontend — `SearchModal.svelte`
- Modal with backdrop blur, centered top-third (VS Code / Raycast style)
- Auto-focused input with 200ms debounce
- Results: type dot, title, type badge, "exact" badge for title matches
- Keyboard: Arrow Up/Down to navigate, Enter to select, Escape to close
- Click result → navigates to graph view with node selected

### Wiring
- `App.svelte`: `$effect` with `window.addEventListener('keydown', ...)` for Cmd+K / Ctrl+K
- `onSelect(nodeId)` → close modal, set `selectedGraphNode`, switch to graph view

---

## 4. In-App Node Editing

### Problem
Editing nodes required opening vault files in a text editor.

### Solution
Edit mode in `NodeDetail.svelte`. No backend changes needed — `POST /api/vault/update` already handles everything.

### UI
- **Toggle**: Edit button next to close. In edit mode: Save + Cancel buttons
- **Title**: `<input>` replacing `<h2>`
- **Tags**: Editable pill list with `×` remove + tag input (Enter to add)
- **Metadata**: Inline `<input>` for each frontmatter value
- **Content**: `<textarea>` replacing formatted content div

### Save Logic
- Diffs each field against original node
- Builds `changes` object: `{title?, content?, add_tags?, remove_tags?, frontmatter?}`
- Calls `updateNode(node.id, changes)`
- After save: calls `onNodeClick(node.id)` to refresh the panel

---

## 5. Session-Aware Retrieval

### Problem
Retrieval treated every message independently. In a multi-message conversation about goals, a vague follow-up like "what about the timeline?" had no context about which goals were being discussed.

### Solution
Backend-only changes in `mentor_agent.py`.

### `_extract_session_topics(history, max_messages=5)`
- Returns last 5 user messages as additional query strings
- Pure text extraction — no API call

### Modified `get_context(query, conversation_history=None)`
- After primary semantic search (10 candidates), runs lightweight searches with each session topic (5 candidates each)
- Builds `session_boost` dict: `{node_id: accumulated_boost}` (+0.05 per topic appearance)
- Adds session_boost to scoring alongside semantic + domain + recency + centrality
- Injects highly-boosted session nodes not in primary results (threshold >= 0.1)

### `chat_store.py` — `get_session_node_ids(session_id)`
- Returns set of all node IDs from graph_updates in the session
- Used to boost "recently touched" nodes in retrieval

---

## 6. Timeline / Activity View

### Problem
No way to see recent graph activity at a glance.

### Backend — `GET /api/activity?limit=50`
Derives activity from vault node metadata (no event log infrastructure):
- Walks all nodes, emits "created" and "updated" entries from frontmatter timestamps
- Sort descending, cap at limit
- Returns `{activities: [{node_id, title, type, action, timestamp}]}`

### Frontend — `TimelineView.svelte`
- Activities grouped by date: Today, Yesterday, This Week, Earlier
- Each entry: action icon (+/~), type dot, title, type badge, relative timestamp
- Click → navigates to graph view with node selected
- Empty state if no activity

### Wiring
- `Sidebar.svelte`: Third view toggle button (Chat / Graph / Activity)
- `App.svelte`: Three-way view switching (chat / graph / timeline)
- `api.js`: `getActivity(limit)` function

---

## New API Endpoints (V4 additions)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/chat/stream` | SSE streaming chat (text + done + error events) |
| GET | `/api/activity?limit=N` | Timeline of created/updated nodes |

Enhanced:
| Method | Path | Change |
|--------|------|--------|
| GET | `/api/search?q=X` | Now combines title substring + semantic search |

---

## File Change Summary

| Phase | File | Change |
|-------|------|--------|
| 1 | `backend/server.py` | Rewritten as ~90 line app factory |
| 1 | `backend/routes/*.py` | New: 4 blueprint files |
| 1 | `backend/services/*.py` | New: vault_service.py, chat_service.py |
| 2 | `backend/mentor_agent.py` | Added `chat_stream()` generator |
| 2 | `backend/routes/chat_routes.py` | Added `/api/chat/stream` SSE endpoint |
| 2 | `frontend/src/lib/api.js` | Added `streamMessage()` |
| 2 | `frontend/src/lib/ChatView.svelte` | Replaced send() with streaming |
| 3 | `backend/routes/graph_routes.py` | Enhanced `/api/search` with title matching |
| 3 | `frontend/src/lib/SearchModal.svelte` | New component |
| 3 | `frontend/src/App.svelte` | Added Cmd+K listener + SearchModal |
| 4 | `frontend/src/lib/NodeDetail.svelte` | Added edit mode |
| 5 | `backend/mentor_agent.py` | Session-aware `get_context()` |
| 5 | `backend/chat_store.py` | Added `get_session_node_ids()` |
| 6 | `backend/routes/graph_routes.py` | Added `/api/activity` endpoint |
| 6 | `frontend/src/lib/TimelineView.svelte` | New component |
| 6 | `frontend/src/lib/Sidebar.svelte` | Added Activity view toggle |

---

## Known Issues & Gotchas

- **Flask streaming context**: `current_app` is a request-scoped proxy — not available inside generator functions. Must capture service references before entering the generator.
- **ChromaDB rebuild race**: Flask debug reloader creates two processes. `rebuild()` uses `get_or_create_collection` to avoid conflicts.
- **Svelte 5 `<svelte:window>`**: Can cause `$window undefined` errors in certain Svelte 5 versions. Use manual `$effect` with `window.addEventListener` instead.
- **`from __future__ import annotations`**: Required in all backend files for Python 3.9 compatibility with modern type hints.
