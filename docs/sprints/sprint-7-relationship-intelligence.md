# Sprint 7: Relationship Intelligence

> **Epoch:** E6 — Reading the Room (completion)
> **Branch:** `sprint/7`
> **Goal:** People stop being contact cards. Athena tracks who you mention, how often, and what role they play — and uses that to surface relationship health, influence patterns, and social drift.

---

## Overview

E6 Part 1 (Sprint 5) gave Athena state inference and fundamentals monitoring. Part 2 — relationship tracking — is the remaining E6 acceptance criterion: "Person mentioned 5x → influence tracked on node." Sprint 6 improved retrieval infrastructure but didn't address relationships.

Right now, person nodes are static. Ethan's node says "best friend" but Athena has no idea whether you've mentioned Ethan this month or six months ago. She can't tell you "you haven't talked about Sarah in 3 weeks" or "you mention your dad mostly when stressed." Person nodes in the graph are contact cards, not relationship models.

This sprint makes relationships dynamic:

1. **Mention tracking service** — scans conversation history and graph updates to count how often each person is referenced, when they were last mentioned, and in what context (positive, negative, planning, conflict). Writes a `mention_stats` summary to person nodes.
2. **Relationship health scoring** — combines mention frequency, recency, and the `frequency` frontmatter field (daily/weekly/monthly/rare) to assess whether the relationship is active, drifting, or neglected. Similar logic to fundamentals monitoring but for the `connection` fundamental specifically.
3. **Relationship alerts** — a `RELATIONSHIP INSIGHTS:` section injected into proactive alerts when drift is detected or influence patterns emerge. "You haven't mentioned Ethan in 4 weeks — you normally talk weekly." "You've mentioned your boss 8 times this week, mostly in stress contexts."
4. **Person context enrichment** — when a person node appears in retrieval context, annotate it with mention stats so Claude can reference the relationship's trajectory, not just its static label.
5. **Relationships dashboard endpoint** — extends `/api/accountability` (or new endpoint) with relationship health data for frontend consumption.

**Not in this sprint:** Cross-person comparison ("you spend more time with X than Y"), relationship advice generation, social network visualization, multi-user relationship awareness (Stage 4).

---

## What Exists

| Component | State | Relevant to This Sprint |
|-----------|-------|------------------------|
| `vault_graph.py` `get_nodes_by_type("person")` | Returns all person nodes with attributes | Source of all person nodes for tracking |
| `vault_graph.py` `get_neighbors_with_edges()` | Returns neighbors with edge type and direction | Used to find what each person is connected to |
| Person node schema in `schema.md` | `relationship`, `frequency`, `met_through`, `company`, `location` frontmatter | `frequency` field drives drift detection thresholds |
| `chat_store.py` `get_session()` / `list_sessions()` | Session JSON with full message history + graph_updates | Source data for mention scanning |
| `accountability_service.py` `check_fundamentals()` | Monitors 6 fundamentals including "connection" | Connection fundamental already flags social neglect — relationship tracking enriches this |
| `_FUNDAMENTAL_KEYWORDS["connection"]` | `["friend", "family", "partner", "social", ...]` | Pattern for keyword-based person detection |
| `_build_proactive_alerts()` in `mentor_agent.py` | Formats broken streaks + overdue + fundamentals | Relationship alerts added alongside |
| `conflict_service.py` `commitment_to_person` | Detects schedule conflicts involving people | Already understands person-linked events |
| `vault_service.py` `update()` | Patches frontmatter, content, edges on existing nodes | Used to write mention_stats back to person nodes |
| `_node_context_full()` / `_node_context_compact()` | Formats node for system prompt context | Person nodes get enriched formatting |
| `GET /api/accountability` | Returns streaks, overdue, fundamentals | Extended with relationship data |
| Person vault files (e.g. `ethan-shorthouse.md`) | 12+ person nodes with relationship types, tags, content | Real data for mention scanning and health scoring |

---

## Architectural Decisions

### 1. Mention scanning operates on chat history stored in session files, not live message interception

Rather than hooking into the chat flow to count mentions in real-time (which adds latency to every message), the service scans historical session data on-demand. Called during alert computation (same phase as accountability), it reads recent sessions and tallies mentions.

**Why:** Real-time counting requires persistent state management, message-by-message hooks, and crash recovery. On-demand scanning from the session store is stateless, idempotent, and follows the same pattern as `calculate_streaks()` (compute from source data each time). The session store already has everything we need. With <100 sessions and <50 person nodes, scanning is instant.

### 2. Mention stats are written to person node frontmatter, not a separate store

When mention stats are computed, they're written back to the person node's frontmatter as `last_mentioned`, `mention_count_30d`, and `mention_contexts`. This means the data is available in retrieval context without any special handling — person nodes naturally carry their relationship trajectory.

**Why:** Keeps the graph as the single source of truth. Person nodes already have `frequency` and `relationship` fields — adding mention stats alongside them is natural. No new persistence layer. The stats are recomputed periodically (not cached permanently), so the frontmatter reflects a recent snapshot, not a stale counter.

### 3. Drift detection uses the existing `frequency` field as the expected cadence, not a fixed threshold

A person with `frequency: weekly` who hasn't been mentioned in 3 weeks is drifting. A person with `frequency: rare` who hasn't been mentioned in 3 months is normal. The threshold adapts per-person based on their `frequency` frontmatter value.

**Why:** Fixed thresholds (e.g., "no mention in 14 days = drift") would produce false positives for acquaintances and false negatives for close relationships. The `frequency` field already captures the user's intended cadence. Drift = actual frequency diverges from stated frequency.

### 4. Context detection (positive/negative/planning/conflict) is keyword-based, not sentiment analysis

When scanning mentions, the service tags each mention's context using keyword lists (same pattern as state inference signals and conflict detection signals). Not LLM-based sentiment analysis.

**Why:** Calling Claude to classify every mention would be expensive and slow. Keyword matching captures the primary signal ("stressed about X" = negative, "planning trip with X" = planning, "cancelled dinner with X" = negative) with the same approach used throughout the codebase. The context distribution (mostly-positive vs mostly-negative) is the signal, not individual mention sentiment.

### 5. Relationship alerts are injected into the existing proactive alerts system, not a separate channel

Relationship insights appear in the same `PROACTIVE ALERTS:` section as broken streaks and overdue commitments. They follow the same state-aware suppression rules (fewer alerts when stressed/low energy).

**Why:** Consistency. The alert system already handles priority, suppression, and formatting. Relationship alerts are conceptually the same as fundamentals monitoring — checking whether a core need (connection) is being met. Adding a separate alert channel would fragment the user's attention.

---

## Tasks

### Task 1: Mention Tracking Service

**Objective:** Create `relationship_service.py` with functions that scan conversation history to track person mention frequency, recency, and context.

**Files to create:**
- `backend/services/relationship_service.py`

**Requirements:**

```
scan_mentions(chat_store: ChatStore, graph: VaultGraph, lookback_days: int = 30) -> list[dict]
```

Returns mention stats for each person node:

```python
{
    "person_id": "ethan-shorthouse",
    "person_title": "Ethan Shorthouse",
    "relationship": "friend",           # from frontmatter
    "expected_frequency": "weekly",      # from frontmatter
    "mention_count": 7,                  # mentions in lookback window
    "last_mentioned": "2026-03-20",      # ISO date of most recent mention
    "days_since_mention": 3,
    "mention_contexts": {                # distribution of mention contexts
        "positive": 3,
        "negative": 1,
        "planning": 2,
        "neutral": 1,
    },
    "recent_topics": ["gym", "liverpool trip"],  # extracted from surrounding context
}
```

**Mention detection logic:**

1. Get all person nodes from graph via `get_nodes_by_type("person")`
2. Build a lookup of person titles and aliases (the node title, plus any `aliases` tag if present). Match case-insensitively.
3. For each session in `chat_store.list_sessions()`, scan user messages within the lookback window (filter by `timestamp` or `created` date)
4. For each user message, check if any person title/alias appears as a substring (case-insensitive)
5. Also check `graph_updates` in assistant messages — if an update targets or links to a person node, count that as a mention
6. For each mention, classify context using keyword co-occurrence in the same message:

**Context keyword lists:**

```python
_POSITIVE_CONTEXT = ["great", "amazing", "love", "fun", "happy", "excited",
                     "looking forward", "enjoyed", "good time", "proud of"]
_NEGATIVE_CONTEXT = ["stressed", "annoyed", "frustrated", "worried", "argue",
                     "conflict", "upset", "angry", "disappointed", "anxious",
                     "cancelled", "flaked", "let down"]
_PLANNING_CONTEXT = ["plan", "trip", "visit", "meet", "dinner", "call",
                     "catch up", "schedule", "going to see", "hanging out"]
```

Default to "neutral" if no context keywords match.

**Edge cases:**
- Person with no mentions in any session → `mention_count: 0`, `last_mentioned: None`
- Person title is a common word (e.g., "Ben") → accept false positives; short names will match more broadly, but that's better than missing real mentions. The frequency/context distribution smooths noise.
- Session with no timestamp → skip (can't determine if within lookback window)
- Person title appears in assistant message → don't count (only user messages + graph_updates indicate the user is thinking about this person)
- Same person mentioned multiple times in one message → count as 1 mention (per-message dedup)

**Pattern to follow:** Same pure-function-over-data pattern as `calculate_streaks()` and `find_overdue_commitments()`. Takes data sources, returns structured dicts. No side effects on the scan itself.

**Acceptance criteria:**
- Person mentioned 5 times in last 30 days → `mention_count: 5`
- Person not mentioned → `mention_count: 0`, `last_mentioned: None`
- Context classification captures positive/negative/planning distribution
- Graph update targeting a person node counts as a mention
- Results include all person nodes in the graph (even those with zero mentions)

**Test cases** (`backend/tests/test_relationship_service.py`, new file):
- `test_mention_count_basic` — person title in 3 user messages → mention_count 3
- `test_mention_case_insensitive` — "ethan" matches "Ethan Shorthouse"
- `test_mention_graph_update_counts` — graph_update targeting person node → counted
- `test_no_mentions_returns_zero` — person with no mentions → mention_count 0, last_mentioned None
- `test_lookback_window_filters` — mention from 45 days ago with 30-day lookback → not counted
- `test_context_positive` — "had a great time with Ethan" → positive context
- `test_context_negative` — "stressed about the argument with Sarah" → negative context
- `test_context_planning` — "planning to visit Ben next week" → planning context
- `test_context_neutral_default` — "talked to Dad" → neutral context
- `test_per_message_dedup` — person mentioned 3 times in one message → counts as 1
- `test_assistant_messages_excluded` — person name in assistant message only → not counted
- `test_all_persons_included` — 3 person nodes, 1 mentioned → all 3 in results

---

### Task 2: Relationship Health Scoring and Drift Detection

**Objective:** Assess relationship health by comparing actual mention frequency to expected frequency, and detect drift.

**Files to modify:**
- `backend/services/relationship_service.py` — add `assess_relationship_health()` function

**Requirements:**

```
assess_relationship_health(mention_stats: list[dict], today: date) -> list[dict]
```

Takes output of `scan_mentions()` and enriches each entry with health assessment:

```python
{
    # ...all fields from scan_mentions()...
    "health": "active" | "drifting" | "neglected" | "no_data",
    "drift_days": 14,               # days past expected frequency threshold
    "influence_score": 0.7,          # 0.0-1.0 based on mention volume relative to all persons
    "influence_rank": 2,             # rank among all persons by mention count
    "context_profile": "mostly_positive",  # "mostly_positive" | "mostly_negative" | "mixed" | "neutral"
}
```

**Health assessment logic:**

Map `expected_frequency` to threshold days:
```python
_FREQUENCY_THRESHOLDS = {
    "daily": 3,       # 3 days without mention = drifting
    "weekly": 14,     # 2 weeks without mention = drifting
    "monthly": 45,    # 45 days without mention = drifting
    "rare": 120,      # 4 months without mention = drifting
    "inactive": None,  # never flag as drifting
}
```

Health states:
- `active` — `days_since_mention` ≤ threshold
- `drifting` — `days_since_mention` > threshold AND ≤ threshold × 2
- `neglected` — `days_since_mention` > threshold × 2
- `no_data` — no `expected_frequency` set, or `expected_frequency` is "inactive", or `last_mentioned` is None and no frequency expectation

`drift_days` = max(0, `days_since_mention` - threshold)

**Influence scoring:**

`influence_score` = this person's `mention_count` / max(`mention_count` across all persons). Capped at 1.0. If max is 0, all scores are 0.0.

`influence_rank` = rank by mention_count descending (1 = most mentioned).

**Context profile:**

Based on `mention_contexts` distribution:
- If positive > (negative + neutral) → `mostly_positive`
- If negative > (positive + neutral) → `mostly_negative`
- If positive > 0 AND negative > 0 → `mixed`
- Otherwise → `neutral`

**Edge cases:**
- Person with `frequency: inactive` → always `no_data` (user explicitly doesn't track this relationship)
- Person without `frequency` field → default to `monthly` threshold
- Zero mentions across all persons → all influence scores 0.0
- Tie in mention count → same rank

**Pattern to follow:** Same enrichment pattern as `calculate_streaks()` returning `streak_status`. Takes raw data, returns health-assessed data.

**Acceptance criteria:**
- `frequency: weekly` person not mentioned in 15 days → `drifting`
- `frequency: weekly` person not mentioned in 30 days → `neglected`
- `frequency: rare` person not mentioned in 90 days → `active` (within threshold)
- Most-mentioned person has `influence_score: 1.0`
- Context profile correctly reflects mention distribution

**Test cases** (add to `backend/tests/test_relationship_service.py`):
- `test_health_active` — mentioned 2 days ago, frequency weekly → active
- `test_health_drifting` — mentioned 15 days ago, frequency weekly → drifting, drift_days=1
- `test_health_neglected` — mentioned 30 days ago, frequency weekly → neglected
- `test_health_no_data_inactive` — frequency "inactive" → no_data regardless of mentions
- `test_health_no_frequency_defaults_monthly` — no frequency field → uses monthly threshold (45 days)
- `test_influence_score_highest` — person with most mentions → influence_score 1.0
- `test_influence_score_relative` — 5 mentions vs max 10 → influence_score 0.5
- `test_influence_rank_ordering` — 3 persons with different counts → ranked correctly
- `test_context_profile_mostly_positive` — 5 positive, 1 negative → mostly_positive
- `test_context_profile_mixed` — 3 positive, 3 negative → mixed
- `test_context_profile_neutral` — all neutral → neutral

---

### Task 3: Relationship Alert Injection and Person Context Enrichment

**Objective:** Surface relationship drift in proactive alerts and enrich person nodes in retrieval context with mention stats.

**Files to modify:**
- `backend/mentor_agent.py` — add `_build_relationship_alerts()`, modify `_build_proactive_alerts()`, modify person node formatting in context assembly
- `backend/services/chat_service.py` — call relationship service in `_build_alerts()`, pass relationship data downstream

**Requirements:**

**In `chat_service.py` `_build_alerts()`:**

Add relationship scanning alongside existing accountability checks:

```python
from services.relationship_service import scan_mentions, assess_relationship_health

mention_stats = scan_mentions(self.chat_store, self.graph)
relationships = assess_relationship_health(mention_stats, date.today())
```

Add to the alerts dict:

```python
alerts = {
    # ...existing fields...
    "drifting_relationships": [r for r in relationships if r["health"] == "drifting"],
    "neglected_relationships": [r for r in relationships if r["health"] == "neglected"],
    "high_influence": [r for r in relationships if r["influence_rank"] <= 3 and r["mention_count"] >= 5],
}
```

**In `mentor_agent.py` `_build_proactive_alerts()`:**

Add after the existing NEGLECTED FUNDAMENTALS section:

```
RELATIONSHIP DRIFT — these people may be falling out of touch:
- "Ethan Shorthouse" (friend, expected: weekly) — last mentioned 18 days ago, 4 days past expected.
  Normally a positive relationship (5 positive, 1 negative mentions this month).
- "Dad" (family, expected: weekly) — last mentioned 21 days ago, 7 days past expected.
  Don't lecture about calling family — ask what's going on.

HIGH INFLUENCE — the people on the user's mind most:
- "Boss" mentioned 8 times this month (mostly in stress contexts) — may be worth exploring.
```

Cap at 2 drifting/neglected + 1 high-influence alert. Respect state-aware suppression.

**Guidance for Claude:**
- For drifting relationships: "Surface this gently when the user mentions anything social. Don't nag."
- For neglected relationships: "Only raise if directly relevant. Don't guilt-trip."
- For high-influence with negative context: "The user keeps bringing this person up in stress. Consider exploring the dynamic when relevant."
- For high-influence with positive context: don't alert (that's healthy — not a concern).

High-influence alerts should ONLY fire when `context_profile` is `mostly_negative` or `mixed` AND `mention_count >= 5`. Positive high influence is normal and healthy — not something to flag.

**Person context enrichment in `_node_context_full()` and `_node_context_compact()`:**

When formatting a person node for retrieval context, check if relationship health data is available (passed via a `relationship_data` dict keyed by person_id). If so, append a line:

```
[person] Ethan Shorthouse (friend, weekly)
  Mentions: 7 this month (5 positive, 1 negative, 1 planning) | Last: 3 days ago | Health: active
```

For compact format, add just: `| Mentions: 7/month, last 3d ago`

Pass relationship data into `get_context()` as an optional parameter. In `_retrieve()`, when assembling context for person-type nodes, look up the enrichment data and include it in the formatted output.

**Edge cases:**
- If relationship service raises → catch, log, proceed with empty relationship data (same defensive pattern)
- If no person nodes exist → empty relationship alerts, no enrichment
- If all relationships are active → no drift alerts (don't inject noise)
- Relationship alerts count toward the total alert cap (existing stress-aware suppression applies)

**Pattern to follow:** Same `_build_X_alerts()` formatting pattern. Same defensive try/except in `chat_service.py`. Same context enrichment pattern as permanence labels on conflict nodes.

**Acceptance criteria:**
- Drifting relationships appear in proactive alerts with days-past-expected
- Neglected relationships appear with stronger framing
- High-influence negative relationships flagged when mention_count ≥ 5
- High-influence positive relationships NOT flagged
- Person nodes in retrieval context include mention stats when available
- Relationship alerts suppressed when user is stressed (same rules as other alerts)
- Relationship service errors don't break chat flow

**Test cases** (add to `backend/tests/test_mentor_agent.py`):
- `test_proactive_alerts_drifting_relationship` — drifting person appears in formatted output
- `test_proactive_alerts_neglected_relationship` — neglected person appears with stronger framing
- `test_proactive_alerts_high_influence_negative` — person mentioned 8x in stress contexts → appears in alerts
- `test_proactive_alerts_high_influence_positive_not_flagged` — person mentioned 10x positively → NOT in alerts
- `test_proactive_alerts_no_drift_no_output` — all relationships active → no relationship section
- `test_proactive_alerts_relationship_cap` — >3 drifting → only 2 shown
- `test_person_context_enriched` — person node formatted with mention stats when data available
- `test_person_context_not_enriched_without_data` — person node formatted normally when no relationship data

**Test cases** (add to `backend/tests/test_routes.py` or `test_chat_service.py`):
- `test_relationship_error_doesnt_break_chat` — mock relationship service to raise → chat proceeds

---

### Task 4: Mention Stats Persistence to Person Nodes

**Objective:** Write computed mention stats back to person node frontmatter so the data persists in the vault and is available in future retrieval without recomputation.

**Files to modify:**
- `backend/services/relationship_service.py` — add `persist_mention_stats()` function
- `backend/services/chat_service.py` — call persistence after alerts are built (not on every message — throttled)

**Requirements:**

```
persist_mention_stats(vault_service: VaultService, relationships: list[dict], today: date) -> int
```

For each person with `mention_count > 0`, update the person node's frontmatter with:

```yaml
last_mentioned: '2026-03-20'
mention_count_30d: 7
mention_profile: mostly_positive
```

Returns the count of nodes updated.

**Throttling:**

Don't run persistence on every message — it's expensive (N vault writes for N person nodes). Instead, in `chat_service.py`, check a simple time-based throttle: only persist if the last persistence was >1 hour ago. Track via a class-level timestamp `_last_relationship_persist`.

```python
import time

class ChatService:
    _last_relationship_persist: float = 0.0

    def _maybe_persist_relationships(self, relationships: list[dict]):
        now = time.time()
        if now - self._last_relationship_persist < 3600:  # 1 hour
            return
        try:
            persist_mention_stats(self.vault_service, relationships, date.today())
            ChatService._last_relationship_persist = now
        except Exception:
            logger.exception("Relationship persistence failed — non-critical, skipping")
```

Call this after `_build_alerts()` in both `send_message()` and `stream_message()`.

**Update logic:**

For each person with `mention_count > 0`:
1. Build frontmatter patch: `{"last_mentioned": str(today), "mention_count_30d": count, "mention_profile": profile}`
2. Call `vault_service.update({"node_id": person_id, "changes": {"frontmatter": patch}})`
3. Skip persons where stats haven't changed (compare against existing frontmatter to avoid unnecessary writes)

**Edge cases:**
- Person node doesn't exist (deleted between scan and persist) → skip, log warning
- Vault service `update()` fails for one person → continue with others (don't let one failure block all)
- First run (no existing stats) → write all; subsequent runs → only write changes
- `mention_count_30d: 0` → don't write (removing stale stats is unnecessary complexity; they'll naturally show 0 on next scan)

**Pattern to follow:** Same vault_service.update() pattern used in `_confirm_pending()` in `chat_service.py`. Same defensive error handling with try/except per operation.

**Acceptance criteria:**
- Person nodes get `last_mentioned`, `mention_count_30d`, `mention_profile` in frontmatter after persistence
- Persistence runs at most once per hour (throttled)
- Failed persistence doesn't break chat flow
- Unchanged stats don't trigger unnecessary writes
- Stats are available in retrieval context from frontmatter (no special handling needed)

**Test cases** (add to `backend/tests/test_relationship_service.py`):
- `test_persist_writes_frontmatter` — person with 5 mentions → frontmatter updated with mention_count_30d: 5
- `test_persist_skips_zero_mentions` — person with 0 mentions → no update call
- `test_persist_skips_unchanged` — same stats as existing frontmatter → no update call
- `test_persist_returns_count` — 3 persons updated → returns 3
- `test_persist_handles_missing_node` — deleted person → skipped, no error raised

**Test cases** (add to `backend/tests/test_chat_service.py` or `test_routes.py`):
- `test_relationship_persist_throttled` — two messages within 1 hour → persistence runs once

---

### Task 5: Relationships Dashboard Endpoint

**Objective:** Extend `GET /api/accountability` with relationship health data and add a dedicated relationship detail endpoint.

**Files to modify:**
- `backend/routes/graph_routes.py` — extend `/api/accountability` response, add `GET /api/relationships` endpoint

**Requirements:**

**Extend `GET /api/accountability` response:**

Add a `relationships` summary section:

```json
{
  "streaks": [...],
  "overdue": [...],
  "summary": {...},
  "fundamentals": [...],
  "fundamentals_summary": {...},
  "relationships_summary": {
    "total_persons": 12,
    "active": 7,
    "drifting": 3,
    "neglected": 1,
    "no_data": 1,
    "most_mentioned": "ethan-shorthouse"
  }
}
```

**New endpoint: `GET /api/relationships`**

Returns full relationship health data:

```json
{
  "relationships": [
    {
      "person_id": "ethan-shorthouse",
      "person_title": "Ethan Shorthouse",
      "relationship": "friend",
      "expected_frequency": "weekly",
      "mention_count": 7,
      "last_mentioned": "2026-03-20",
      "days_since_mention": 3,
      "health": "active",
      "drift_days": 0,
      "influence_score": 0.7,
      "influence_rank": 2,
      "context_profile": "mostly_positive",
      "mention_contexts": {
        "positive": 5,
        "negative": 1,
        "planning": 1,
        "neutral": 0
      },
      "recent_topics": ["gym", "liverpool trip"]
    }
  ],
  "summary": {
    "total_persons": 12,
    "active": 7,
    "drifting": 3,
    "neglected": 1,
    "no_data": 1,
    "most_mentioned": "ethan-shorthouse",
    "highest_influence": ["ethan-shorthouse", "dad", "boss-name"]
  }
}
```

Results sorted by: neglected first, then drifting, then active. Within each health tier, sorted by influence_score descending.

**Error handling:** If relationship service raises, return 500 with `{"error": "Failed to compute relationship data"}`. Log the exception.

**Pattern to follow:** Same endpoint pattern as `/api/accountability` and `/api/debug/retrieval`. Pure read operation. The chat_store and graph are the only inputs.

**Acceptance criteria:**
- `GET /api/accountability` includes `relationships_summary` section
- `GET /api/relationships` returns full relationship health data for all persons
- Results sorted by health urgency (neglected → drifting → active)
- Summary counts match the detail array
- Endpoint handles errors gracefully

**Test cases** (add to `backend/tests/test_routes.py`):
- `test_accountability_includes_relationships_summary` — response has `relationships_summary` key
- `test_relationships_endpoint_returns_data` — GET `/api/relationships` → 200 with relationships array
- `test_relationships_endpoint_sorted_by_health` — neglected persons appear before active
- `test_relationships_endpoint_summary_counts` — summary counts match array
- `test_relationships_endpoint_empty_graph` — no person nodes → empty array, zero counts
- `test_relationships_endpoint_error_handling` — service raises → 500 response

---

## API Response Contracts

### Extended endpoint: `GET /api/accountability`

Existing shape unchanged. New addition:

```json
{
  "...existing fields...",
  "relationships_summary": {
    "total_persons": "number",
    "active": "number",
    "drifting": "number",
    "neglected": "number",
    "no_data": "number",
    "most_mentioned": "string | null"
  }
}
```

### New endpoint: `GET /api/relationships`

```json
{
  "relationships": [
    {
      "person_id": "string",
      "person_title": "string",
      "relationship": "string | null",
      "expected_frequency": "string | null",
      "mention_count": "number",
      "last_mentioned": "string | null",
      "days_since_mention": "number | null",
      "health": "active | drifting | neglected | no_data",
      "drift_days": "number",
      "influence_score": "number (0.0-1.0)",
      "influence_rank": "number",
      "context_profile": "mostly_positive | mostly_negative | mixed | neutral",
      "mention_contexts": {
        "positive": "number",
        "negative": "number",
        "planning": "number",
        "neutral": "number"
      },
      "recent_topics": ["string"]
    }
  ],
  "summary": {
    "total_persons": "number",
    "active": "number",
    "drifting": "number",
    "neglected": "number",
    "no_data": "number",
    "most_mentioned": "string | null",
    "highest_influence": ["string"]
  }
}
```

### Chat API response: unchanged

No changes to the chat response shape. The system prompt now may include a `RELATIONSHIP DRIFT:` section within proactive alerts, and person nodes in context may include mention stats.

---

## Implementation Order

```
Task 1: Mention Tracking Service (independent — new file, no dependencies)
  └→ Task 2: Relationship Health Scoring (depends on Task 1 for mention data)
      └→ Task 3: Alert Injection + Person Context Enrichment (depends on Tasks 1 + 2)
      └→ Task 4: Mention Stats Persistence (depends on Tasks 1 + 2 for data)
      └→ Task 5: Relationships Dashboard Endpoint (depends on Tasks 1 + 2 for service functions)
```

Task 1 is the foundation. Tasks 3, 4, and 5 all depend on Tasks 1 + 2 but are independent of each other.

Recommended sequence: **1 → 2 → 3 → 4 → 5** (build the service, then wire it into chat flow, then persist stats, then expose via API).

---

## New Files

| File | Purpose |
|------|---------|
| `backend/services/relationship_service.py` | Mention tracking, health scoring, persistence |
| `backend/tests/test_relationship_service.py` | Relationship service unit tests |

**Total new files: 2** (well within the 15-file limit)

---

## Definition of Done

1. All existing tests pass (`python -m pytest` from `backend/`)
2. `npm run build` succeeds in `frontend/`
3. New test files pass with all cases green
4. Person mentioned 5+ times in 30 days → `influence_score` > 0 and appears in high-influence data
5. Person with `frequency: weekly` not mentioned in 15+ days → `health: drifting`
6. Drifting/neglected relationships appear in `PROACTIVE ALERTS:` section of system prompt
7. High-influence persons with negative context profile flagged in alerts
8. Person nodes in retrieval context include mention stats (count, last mentioned, profile)
9. Mention stats persisted to person node frontmatter (throttled to 1x/hour)
10. `GET /api/accountability` includes `relationships_summary`
11. `GET /api/relationships` returns full relationship health data
12. Relationship service errors don't break chat flow
13. No regression in conflict detection, mode classification, permanence scoring, challenge ladder, accountability, state inference, or query-aware retrieval
14. No new dependencies added to `requirements.txt`
