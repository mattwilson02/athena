# Athena

A self-hosted personal knowledge graph with an AI agent that captures, connects, and explores everything that matters to you.

Athena builds a graph about your life — goals, fears, people, places, habits, finances, ideas, plans — stored as plain markdown files. The AI agent (named Athena) is sharp, direct, and strategic: she thinks in systems, surfaces connections you'd miss, and aggressively proposes graph updates from every conversation.

**Local-first.** Everything runs on your machine. The only external call is to the Claude API for reasoning.

## Quick Start

```bash
# Backend (requires ANTHROPIC_API_KEY in backend/.env)
cd backend
pip3 install -r requirements.txt
python3 server.py                  # localhost:5001

# Frontend
cd frontend
npm install
npm run dev                        # localhost:5173
```

Create `backend/.env`:
```
ANTHROPIC_API_KEY=sk-ant-...
```

## Tech Stack

| Layer | Tech |
|-------|------|
| Data store | Markdown files + YAML frontmatter in `vault/` |
| Schema | `vault/_meta/schema.md` — single source of truth |
| Graph engine | NetworkX — in-memory directed graph |
| Vector search | ChromaDB — local semantic similarity |
| API server | Flask — REST on localhost:5001 |
| AI reasoning | Claude via Anthropic SDK |
| Frontend | Svelte 5 + Vite |

## Project Structure

```
athena/
├── vault/                    # Knowledge graph (markdown, git-tracked)
│   ├── Self/People/Knowledge/Life/Planning/Places/Finance/
│   ├── _meta/schema.md       # Executable schema — domains, types, edges
│   └── _templates/            # Node file templates
├── backend/
│   ├── server.py              # App factory (~90 lines)
│   ├── routes/                # Flask blueprints (chat, graph, vault, insights)
│   ├── services/              # Business logic (vault_service, chat_service)
│   ├── mentor_agent.py        # Claude integration + hybrid retrieval
│   ├── vault_parser.py        # Markdown → nodes + edges
│   ├── vault_graph.py         # NetworkX graph wrapper
│   ├── schema_parser.py       # Parses schema.md at boot
│   ├── vector_search.py       # ChromaDB semantic search
│   ├── chat_store.py          # Chat session persistence (JSON)
│   └── tests/                 # pytest suite (118 tests)
├── frontend/
│   └── src/
│       ├── App.svelte         # Root — view switching, Cmd+K search
│       └── lib/               # ChatView, GraphView, NodeDetail, Sidebar, etc.
├── docs/
│   ├── ARCHITECTURE.md        # Technical deep-dive
│   └── archive/               # Historical specs (V1, V2, V4)
├── CLAUDE.md                  # Dev instructions (for Claude Code)
└── SOUL.md                    # Athena's personality definition
```

## How It Works

### Schema-Driven

Everything flows from `vault/_meta/schema.md`. At boot, the backend parses it to extract domains, types, frontmatter fields, folder mappings, and edge types. The system prompt, validation rules, and folder structure all derive from this one file. Adding a new node type means editing schema.md and creating a template — zero code changes.

### Two-Tier Type System

7 domains containing 27 types:

| Domain | Types |
|--------|-------|
| Self | goal, fear, belief, value, habit, skill |
| People | person, organisation |
| Knowledge | book, article, idea, note, interest, movie, quote, pill |
| Life | experience, daily, memory |
| Planning | task, project, reminder, event |
| Places | place |
| Finance | expense, subscription, budget |

### AI Agent

Athena uses hybrid retrieval to pull relevant context before every Claude call:

1. Classify query domains (keyword heuristics)
2. Semantic search via ChromaDB
3. Session topic boost from recent messages
4. Score & rank: semantic + domain + recency + centrality
5. 2-hop graph traversal in NetworkX
6. Tiered context assembly (~3000 tokens)

Responses stream via SSE. The agent returns clean text plus `<graph_updates>` blocks proposing creates, updates, or links. The frontend renders these as accept/dismiss cards.

### Vault Format

Every node is a markdown file with YAML frontmatter. Relationships are `[[wikilinks]]` under section headings (`## Blockers` → `blocked_by`, `## People` → `involves`, etc.). Compatible with Obsidian.

See [ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full technical deep-dive.

## Athena's Personality

Athena is the goddess of wisdom and strategy. She sees the whole board — sharp, direct, no filler. Her personality is defined in [SOUL.md](SOUL.md) and parsed at boot.

## Testing

```bash
cd backend
python3 -m pytest tests/ -v
python3 -m pytest tests/ -v --cov=. --cov-report=term-missing
```

118 tests covering vault parsing, graph operations, schema parsing, chat sessions, vault writes/updates, and API routes.

## Privacy

- All data stays on your machine in `vault/` as plain markdown
- The only external call is to the Claude API for reasoning
- No telemetry, analytics, or tracking
- Chat sessions stored locally as JSON files
