# Sprint 9: Retrieval Pipeline Completion

> **Epoch:** E6 — Reading the Room (final gaps)
> **Branch:** `sprint/9`
> **Goal:** Close the three remaining gaps in the query-aware retrieval pipeline: relational intent classification, dynamic token budgets, and pre-injection summarization. After this sprint, asking "how's my relationship with Ben?" pulls Ben + all linked nodes, broad temporal queries get more context space, and high-volume retrievals are compressed before hitting the prompt.

---

## Overview

Sprint 6 shipped the query-aware retrieval pipeline with five intent categories: `temporal_broad`, `temporal_specific`, `domain_filter`, `entity_lookup`, and `general`. This was a major improvement over the one-size-fits-all top-5 approach, but three gaps remain from the E6 acceptance criteria (items 9-11) and Open Problem #5:

1. **No relational intent** — Asking "how's my relationship with Ben?" falls through to `entity_lookup` (k=5), which pulls the person node but misses linked experiences, events, and memories. The pipeline needs a dedicated `relational` intent that retrieves the person node + all linked nodes to give Claude the full picture of a relationship.

2. **Fixed token budget** — `_MAX_CONTEXT_TOKENS = 3000` is hardcoded. A focused query about one goal needs 3000 tokens. A temporal sweep across 2 weeks also gets 3000 tokens. The budget should scale with data volume and query breadth — broad queries deserve more context space, especially when compact assembly is already compressing nodes.

3. **No pre-injection summarization** — When 15+ nodes are retrieved for a broad query, compact assembly (`_node_context_compact`) helps but still uses ~150-200 chars per node. For very high-volume retrievals (20+ nodes in a temporal sweep, or all linked nodes for a well-connected person), a summarization step should compress nodes into dense one-liners before assembly to maximize coverage within the budget.

These three changes are complementary: relational intent retrieves more nodes, dynamic budgets give them room, and summarization ensures they fit.

---

## What Exists

| Component | State | Relevant to This Sprint |
|-----------|-------|------------------------|
| `classify_query_intent()` in `mentor_agent.py` | 5 intent categories with signal phrase matching. Returns `{intent, k, compact, pre_filter, scoring_adjustments}` | Extended with `relational` intent |
| `_INTENT_ENTITY_SIGNALS` | `["tell me about", "what is", "who is", "details on", "more about"]` | Relational queries currently match these → entity_lookup. Need relational signals to take priority |
| `_INTENT_BROAD_SIGNALS` | `["summary", "recap", "review", "overview", ...]` | Unchanged |
| `_MAX_CONTEXT_TOKENS = 3000` | Hardcoded constant used in `_retrieve()` Step 5 | Replaced with dynamic calculation |
| `_CHARS_PER_TOKEN = 4` | Conservative estimate for token→char conversion | Unchanged |
| `_node_context_compact()` | ~150-200 chars per node: title/type/status + key date + neighbor count + first sentence | Pattern for summarization format |
| `_node_context_minimal()` | One-line: `- Title (type) [ID: id]` | Existing densest format — summarization builds on this |
| `_node_context_full()` | Full content + neighbors + relationship stats | Used for non-compact Tier 1 |
| `_retrieve()` in `MentorAgent` | Full pipeline: temporal resolve → domain classify → intent classify → semantic search → score → top-k → 2-hop → tiered assembly | Modified for dynamic budget and summarization step |
| `get_neighbors()` in `VaultGraph` | Returns list of neighbor node dicts at given depth | Used by relational intent to pull all linked nodes |
| `relationship_service.py` | `scan_mentions()`, `assess_relationship_health()` — mention tracking and health assessment | Relationship data already enriches person nodes in context; relational intent ensures all linked nodes are present |
| `build_search_filter()` in `vector_search.py` | Builds ChromaDB `where` clauses from types/statuses | Used by relational intent to filter linked node types |
| `backend/tests/test_query_intent.py` | 12 test cases for intent classification | Extended with relational intent tests |
| `backend/tests/test_retrieval_quality.py` | Integration tests for retrieval quality | Extended with relational and dynamic budget tests |

---

## Architectural Decisions

### 1. Relational intent is a new priority level between domain_filter and entity_lookup in the existing classifier

The `classify_query_intent()` function evaluates intents in priority order. Relational intent slots in at position 3.5 — after `domain_filter` but before `entity_lookup`. It detects relationship-focused queries via signal phrases ("relationship with", "how's X", "dynamics with") combined with the presence of a person reference.

**Why:** Relational queries currently fall to `entity_lookup` which only returns k=5 with no linked-node expansion. Entity_lookup is the right default for "tell me about Piano" but wrong for "tell me about Ben" when Ben has 15 linked experiences. The relational intent needs its own retrieval strategy: pull the person node + all directly linked nodes regardless of k. This follows the same priority-ordered pattern already established in `classify_query_intent()`.

### 2. Dynamic token budget is computed from intent + graph size, not a new config parameter

The budget calculation lives in `_retrieve()` Step 5, replacing the `_MAX_CONTEXT_TOKENS` constant. It uses the intent category and the number of nodes selected (after scoring) to determine the budget. The constant becomes a base/minimum value.

**Why:** A config parameter would require tuning and adds a knob nobody will touch. The budget should be a function of the data: focused queries get the base 3000 tokens, broad queries scale up based on how many relevant nodes were found. The formula is simple arithmetic in `_retrieve()`, not a separate service. Follows the project's "no over-engineering" principle.

### 3. Pre-injection summarization is a new format function, not an LLM call

Summarization compresses nodes into dense one-liners (~50-80 chars) using the same pure-function pattern as `_node_context_minimal()`. It extracts title, type, date, and a key fact from the first sentence. No LLM call — that would add latency and cost per retrieval.

**Why:** The existing format functions (`_node_context_full`, `_node_context_summary`, `_node_context_compact`, `_node_context_minimal`) form a compression spectrum from ~500+ chars to ~50 chars. Summarization adds a new format at the dense end specifically for high-volume retrievals. The pattern is identical: pure function, takes node dict, returns string. This is AD#4 from Sprint 6 — "compact assembly is a mode within existing tiered assembly, not a separate path."

### 4. Relational retrieval uses graph traversal, not vector search, for linked nodes

When a relational intent is detected, the pipeline uses `VaultGraph.get_neighbors()` to pull all 1-hop neighbors of the person node, rather than relying on semantic search to find them. Semantic search still finds the person node itself, but the linked experiences, events, and memories are pulled via graph edges.

**Why:** Semantic search for "Ben" might surface nodes semantically related to "Ben" but miss structurally linked nodes (an event where Ben was tagged but his name doesn't appear in the content). Graph traversal guarantees complete coverage of the relationship's structural footprint. This is the same traversal used in Step 4 (2-hop expansion) but applied earlier and more aggressively for relational queries.

### 5. Budget cap prevents runaway API costs

The dynamic budget has a hard ceiling of 8000 tokens (~32,000 chars). Even with summarization, unbounded context degrades Claude's attention and increases latency/cost. The ceiling is generous enough for any practical query while preventing pathological cases.

**Why:** Open Problem #5 notes "cap at a sensible maximum to manage API cost and latency." 8000 tokens is ~2.5x the current budget, enough for a 2-week temporal sweep or a well-connected person's full relationship map, while staying well under Claude's context window limit.

---

## Tasks

### Task 1: Relational Intent Classification

**Objective:** Add a `relational` intent type to `classify_query_intent()` that fires when the query is about a person or relationship, pulling the person node + all linked nodes.

**Files to modify:**
- `backend/mentor_agent.py` — add relational signal phrases, add relational intent branch, adjust priority order

**Requirements:**

Add a new signal list:

```
_INTENT_RELATIONAL_SIGNALS: list[str]
```

Containing phrases like: `"relationship with"`, `"how's my relationship"`, `"dynamics with"`, `"history with"`, `"interactions with"`, `"how do i know"`, `"what's my relationship"`, `"how are things with"`.

Add detection logic for relational intent:
- Check if query matches relational signals, OR
- Check if query matches entity signals ("tell me about", "who is") AND the People domain is in the top domains

The relational intent returns:

```python
{
    "intent": "relational",
    "k": 5,  # person node + semantic matches
    "compact": True,  # many linked nodes → compact format
    "pre_filter": None,
    "scoring_adjustments": {
        "centrality_multiplier": 2.0,
        "session_multiplier": 1.5,
    },
    "expand_person": True,  # signal to _retrieve() to pull all linked nodes
}
```

The `expand_person` flag is new — it tells `_retrieve()` to aggressively expand the top-scoring person node by pulling all 1-hop neighbors and injecting them as Tier 1 nodes (Task 3 handles this).

**Priority order update:** Relational is evaluated after `domain_filter` (priority 3) and before `entity_lookup` (priority 4). The evaluation order becomes:
1. temporal_broad
2. temporal_specific
3. domain_filter
4. **relational** (NEW)
5. entity_lookup
6. general

**Edge cases:**
- Query mentions a person but is temporal ("what did I do with Ben last week") → temporal_broad wins (higher priority), but the person's linked nodes should still be pulled. This is handled by Task 3 adding person-expansion logic that works across intents.
- Query is "tell me about my fear of failure" → entity_lookup, not relational (no People domain signal)
- Query is "who is Sarah" → relational (entity signal + People domain)
- Query is "Ben" (short, no signals) → entity_lookup as before (relational requires either relational signals or entity signals + People domain)

**Pattern to follow:** Same signal-list-and-check approach as `_INTENT_BROAD_SIGNALS`, `_INTENT_FILTER_SIGNALS`, `_INTENT_ENTITY_SIGNALS`. Same priority-ordered if/elif structure.

**Acceptance criteria:**
- "How's my relationship with Ben?" → relational, k=5, compact=True, expand_person=True
- "Tell me about Sarah" with People as top domain → relational
- "Dynamics with my team" → relational
- "Tell me about my fear of failure" → entity_lookup (no People domain)
- "What happened with Ben this week" → temporal_broad (higher priority)
- Relational intent includes expand_person=True flag

**Test cases** (add to `backend/tests/test_query_intent.py`):
- `test_relational_explicit_signal` — "how's my relationship with ben" → relational
- `test_relational_entity_plus_people_domain` — "tell me about sarah" with People domain top → relational
- `test_relational_who_is_person` — "who is ethan" with People domain → relational
- `test_relational_does_not_match_non_person` — "tell me about piano" without People domain → entity_lookup
- `test_relational_loses_to_temporal` — "what did i do with ben this week" with date range → temporal_broad
- `test_relational_expand_person_flag` — relational intent has expand_person=True
- `test_relational_k_and_compact` — relational intent has k=5, compact=True

---

### Task 2: Dynamic Token Budget

**Objective:** Replace the fixed `_MAX_CONTEXT_TOKENS = 3000` with a dynamic budget that scales with query breadth and data volume.

**Files to modify:**
- `backend/mentor_agent.py` — add `_compute_token_budget()` function, modify `_retrieve()` Step 5 to use it

**Requirements:**

Rename the existing constant to make its role clear:

```
_BASE_TOKEN_BUDGET = 3000  # minimum budget for focused queries
_MAX_TOKEN_BUDGET = 8000   # hard ceiling for any query
```

Add a new function:

```
_compute_token_budget(intent: dict, selected_count: int, total_nodes: int) -> int
```

Parameters:
- `intent` — the classified intent dict (used for `compact` flag and intent type)
- `selected_count` — number of nodes selected after scoring (len of top_results)
- `total_nodes` — total nodes in the graph (for scaling)

Budget logic:
- **Base:** Start at `_BASE_TOKEN_BUDGET` (3000)
- **Compact scaling:** If `intent["compact"]` is True and `selected_count > 5`, add `(selected_count - 5) * 300` tokens. This gives each additional compact node ~300 tokens (~1200 chars) of headroom.
- **Intent multiplier:** `temporal_broad` → 1.5x, `relational` → 1.5x, `domain_filter` → 1.2x, all others → 1.0x
- **Graph scale factor:** If `total_nodes > 100`, add 10% to the budget (more data = slightly more context). If `total_nodes > 200`, add 20%.
- **Cap:** Never exceed `_MAX_TOKEN_BUDGET` (8000)
- **Floor:** Never go below `_BASE_TOKEN_BUDGET` (3000)

Example outcomes:
- General query, 5 nodes selected → 3000 tokens (unchanged from today)
- Temporal broad, 12 nodes selected, compact, 50-node graph → `3000 + (12-5)*300 = 5100`, × 1.5 = 7650, capped at 7650
- Temporal broad, 15 nodes selected, compact, 150-node graph → `3000 + (15-5)*300 = 6000`, × 1.5 = 9000, + 10% = 9900, capped at 8000
- Entity lookup, 5 nodes selected → 3000 × 1.0 = 3000

**Integration in `_retrieve()`:**

Replace line 1304:
```python
char_budget = _MAX_CONTEXT_TOKENS * _CHARS_PER_TOKEN
```

With:
```python
token_budget = _compute_token_budget(intent, len(top_results), total_nodes)
char_budget = token_budget * _CHARS_PER_TOKEN
```

Note: `total_nodes` is already computed at line 1049 (`len(self.graph.get_all_nodes())`). It needs to be available at Step 5. Currently it's computed in the bootstrap check and discarded. Store it in a local variable that persists through the method.

**Edge cases:**
- Graph with 0 nodes → bootstrap mode fires before budget is needed (no impact)
- Intent classification failed → general intent, budget = 3000 (unchanged)
- selected_count = 0 → budget stays at base (no scaling)

**Pattern to follow:** Same simple arithmetic as `_recency_score()` — a pure function with bounded output. No external state.

**Acceptance criteria:**
- General query → budget = 3000 tokens (no regression)
- Temporal broad with 12 selected nodes → budget > 3000 tokens
- Budget never exceeds 8000 tokens
- Budget never drops below 3000 tokens
- Large graph (200+ nodes) gets a slight budget increase
- `get_context_debug()` response includes the computed token budget

**Test cases** (new file: `backend/tests/test_token_budget.py`):
- `test_base_budget_for_general_intent` — general intent, 5 nodes, 50-node graph → 3000
- `test_temporal_broad_scales_up` — temporal_broad, compact, 12 nodes → budget > 3000
- `test_relational_scales_up` — relational, compact, 10 nodes → budget > 3000
- `test_cap_at_max` — extreme values → capped at 8000
- `test_floor_at_base` — minimal values → at least 3000
- `test_large_graph_bonus` — 200+ nodes → budget slightly higher than same query on 50-node graph
- `test_non_compact_no_scaling` — general intent with compact=False → no selected_count scaling
- `test_entity_lookup_unchanged` — entity_lookup, 5 nodes → 3000

---

### Task 3: Relational Node Expansion in `_retrieve()`

**Objective:** When the intent is `relational` (or any intent where the top-scoring node is a person), expand that person's linked nodes into the retrieval results before context assembly.

**Files to modify:**
- `backend/mentor_agent.py` — modify `_retrieve()` between Step 3 (scoring) and Step 4 (2-hop expansion) to inject person-linked nodes

**Requirements:**

After top-k selection (line 1278: `top_results = [r for _, r in scored[:intent["k"]]]`), add a person expansion step:

**When to expand:**
- `intent.get("expand_person")` is True, OR
- Any top result has `type == "person"` and the intent is `relational`

**How to expand:**
1. Find the first person node in `top_results`
2. Call `self.graph.get_neighbors(person_id, depth=1)` to get all 1-hop neighbors
3. For each neighbor not already in `top_results`:
   - Add it to `top_results` (these become Tier 1 nodes)
   - Add it to `direct_ids`
   - Add a candidates_debug entry with `selected: True, tier: 1`
4. Do NOT add these expansion nodes to the scoring — they're structurally retrieved, not semantically scored. Mark them with a `source: "relational_expansion"` field in candidates_debug.

**Limit:** Cap person expansion at 20 additional nodes. If a person has 50+ linked nodes, take the 20 most recently updated (sort by `updated` or `created` date descending).

**Impact on 2-hop expansion:** The expanded nodes become part of `direct_ids`, so Step 4's 2-hop traversal expands from them too. This is intentional — it gives Claude the full relationship graph. However, with more Tier 1 nodes, the budget may fill before 2-hop nodes are added. The dynamic budget (Task 2) mitigates this.

**Edge cases:**
- Person node has 0 neighbors → no expansion, proceed normally
- Person node has 30 neighbors → take top 20 by recency
- Multiple person nodes in top results → only expand the first (highest-scored) one. Expanding multiple would produce too much context.
- Person node's neighbors include other person nodes → include them (they're part of the relationship graph)
- Non-relational intent with a person in top results → no expansion (only fires when `expand_person` is True)

**Pattern to follow:** Same injection pattern as temporal node injection (lines 1216-1249) — nodes not in the scored list are added with synthetic scores and debug entries.

**Acceptance criteria:**
- "How's my relationship with Ben?" → Ben node + all linked experiences/events/memories in context
- Linked nodes appear as Tier 1 in compact format
- Expansion is capped at 20 additional nodes
- Multiple linked nodes are sorted by recency
- Non-relational queries are unaffected (no expansion)
- Debug output shows expanded nodes with `source: "relational_expansion"`

**Test cases** (add to `backend/tests/test_retrieval_quality.py`):
- `test_relational_expands_person_neighbors` — person with 5 linked nodes → all 5 appear in context
- `test_relational_expansion_capped_at_20` — person with 25 linked nodes → only 20 appear
- `test_relational_expansion_sorted_by_recency` — newest linked nodes appear first
- `test_non_relational_no_expansion` — entity_lookup on a person → no neighbor expansion
- `test_relational_debug_shows_expansion_source` — debug output includes `source: "relational_expansion"`

---

### Task 4: Pre-Injection Summarization

**Objective:** Add a summarization format that compresses nodes into dense one-liners (~50-80 chars) for high-volume retrievals, used as Tier 1 when selected count exceeds a threshold.

**Files to modify:**
- `backend/mentor_agent.py` — add `_node_context_oneliner()` function, modify tiered assembly in `_retrieve()` Step 5

**Requirements:**

**New format function:**

```
_node_context_oneliner(node: dict) -> str
```

Produces a single dense line per node. Format:
```
Mon 16 Mar: [daily] Training day — upper body session, portfolio review
```

For non-daily nodes:
```
[goal] Learn Piano — active, high priority, due Jun 01
```

Construction rules:
- Start with date (if daily/event/experience with a date field) formatted as short weekday + day + month
- Then `[type]` tag
- Then title (truncated to 30 chars if longer)
- Then a dash and the first clause of content (up to 40 chars, cut at word boundary)
- For non-temporal types: append key metadata (status, priority, due date) instead of content
- Target: 50-80 chars per node

**When to use oneliner format:**

In `_retrieve()` Step 5 (tiered assembly), add a threshold check:

- If `len(top_results) > 12` AND `compact` is True → use `_node_context_oneliner()` for Tier 1 instead of `_node_context_compact()`
- If `len(top_results) > 12` AND `compact` is False → still use `_node_context_full()` (the user asked for a focused query, don't compress)
- Tier 2 and Tier 3 formats are unchanged

This creates a three-level compression ladder for Tier 1:
- `≤5 nodes` → `_node_context_full()` (~500+ chars)
- `6-12 nodes` → `_node_context_compact()` (~150-200 chars)
- `>12 nodes` → `_node_context_oneliner()` (~50-80 chars)

The ladder is controlled by the existing `compact` flag from intent classification + the node count threshold.

**Edge cases:**
- Node with no content and no date → `[type] Title` (just type and title)
- Node with very long title → truncate at 30 chars with "..."
- Daily node with no date field → skip the date prefix, use `[daily] Title — content`
- Exactly 12 nodes → use compact, not oneliner (threshold is >12)
- Relational intent with 15 linked nodes → oneliner kicks in (compact=True + >12 nodes)

**Pattern to follow:** Same pure function pattern as `_node_context_minimal()`. Single return value, no side effects.

**Acceptance criteria:**
- Oneliner format produces 50-80 chars per node
- 20 oneliner nodes fit within even the base 3000-token budget (~1600 chars total)
- Temporal sweeps with 15+ dailies use oneliner format
- Relational queries with 15+ linked nodes use oneliner format
- Focused queries (compact=False) never use oneliner regardless of node count
- Oneliner includes date for temporal nodes and status/priority for non-temporal

**Test cases** (add to `backend/tests/test_mentor_agent.py`):
- `test_node_context_oneliner_daily_format` — daily node with date → starts with formatted date
- `test_node_context_oneliner_goal_format` — goal node → includes status and priority
- `test_node_context_oneliner_length` — output is 50-80 chars for typical nodes
- `test_node_context_oneliner_title_truncation` — 50-char title → truncated to 30 with "..."
- `test_oneliner_threshold_at_13` — 13 compact nodes → oneliner format used
- `test_compact_threshold_at_12` — 12 compact nodes → compact format used (not oneliner)
- `test_non_compact_never_oneliner` — 15 nodes with compact=False → full format used

---

### Task 5: Debug Output and Integration Tests

**Objective:** Update the debug endpoint output to include the new fields (dynamic budget, expansion source, oneliner usage) and add integration tests verifying the three new capabilities work together.

**Files to modify:**
- `backend/mentor_agent.py` — update `_retrieve()` return dict and `get_context_debug()` to include `token_budget` and `format_used`
- `backend/tests/test_retrieval_quality.py` — add integration tests for relational + budget + summarization

**Requirements:**

**Debug output additions:**

Add to the `_retrieve()` return dict:
- `"token_budget": token_budget` — the computed dynamic budget
- `"format_used": "full" | "compact" | "oneliner"` — which Tier 1 format was selected

Update `get_context_debug()` to expose these:
```python
"token_budget": result.get("token_budget", _BASE_TOKEN_BUDGET),
"format_used": result.get("format_used", "full"),
```

**Integration test scenarios** (add to `backend/tests/test_retrieval_quality.py`):

These tests verify the three features work together end-to-end:

- `test_relational_query_full_context` — Create a person node with 8 linked experience/event nodes. Query "how's my relationship with [person]". Assert: all 8 linked nodes appear in context, format is compact or oneliner, budget > 3000.
- `test_temporal_broad_with_dynamic_budget` — Create 15 daily nodes across a week. Query "what happened this week" with appropriate date range. Assert: >10 dailies in context, budget > 3000, format is compact or oneliner.
- `test_focused_query_unchanged` — Create a goal node with content. Query "tell me about [goal]" (no People domain). Assert: full content format, budget = 3000, no expansion.
- `test_relational_expansion_with_summarization` — Create a person node with 18 linked nodes. Query relationship. Assert: oneliner format used (>12 nodes), all fit within budget.
- `test_debug_endpoint_includes_budget` — Call `/api/debug/retrieval?q=relationship+with+ben`. Assert response includes `token_budget` and `format_used` fields.

**Pattern to follow:** Same fixture-based test pattern as existing `test_retrieval_quality.py`. Use conftest.py fixtures for graph/vector index setup.

**Acceptance criteria:**
- Debug endpoint returns `token_budget` and `format_used` fields
- Relational query with many linked nodes → all appear in context within budget
- Temporal broad with 15+ nodes → dynamic budget accommodates them
- Focused query → no regression from current behavior
- All existing tests continue to pass

**Test cases** listed above in requirements section.

---

## Implementation Order

```
Task 1: Relational Intent Classification (independent — extends classify_query_intent)
Task 2: Dynamic Token Budget (independent — new function + constant rename)
  └→ Task 3: Relational Node Expansion (depends on Task 1 for intent, Task 2 for budget)
      └→ Task 4: Pre-Injection Summarization (depends on Task 3 for node counts)
          └→ Task 5: Debug Output + Integration Tests (depends on all)
```

Tasks 1 and 2 can be built in parallel. Task 3 depends on both. Task 4 depends on Task 3. Task 5 depends on all.

Recommended sequence: **1 → 2 → 3 → 4 → 5**

---

## New Files

| File | Purpose |
|------|---------|
| `backend/tests/test_token_budget.py` | Dynamic token budget unit tests |

**Total new files: 1** (well within the 15-file limit)

**Modified files: 3**
- `backend/mentor_agent.py` — all 5 tasks touch this file
- `backend/tests/test_query_intent.py` — Task 1 adds relational tests
- `backend/tests/test_retrieval_quality.py` — Tasks 3 and 5 add integration tests

---

## Definition of Done

1. All existing tests pass (`python -m pytest` from `backend/`)
2. `npm run build` succeeds in `frontend/`
3. New and updated test files pass with all cases green
4. "How's my relationship with [person]?" → person node + all linked experiences/events/memories in context (E6 acceptance criterion 10)
5. Temporal broad with 15+ nodes → dynamic budget > 3000 tokens, all nodes fit (E6 acceptance criterion 9)
6. Focused query ("tell me about my fear of failure") → unchanged behavior, budget = 3000, full content (E6 acceptance criterion 11 — no regression)
7. High-volume retrievals (>12 nodes) → oneliner format, dense coverage within budget
8. `GET /api/debug/retrieval` returns `token_budget` and `format_used` in diagnostic output
9. Relational expansion capped at 20 nodes per person
10. Budget never exceeds 8000 tokens, never drops below 3000
11. No regression in conflict detection, mode classification, permanence scoring, challenge ladder, accountability, state inference, or habit inference
12. No new dependencies added to `requirements.txt`
