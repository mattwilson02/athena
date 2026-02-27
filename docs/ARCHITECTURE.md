# Athena — Architecture

Technical reference for how the system works. For project overview and setup, see [README.md](../README.md).

---

## Boot Sequence

When `python3 server.py` runs:

```
1. parse_schema("vault/_meta/schema.md")
   → domains, types, frontmatter fields, folder mappings, edge types

2. VaultParser(vault_path, edge_map).parse()
   → walks vault/, reads every .md file, extracts frontmatter + wikilinks
   → returns (nodes[], edges[])

3. VaultGraph.build_from_parsed(nodes, edges)
   → populates NetworkX DiGraph, skips dangling edges

4. VectorIndex.rebuild(nodes)
   → indexes all node content into ChromaDB for semantic search

5. MentorAgent(graph, vector_index, schema)
   → loads SOUL.md, builds system prompt from schema + personality
   → ready to accept chat messages

6. Flask app registers blueprints, serves on port 5001
```

Components are stored on `app.config` for blueprint access via `current_app.config`.

---

## Chat Message Lifecycle

```
User sends message
        │
        ▼
POST /api/chat/stream (SSE)
        │
        ▼
ChatService.stream_message()
  ├─ Save user message to ChatStore
  ├─ Load conversation history
  │
  ▼
MentorAgent.chat_stream()
  ├─ get_context(query, history)
  │   ├─ Classify domains (keyword heuristics, no API call)
  │   ├─ Semantic search (ChromaDB, top 10)
  │   ├─ Session topic boost (last 5 user messages)
  │   ├─ Score: semantic + domain + recency + centrality + session
  │   ├─ Take top 5, traverse 2 hops (NetworkX)
  │   └─ Tiered assembly: direct (full) → 1-hop (summary) → 2-hop (one-liner)
  │       Cap at ~3000 tokens
  │
  ├─ Build messages: system prompt + history + context + user message
  ├─ client.messages.stream() → Claude API
  │
  ├─ Yield ("text", token) for each streamed chunk
  │   └─ Buffer any <graph_updates> block (don't stream to frontend)
  │
  └─ Yield ("done", {response, graph_updates, relevant_nodes})
        │
        ▼
ChatService post-processing
  ├─ _dedup_check() — annotate creates with potential duplicates
  ├─ Save assistant message to ChatStore
  └─ Yield final SSE event to frontend
        │
        ▼
Frontend renders text + GraphUpdateCards (accept / dismiss / merge)
```

---

## Node Write Lifecycle

```
User accepts a graph update card
        │
        ▼
POST /api/vault/write
  {node_id, title, type, content, edges, frontmatter}
        │
        ▼
VaultService.write()
  ├─ _sanitize_id() — lowercase, underscores→hyphens, strip special chars
  ├─ Validate type against schema.type_list
  ├─ get_folder_for_type() — resolve vault folder from schema
  ├─ Build markdown: YAML frontmatter + # Title + content + ## Section wikilinks
  ├─ Write to vault/{folder}/{node_id}.md
  ├─ rebuild_all() — re-parse vault, rebuild graph + vector index
  └─ find_cross_references(node_id)
      ├─ Reverse scan: existing nodes mentioning this node's title
      ├─ Forward scan: this node's content mentioning existing titles
      └─ Semantic similarity: related nodes not yet linked
        │
        ▼
Returns {ok, filepath, stats, suggested_links}
  → Frontend shows suggested link cards
```

---

## Two-Tier Type System

7 domains, 27 types. The schema defines everything:

```
Self         → goal, fear, belief, value, habit, skill
People       → person, organisation
Knowledge    → book, article, idea, note, interest, movie, quote, pill
Life         → experience, daily, memory
Planning     → task, project, reminder, event
Places       → place
Finance      → expense, subscription, budget
```

Each type specifies:
- **Domain** — which domain it belongs to
- **Folder** — vault path (e.g. `Self/Goals`)
- **Frontmatter** — type-specific YAML fields (e.g. `status`, `priority` for goals)
- **Description** — used in system prompt for AI type selection

---

## Vault File Format

Every node is a markdown file:

```markdown
---
id: learn-piano
type: goal
title: Learn Piano
created: 2026-01-15
updated: 2026-02-20
tags:
  - music
  - learning
status: active
priority: high
---

# Learn Piano

I want to learn to play piano at an intermediate level.

## Blockers
- [[time-management]]

## Related
- [[music-theory]]

## People
- [[alice]]
```

**Frontmatter**: YAML between `---` fences. Contains `id`, `type`, `title`, timestamps, tags, and type-specific fields.

**Relationships**: `[[wikilinks]]` under `## Section` headings. The section heading determines the edge type:

| Section Heading | Edge Type |
|-----------------|-----------|
| `## Blockers` | `blocked_by` |
| `## Supports` | `supported_by` |
| `## Related` | `relates_to` |
| `## Contradicts` | `contradicts` |
| `## Inspired By` | `inspired_by` |
| `## People` | `involves` |
| `## Part Of` | `part_of` |
| `## Located In` | `located_in` |
| `## Funded By` | `funded_by` |
| `## Met At` | `met_at` |
| Outside any section | `relates_to` (default) |

This makes the vault Obsidian-compatible — wikilinks render as clickable references.

---

## Hybrid Retrieval Pipeline

When the agent needs context for a query:

1. **Domain classification** — keyword heuristics map the query to likely domains (no API call)
2. **Semantic search** — ChromaDB returns top 10 candidates by embedding similarity
3. **Session topic boost** — extract last 5 user messages, run additional searches, +0.05 per topic hit
4. **Scoring** — combine: semantic similarity + domain relevance + recency (created/updated) + graph centrality + session boost
5. **Top-K selection** — take top 5 scored nodes
6. **Graph traversal** — 2-hop expansion via NetworkX (undirected view)
7. **Tiered assembly** — direct matches get full content, 1-hop neighbors get summaries, 2-hop get one-liners. Cap at ~3000 tokens.

---

## Streaming Architecture

Chat uses Server-Sent Events (SSE) via `POST /api/chat/stream`:

- Flask `Response(generator(), mimetype='text/event-stream')`
- The mentor agent's `chat_stream()` yields text tokens as they arrive from Claude
- `<graph_updates>` XML blocks are buffered (never streamed to the user)
- After the stream completes, a final `done` event carries the full response, parsed graph updates, and relevant nodes
- Frontend uses `fetch` with `ReadableStream` (not `EventSource` — needs POST body)

SSE event types:
- `text` — streamed token: `{"type": "text", "content": "..."}`
- `done` — stream complete: `{"type": "done", "response": "...", "graph_updates": [...], "relevant_nodes": [...]}`
- `error` — failure: `{"type": "error", "error": "..."}`

---

## Backend Structure

`server.py` is an app factory (~90 lines). Routes are Flask blueprints in `routes/`. Business logic lives in `services/`.

```
server.py              create_app() — boot, wire, serve
routes/
  chat_routes.py       /api/chat/*, /api/chat/stream
  graph_routes.py      /api/graph/*, /api/node/*, /api/search, /api/schema, /api/activity
  vault_routes.py      /api/vault/write, /api/vault/update, /api/vault/rebuild, /api/vault/repair
  insights_routes.py   /api/insights
services/
  vault_service.py     File I/O, cross-referencing, repair, helper functions
  chat_service.py      Message orchestration, dedup checking
```

---

## Extending the Schema

Adding a new node type requires zero code changes:

1. **Edit `vault/_meta/schema.md`** — add a `### type_name` block under the TYPES section with domain, folder, description, and frontmatter fields
2. **Add a domain row** if the type belongs to a new domain (in the DOMAINS table)
3. **Create a template** in `vault/_templates/` (optional, for default frontmatter)
4. **Create the folder** in `vault/` matching the folder path
5. **Restart the backend** — schema is parsed at boot

The system prompt, validation rules, folder routing, and frontend type rendering all derive automatically from the schema.
