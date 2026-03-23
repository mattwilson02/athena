# Sprint 6: Query-Aware Retrieval Pipeline

> **Epoch:** Stage 2 Preparation — Retrieval Infrastructure
> **Branch:** `sprint/6`
> **Goal:** Athena adapts how she searches based on what you're asking. Broad questions get broad context. Specific questions get deep context. The top-5 ceiling is gone.

---

## Overview

Stage 1 is complete. Athena has conflict detection, permanence scoring, accountability tracking, state inference, and fundamentals monitoring. But every query — whether "what did I do last week?" or "tell me about my fear of failure" — runs through the same retrieval pipeline: 10 semantic candidates, scored and ranked, top 5 returned with full content.

This produces a known failure mode: broad temporal queries ("what happened this week?", "give me a summary of March") return only 5 nodes when 15+ are relevant. The user gets an incomplete picture. Meanwhile, specific entity lookups waste budget on nodes that aren't relevant because the pipeline can't filter before ranking.

This sprint makes retrieval query-aware:

1. **Query intent classifier** — categorizes queries as temporal_broad, temporal_specific, domain_filter, entity_lookup, or general. Pure heuristic, no API call.
2. **Metadata-filtered vector search** — extends `VectorIndex.search()` to accept ChromaDB `where` filters (type, status, date ranges), so irrelevant nodes are excluded before semantic ranking.
3. **Adaptive retrieval parameters** — based on query intent, adjusts k (5→15 for broad queries), scoring weights, and filtering strategy within `get_context()`.
4. **Compact context assembly** — when more than 5 nodes are selected, switches Tier 1 from full content to a compressed summary format so more nodes fit within the 3000-token budget.

**Not in this sprint:** Cross-session retrieval analysis, retrieval caching, multi-query decomposition (breaking complex questions into sub-queries), or any new features that consume retrieval output.

---

## What Exists

| Component | State | Relevant to This Sprint |
|-----------|-------|------------------------|
| `mentor_agent.py` `get_context()` | 7-step hybrid retrieval: temporal → domain classify → semantic search (n=10) → session boost → score/rank → top-5 select → tiered assembly | Core function being modified |
| `vector_search.py` `VectorIndex.search()` | `collection.query(query_texts=[query], n_results=n)` — no metadata filtering | Extended with `where` parameter |
| `VectorIndex._build_doc()` | Stores `type`, `title`, `tags`, and date fields in ChromaDB metadata | Already has the metadata needed for filtering |
| `_classify_domains()` in `mentor_agent.py` | Keyword-based domain classification, returns ranked domain list | Pattern to follow for intent classification |
| `_resolve_temporal_query()` in `mentor_agent.py` | Detects date phrases ("this week", "tomorrow"), returns `(start, end)` date range | Already exists — intent classifier builds on top |
| `_get_nodes_in_date_range()` | Full graph scan for nodes with dates in a range | Used as fallback when ChromaDB filtering misses non-embedded nodes |
| `_RECENCY_SENSITIVE_TYPES` | Set of types where recency matters | Input for adaptive scoring weights |
| `_STATUS_PENALTIES` | Dict of status → score penalty | Used by metadata filtering to pre-exclude resolved nodes |
| `_PERMANENCE_DEFAULTS` | Dict of type → (level, boost) | Used by adaptive scoring — broad queries reduce permanence weight |
| `_node_context_full()` | Formats a node with all content + neighbors | Tier 1 format for specific queries |
| `_node_context_summary()` | Formats a node with frontmatter + first paragraph | Tier 2 format, becomes Tier 1 for broad queries |
| `_node_context_minimal()` | One-line node format | Tier 3 unchanged |
| `_MAX_CONTEXT_TOKENS = 3000` | Token budget for context assembly (~12,000 chars) | Unchanged — compact assembly fits more nodes within same budget |

---

## Architectural Decisions

### 1. Intent classification is a heuristic extension of existing temporal + domain classification, not a new service

The project already has `_resolve_temporal_query()` and `_classify_domains()` in `mentor_agent.py`. Query intent classification combines their outputs with a few additional signals (question patterns, entity references) into a single function. This is ~60 lines of keyword matching, not a separate service.

**Why:** Follows AD#2 from Sprint 2 — "mode classifier lives in mentor_agent.py, not a separate service." Intent classification has the same profile: lightweight heuristic, simple inputs/outputs, doesn't warrant extraction. It's a pre-processing step within `get_context()`.

### 2. ChromaDB `where` filtering is added to VectorIndex, not bypassed with raw graph scans

ChromaDB natively supports `where` clauses on metadata fields. The metadata already includes `type`, date fields, and `tags`. Using this is dramatically more efficient than fetching all results and post-filtering, especially as the graph grows.

**Why:** The metadata is already indexed by ChromaDB (stored in `_build_doc()`). Using `where` filters means ChromaDB excludes non-matching documents before computing embeddings/distances. This is O(matching) not O(all). The graph scan fallback (`_get_nodes_in_date_range`) remains for nodes that might have dates in content but not metadata.

### 3. Adaptive k is bounded, not unlimited

Broad queries raise k from 5 to 15. Not 50, not "all matching." The token budget is fixed at 3000 tokens. Even with compact assembly, 15 nodes is a practical ceiling before context becomes noise.

**Why:** More context is not always better context. Claude's attention degrades with excessive context. 15 summary-format nodes ≈ 15 × 200 chars = 3000 chars ≈ 750 tokens, leaving ~2250 tokens for temporal header, hop expansions, and alerts. This is the sweet spot — broad enough to answer "what happened this week" with 10+ nodes, tight enough to stay focused.

### 4. Compact assembly is a mode within existing tiered assembly, not a separate path

When k > 5, the context assembly switches Tier 1 from `_node_context_full()` to `_node_context_summary()`. Tier 2 and Tier 3 still use their existing formats. This is a conditional within the existing assembly loop, not a forked code path.

**Why:** Minimises code duplication. The tiered assembly logic (budget tracking, hop expansion, context joining) is identical regardless of node format. Only the format function pointer changes based on intent.

### 5. Intent classification output is passed downstream but does NOT change mode classification or alert injection

The query intent affects only retrieval (k, filters, scoring, assembly format). It does NOT change the communication mode (mirror/advisor/guardian/dialectic), proactive alert injection, or any other system prompt section. Those systems have their own classification logic.

**Why:** Separation of concerns. Retrieval answers "what context does Claude need?" Mode answers "what tone should Claude use?" Mixing them would create coupling that makes both harder to reason about. A broad temporal query can still trigger Guardian mode if conflicts are detected.

---

## Tasks

### Task 1: Query Intent Classifier

**Objective:** Classify incoming queries into intent categories that determine retrieval strategy.

**Files to modify:**
- `backend/mentor_agent.py` — add `classify_query_intent()` function

**Requirements:**

```
classify_query_intent(query: str, date_range: tuple[date, date] | None, domains: list[str]) -> dict
```

Takes the query text plus the already-computed temporal resolution and domain classification. Returns:

```python
{
    "intent": "temporal_broad" | "temporal_specific" | "domain_filter" | "entity_lookup" | "general",
    "k": 15 | 10 | 8 | 5 | 5,           # how many candidates to fetch
    "compact": True | False,              # whether to use compact assembly
    "pre_filter": {...} | None,           # ChromaDB where clause, if applicable
    "scoring_adjustments": {...},          # weight overrides for this query type
}
```

**Intent categories and detection logic (evaluated in priority order):**

1. **`temporal_broad`** — query has a date range spanning 3+ days (e.g., "this week", "this month", "last week", "what happened in March"). Detection: `date_range` is not None AND `(end - start).days >= 3`. Also matches summary signals: "summary", "recap", "review", "overview", "what happened", "what did I do", "how did", "how was".
   - `k`: 15
   - `compact`: True
   - `pre_filter`: exclude completed/cancelled/archived/superseded statuses via ChromaDB `where`
   - `scoring_adjustments`: temporal_boost increased to 0.5 (from 0.3), permanence_boost halved (broad queries need tactical nodes too)

2. **`temporal_specific`** — query has a date range spanning 0-2 days (e.g., "today", "tomorrow", "next Friday"). Detection: `date_range` is not None AND `(end - start).days < 3`.
   - `k`: 10
   - `compact`: False
   - `pre_filter`: None (specific dates are already well-handled by temporal injection)
   - `scoring_adjustments`: temporal_boost 0.4, recency_boost weight doubled

3. **`domain_filter`** — query strongly targets a single domain (≥3 keyword hits in one domain AND that domain has 2x the hits of the second). Also matches explicit filter language: "show me my goals", "list my habits", "what are my values", "all my tasks", "my expenses". Detection: `len(domains) >= 1` AND `domains[0]` has dominant score, OR filter signal phrases matched.
   - `k`: 10
   - `compact`: True (when showing many same-domain nodes, summaries suffice)
   - `pre_filter`: ChromaDB `where` filtering to types within the dominant domain
   - `scoring_adjustments`: domain_boost increased to 0.3 (from 0.2), reduce permanence_boost to 0.0 (domain filtering already handles relevance)

4. **`entity_lookup`** — query references a specific node by name or asks about a particular thing/person. Detection: query is short (< 40 chars) AND doesn't match temporal/domain signals, OR contains "tell me about", "what is", "who is", "details on", "more about".
   - `k`: 5
   - `compact`: False (entity lookups benefit from full content)
   - `pre_filter`: None
   - `scoring_adjustments`: centrality_boost doubled (entity lookups benefit from hub nodes), session_boost doubled (conversation continuity matters)

5. **`general`** — default. Doesn't match any of the above.
   - `k`: 5
   - `compact`: False
   - `pre_filter`: None
   - `scoring_adjustments`: no changes (use existing defaults)

**Edge cases:**
- Empty query → general intent
- Query matching both temporal_broad and domain_filter → temporal_broad wins (it's higher priority)
- Query matching temporal_specific and entity_lookup → temporal_specific wins
- `date_range` is None but summary signals present → still treat as temporal_broad with `k: 10` (no date filter, but fetch more candidates)

**Pattern to follow:** Same keyword-list-and-loop approach as `_classify_domains()` and `classify_mode()`. Dict of signal phrases per intent. Returns structured dict, not just a string.

**Acceptance criteria:**
- "What did I do this week?" → temporal_broad, k=15, compact=True
- "What's on for tomorrow?" → temporal_specific, k=10
- "Show me my goals" → domain_filter, k=10, pre_filter targets Self domain types
- "Tell me about Sarah" → entity_lookup, k=5, full content
- "Hey, quick question about something" → general, k=5
- Classification runs in <1ms (pure string matching + dict lookup)

**Test cases** (`backend/tests/test_query_intent.py`, new file):
- `test_temporal_broad_this_week` — "what happened this week" with 7-day range → temporal_broad
- `test_temporal_broad_summary_signal` — "give me a summary" without date range → temporal_broad with k=10
- `test_temporal_specific_tomorrow` — "what's on tomorrow" with 1-day range → temporal_specific
- `test_domain_filter_show_goals` — "show me my goals" with Self as dominant domain → domain_filter
- `test_domain_filter_needs_dominance` — evenly split domains → general (no dominant domain)
- `test_entity_lookup_short_query` — "Sarah" (short, no temporal/domain) → entity_lookup
- `test_entity_lookup_tell_me_about` — "tell me about my fear of failure" → entity_lookup
- `test_general_default` — "hey how's it going" → general
- `test_temporal_broad_beats_domain` — "what did I work on this month" (temporal + domain) → temporal_broad
- `test_empty_query_general` — "" → general
- `test_k_values_match_intent` — verify k values: broad=15, specific=10, domain=10, entity=5, general=5
- `test_compact_flag_correct` — temporal_broad and domain_filter → compact=True; others → compact=False

---

### Task 2: Metadata-Filtered Vector Search

**Objective:** Extend `VectorIndex.search()` to accept optional ChromaDB `where` clauses for pre-filtering by type, status, and date range.

**Files to modify:**
- `backend/vector_search.py` — add `where` parameter to `search()`, add helper for building where clauses
- `backend/mentor_agent.py` — no changes here (Task 3 will call the new parameter)

**Requirements:**

**Extended `search()` signature:**

```python
def search(self, query: str, n: int = 5, where: dict | None = None) -> list[dict]:
```

The `where` parameter is passed directly to ChromaDB's `collection.query(where=where)`. ChromaDB supports:
- `{"type": "goal"}` — exact match
- `{"type": {"$in": ["goal", "habit", "value"]}}` — type in list
- `{"$and": [{"type": {"$in": [...]}}, {"status": {"$nin": ["completed", "cancelled"]}}]}` — compound filters

**New helper function:**

```
build_search_filter(types: list[str] | None = None, exclude_statuses: list[str] | None = None) -> dict | None
```

Builds a ChromaDB `where` clause from friendly parameters:
- `types` → `{"type": {"$in": types}}`
- `exclude_statuses` → requires metadata field `status` to exist; ChromaDB doesn't support `$nin` on missing fields, so this only works for nodes that have status in metadata
- Both → `{"$and": [type_filter, status_filter]}`
- Neither → `None`

**Metadata enrichment:**

Currently `_build_doc()` stores `type`, `title`, `tags`, and date fields in metadata. Add `status` to metadata:

```python
metadata["status"] = node.get("status", "")
```

This enables status-based filtering. The `domain` field should also be added for domain filtering:

```python
metadata["domain"] = node.get("domain", "")
```

Note: `domain` isn't currently on node dicts. It needs to be derived from the type→domain mapping at index time. Pass the schema's type→domain mapping to the index, or add a `domain` field during indexing. The simplest approach: add a `schema` parameter to `index_all()` and `upsert_one()` OR have the caller set `domain` on the node dict before indexing.

Prefer the simpler approach: have `_build_doc()` accept the node dict as-is. The caller (vault_service or server.py) should ensure nodes have a `domain` field set. If not present, omit from metadata (backward compatible).

**Edge cases:**
- `where` filter matches zero documents → return empty list (ChromaDB handles this)
- `where` filter with invalid field names → ChromaDB raises ValueError; catch and fall back to unfiltered search with a log warning
- `n` exceeds matching documents → ChromaDB returns fewer results (already handled)
- Nodes indexed without `status`/`domain` metadata (pre-existing index) → filtering on those fields returns fewer results. This is acceptable — a full reindex (`/api/vault/rebuild`) populates the metadata.

**Pattern to follow:** Same defensive error handling as existing `search()` method. The `where` parameter is a pass-through — ChromaDB's query API handles the filtering.

**Acceptance criteria:**
- `search("goals", where={"type": {"$in": ["goal"]}})` returns only goal nodes
- `search("plans", where=build_search_filter(exclude_statuses=["completed", "cancelled"]))` excludes resolved nodes
- `search("anything", where=None)` behaves identically to current implementation (backward compatible)
- Invalid `where` clause falls back to unfiltered search with log warning
- `status` and `domain` fields present in metadata for newly indexed nodes

**Test cases** (`backend/tests/test_vector_search.py`, new file):
- `test_search_no_filter_backward_compat` — search without where returns results (same as before)
- `test_search_type_filter` — index 3 types, filter to one → only that type returned
- `test_search_status_exclusion` — index nodes with statuses, exclude completed → completed nodes absent
- `test_search_compound_filter` — type + status filter together works
- `test_search_filter_no_matches` — filter that matches nothing → empty list
- `test_search_invalid_filter_fallback` — malformed where clause → falls back to unfiltered, logs warning
- `test_build_search_filter_types_only` — builds correct `$in` clause
- `test_build_search_filter_statuses_only` — builds correct exclusion clause
- `test_build_search_filter_combined` — builds `$and` clause
- `test_build_search_filter_none` — no params → returns None
- `test_metadata_includes_status` — indexed node has status in metadata
- `test_metadata_includes_domain` — indexed node has domain in metadata (when provided)

---

### Task 3: Adaptive Retrieval Pipeline

**Objective:** Modify `get_context()` to use query intent classification for adaptive k, metadata filtering, and scoring weight adjustments.

**Files to modify:**
- `backend/mentor_agent.py` — modify `get_context()` to call `classify_query_intent()`, use results to adjust retrieval parameters

**Requirements:**

**Integration point in `get_context()`:**

After the existing temporal resolution (step 0) and domain classification (step 1), add:

```python
# Step 1.5: Classify query intent for adaptive retrieval
intent = classify_query_intent(query, date_range, relevant_domains)
```

Then use `intent` to adjust subsequent steps:

**Step 2 (semantic search):** Replace hardcoded `n=10` with `intent["k"] + 5` (fetch extra candidates for ranking headroom). Pass `intent["pre_filter"]` as the `where` parameter.

```python
search_n = intent["k"] + 5  # extra headroom for scoring
search_results = self.vector_index.search(query, n=search_n, where=intent.get("pre_filter"))
```

If the filtered search returns fewer than `intent["k"] // 2` results (filter was too aggressive), fall back to unfiltered search:

```python
if len(search_results) < intent["k"] // 2:
    search_results = self.vector_index.search(query, n=search_n)
```

**Step 3 (scoring):** Apply `intent["scoring_adjustments"]` to the scoring weights. The adjustments dict contains multipliers for named boosts:

```python
adjustments = intent.get("scoring_adjustments", {})
temporal_boost_weight = adjustments.get("temporal_boost", 0.3)  # default 0.3
permanence_multiplier = adjustments.get("permanence_multiplier", 1.0)
recency_multiplier = adjustments.get("recency_multiplier", 1.0)
domain_boost_weight = adjustments.get("domain_boost", 0.2)
centrality_multiplier = adjustments.get("centrality_multiplier", 1.0)
session_multiplier = adjustments.get("session_multiplier", 1.0)
```

Replace hardcoded boost values in the scoring loop with these variables:
- `temporal_boost = temporal_boost_weight if result["id"] in temporal_node_ids else 0.0`
- `_, perm_boost = _get_permanence(node_type); permanence_boost = perm_boost * permanence_multiplier`
- `recency_boost = _recency_score(node) * 0.15 * recency_multiplier`
- Similarly for domain, centrality, session boosts

**Top-K selection:** Replace hardcoded `scored[:5]` with `scored[:intent["k"]]`:

```python
top_results = [r for _, r in scored[:intent["k"]]]
```

**Step 5 (context assembly):** Pass `intent["compact"]` flag to control Tier 1 format (Task 4 handles the assembly changes).

**Edge cases:**
- If `classify_query_intent()` raises → catch, log, proceed with general intent (k=5, no filter, default weights)
- If filtered search returns 0 results but unfiltered would return results → always fall back (never return empty when nodes exist)
- Intent classification doesn't change the overdue sweep — overdue nodes are always injected regardless of intent

**Pattern to follow:** Same defensive try/except pattern used for `_detect_conflicts()` and `_infer_state()` in `chat_service.py`. The intent is advisory — failure should never break retrieval.

**Acceptance criteria:**
- "What did I do this week?" fetches 15+ candidates and returns up to 15 in top results
- "Tell me about my fear of failure" fetches 5 candidates with full content
- "Show me my goals" filters to Self-domain types before ranking
- Existing tests still pass — general intent uses same parameters as before
- Filtered search falls back to unfiltered when filter is too aggressive

**Test cases** (add to `backend/tests/test_mentor_agent.py`):
- `test_get_context_broad_temporal_returns_more` — mock graph with 12 date-bearing nodes in range → context contains >5 nodes
- `test_get_context_entity_lookup_full_content` — entity query → Tier 1 uses full content format
- `test_get_context_domain_filter_types` — domain query → results biased toward that domain's types
- `test_get_context_general_unchanged` — general query → same behavior as before (k=5, full content)
- `test_get_context_intent_error_fallback` — mock classify_query_intent to raise → proceeds with general intent
- `test_get_context_filter_fallback_on_empty` — filtered search returns 0, unfiltered returns results → uses unfiltered

---

### Task 4: Compact Context Assembly

**Objective:** When the query intent requests compact mode, switch Tier 1 from full content to summary format so more nodes fit within the token budget.

**Files to modify:**
- `backend/mentor_agent.py` — modify tiered context assembly in `get_context()` to accept compact flag, add `_node_context_compact()` helper

**Requirements:**

**New helper function:**

```
_node_context_compact(node: dict, neighbor_names: list[str]) -> str
```

A format between `_node_context_full()` (full content + neighbors) and `_node_context_summary()` (frontmatter + first paragraph). Compact shows:

- Node title, type, status (one line)
- Key frontmatter fields (priority, deadline/due, date, frequency — skip verbose fields like content)
- First sentence of content (not full paragraph)
- Neighbor count (not full list) — e.g., "4 connected nodes" instead of listing them

Target: ~150-200 chars per node (vs ~500+ for full, ~300 for summary). This allows 15 nodes within ~3000 chars.

Example output:
```
[goal] Learn Piano (active, high priority)
  Due: 2026-06-01 | Connected: 4 nodes
  I want to learn to play piano at an intermediate level.
```

**Assembly mode switch:**

In the tiered context assembly (Step 5 of `get_context()`), accept a `compact` boolean:

```python
compact = intent.get("compact", False)
```

When `compact` is True:
- Tier 1 uses `_node_context_compact()` instead of `_node_context_full()`
- Tier 2 uses `_node_context_minimal()` instead of `_node_context_summary()` (even more compressed for 1-hop)
- Tier 3 unchanged (already minimal)
- The temporal facts header is shortened: only include date facts for direct matches, not all temporal nodes (reduces header bloat for broad queries with many date-bearing nodes). Cap temporal facts at 10 entries.

When `compact` is False:
- Everything works exactly as before (backward compatible)

**Token budget note:**

The `_MAX_CONTEXT_TOKENS = 3000` and `_CHARS_PER_TOKEN = 4` remain unchanged. The compact format simply fits more nodes within the same budget. With 15 compact nodes at ~200 chars each = ~3000 chars = ~750 tokens, leaving generous room for temporal header + Tier 2/3 + hop expansion.

**Edge cases:**
- Node with no content (empty body) → compact shows just the metadata line
- Node with no frontmatter date fields → skip the date portion of the compact line
- Compact mode with only 3 results → still uses compact format (consistency)
- Budget exceeded before all top results rendered → same truncation as existing code (break from loop)

**Pattern to follow:** Same format function pattern as `_node_context_full()`, `_node_context_summary()`, `_node_context_minimal()`. Pure function, takes node dict + neighbor info, returns string.

**Acceptance criteria:**
- Compact assembly fits 15 nodes within 3000-token budget
- Compact nodes include title, type, status, key dates, and first sentence
- Non-compact queries use the same format as before (no regression)
- Temporal header is capped at 10 facts in compact mode
- Budget tracking still prevents overflow

**Test cases** (add to `backend/tests/test_mentor_agent.py`):
- `test_node_context_compact_format` — compact output has title, type, status, date, first sentence
- `test_node_context_compact_shorter_than_full` — compact output is < 50% the length of full output for same node
- `test_compact_assembly_fits_15_nodes` — 15 nodes in compact mode fit within char budget
- `test_compact_temporal_header_capped` — 20 date-bearing nodes → only 10 temporal facts in header
- `test_non_compact_unchanged` — compact=False produces identical output to current behavior

---

### Task 5: Retrieval Diagnostics and Quality Tests

**Objective:** Add a debug endpoint that returns retrieval diagnostics (intent classification, scoring breakdown, candidate list) and integration tests that verify broad query quality.

**Files to modify:**
- `backend/routes/graph_routes.py` — add `GET /api/debug/retrieval` endpoint
- `backend/mentor_agent.py` — add `get_context_debug()` method that returns the full scoring breakdown

**Requirements:**

**New method on MentorAgent:**

```
get_context_debug(query: str, conversation_history: list[dict] | None = None) -> dict
```

Returns the full retrieval pipeline state for debugging:

```python
{
    "query": "what did I do this week",
    "intent": {
        "intent": "temporal_broad",
        "k": 15,
        "compact": True,
        "pre_filter": {"status": {"$nin": ["completed", "cancelled", "archived", "superseded"]}},
        "scoring_adjustments": {"temporal_boost": 0.5, "permanence_multiplier": 0.5},
    },
    "date_range": ["2026-03-17", "2026-03-23"],
    "domains": ["Life", "Planning"],
    "candidates": [
        {
            "id": "strength-training-mar-20",
            "title": "Strength Training — Mar 20",
            "type": "daily",
            "scores": {
                "semantic": 0.72,
                "domain": 0.1,
                "recency": 0.12,
                "centrality": 0.04,
                "temporal": 0.5,
                "session": 0.0,
                "status_penalty": 0.0,
                "permanence": -0.025,
                "total": 1.355,
            },
            "selected": True,
            "tier": 1,
        }
    ],
    "context_length_chars": 8500,
    "context_length_tokens_est": 2125,
    "nodes_in_context": {
        "tier1": 12,
        "tier2": 5,
        "tier3": 3,
    },
}
```

This method runs the same pipeline as `get_context()` but collects and returns the intermediate state instead of just the final context string. Implement by extracting the scoring logic into a shared internal method that both `get_context()` and `get_context_debug()` call, or by having `get_context_debug()` call `get_context()` with a debug flag.

**New endpoint:**

```
GET /api/debug/retrieval?q=<query>
```

Calls `mentor.get_context_debug(q)` and returns the result as JSON. This is a debug/development endpoint — no authentication changes needed (same bearer token as other endpoints).

**Error handling:** If retrieval fails, return 500 with `{"error": "Retrieval failed: <message>"}`.

**Integration test file:**

`backend/tests/test_retrieval_quality.py` — tests that verify the query-aware pipeline produces better results for broad queries. These tests use the existing test fixtures (conftest.py) with additional node fixtures that simulate a week of activity.

**Pattern to follow:** Same endpoint pattern as `/api/graph/stats` in `graph_routes.py`. Same method extension pattern as `get_context()`.

**Acceptance criteria:**
- `GET /api/debug/retrieval?q=what+happened+this+week` returns full diagnostic breakdown
- Diagnostic includes intent classification, per-node scoring, and tier assignment
- Integration tests verify broad temporal queries return >5 relevant nodes
- Integration tests verify entity lookups return focused, full-content results

**Test cases** (`backend/tests/test_retrieval_quality.py`, new file):
- `test_broad_temporal_returns_many_nodes` — graph with 10 daily nodes from this week, query "what happened this week" → context includes ≥8 of them
- `test_broad_temporal_compact_format` — broad query → tier 1 nodes use compact format (verify by checking context string length is reasonable for node count)
- `test_entity_lookup_full_content` — graph with a detailed goal node, query "tell me about learn piano" → context includes full content of that node
- `test_domain_filter_excludes_other_domains` — graph with mixed types, query "show me my goals" → majority of tier 1 results are Self domain
- `test_specific_temporal_not_compact` — "what's on tomorrow" with 2 nodes → uses full content format
- `test_general_query_backward_compatible` — generic query → k=5, full content, same as Sprint 5 behavior
- `test_debug_endpoint_returns_diagnostics` — call `/api/debug/retrieval?q=test` → response has intent, candidates, scores

**Test cases** (add to `backend/tests/test_routes.py`):
- `test_debug_retrieval_endpoint_exists` — GET `/api/debug/retrieval?q=test` → 200 response
- `test_debug_retrieval_endpoint_no_query` — GET `/api/debug/retrieval` (no q param) → 400 response

---

## API Response Contracts

### New endpoint: `GET /api/debug/retrieval`

**Query parameters:**
- `q` (required) — the query string to analyze

**Response:**
```json
{
  "query": "string",
  "intent": {
    "intent": "temporal_broad | temporal_specific | domain_filter | entity_lookup | general",
    "k": "number",
    "compact": "boolean",
    "pre_filter": "object | null",
    "scoring_adjustments": "object"
  },
  "date_range": ["string", "string"] | null,
  "domains": ["string"],
  "candidates": [
    {
      "id": "string",
      "title": "string",
      "type": "string",
      "scores": {
        "semantic": "number",
        "domain": "number",
        "recency": "number",
        "centrality": "number",
        "temporal": "number",
        "session": "number",
        "status_penalty": "number",
        "permanence": "number",
        "total": "number"
      },
      "selected": "boolean",
      "tier": "number | null"
    }
  ],
  "context_length_chars": "number",
  "context_length_tokens_est": "number",
  "nodes_in_context": {
    "tier1": "number",
    "tier2": "number",
    "tier3": "number"
  }
}
```

### Chat API response: unchanged

No changes to the chat response shape. The retrieval improvements are internal — Claude sees better context, but the API contract is identical.

---

## Implementation Order

```
Task 1: Query Intent Classifier (independent — pure function, no dependencies)
Task 2: Metadata-Filtered Vector Search (independent — extends vector_search.py)
  └→ Task 3: Adaptive Retrieval Pipeline (depends on Tasks 1 + 2)
      └→ Task 4: Compact Context Assembly (depends on Task 3 for intent-driven compact flag)
          └→ Task 5: Retrieval Diagnostics + Quality Tests (depends on Tasks 3 + 4 for full pipeline)
```

Tasks 1 and 2 can be built in parallel. Task 3 depends on both. Task 4 depends on Task 3. Task 5 depends on all.

Recommended sequence: **1 → 2 → 3 → 4 → 5** (build classifiers and search extensions, wire them into the pipeline, add compact assembly, then verify with diagnostics and tests).

---

## New Files

| File | Purpose |
|------|---------|
| `backend/tests/test_query_intent.py` | Query intent classification unit tests |
| `backend/tests/test_vector_search.py` | Metadata-filtered vector search tests |
| `backend/tests/test_retrieval_quality.py` | Integration tests for retrieval quality |

**Total new files: 3** (well within the 15-file limit)

---

## Definition of Done

1. All existing tests pass (`python -m pytest` from `backend/`)
2. `npm run build` succeeds in `frontend/`
3. New test files pass with all cases green
4. "What did I do this week?" with 10+ date-bearing nodes → context includes ≥8 of them (up from ≤5)
5. "Tell me about [specific node]" → full content in context (no regression)
6. "Show me my goals" → results biased toward Self-domain types
7. ChromaDB search supports `where` filtering by type and status
8. Compact assembly fits 15 nodes within the 3000-token budget
9. `GET /api/debug/retrieval` returns full diagnostic breakdown with per-node scores
10. Intent classification adds <1ms latency (pure heuristic)
11. Filtered search falls back to unfiltered when filter is too aggressive
12. No regression in conflict detection, mode classification, permanence scoring, challenge ladder, accountability, or state inference
13. No new dependencies added to `requirements.txt`
