# Athena — Project Instructions

> A self-hosted personal knowledge graph with an AI agent that captures, connects, and explores everything that matters to you.

## Quick Context

Athena builds a knowledge graph about your life — goals, fears, people, places, habits, finances, ideas, plans — stored as plain markdown files. The AI (named Athena) is sharp, direct, and strategic: she thinks in systems, surfaces connections you'd miss, and is aggressive about proposing graph updates.

**Local-first.** Everything runs on your machine. The only external call is to the Claude API for reasoning.

## Athena's Personality

Athena is the goddess of wisdom and strategy. She sees the whole board.

- **Sharp and direct** — no filler, no "Great question!", no pleasantries
- **Thinks in systems** — every piece of information connects to something else
- **Aggressive about capturing** — most conversations contain at least one node worth creating
- **Calls out blind spots** — contradictions, neglected goals, patterns the user can't see
- **Not a therapist** — a knowledge tool that tells you what you need to hear

This personality is encoded in `backend/mentor_agent.py` (`_IDENTITY`, `_INSTRUCTIONS`) and should be consistent across all user-facing text (starter prompts, empty states, insights, error messages).

## Tech Stack

| Layer | Tech |
|-------|------|
| Data store | Markdown files + YAML frontmatter in `vault/` |
| Schema | `vault/_meta/schema.md` — single source of truth, parsed at boot |
| Graph engine | NetworkX (Python) — in-memory directed graph |
| Vector search | ChromaDB (local) — semantic similarity |
| API server | Flask + flask-cors — REST, localhost:5000 |
| AI reasoning | Claude Sonnet via Anthropic SDK |
| Frontend | Svelte 5 + Vite — localhost:5173 |
| Colours | `frontend/src/lib/colors.js` — shared type/domain colour maps |

## Directory Structure

```
athena/
├── vault/                        # Knowledge graph (markdown, git-tracked)
│   ├── Self/Goals/Fears/Beliefs/Values/Habits/Skills/
│   ├── People/Persons/Organisations/
│   ├── Knowledge/Books/Articles/Ideas/Notes/
│   ├── Life/Experiences/Daily/Memories/
│   ├── Planning/Tasks/Projects/Reminders/Events/
│   ├── Places/
│   ├── Finance/Expenses/Subscriptions/Budgets/
│   ├── _meta/schema.md           # Executable schema — domains, types, edges
│   ├── _templates/               # Node file templates (24 types)
│   └── _backup/                  # Archived V1 nodes (skipped by parser)
├── backend/
│   ├── server.py                 # Flask API entry point
│   ├── schema_parser.py          # Parses schema.md at boot
│   ├── vault_parser.py           # Markdown → nodes + edges
│   ├── vault_graph.py            # NetworkX graph wrapper
│   ├── vector_search.py          # ChromaDB semantic search
│   ├── mentor_agent.py           # Claude integration + retrieval
│   ├── chat_store.py             # Chat session persistence (JSON files)
│   ├── requirements.txt
│   └── .env                      # ANTHROPIC_API_KEY (never commit)
├── frontend/
│   ├── src/
│   │   ├── App.svelte            # Root — schema loading, view switching
│   │   ├── app.css               # Dark theme, CSS custom properties
│   │   ├── main.js               # Svelte 5 mount
│   │   └── lib/
│   │       ├── api.js            # Fetch wrappers for all endpoints
│   │       ├── colors.js         # Shared type/domain colour maps
│   │       ├── ChatView.svelte   # Chat interface + starter prompts
│   │       ├── GraphView.svelte  # Canvas force-directed graph
│   │       ├── NodeDetail.svelte # Slide-in node detail panel
│   │       ├── Sidebar.svelte    # Sessions, stats, domain filters, insights
│   │       └── GraphUpdateCard.svelte  # Accept/dismiss/merge cards
│   ├── index.html
│   ├── vite.config.js
│   └── package.json
└── docs/
    ├── PRODUCT_SPEC.md           # V1 spec (historical)
    └── V2_SPEC.md                # V2 spec (current)
```

## Running Locally

```bash
# Backend (requires ANTHROPIC_API_KEY in backend/.env)
cd backend && python3 server.py

# Frontend
cd frontend && npm run dev
```

Note: On macOS with system Python 3.9, all backend files use `from __future__ import annotations` for modern type hint syntax.

## Architecture

### Schema-Driven

`vault/_meta/schema.md` is the single source of truth. At boot:
1. `schema_parser.py` extracts domains, types, frontmatter fields, folder mappings, edge types
2. System prompt is generated dynamically from the parsed schema
3. Frontend fetches schema via `GET /api/schema` — UI renders dynamically
4. Adding a new type = edit schema.md + create a template + restart. Zero code changes.

### Two-Tier Type System

7 domains containing 24 types:
- **Self**: goal, fear, belief, value, habit, skill
- **People**: person, organisation
- **Knowledge**: book, article, idea, note
- **Life**: experience, daily, memory
- **Planning**: task, project, reminder, event
- **Places**: place
- **Finance**: expense, subscription, budget

### Hybrid Retrieval (2-hop)

1. Classify query domains (keyword heuristics, no API call)
2. Semantic search (ChromaDB) — top 10 candidates
3. Score & rank: semantic + domain boost + recency + centrality
4. Take top 5, traverse 2 hops in NetworkX
5. Tiered assembly: direct matches (full content), 1-hop (summary), 2-hop (one-liner)
6. Cap at ~3000 tokens

### Vault Nodes

Every node is a markdown file with YAML frontmatter. Relationships are `[[wikilinks]]` under section headings:
- `## Blockers` → `blocked_by`
- `## Supports` → `supported_by`
- `## Related` → `relates_to`
- `## Contradicts` → `contradicts`
- `## Inspired By` → `inspired_by`
- `## People` → `involves`
- `## Part Of` → `part_of`
- `## Located In` → `located_in`
- `## Funded By` → `funded_by`
- `## Met At` → `met_at`
- Links outside sections → `relates_to` (default)

### AI Response Format

The agent returns clean text plus optional `<graph_updates>` blocks proposing creates, updates, or links. The frontend shows these as accept/dismiss/merge cards. On accept, nodes are written to vault and the graph rebuilds.

### Smart Linking

When a node is accepted, the backend runs cross-reference scanning:
1. Reverse scan — existing nodes whose content mentions the new node
2. Forward scan — the new node's content mentions existing nodes
3. Semantic similarity — related nodes not yet linked
Results appear as suggested link cards below the accepted node.

## API Endpoints

### Chat
- `GET /api/chat/sessions` — list sessions
- `POST /api/chat/sessions` — create session
- `GET /api/chat/sessions/:id` — get session with messages
- `DELETE /api/chat/sessions/:id` — delete session
- `POST /api/chat` — send message `{session_id, message}` → `{response, graph_updates, relevant_nodes}`

### Graph
- `GET /api/graph` — all nodes + edges
- `GET /api/graph/stats` — counts and type breakdown
- `GET /api/node/:id` — single node + neighbors
- `GET /api/nodes?type=X&domain=Y` — filter by type or domain
- `GET /api/schema` — parsed schema (domains, types, frontmatter, colours)
- `GET /api/search?q=X` — semantic search
- `POST /api/graph/suggest-links` — AI-powered link suggestions

### Insights
- `GET /api/insights` — Athena's strategic analysis of the full graph

### Vault
- `POST /api/vault/write` — write node, returns suggested_links
- `POST /api/vault/update` — patch existing node (frontmatter, content, tags, edges)
- `POST /api/vault/rebuild` — rebuild graph + vector indexes

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
