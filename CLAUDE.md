# Athena — Project Instructions

> See [README.md](README.md) for project overview. See [SOUL.md](SOUL.md) for Athena's personality. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for technical deep-dive.

## Tech Stack

| Layer | Tech |
|-------|------|
| Data store | Markdown files + YAML frontmatter in `vault/` |
| Schema | `vault/_meta/schema.md` — single source of truth, parsed at boot |
| Graph engine | NetworkX (Python) — in-memory directed graph |
| Vector search | ChromaDB (local) — semantic similarity |
| API server | Flask + flask-cors — REST, localhost:5001 |
| AI reasoning | Claude via Anthropic SDK |
| Frontend | Svelte 5 + Vite — localhost:5173 |
| Colours | `frontend/src/lib/colors.js` — shared type/domain colour maps |

## Directory Structure

```
athena/
├── vault/                        # Knowledge graph (markdown, git-tracked)
│   ├── Self/Goals/Fears/Beliefs/Values/Habits/Skills/
│   ├── People/Persons/Organisations/
│   ├── Knowledge/Books/Articles/Ideas/Notes/Movies/Quotes/Pills/
│   ├── Life/Experiences/Daily/Memories/
│   ├── Planning/Tasks/Projects/Reminders/Events/
│   ├── Places/
│   ├── Finance/Expenses/Subscriptions/Budgets/
│   ├── _meta/schema.md           # Executable schema — domains, types, edges
│   ├── _templates/               # Node file templates
│   └── _backup/                  # Archived V1 nodes (skipped by parser)
├── backend/
│   ├── server.py                 # App factory (~90 lines) — boot + blueprint registration
│   ├── routes/
│   │   ├── chat_routes.py        # /api/chat/*, /api/chat/stream (SSE)
│   │   ├── graph_routes.py       # /api/graph/*, /api/node/*, /api/search, /api/activity
│   │   ├── vault_routes.py       # /api/vault/*
│   │   └── insights_routes.py    # /api/insights
│   ├── services/
│   │   ├── vault_service.py      # File I/O, cross-referencing, repair
│   │   └── chat_service.py       # Message orchestration (streaming + sync)
│   ├── schema_parser.py          # Parses schema.md at boot
│   ├── vault_parser.py           # Markdown → nodes + edges
│   ├── vault_graph.py            # NetworkX graph wrapper
│   ├── vector_search.py          # ChromaDB semantic search
│   ├── mentor_agent.py           # Claude integration + hybrid retrieval
│   ├── chat_store.py             # Chat session persistence (JSON files)
│   ├── requirements.txt
│   └── .env                      # ANTHROPIC_API_KEY (never commit)
├── frontend/
│   ├── src/
│   │   ├── App.svelte            # Root — view switching, Cmd+K search, schema loading
│   │   ├── app.css               # Dark theme, CSS custom properties
│   │   ├── main.js               # Svelte 5 mount
│   │   └── lib/
│   │       ├── api.js            # Fetch wrappers + streamMessage() for SSE
│   │       ├── colors.js         # Shared type/domain colour maps
│   │       ├── format.js         # Zero-dep markdown → HTML formatter
│   │       ├── ChatView.svelte   # Streaming chat + starter prompts
│   │       ├── GraphView.svelte  # Canvas force-directed graph
│   │       ├── NodeDetail.svelte # Slide-in node detail + edit mode
│   │       ├── Sidebar.svelte    # Sessions, stats, domain filters, insights
│   │       ├── GraphUpdateCard.svelte  # Accept/dismiss/merge cards
│   │       ├── SearchModal.svelte      # Cmd+K global search
│   │       └── TimelineView.svelte     # Activity timeline
│   ├── index.html
│   ├── vite.config.js
│   └── package.json
├── docs/
│   ├── ARCHITECTURE.md           # Technical deep-dive
│   └── archive/                  # Historical specs (V1, V2, V4, TEST_PLAN)
├── SOUL.md                       # Athena's identity, voice, values, boundaries
├── README.md                     # Project overview + quick start
└── CLAUDE.md                     # This file
```

## Running Locally

```bash
# Backend (requires ANTHROPIC_API_KEY in backend/.env)
cd backend && python3 server.py    # runs on port 5001

# Frontend
cd frontend && npm run dev         # runs on port 5173
```

Note: On macOS with system Python 3.9, all backend files use `from __future__ import annotations` for modern type hint syntax. Flask runs on port **5001** (macOS AirPlay conflict on 5000).

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for full technical deep-dive (boot sequence, data flows, retrieval pipeline, vault format, extension guide).

Key points for development:
- `vault/_meta/schema.md` is the single source of truth — parsed at boot, drives everything
- `server.py` is an app factory (~90 lines). Components stored on `app.config` for blueprint access
- 7 domains, 27 types — never hardcode, always derive from schema
- Wikilinks under `## Section` headings define edge types (see ARCHITECTURE.md for full mapping)

## API Endpoints

### Chat
- `GET /api/chat/sessions` — list sessions
- `POST /api/chat/sessions` — create session
- `GET /api/chat/sessions/:id` — get session with messages
- `PATCH /api/chat/sessions/:id` — rename session
- `DELETE /api/chat/sessions/:id` — delete session
- `POST /api/chat/sessions/:id/dismiss` — dismiss a graph update
- `POST /api/chat` — send message (sync) `{session_id, message}` → `{response, graph_updates, relevant_nodes}`
- `POST /api/chat/simple` — non-streaming text-only chat for Telegram/n8n `{session_id, message}` → `{response}`. Auto-creates tg-* sessions, handles confirm/dismiss keywords for pending graph updates.
- `POST /api/chat/stream` — send message (SSE) → text/done/error events

### Graph
- `GET /api/graph` — all nodes + edges
- `GET /api/graph/stats` — counts and type breakdown
- `GET /api/node/:id` — single node + neighbors with edge types
- `GET /api/nodes?type=X&domain=Y` — filter by type or domain
- `GET /api/schema` — parsed schema (domains, types, frontmatter, colours)
- `GET /api/search?q=X` — title substring + semantic search (combined)
- `GET /api/activity?limit=N` — timeline of created/updated nodes
- `POST /api/graph/suggest-links` — AI-powered link suggestions

### Insights
- `GET /api/insights` — Athena's strategic analysis of the full graph

### Vault
- `POST /api/vault/write` — write node, returns suggested_links
- `POST /api/vault/update` — patch existing node (frontmatter, content, tags, edges)
- `POST /api/vault/rebuild` — rebuild graph + vector indexes
- `POST /api/vault/repair` — walk vault and fix corrupted files

## Code Style

- Python: snake_case, type hints, docstrings on public methods, `from __future__ import annotations`
- Svelte 5: `$props()`, `$state()`, `$effect()`, `$derived()` — no legacy `export let` or `$:`
- One colour source: `frontend/src/lib/colors.js` — never hardcode TYPE_COLORS elsewhere
- Keep modules focused — each backend file does one thing
- No over-engineering. Simple > clever.

## Things to Never Do

- Never commit `.env` or API keys
- Never add telemetry, analytics, or external tracking
- Never store user data outside `vault/`
- Never make outbound calls other than to the Claude API
- Never hardcode type colours — use `colors.js`
- Never hardcode type lists — derive from schema
