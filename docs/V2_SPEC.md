# Athena V2 — Product Specification

> A self-hosted personal management system built on an AI-powered knowledge graph.

V1 was a mentor/therapist with a fixed ontology (goals, fears, beliefs). V2 broadens Athena into a **personal management system** — capable of organising everything in your life through conversational AI that writes, links, updates, and deduplicates a growing knowledge graph.

---

## 1. Design Principles

1. **Graph-native** — Everything is a node. Every relationship is an edge. The graph is the product.
2. **AI-triaged, human-approved** — The AI proposes all graph mutations. You accept, edit, or dismiss. Nothing writes silently.
3. **No duplication** — Before proposing a new node, the AI must check for existing matches. Same person, same place, same goal = update, don't duplicate.
4. **Local-first** — All data stays on your machine. Only outbound call is Claude API.
5. **Obsidian-compatible** — Vault remains plain markdown + YAML frontmatter. You can always open it in Obsidian.
6. **Additive schema** — New types are defined in config, not code. Adding a type never requires a code change.

---

## 2. Two-Tier Type System: Domains & Types

V1 had 11 flat types. V2 organises types under **domains** — high-level categories that group related node types.

### 2.1 Domains

| Domain | Purpose | Types |
|--------|---------|-------|
| **Self** | Inner world — who you are | `goal`, `fear`, `belief`, `value`, `habit`, `skill` |
| **People** | Your network | `person`, `organisation` |
| **Knowledge** | Things you've learned or want to remember | `book`, `article`, `idea`, `note` |
| **Life** | What's happened and what you're doing | `experience`, `daily`, `memory` |
| **Planning** | Getting things done | `task`, `project`, `reminder`, `event` |
| **Places** | Geography | `place` |
| **Finance** | Money | `expense`, `subscription`, `budget` |

### 2.2 Why Two Tiers

The AI picks a **domain** first, then a **type** within that domain. This is a much easier classification than choosing 1 of 25+ flat types. The system prompt presents domains as the first decision, with types as the second.

Example triage:
- "Went climbing with Jake last weekend" → **Life** domain → `experience` type, **People** domain → `person` type (Jake)
- "I need to renew my passport by March" → **Planning** domain → `task` type
- "That restaurant in Shoreditch was amazing" → **Places** domain → `place` type

### 2.3 New Type Definitions

Types carried forward from V1 (`goal`, `fear`, `belief`, `value`, `habit`, `skill`, `person`, `book`, `interest`, `experience`, `daily`) retain their existing frontmatter schemas. New types:

#### Organisation
```yaml
type: organisation
industry:              # Optional. e.g. "fintech", "education"
role:                  # Optional. Your relationship — "employer", "client", "partner"
status: active         # active | past | prospective
```

#### Article
```yaml
type: article
author:                # Optional.
source:                # Optional. URL or publication name.
status: read           # read | to-read | reference
```

#### Idea
```yaml
type: idea
status: raw            # raw | developing | validated | archived
domain:                # Optional. What area this idea relates to.
```

#### Note
```yaml
type: note
# Freeform catch-all. No required type-specific fields.
# Use when nothing else fits. Tags provide categorisation.
```

#### Memory
```yaml
type: memory
date:                  # When it happened. ISO date.
people: []             # Optional. List of person node IDs involved.
place:                 # Optional. Place node ID.
mood:                  # Optional. How you felt.
```

#### Task
```yaml
type: task
status: todo           # todo | in-progress | done | cancelled
priority: medium       # high | medium | low
due:                   # Optional. ISO date.
project:               # Optional. Project node ID this belongs to.
assigned_to:           # Optional. Person node ID.
```

#### Project
```yaml
type: project
status: active         # active | paused | completed | cancelled
priority: high         # high | medium | low
deadline:              # Optional. ISO date.
progress: 0            # 0-100 percentage.
```

#### Reminder
```yaml
type: reminder
due:                   # ISO datetime.
status: pending        # pending | done | dismissed
recurring:             # Optional. "daily" | "weekly" | "monthly" | "yearly"
```

#### Event
```yaml
type: event
date:                  # ISO date or datetime.
location:              # Optional. Place node ID or freeform string.
people: []             # Optional. Person node IDs.
status: upcoming       # upcoming | attended | cancelled
```

#### Place
```yaml
type: place
category: restaurant   # restaurant | city | country | bar | cafe | hotel | landmark | neighbourhood | other
location:              # Optional. City, country, or coordinates.
visited: false         # true | false
rating:                # Optional. 1-5. Only if visited.
```

#### Expense
```yaml
type: expense
amount:                # Required. Numeric.
currency: GBP          # ISO currency code.
date:                  # ISO date.
category:              # food | transport | housing | entertainment | health | education | travel | other
recurring: false       # true | false
```

#### Subscription
```yaml
type: subscription
amount:                # Required. Numeric.
currency: GBP          # ISO currency code.
frequency: monthly     # monthly | yearly | weekly
category:              # software | media | fitness | food | other
status: active         # active | cancelled | paused
renewal_date:          # Optional. ISO date.
```

#### Budget
```yaml
type: budget
period: monthly        # monthly | weekly | yearly
amount:                # Required. Target spend.
currency: GBP          # ISO currency code.
category:              # Optional. Matches expense categories. Omit for overall budget.
```

### 2.4 Event-Project Clustering

A single real-world situation often spawns nodes across multiple domains. For example, "racing an ultra in the Lake District in May" generates an event, a place, tasks (book ferry, pack kit, bring bike), goals (cycle there, live there one day), and links to existing interests.

The AI should recognise when a cluster of proposed nodes revolve around one anchor event or project, and:
1. Create or identify the anchor node (event or project)
2. Link satellite nodes to the anchor via `part_of` or `relates_to`
3. Group the proposals in the response so the user sees them as a coherent cluster, not 9 unrelated cards

This ensures the graph captures both the **individual items** (each a first-class node) and the **structure** binding them together.

### 2.5 Updated Person Node (from V1 section 11)

```yaml
type: person
relationship: friend   # mentor | friend | family | colleague | acquaintance | partner
frequency: weekly      # daily | weekly | monthly | rare | inactive
met_through:           # Optional. How you met.
company:               # Optional. Where they work.
location:              # Optional. City or region.
```

---

## 3. Schema-Driven Architecture

### 3.1 Problem with V1

V1 hardcodes types in:
- `_meta/schema.md` (documentation)
- `mentor_agent.py` SYSTEM_PROMPT (type enum + rules)
- `vault_parser.py` (folder mapping)
- Frontend components (type badges, colours)

Adding a type means editing 4+ files. This doesn't scale.

### 3.2 V2 Approach: Single Source of Truth

`vault/_meta/schema.md` becomes the **executable schema**, not just documentation. At boot:

1. **VaultParser reads `schema.md`** — extracts all type definitions, domains, frontmatter fields, and folder mappings into a structured dict.
2. **System prompt is generated dynamically** — the NODE TYPE RULES section is built from the parsed schema at runtime, not hardcoded in Python.
3. **Frontend fetches schema via API** — `GET /api/schema` returns domains, types, colours, icons. The UI renders dynamically.
4. **Adding a new type** = edit `schema.md` + create a template in `_templates/` + restart backend. Zero code changes.

### 3.3 Schema Format

The schema file uses fenced code blocks that are both human-readable documentation and machine-parseable config. Each type definition block is parsed at boot. The domain table maps types to domains. The parser extracts:
- Domain → type mapping
- Frontmatter fields per type (with defaults and allowed values)
- Folder mapping per type
- Edge types

---

## 4. Deduplication & Conflict Resolution

### 4.1 Problem

V1 creates a new node every time. If you mention "Jake" in three conversations, you get three Jake nodes. There's no check against existing nodes.

### 4.2 Dedup Strategy

Before proposing any `create` action, the AI receives existing node context that may match. The pipeline:

1. **Pre-proposal search** — When the AI's response contains graph updates, the backend intercepts and runs a semantic search for each proposed `node_id` and `title` against existing nodes.
2. **Match injection** — If a close match is found (same type, similar title/content), the backend appends match info to the graph update before sending to the frontend.
3. **Frontend resolution** — The graph update card shows:
   - **No match**: Standard accept/dismiss (create new node)
   - **Likely match**: "Did you mean **[existing node]**?" with options to **merge into existing** or **create new**
   - **Exact match**: Auto-converts to an `update` action on the existing node

### 4.3 Matching Heuristics

| Signal | Weight | Example |
|--------|--------|---------|
| Exact ID match | Definite match | `jake-smith` already exists |
| Title similarity > 0.9 | Likely match | "Jake Smith" vs "Jake" (same type) |
| Type + semantic similarity > 0.85 | Possible match | New person "Jake" vs existing person "Jacob Smith" |
| Different type | No match | Person "Jake" vs experience "Jake's wedding" |

### 4.4 Implementation

- New method on `VectorIndex`: `find_duplicates(node_id, title, type, n=3)` — returns potential matches with similarity scores.
- Backend post-processes AI graph updates before returning to frontend.
- Frontend `GraphUpdateCard` gains a merge flow alongside accept/dismiss.

---

## 5. Node Updates & Mutations

### 5.1 Problem

V1 only supports `create` and `link` actions. If you say "Sarah got a new job at Google," V1 either creates a duplicate Sarah node or ignores the info.

### 5.2 V2 Graph Actions

| Action | What it does | When to use |
|--------|-------------|-------------|
| `create` | Creates a new node | New person, goal, place, etc. not in the graph |
| `update` | Patches an existing node | New info about an existing node — job change, status update, added context |
| `link` | Adds an edge between two nodes | Connecting existing nodes that should be related |
| `unlink` | Removes an edge | Relationship no longer relevant |

### 5.3 Update Action Format

```json
{
  "action": "update",
  "node_id": "sarah-jones",
  "changes": {
    "frontmatter": {
      "company": "Google",
      "updated": "2026-02-25"
    },
    "append_content": "Started at Google as a senior PM in February 2026.",
    "add_tags": ["big-tech"],
    "add_edges": [
      {"target": "google", "type": "involves"}
    ]
  }
}
```

### 5.4 Update Mechanics

- `frontmatter`: Merges into existing YAML. Only specified fields change.
- `append_content`: Adds text to the end of the node body (before any `##` sections, or at the very end).
- `add_tags`: Appends to existing tags (deduped).
- `remove_tags`: Removes specified tags.
- `add_edges`: New wikilinks added under the appropriate `##` section.
- `remove_edges`: Wikilinks removed.

### 5.5 Frontend UX for Updates

The `GraphUpdateCard` shows:
- **Creates**: Current accept/dismiss flow (unchanged from V1)
- **Updates**: Shows a diff view — current value → proposed value. User can accept all, accept partial, or dismiss.
- **Links**: Shows "Connect A → B (relationship type)". Accept/dismiss.

---

## 6. Smart Linking

### 6.1 Problem

V1's graph has 13 nodes and 0 edges. The AI creates isolated nodes. The graph is a collection of dots, not a web.

### 6.2 V2 Linking Strategy

**At creation time**: When the AI proposes a new node, it should also propose edges to relevant existing nodes. The system prompt explicitly instructs this.

**Cross-reference on accept**: When a node is accepted, the backend automatically:
1. Scans the new node's content for `[[wikilinks]]` → creates edges
2. Scans existing nodes for references to the new node's title/id → proposes reverse edges
3. Runs semantic similarity against all nodes → suggests 2-3 "Related" edges for user approval

**Retroactive linking**: New endpoint `POST /api/graph/suggest-links` that analyses the full graph and proposes missing edges. Can be triggered manually or run periodically.

### 6.3 Edge Types (Expanded)

V1 edge types plus new ones for V2 domains:

| Edge Type | Meaning | Example |
|-----------|---------|---------|
| `relates_to` | General association (default) | Skill → Interest |
| `blocked_by` | A blocks B | Goal → Fear |
| `supported_by` | A supports B | Habit → Goal |
| `contradicts` | A contradicts B | Belief → Belief |
| `inspired_by` | A was inspired by B | Goal → Book |
| `involves` | A involves person/org B | Experience → Person |
| `part_of` | A is part of B | Task → Project |
| `located_in` | A is at place B | Event → Place |
| `funded_by` | A is paid for by B | Subscription → Budget |
| `met_at` | Person met at place/event | Person → Event |

---

## 7. Vault Structure

### 7.1 V1 Structure (flat)
```
vault/
├── Goals/
├── Fears/
├── People/
...
```

### 7.2 V2 Structure (domain-grouped)
```
vault/
├── Self/
│   ├── Goals/
│   ├── Fears/
│   ├── Beliefs/
│   ├── Values/
│   ├── Habits/
│   └── Skills/
├── People/
│   ├── Persons/
│   └── Organisations/
├── Knowledge/
│   ├── Books/
│   ├── Articles/
│   ├── Ideas/
│   └── Notes/
├── Life/
│   ├── Experiences/
│   ├── Daily/
│   └── Memories/
├── Planning/
│   ├── Tasks/
│   ├── Projects/
│   ├── Reminders/
│   └── Events/
├── Places/
├── Finance/
│   ├── Expenses/
│   ├── Subscriptions/
│   └── Budgets/
├── _meta/
│   └── schema.md
└── _templates/
```

### 7.3 Migration from V1

V1 folders (`Goals/`, `People/`, etc.) move into their domain parents. A one-time migration script:
1. Creates new directory structure
2. Moves existing files (e.g. `Goals/*.md` → `Self/Goals/*.md`)
3. Updates any wikilinks that reference moved files (IDs don't change, so wikilinks still work)
4. Node IDs remain unchanged — no content edits needed

---

## 8. Improved Retrieval

### 8.1 V1 Retrieval
- Semantic search → top 5 → 1-hop traversal → deduplicate

### 8.2 V2 Retrieval Enhancements

**Multi-hop traversal**: Increase to 2 hops for richer context. A query about "career" should traverse: career goal → blocker fear → related habit → supporting book.

**Domain-aware filtering**: When the query clearly relates to a domain, weight results from that domain higher. "How much am I spending on subscriptions?" should prioritise Finance nodes, not pull in fears and beliefs.

**Recency weighting**: For time-sensitive types (task, daily, reminder, event, expense), boost recently created/updated nodes. A task from today is more relevant than one from 6 months ago.

**Retrieval pipeline**:
1. Classify query domain(s) — lightweight Claude call or keyword heuristic
2. Semantic search (ChromaDB) — top 10 nodes, weighted by domain relevance and recency
3. Graph traversal (NetworkX) — 2-hop from matches
4. Deduplicate, rank by combined score (semantic similarity + graph centrality + recency)
5. Assemble context string — cap at ~3000 tokens to leave room for conversation

### 8.3 Context Window Management

V1 dumps all retrieved context into one system prompt. V2 needs to be smarter as the vault grows:
- Summarise distant nodes (2-hop) instead of including full content
- Prioritise nodes with more edges (higher graph centrality = more important)
- Include frontmatter + first paragraph for context nodes, full content only for direct matches

---

## 9. System Prompt Architecture

### 9.1 Problem with V1

The system prompt is a single hardcoded string with manually written type rules. Adding types means editing Python code.

### 9.2 V2: Composed Prompt

The system prompt is assembled at runtime from components:

```
SYSTEM_PROMPT = [
  IDENTITY          # "You are Athena — goddess of wisdom, strategy..."
  GRAPH_CONTEXT     # Injected from hybrid retrieval (per-request)
  SCHEMA_RULES      # Generated from schema.md at boot — domains, types, when to use each
  DEDUP_RULES       # "Before creating, check if a similar node exists..."
  FORMAT_SPEC       # Graph update JSON format with all action types
]
```

`SCHEMA_RULES` is generated dynamically: iterate over domains and types from the parsed schema, generate a concise rule for each type. When you add a type to schema.md, the prompt updates automatically on next boot.

### 9.3 Athena's Personality

Athena is the goddess of wisdom and strategic thinking. She sees the whole board.

**Identity**: Sharp, direct, strategic. Thinks in systems — when someone mentions a restaurant, she's already linking it to the trip they're planning and the friend who recommended it. When they mention a fear, she sees which goals it's blocking.

**Tone rules**:
- No filler, no preamble, no "Great question!"
- Reference nodes by name — "Get Promoted" not "your goals"
- Aggressive about proposing graph updates — most conversations contain at least one node
- Calls out contradictions, blind spots, things the user seems to be avoiding
- "A knowledge tool, not a therapist. Wisdom means telling people what they need to hear."

**Consistency**: This personality should be reflected in:
- System prompt (`_IDENTITY`, `_INSTRUCTIONS` in `mentor_agent.py`)
- Insights endpoint system prompt (`server.py`)
- Starter prompts and empty states (`ChatView.svelte`)
- Input placeholder text
- "What do I see?" insights button (`Sidebar.svelte`)

---

## 10. API Changes

### 10.1 New Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/schema` | Returns parsed schema — domains, types, frontmatter fields, colours |
| POST | `/api/vault/update` | Update an existing node (patch frontmatter, append content, add/remove edges) |
| POST | `/api/graph/suggest-links` | AI analyses graph, proposes missing edges |
| GET | `/api/nodes?domain=X` | Filter nodes by domain |

### 10.2 Modified Endpoints

| Endpoint | Change |
|----------|--------|
| `POST /api/chat` | Response now includes `update` and `unlink` actions alongside `create` and `link` |
| `POST /api/vault/write` | Now handles dedup check — returns `{ written: true, deduplicated: false }` or `{ written: false, deduplicated: true, merged_into: "existing-id" }` |

---

## 11. Frontend Changes

### 11.1 GraphUpdateCard V2

The card handles four action types:
- **Create**: Title, type badge, content preview. Accept / Dismiss. If dedup match found, shows "Similar node exists: [name]" with Merge / Create New / Dismiss.
- **Update**: Shows node name, diff of changes (current → proposed). Accept All / Accept Partial / Dismiss.
- **Link**: Shows "Connect [A] → [B] (edge type)". Accept / Dismiss.
- **Unlink**: Shows "Disconnect [A] → [B]". Accept / Dismiss.

### 11.2 Schema-Driven UI

- Node type badges, colours, and icons loaded from `GET /api/schema`
- Graph visualisation groups nodes by domain (domain = colour family, type = shade)
- Sidebar filters by domain, then type

### 11.3 Search & Filter

- Domain tabs in sidebar for quick filtering
- Full-text search across all nodes
- Type-specific filters (e.g. tasks by status, expenses by category)

---

## 12. Implementation Plan

All phases are complete.

### Phase 1 — Schema & Vault Restructure [DONE]
1. Rewrite `_meta/schema.md` with V2 domains and types
2. Build schema parser (`schema_parser.py`) — reads schema.md into structured dict at boot
3. Create V2 vault directory structure (domain-grouped folders)
4. V1 nodes backed up to `vault/_backup/v1/`
5. Create `_templates/` for all 24 types
6. Update `vault_parser.py` to use parsed schema for folder mapping

### Phase 2 — Graph Actions & Dedup [DONE]
7. `update` and `link` actions in graph update format
8. `find_duplicates()` on VectorIndex (semantic + ID matching)
9. Backend post-processing: dedup check on AI proposals before returning to frontend
10. `POST /api/vault/update` endpoint — patch frontmatter, append content, add/remove tags and edges
11. `POST /api/vault/write` with dedup awareness + suggested links

### Phase 3 — System Prompt & Retrieval [DONE]
12. Composed system prompt generated from parsed schema at boot
13. 2-hop retrieval via `get_neighbors_by_hop()` on VaultGraph
14. Domain-aware filtering via `_classify_domains()` keyword heuristics
15. Recency weighting for time-sensitive types (90-day decay)
16. Tiered context assembly: full → summary → one-liner, capped at ~3000 tokens

### Phase 4 — Smart Linking [DONE]
17. Cross-reference on node accept: reverse scan, forward scan, semantic similarity
18. `POST /api/graph/suggest-links` — heuristic (with node_id) or full AI analysis (without)
19. `_infer_edge_type()` for sensible defaults based on source/target types

### Phase 5 — Frontend V2 [DONE]
20. GraphUpdateCard: all action types + dedup merge flow + suggested links UI
21. `GET /api/schema` endpoint + frontend schema loading via `App.svelte`
22. Shared `colors.js` — domain/type colours, used everywhere (no more hardcoded maps)
23. Domain/type clickable filters in sidebar with graph view integration
24. NodeDetail: domain badges, schema-driven metadata display

### Phase 6 — Polish & Personality [DONE]
25. Athena personality defined and wired through all touchpoints (see Section 9.3)
26. Insights feature: "What do I see?" button in sidebar, fetches `GET /api/insights`
27. Textarea auto-resize, empty state text, Python 3.9 compat (`from __future__ import annotations`)
28. Unused CSS vars cleaned up, shared colour system consolidated

---

## 13. What V2 Does NOT Include

Keeping scope bounded. These are explicitly deferred:

- **Mobile app** — Desktop/localhost only for now
- **Multi-user** — Single user system
- **Scheduling/calendar sync** — Reminders exist but don't integrate with external calendars
- **Bank import** — Expenses are manual or AI-proposed, no Plaid/bank API
- **Local LLM** — Claude API only
- **Neo4j** — NetworkX until we hit real scale limits (10k+ nodes)
- **Plugin system** — Schema-driven types replace the need for plugins in the medium term
