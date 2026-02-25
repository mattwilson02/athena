# Athena — Project Instructions

> A self-hosted personal AI mentor built on an Obsidian-compatible knowledge graph.

## Quick Context

Athena builds a persistent knowledge graph about a person (goals, fears, relationships, habits, values, skills, beliefs) stored as plain markdown files. It uses that graph to provide genuinely contextual AI mentoring via Claude that gets smarter over time.

**Local-first.** Everything runs on the user's machine. The only external call is to the Claude API for reasoning.

## Tech Stack

| Layer | Tech |
|-------|------|
| Data store | Markdown files + YAML frontmatter in `vault/` |
| Graph engine | NetworkX (Python) — in-memory directed graph |
| Vector search | ChromaDB (local) — semantic similarity |
| API server | Flask + flask-cors — REST, localhost:5000 |
| AI reasoning | Claude Sonnet via Anthropic SDK |
| Frontend | Svelte + Vite — localhost:5173 |
| Version control | Git on the vault directory |

## Directory Structure

```
athena/
├── vault/                   # Knowledge graph (markdown files, git-tracked)
│   ├── People/Goals/Fears/Books/Skills/Habits/Values/Beliefs/Interests/Experiences/Daily/
│   ├── _meta/schema.md      # Ontology & schema reference
│   └── _templates/          # Node file templates
├── backend/
│   ├── server.py            # Flask API entry point
│   ├── vault_parser.py      # Markdown → graph
│   ├── vector_search.py     # ChromaDB semantic search
│   ├── mentor_agent.py      # Claude integration
│   ├── requirements.txt
│   └── .env                 # ANTHROPIC_API_KEY (never commit)
├── frontend/
│   ├── src/
│   ├── index.html
│   ├── vite.config.js
│   └── package.json
└── docs/                    # Product spec, architecture docs
```

## Running Locally

```bash
# Backend
cd backend && python server.py

# Frontend
cd frontend && npm run dev
```

## Key Patterns

### Vault Nodes
Every node is a markdown file with YAML frontmatter. Relationships are `[[wikilinks]]` under section headings:
- `## Blockers` → `blocked_by`
- `## Supports` → `supported_by`
- `## Related` → `relates_to`
- `## Contradicts` → `contradicts`
- `## Inspired By` → `inspired_by`
- `## People` → `involves`
- Links outside sections → `relates_to` (default)

### AI Response Format
The mentor agent returns clean text plus optional `<graph_updates>` blocks that propose new nodes/edges. The frontend shows these as accept/dismiss cards.

### Hybrid Retrieval
Queries go through: (1) ChromaDB semantic search → top 5 nodes, (2) NetworkX 1-hop traversal of matches, (3) deduplicate and assemble context string. This is what makes the AI actually smart about the user's life.

## API Endpoints

- `POST /api/chat` — send message, get AI response + graph updates
- `POST /api/chat/reset` — clear conversation history
- `GET /api/graph` — all nodes + edges
- `GET /api/graph/stats` — counts and breakdowns
- `GET /api/node/:id` — single node + neighbors
- `GET /api/nodes?type=X` — filter by type
- `GET /api/search?q=X` — semantic search
- `GET /api/insights` — proactive AI observations
- `POST /api/vault/write` — write node to vault
- `POST /api/vault/rebuild` — rebuild indexes

## Code Style

- Python: snake_case, type hints encouraged, docstrings on public methods
- Svelte: component-per-file, props clearly defined
- Keep modules focused — each backend file does one thing
- No over-engineering. This is an MVP. Simple > clever.

## Things to Never Do

- Never commit `.env` or API keys
- Never add telemetry, analytics, or external tracking
- Never store user data outside `vault/`
- Never make outbound calls other than to the Claude API
