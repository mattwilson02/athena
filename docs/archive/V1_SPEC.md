# Athena — V1 Product Specification

> **Note:** This is the V1 spec. Athena V2 is now the current version — see [V2_SPEC.md](V2_SPEC.md) for the active spec.

> A self-hosted personal AI mentor built on an Obsidian-compatible knowledge graph.
> V2 expanded this into a personal management system with 7 domains, 24 types, schema-driven architecture, and the Athena personality (goddess of wisdom and strategy).

---

## 1. What Is Athena

Athena is a local-first system that builds a persistent knowledge graph about you — your goals, fears, relationships, habits, values, skills, beliefs — and uses that graph to give you genuinely contextual AI mentoring that gets smarter over time.

Everything runs on your machine. The only external dependency is the Claude API for reasoning. No accounts, no telemetry, no analytics. Your data is plain markdown files you can open in Obsidian, grep through, and version with git.

---

## 2. Core User Flow

### 2.1 Chat Flow (Primary Interaction)

1. User types a message (e.g. "What's blocking my career goals?")
2. Frontend sends `POST /api/chat`
3. Backend runs **hybrid retrieval**: ChromaDB semantic search → top 5 nodes → NetworkX 1-hop traversal → deduplicate
4. Mentor Agent assembles: system prompt + graph context + conversation history → Claude API call
5. Parse response: clean text + `<graph_updates>` block
6. Return to frontend: `{ response, graph_updates, relevant_nodes }`

### 2.2 Graph Enrichment Loop

1. AI proposes graph updates in its response
2. Frontend displays update cards with accept/dismiss buttons
3. User accepts → `POST /api/vault/write` → writes `.md` file to vault
4. Rebuild NetworkX graph + re-index ChromaDB
5. Future queries now include the new knowledge — **the graph gets smarter**

### 2.3 Hybrid Retrieval Strategy

Given a user query:
1. **Semantic search** (ChromaDB) — find the 5 most relevant nodes by embedding similarity
2. **Graph traversal** (NetworkX) — for each match, follow edges 1-hop to get neighbors
3. **Deduplicate & assemble** — combine all nodes into a rich context string with content + metadata + connections

This is what makes Athena smart about your life — it doesn't just keyword match, it follows the graph to understand *why* things are connected.

### 2.4 Boot Sequence

1. VaultParser walks `vault/` — reads every `.md`, extracts frontmatter + wikilinks, builds NetworkX DiGraph
2. VectorIndex indexes all nodes into ChromaDB (persisted to `chroma_db/`)
3. Flask API ready on `:5000`

---

## 3. Architecture

### 3.1 Technology Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| Data store | Markdown + YAML frontmatter | Human-readable, git-versioned, Obsidian-compatible |
| Graph engine | NetworkX (Python) | In-memory directed graph with typed edges |
| Vector search | ChromaDB (local) | Semantic similarity search over node content |
| API server | Flask + flask-cors | REST API, stateless, lightweight |
| AI reasoning | Claude Sonnet via Anthropic API | Mentoring, graph-aware responses, update proposals |
| Frontend | Svelte + Vite | Chat interface + canvas graph visualization |
| Version control | Git | Track every vault change with full rollback |
| Optional | Obsidian desktop app | Open same vault folder for manual browsing/editing |

### 3.2 Key Design Decisions

- **Markdown files over a database** — Human-readable. Git-versioned. Obsidian-compatible. No migration headaches.
- **NetworkX over Neo4j** — For a personal vault (hundreds to low thousands of nodes), in-memory is instant and zero infrastructure.
- **ChromaDB** — Local-first, file-persisted, zero config. Swap to better embeddings later.
- **Claude over local LLM** — Reasoning quality matters enormously for mentoring.
- **Canvas over D3/SVG** — Canvas performs significantly better with 100+ animated nodes.
- **Svelte over React** — Compiles away the framework, excellent reactivity for real-time updates.

### 3.3 Security & Privacy

Everything runs locally except one outbound HTTPS call to the Claude API. No telemetry, no analytics, no accounts. Git-backed vault gives full history and rollback.

---

## 4. Data Schema

See [vault/_meta/schema.md](../vault/_meta/schema.md) for the complete ontology, frontmatter schema, and relationship conventions.

### Example Node

```markdown
---
id: get-promoted
type: goal
title: Get Promoted to Senior Engineer
created: 2026-01-15
updated: 2026-02-20
status: active
priority: high
deadline: 2026-06-01
tags: [career, growth]
---

# Get Promoted to Senior Engineer

I want to reach senior level by mid-2026. This means demonstrating technical leadership,
owning larger projects end-to-end, and improving my communication in design reviews.

## Blockers
- [[public-speaking-fear]] — I freeze up in large meetings
- [[time-management]] — I spend too much time on small tasks

## Supports
- [[atomic-habits]] — The systems-based approach is helping me build better routines
- [[mentor-sarah]] — Sarah has been giving me great advice on visibility

## Related
- [[systems-design]] — Need to level up here
- [[writing-skill]] — Technical writing for RFCs
```

---

## 5. API Reference

### Chat
| Method | Endpoint | Description | Request Body | Response |
|--------|----------|-------------|-------------|----------|
| GET | `/api/chat/sessions` | List all sessions | — | `{ sessions: [{id, title, created, updated, message_count}] }` |
| POST | `/api/chat/sessions` | Create new session | — | `{ id, title, created }` |
| GET | `/api/chat/sessions/:id` | Get session with messages | — | `{ id, title, created, updated, messages }` |
| DELETE | `/api/chat/sessions/:id` | Delete a session | — | `{ ok: true }` |
| POST | `/api/chat` | Send message, get AI response | `{ session_id, message }` | `{ response, graph_updates, relevant_nodes }` |

### Graph
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/graph` | All nodes + edges |
| GET | `/api/graph/stats` | Node counts, type breakdown |
| GET | `/api/node/:id` | Single node + neighbors |
| GET | `/api/nodes?type=X` | Filter nodes by type |
| GET | `/api/search?q=X` | Semantic search across vault |

### Insights
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/insights` | Proactive AI observations (blocked goals, contradictions, patterns) |

### Vault
| Method | Endpoint | Description | Request Body |
|--------|----------|-------------|-------------|
| POST | `/api/vault/write` | Write a node to the vault | `{ node_id, title, type, folder, content, frontmatter }` |
| POST | `/api/vault/rebuild` | Rebuild graph + vector indexes | — |

---

## 6. Backend Components

### VaultParser (`vault_parser.py`)
- Walks vault directory, reads every `.md` file
- Extracts YAML frontmatter with `pyyaml`
- Extracts `[[wikilinks]]` with regex, maps section headings to edge types
- Handles edge cases: missing frontmatter, broken links, encoding issues

### VaultGraph (NetworkX)
- `VaultGraph` class wrapping `DiGraph`
- Nodes store all metadata (frontmatter + content)
- Edges store relationship type
- Methods: `get_node()`, `get_neighbors(depth)`, `get_nodes_by_type()`, `get_edges()`, `get_stats()`

### VectorIndex (`vector_search.py`)
- `VectorIndex` class wrapping ChromaDB
- Embeds each node (content + frontmatter) into ChromaDB
- Semantic search returning ranked node IDs with similarity scores
- Persists to disk at `chroma_db/`

### MentorAgent (`mentor_agent.py`)
- Accepts user message, builds graph context via hybrid retrieval
- Assembles Claude API call: system prompt + context + conversation history
- Parses response: clean text + `<graph_updates>` block
- Maintains conversation state (multi-turn)

---

## 7. Frontend Components (Svelte)

### Chat Interface
- Message input (Enter to send), message history with user/assistant bubbles
- Loading state, suggested starter prompts, new session button
- Graph update proposals displayed inline as accept/dismiss cards

### Force-Directed Graph Visualization
- Canvas-based for performance, force simulation on requestAnimationFrame
- Nodes colored by type, sized by connection count
- Draggable nodes, pan & zoom, click to open detail panel
- Smooth 60fps for 100+ nodes

### Node Detail Panel
- Slide-in panel: type badge, title, frontmatter, full content, connected nodes

### Graph Update Accept/Reject Flow
- AI proposals appear as cards → accept writes to vault → triggers rebuild → graph refreshes

---

## 8. Sprint Plan

### Sprint 0 — Foundation
- Define ontology & frontmatter schema → `_meta/schema.md`
- Create vault folder structure
- Seed vault with 10-15 personal nodes

### Sprint 1 — Vault & Parser
- Build markdown parser (frontmatter + wikilinks)
- Build NetworkX graph from parsed vault
- Build ChromaDB vector index
- Build context retriever (hybrid search)
- Flask API: graph + vault endpoints

### Sprint 2 — AI Mentor
- Design mentor system prompt
- Build MentorAgent class with Claude API
- Implement graph update extraction
- Flask API: chat endpoints
- Proactive insights endpoint

### Sprint 3 — Frontend (Svelte)
- Chat interface
- Force-directed graph visualization
- Node detail panel
- Graph update accept/reject flow

### Sprint 4 — Polish & Deploy
- Error handling & loading states
- Docker Compose for self-hosting
- README & setup docs

---

## 9. Deployment

```yaml
# docker-compose.yml
services:
  backend:
    build: ./backend
    ports: ["5000:5000"]
    volumes:
      - ./vault:/app/vault
      - ./backend/chroma_db:/app/chroma_db
    env_file: [.env]

  frontend:
    build: ./frontend
    ports: ["5173:5173"]
    depends_on: [backend]
```

---

## 10. Chat Persistence

### 10.1 Problem

Conversations are currently in-memory only. Restarting the backend or starting a new session wipes all history. There's no way to revisit past conversations or build on them over time.

### 10.2 Storage

Chat sessions are stored as JSON files in `backend/chat_sessions/`. Each session is one file.

Why JSON files over SQLite:
- Consistent with the local-first, file-based philosophy
- Easy to inspect, back up, grep through
- No additional dependency
- Sessions are independent documents — no relational queries needed

### 10.3 Data Model

**Session file** (`backend/chat_sessions/{session_id}.json`):
```json
{
  "id": "uuid-v4",
  "title": "Auto-generated from first message",
  "created": "2026-02-25T10:30:00Z",
  "updated": "2026-02-25T11:15:00Z",
  "messages": [
    {
      "role": "user",
      "content": "What's blocking my career goals?",
      "timestamp": "2026-02-25T10:30:00Z"
    },
    {
      "role": "assistant",
      "content": "Looking at your graph...",
      "graph_updates": [],
      "relevant_nodes": [{"id": "get-promoted", "title": "Get Promoted", "type": "goal"}],
      "timestamp": "2026-02-25T10:30:05Z"
    }
  ]
}
```

**Title generation**: First user message, truncated to 60 characters. No AI title generation — keep it simple.

### 10.4 API Changes

| Method | Endpoint | Description | Request / Response |
|--------|----------|-------------|--------------------|
| GET | `/api/chat/sessions` | List all sessions (id, title, created, updated, message count) | `{ sessions: [...] }` |
| POST | `/api/chat/sessions` | Create a new session | `{ id, title, created }` |
| GET | `/api/chat/sessions/:id` | Get full session with messages | `{ id, title, ..., messages: [...] }` |
| DELETE | `/api/chat/sessions/:id` | Delete a session | `{ ok: true }` |
| POST | `/api/chat` | Send message (now requires `session_id`) | Request: `{ session_id, message }` — Response: `{ response, graph_updates, relevant_nodes }` |

The existing `POST /api/chat/reset` is **removed** — replaced by creating a new session.

### 10.5 Backend Changes

**New module: `chat_store.py`**
- `ChatStore` class — reads/writes session JSON files
- `list_sessions()` → returns summary list sorted by `updated` descending
- `create_session()` → generates UUID, writes empty session file
- `get_session(id)` → reads and returns full session
- `append_message(id, message)` → appends to messages array, updates `updated` timestamp
- `update_title(id, title)` → sets session title (auto-set on first user message)
- `delete_session(id)` → deletes the file

**MentorAgent changes:**
- `chat()` accepts conversation history as a parameter instead of storing it internally
- Remove `self.conversation` state — the store is now the source of truth
- Remove `reset()` method

### 10.6 Frontend Changes

- **Sidebar**: Add session list below the view toggle. Each item shows title + relative time. Click to switch. "New Session" button at top.
- **ChatView**: Receives `sessionId` as prop. Loads messages on mount. Sends `session_id` with every `POST /api/chat`.
- **App.svelte**: Manages `currentSessionId` state. Passes it down.

### 10.7 Session Lifecycle

1. User opens Athena → frontend calls `GET /api/chat/sessions` → sidebar shows list
2. If sessions exist, load the most recent one. If none, auto-create a new one.
3. User sends a message → `POST /api/chat` with `session_id` → response saved to session file
4. User clicks "New Session" → `POST /api/chat/sessions` → new empty session, switch to it
5. User clicks an old session in sidebar → `GET /api/chat/sessions/:id` → load its messages

---

## 11. Enhanced Person Nodes

### 11.1 Problem

The current `person` type only has `relationship` (mentor/friend/family/colleague) and `frequency`. This doesn't capture the richness of how people connect to your life — shared context, how you met, what they're working on, trust level, etc.

### 11.2 Updated Frontmatter

```yaml
### Person
relationship: mentor       # mentor | friend | family | colleague | acquaintance | partner
frequency: weekly          # daily | weekly | monthly | rare | inactive
met_through:               # Optional. How you met — e.g. "Fast Bitcoins", "university", "conference"
company:                   # Optional. Where they work / what they do.
location:                  # Optional. City or region.
```

Keep it minimal — no phone numbers, emails, or social links. This is a knowledge graph about *how people connect to your life*, not a contact book.

### 11.3 Template Sections

The person template body should support richer context through section headings:

```markdown
# {title}

<!-- Who is this person? What's their role in your life? -->

## Context
<!-- How you met, shared history, what you've done together -->

## Related
<!-- [[node-id]] — goals, experiences, skills, interests they connect to -->

## People
<!-- [[other-person]] — mutual connections, who introduced you -->
```

### 11.4 Changes Required

- **Schema** (`vault/_meta/schema.md`): Update Person frontmatter — add `met_through`, `company`, `location`. Expand `relationship` enum with `acquaintance` and `partner`.
- **Template** (`vault/_templates/person.md`): Add `Context` section and new frontmatter fields.
- **Frontend** (`GraphUpdateCard.svelte`, `NodeDetail.svelte`): Already handle person type — no changes needed since frontmatter renders dynamically.
- **Mentor system prompt**: No changes needed — the AI already sees all frontmatter and content in context.

### 11.5 Migration

Existing person nodes are unaffected. New fields are all optional. The parser already handles missing frontmatter fields gracefully.

---

## 12. Future Considerations (Post-MVP)

- Better embedding model
- Neo4j if vault exceeds 10,000+ nodes
- Local LLM option
- Mobile app
- Plugin system for custom node types
