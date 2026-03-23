# Sprint 6: Relationship Intelligence

> **Epoch:** E6 — Reading the Room (completion)
> **Branch:** `sprint/6`
> **Goal:** People nodes become dynamic — Athena tracks who the user mentions, how often, and in what context. Social patterns surface naturally. Relationships stop being contact cards and start being intelligence.

---

## Overview

Sprint 5 gave Athena state inference (stress/energy detection from message patterns) and fundamentals monitoring (alerting when core human needs go neglected). But person nodes are still static contact cards — "Ben: co-founder, friend." Athena doesn't know that you've mentioned Sarah 12 times this month but haven't seen her, that your dad only comes up in stressful contexts, or that you're socially isolating after a breakup.

This sprint adds the remaining E6 capability:

1. **Relationship service** — a new `relationship_service.py` that scans conversation history and graph activity to compute person mention frequency, interaction recency, and sentiment context per person node.
2. **Social pattern detection** — analyzes overall social activity to flag isolation (declining person mentions, no connection-fundamental activity) or overcommitting (excessive social obligations consuming time/energy).
3. **Relationship context injection** — a `SOCIAL CONTEXT:` section in the system prompt that surfaces relevant relationship intelligence when the user mentions a person or discusses social topics.
4. **SOUL.md Relationships section** — persistent behavioral instructions for how Athena handles relationship observations.
5. **Relationship data in accountability endpoint** — extends `GET /api/accountability` with a `relationships` key for frontend consumption.

This completes E6 and Stage 1 of the roadmap.

**Not in this sprint:** Cross-session longitudinal analysis (requires reading all historical sessions — deferred to a dedicated performance pass), influence mapping beyond mention frequency (requires deeper semantic analysis), relationship quality scoring (too subjective without more data).

---

## What Exists

| Component | State | Relevant to This Sprint |
|-----------|-------|------------------------|
| `vault/People/Persons/*.md` | 11 person nodes with `relationship` frontmatter, tags, edges | Source of truth for who exists in the graph |
| `schema.md` person type | `relationship: friend\|mentor\|family\|colleague\|acquaintance\|partner` | Relationship type available on every person node |
| `chat_store.py` `get_session()` | Returns session JSON with messages array (role, content, timestamp) | Message content is scanned for person name mentions |
| `chat_store.py` session files | JSON files in `backend/chat_sessions/` | Historical sessions provide mention frequency data |
| `_build_proactive_alerts()` in `mentor_agent.py` | Formats broken streaks + overdue + fundamentals for system prompt | Social pattern alerts injected alongside existing alerts |
| `_build_state_note()` in `mentor_agent.py` | Injects USER STATE section with energy/stress | Social context injected after state, before alerts |
| `accountability_service.py` `check_fundamentals()` | Already checks "connection" fundamental via keyword matching | Relationship service complements this with person-level detail |
| `_DOMAIN_KEYWORDS["People"]` in `mentor_agent.py` | Keywords: friend, family, colleague, partner, mentor, person, etc. | Domain classification already detects people-related queries |
| `_FUNDAMENTAL_KEYWORDS["connection"]` in `accountability_service.py` | Keywords: friend, family, partner, social, call, meet, dinner with, etc. | Overlapping signal — relationship service adds per-person granularity |
| `graph_routes.py` `/api/accountability` | Returns streaks, overdue, fundamentals, summaries | Extended with relationship data |
| `vault_graph.py` `get_nodes_by_type()` | Returns all nodes of a given type | Used to get all person nodes |
| `vault_graph.py` `get_neighbors_with_edges()` | Returns neighbors with `_edge_type` metadata | Used to find nodes connected to person nodes |

---

## Architectural Decisions

### 1. Relationship service is a standalone module, like conflict and state services

Person mention scanning, frequency computation, and social pattern detection is ~150-200 lines — enough to warrant its own file. Follows the `conflict_service.py` / `state_service.py` / `accountability_service.py` pattern: pure functions over data, structured dict output, no side effects.

**Why:** Consistent with every service added since E3. Clear input (graph, session messages) and output (relationship intelligence dicts). Keeps `mentor_agent.py` from growing further.

### 2. Mention detection uses person node titles and tags, not NER

Rather than running named entity recognition (which would require an NLP dependency or an LLM call), the service builds a lookup from existing person node titles (e.g., "Ben", "Dad", "Juno") and scans message content for case-insensitive word-boundary matches. This is the same keyword-matching approach used throughout the codebase (`_DOMAIN_KEYWORDS`, `_CANCELLATION_SIGNALS`, `_FUNDAMENTAL_KEYWORDS`).

**Why:** Zero new dependencies. Person nodes already exist in the graph with human-readable titles. NER would add latency, complexity, and a dependency for marginal gain — most people are mentioned by name, which the graph already knows. False positives (e.g., "ben" as a common word) are mitigated by word-boundary matching and context (People domain classification).

### 3. Mention scanning covers the current session only (not all historical sessions)

The service scans messages in the current session (same as state inference) to compute per-session mention counts. Cross-session frequency analysis (scanning all historical session files) is deferred — it requires iterating potentially hundreds of JSON files, which adds I/O and latency to every chat request.

**Why:** Sprint 5 established this boundary for state inference (AD#2). Single-session data still provides value: "you've mentioned Sarah 4 times in this conversation but haven't proposed seeing her" is useful. The graph itself provides longer-term signal — person nodes with recent `updated` timestamps indicate active relationships. Cross-session frequency can be computed as a background job in a follow-up sprint.

### 4. Social pattern detection uses graph activity + session signals, not just mentions

Isolation detection doesn't rely solely on mention frequency. It combines: (a) connection fundamental status from `check_fundamentals()`, (b) person node recency (when were person nodes last updated), (c) session mention patterns (declining mentions). This multi-signal approach follows the state inference precedent (AD#5 from Sprint 5: "require 3+ signals before adjusting").

**Why:** A single signal is noisy. Someone might not mention friends in a work-focused session — that's not isolation. But no person-node updates in 3 weeks + connection fundamental neglected + declining mentions across sessions = real signal.

### 5. Relationship context is injected only when relevant, not every message

Unlike proactive alerts (which are always computed and suppressed by state), relationship context is only injected when the user's message mentions a person or is classified in the People domain. This avoids adding noise to non-social conversations.

**Why:** The system prompt is already dense (identity + instructions + context + dismissed + state + alerts + conflicts + mode + challenges). Adding relationship intelligence to every message would bloat the prompt. Gating on People-domain classification (which already exists in `_classify_domains()`) keeps it focused.

---

## Tasks

### Task 1: Relationship Service — Mention Scanning and Person Intelligence

**Objective:** Create `relationship_service.py` with functions that analyze session messages and graph state to compute per-person intelligence.

**Files to create:**
- `backend/services/relationship_service.py`

**Requirements:**

```
scan_person_mentions(messages: list[dict], person_nodes: list[dict]) -> list[dict]
```

Takes session messages and all person nodes from the graph. Returns per-person mention reports:

```python
{
    "person_id": "ben",
    "person_title": "Ben",
    "relationship": "friend",         # from person node frontmatter
    "mention_count": 4,               # times mentioned in this session
    "contexts": [                     # snippets of messages where mentioned (first 3)
        "Ben and I are planning to start Citadel Technica in May",
        "Need to sync with Ben about the investor deck",
    ],
    "sentiment": "positive" | "neutral" | "negative" | "mixed",  # simple keyword heuristic
}
```

**Mention detection logic:**

1. Build a lookup dict from person nodes: `{title_lower: node, ...}`. Also index any `aliases` tag values (for nicknames).
2. For each user message, scan for person title matches using word-boundary-aware substring matching. A "match" means the person's title appears as a whole word (not inside another word). For single-word titles like "Ben", use `\bBen\b` regex pattern. For multi-word titles like "Harry Heppleston", match any component word as well as the full title.
3. For each matched person, extract the surrounding sentence as context (up to 100 chars).
4. Count total mentions per person across all user messages in the session.

**Sentiment heuristic:**

Simple keyword scoring on the context sentences where the person is mentioned:
- Positive signals: "love", "great", "amazing", "happy", "excited", "support", "helpful", "proud", "fun", "enjoy"
- Negative signals: "annoyed", "frustrated", "upset", "angry", "conflict", "difficult", "toxic", "avoid", "stressed", "worried about"
- If both present → "mixed". If neither → "neutral". More positive than negative → "positive". Vice versa → "negative".

This is deliberately simple — the same keyword-list approach as every other heuristic in the codebase. It's not trying to be a sentiment analysis engine; it's providing a rough signal.

```
get_person_intelligence(graph, today: date) -> list[dict]
```

Returns graph-level intelligence for each person node:

```python
{
    "person_id": "ben",
    "person_title": "Ben",
    "relationship": "friend",
    "last_updated": "2026-03-16",        # person node's updated field
    "days_since_update": 7,              # how stale the person node is
    "connected_node_count": 5,           # how many nodes link to this person
    "connected_active_count": 3,         # how many of those are active (not completed/cancelled)
    "connection_types": ["project", "event", "experience"],  # types of connected nodes
}
```

This provides the graph-level view: how embedded is this person in the user's life graph? Someone with 15 connections across 5 domains is a core relationship. Someone with 1 connection is peripheral.

**Edge cases:**
- Person title is a common word (e.g., if someone names a person "Will") — word-boundary matching reduces false positives but can't eliminate them entirely. Accept this limitation.
- Person title appears in assistant messages — only scan user messages (same as state inference).
- Empty messages list → return empty results.
- Person node with no title → use node ID as title.
- Multiple person nodes with the same first name → match both, report separately.

**Pattern to follow:** Same pure-function-over-data pattern as `infer_state()` in `state_service.py` and `calculate_streaks()` in `accountability_service.py`. Takes data, returns structured dicts. No side effects.

**Acceptance criteria:**
- Message mentioning "Ben" correctly matches the `ben` person node
- Mention count accurately reflects occurrences across session messages
- Context snippets capture the sentence where the person was mentioned
- Sentiment defaults to "neutral" when no signal words present
- Person nodes with recent `updated` dates have low `days_since_update`
- Connected node counts accurately reflect graph edges

**Test cases** (`backend/tests/test_relationship_service.py`, new file):
- `test_mention_detection_basic` — message containing "Ben" matches person node with title "Ben"
- `test_mention_detection_word_boundary` — "benefit" does NOT match person "Ben"
- `test_mention_count_across_messages` — 3 messages mentioning "Ben" → mention_count 3
- `test_mention_context_extracted` — context snippet captures surrounding text
- `test_sentiment_positive` — mention with "great" and "excited" → sentiment "positive"
- `test_sentiment_negative` — mention with "frustrated" → sentiment "negative"
- `test_sentiment_neutral_default` — mention with no signal words → "neutral"
- `test_sentiment_mixed` — both positive and negative signals → "mixed"
- `test_no_person_nodes_empty_result` — no person nodes → empty list
- `test_only_user_messages_scanned` — assistant message mentioning a person → not counted
- `test_person_intelligence_days_since` — person updated 7 days ago → days_since_update 7
- `test_person_intelligence_connected_counts` — person with 3 active, 2 completed neighbors → connected_active_count 3
- `test_empty_messages_empty_result` — empty messages → empty scan result

---

### Task 2: Social Pattern Detection

**Objective:** Add a function that analyzes overall social health by combining mention data, graph state, and fundamentals to detect isolation or overcommitting patterns.

**Files to modify:**
- `backend/services/relationship_service.py` — add `detect_social_patterns()`

**Requirements:**

```
detect_social_patterns(
    person_intelligence: list[dict],
    fundamentals: list[dict],
    graph,
    today: date,
) -> dict
```

Returns a social health assessment:

```python
{
    "pattern": "healthy" | "isolating" | "overcommitting" | "no_data",
    "confidence": "low" | "medium" | "high",
    "signals": [
        {"type": "stale_relationships", "detail": "8 of 11 person nodes not updated in 30+ days"},
        {"type": "connection_neglected", "detail": "Connection fundamental neglected for 21 days"},
    ],
    "stale_relationships": [         # person nodes not updated in 30+ days
        {"person_id": "romane-french-friend", "person_title": "Romane", "days_since_update": 45}
    ],
    "active_relationships": [        # person nodes updated in last 14 days
        {"person_id": "ben", "person_title": "Ben", "days_since_update": 7}
    ],
}
```

**Signal detectors:**

1. **Stale relationships** — count person nodes with `days_since_update > 30`. If >60% of person nodes are stale → score 1.0 (isolation signal). 40-60% → 0.5. <40% → 0.0.

2. **Connection fundamental** — check if the connection fundamental is `neglected` in the fundamentals data. Neglected → score 1.0. No data → 0.5. Active → 0.0.

3. **Social node activity** — count recently created/updated nodes of social types (event, experience, daily) that have edges to person nodes. <2 in last 14 days → 1.0 (isolation). 2-4 → 0.5. >4 → 0.0.

4. **Social overload** — count active events/tasks with person edges due in the next 7 days. >5 → 1.0 (overcommitting). 3-5 → 0.5. <3 → 0.0. This is an overcommitting signal, not isolation.

**Pattern derivation:**

- Count isolation signals (stale_relationships + connection_neglected + low social_node_activity) with score ≥ 0.5
- Count overcommit signals (social_overload) with score ≥ 0.5
- `confidence`: 0-1 signals → "low", 2 → "medium", 3+ → "high"
- If isolation signals ≥ 2 → pattern: "isolating"
- If overcommit signal ≥ 1.0 AND isolation signals == 0 → pattern: "overcommitting"
- If no person nodes exist → pattern: "no_data"
- Otherwise → pattern: "healthy"

**Edge cases:**
- No person nodes in graph → `no_data` pattern with confidence "low"
- Only 1-2 person nodes → scale thresholds (60% of 2 = 1.2, so 2/2 stale triggers)
- Person nodes that are `status: inactive` → exclude from analysis

**Pattern to follow:** Same multi-signal threshold approach as `infer_state()` in `state_service.py` (Sprint 5 AD#5). Same keyword-list + score derivation pattern.

**Acceptance criteria:**
- 8/11 person nodes stale + connection neglected → "isolating" with medium+ confidence
- All person nodes recently updated + active social events → "healthy"
- 6+ social events this week → "overcommitting"
- No person nodes → "no_data"
- Stale/active relationship lists populated correctly

**Test cases** (add to `backend/tests/test_relationship_service.py`):
- `test_social_pattern_healthy` — recent updates, connection active → "healthy"
- `test_social_pattern_isolating` — 80% stale + connection neglected → "isolating"
- `test_social_pattern_overcommitting` — 6 social events this week, no isolation → "overcommitting"
- `test_social_pattern_no_data` — no person nodes → "no_data"
- `test_social_pattern_confidence_scales` — 1 signal → "low", 2 → "medium", 3 → "high"
- `test_stale_relationships_listed` — person nodes >30 days stale appear in stale_relationships
- `test_active_relationships_listed` — person nodes <14 days appear in active_relationships
- `test_inactive_person_excluded` — person with status inactive not counted

---

### Task 3: Relationship Context Injection

**Objective:** Inject relationship intelligence into the system prompt when the user's message is people-related, and add social pattern alerts to proactive alerts.

**Files to modify:**
- `backend/mentor_agent.py` — add `_build_relationship_note()` method
- `backend/services/chat_service.py` — call relationship service, pass data to mentor

**Requirements:**

**In `chat_service.py`:**

Add a new method `_build_relationship_context()` that's called when the message is People-domain classified:

```python
from services.relationship_service import (
    scan_person_mentions, get_person_intelligence, detect_social_patterns,
)
```

In `send_message()` and `stream_message()`, after state inference and conflict detection:

```python
# Relationship context (only when people-related)
relationship_context = None
from mentor_agent import _classify_domains
if "People" in _classify_domains(message):
    person_nodes = self.graph.get_nodes_by_type("person")
    session = self.chat_store.get_session(session_id)
    recent_messages = session.get("messages", [])[-20:]  # wider window for mention scanning
    mentions = scan_person_mentions(recent_messages, person_nodes)
    person_intel = get_person_intelligence(self.graph, date.today())
    # Get fundamentals for social pattern detection
    fundamentals = alerts.get("neglected_fundamentals", []) + alerts.get("untracked_fundamentals", [])
    social_patterns = detect_social_patterns(person_intel, fundamentals, self.graph, date.today())
    relationship_context = {
        "mentions": mentions,
        "person_intelligence": person_intel,
        "social_patterns": social_patterns,
    }
```

Pass `relationship_context` to `chat_stream()` and `chat()` alongside existing parameters. Default to `None`.

**In `mentor_agent.py`:**

Add `_build_relationship_note(relationship_context: dict | None) -> str`:

Only produces output when `relationship_context` is not None (i.e., only for people-related messages).

```
SOCIAL CONTEXT — the user's message relates to people. Here's what you know:

MENTIONED IN THIS SESSION:
- Ben (friend) — mentioned 4 times. Contexts: "planning Citadel Technica", "investor deck sync". Sentiment: positive.
- Dad (family) — mentioned 2 times. Contexts: "redundancy situation", "shared transition". Sentiment: mixed.

RELATIONSHIP HEALTH:
- Active relationships (updated <14 days): Ben, Dad, Ethan
- Stale relationships (no updates 30+ days): Romane, Harry, Anotela, Laila
- Social pattern: healthy (confidence: low)

When a person is mentioned:
- Reference their node and connections — don't treat them as strangers.
- If you notice a pattern (mentioned often but never seen, always in stressful context), name it.
- If a relationship is stale, consider asking about it naturally: "You haven't mentioned X in a while."
```

**System prompt placement:**

Inject after state note and before proactive alerts:

```python
system += self._build_dismissed_note(dismissed_ids or [])
system += self._build_state_note(state or {})
system += self._build_relationship_note(relationship_context)   # NEW
system += self._build_proactive_alerts(alerts or {}, state=state)
system += self._build_conflict_note(conflicts or [])
system += self._build_mode_note(mode)
system += self._build_challenge_note(challenges or {})
```

**Social pattern alert injection:**

When `social_patterns["pattern"]` is "isolating" with medium/high confidence, add a line to `_build_proactive_alerts()`:

```
SOCIAL PATTERN: Possible isolation detected. 8 of 11 relationships are stale (30+ days). Connection fundamental neglected.
Don't lecture — ask naturally about people they haven't mentioned. "How's [stale person] doing?"
```

This is added as a new optional key in the `alerts` dict: `social_pattern`. The `_build_proactive_alerts()` method checks for it alongside existing alert types.

**Edge cases:**
- If relationship service raises an exception → catch, log, proceed with None context (defensive pattern)
- Message classified in multiple domains including People → still inject
- No person nodes in graph → skip relationship context entirely
- Mentions list empty (no person names found in messages) → still inject person_intelligence if people-domain classified

**Pattern to follow:** Same `_build_X_note()` injection pattern as `_build_state_note()` and `_build_conflict_note()`. Same defensive try/except in `chat_service.py` as other services.

**Acceptance criteria:**
- People-related message includes SOCIAL CONTEXT in system prompt
- Non-people message has no relationship context injected
- Mentioned persons appear with mention count and contexts
- Stale/active relationship lists populated from graph data
- Social pattern alert surfaces when isolation detected
- Relationship service errors don't break chat flow

**Test cases** (add to `backend/tests/test_mentor_agent.py`):
- `test_relationship_note_with_mentions` — mentions present → note includes "MENTIONED IN THIS SESSION"
- `test_relationship_note_none_when_empty` — None context → empty string
- `test_relationship_note_includes_health` — person_intelligence present → note includes "RELATIONSHIP HEALTH"
- `test_system_prompt_includes_relationship_context` — people-related message → system prompt contains "SOCIAL CONTEXT"
- `test_relationship_note_isolation_alert` — isolating pattern → note includes guidance about stale relationships

**Test cases** (add to `backend/tests/test_routes.py` or `test_chat_service.py`):
- `test_relationship_service_error_doesnt_break_chat` — mock relationship service to raise → chat proceeds with None context

---

### Task 4: SOUL.md Relationship Intelligence Section

**Objective:** Add a `## Relationship Intelligence` section to SOUL.md that gives Athena persistent behavioral instructions for handling relationship observations.

**Files to modify:**
- `SOUL.md` — add `## Relationship Intelligence` section after `## State Awareness`
- `backend/mentor_agent.py` — ensure `_load_soul()` captures the new section (no code changes needed — existing parser handles `##` sections automatically)

**Requirements:**

Add to SOUL.md after `## State Awareness` and before `## Modes`:

```markdown
## Relationship Intelligence

When the system injects SOCIAL CONTEXT, it has analyzed the user's relationships based on person nodes in the graph and who they mention in conversation. Use this to enrich your responses — people aren't isolated data points, they're part of the user's life structure.

### When a person is mentioned
Reference their node. If they're connected to goals, projects, or habits, mention the connection. "Ben — your Citadel Technica co-founder" is better than just "Ben." If the person isn't in the graph yet, propose creating a person node.

### Stale relationships
If the system flags a relationship as stale (not updated in 30+ days), consider mentioning it naturally — but ONLY when it's contextually relevant. Don't randomly say "you haven't mentioned Romane lately" in the middle of a career discussion. Wait for a social context or a natural opening. One mention per session maximum — don't nag about social life.

### Social patterns
If the system detects isolation, don't diagnose. Ask about specific people: "How's Ben? You two haven't caught up in a while." If it detects overcommitting, surface the load: "You've got 5 social things this week on top of [commitments]. What's the priority?"

### What NOT to do
- Don't psychoanalyze relationships. "Your mention of Dad always correlates with stress" is invasive. "You mentioned your dad — how's the job search going for him?" is helpful.
- Don't rank relationships or imply some people matter more than others.
- Don't suggest the user reach out to every stale relationship. That's a chore list, not wisdom.
- Don't comment on relationship patterns unless confidence is medium or high.
```

**Acceptance criteria:**
- SOUL.md contains the Relationship Intelligence section with subsections
- `_load_soul()` captures it in the sections dict (automatic — existing `##` parser)
- The section text is included in the instructions portion of the system prompt
- No changes to mode or state awareness parsing

**Test cases** (add to `backend/tests/test_mentor_agent.py`):
- `test_soul_relationship_intelligence_parsed` — "relationship intelligence" key present in parsed sections
- `test_system_prompt_includes_relationship_intelligence` — system prompt contains "Relationship Intelligence" text

---

### Task 5: Relationship Data in Accountability Endpoint

**Objective:** Extend `GET /api/accountability` to include relationship intelligence for frontend consumption.

**Files to modify:**
- `backend/routes/graph_routes.py` — add relationship data to `/api/accountability` response

**Requirements:**

Extend the existing `/api/accountability` response:

```json
{
  "streaks": ["...unchanged..."],
  "overdue": ["...unchanged..."],
  "summary": {"...unchanged..."},
  "fundamentals": ["...unchanged..."],
  "fundamentals_summary": {"...unchanged..."},
  "relationships": {
    "persons": [
      {
        "person_id": "ben",
        "person_title": "Ben",
        "relationship": "friend",
        "last_updated": "2026-03-16",
        "days_since_update": 7,
        "connected_node_count": 5,
        "connected_active_count": 3,
        "connection_types": ["project", "event", "experience"],
        "staleness": "active"
      }
    ],
    "social_pattern": {
      "pattern": "healthy",
      "confidence": "low",
      "signals": []
    },
    "summary": {
      "total_persons": 11,
      "active": 3,
      "stale": 6,
      "inactive": 2
    }
  }
}
```

**Implementation:**

Call `get_person_intelligence()` and `detect_social_patterns()` from the relationship service. Classify each person as:
- `active`: updated within 14 days
- `stale`: updated 15-90 days ago (or never updated)
- `inactive`: person node has status inactive/archived

Compute `social_pattern` using `detect_social_patterns()`, passing in fundamentals from the existing `check_fundamentals()` call.

**Error handling:** Same pattern as existing endpoint — if relationship service raises, return 500 with error. Or, since the existing endpoint already has error handling, catch relationship service errors independently and return the response without the `relationships` key (graceful degradation).

**Pattern to follow:** Same endpoint extension as Sprint 5's fundamentals addition. Add data to the existing response dict.

**Acceptance criteria:**
- `GET /api/accountability` returns `relationships` key with persons, social_pattern, and summary
- Each person includes staleness classification
- Summary counts match the detail array
- Social pattern assessment included
- Errors in relationship service don't break the entire endpoint (graceful degradation)

**Test cases** (add to `backend/tests/test_routes.py`):
- `test_accountability_endpoint_returns_relationships` — response includes `relationships` key
- `test_accountability_relationships_summary_counts` — summary.active + stale + inactive = total_persons
- `test_accountability_relationships_social_pattern` — social_pattern included with pattern/confidence
- `test_accountability_relationships_staleness` — person updated yesterday → "active", person updated 45 days ago → "stale"
- `test_accountability_relationships_error_graceful` — relationship service raises → response still returns streaks/overdue/fundamentals without relationships

---

## API Response Contracts

### Extended endpoint: `GET /api/accountability`

New `relationships` key added to existing response:

```json
{
  "relationships": {
    "persons": [
      {
        "person_id": "string",
        "person_title": "string",
        "relationship": "string | null",
        "last_updated": "string | null",
        "days_since_update": "number | null",
        "connected_node_count": "number",
        "connected_active_count": "number",
        "connection_types": ["string"],
        "staleness": "active | stale | inactive"
      }
    ],
    "social_pattern": {
      "pattern": "healthy | isolating | overcommitting | no_data",
      "confidence": "low | medium | high",
      "signals": [{"type": "string", "detail": "string"}]
    },
    "summary": {
      "total_persons": "number",
      "active": "number",
      "stale": "number",
      "inactive": "number"
    }
  }
}
```

### Chat API response: unchanged

The chat response shape is unchanged. Internally, the system prompt now may include a `SOCIAL CONTEXT:` section for people-related messages and a social pattern line within proactive alerts when isolation is detected.

---

## Implementation Order

```
Task 1: Relationship Service — Mention Scanning + Person Intelligence (independent)
  └→ Task 2: Social Pattern Detection (depends on Task 1 for person_intelligence)
      └→ Task 3: Relationship Context Injection (depends on Tasks 1 + 2)
  Task 4: SOUL.md Relationship Intelligence Section (independent — no code dependencies)
  └→ Task 5: Relationship Data in Accountability Endpoint (depends on Tasks 1 + 2)
```

Tasks 1 and 4 can be built in parallel. Task 2 depends on Task 1. Tasks 3 and 5 both depend on Tasks 1 + 2.

Recommended sequence: **1 → 2 → 4 → 3 → 5** (build the service, then the SOUL.md section, then wire into chat flow and API).

---

## New Files

| File | Purpose |
|------|---------|
| `backend/services/relationship_service.py` | Person mention scanning, intelligence computation, social pattern detection |
| `backend/tests/test_relationship_service.py` | Relationship service unit tests |

**Total new files: 2** (well within the 15-file limit)

---

## Definition of Done

1. All existing tests pass (`python -m pytest` from `backend/`)
2. `npm run build` succeeds in `frontend/`
3. New test files pass with all cases green
4. Messages mentioning a person by name correctly match the corresponding person node
5. Person mention counts and context snippets are accurate within a session
6. Social pattern detection identifies isolation when 60%+ person nodes are stale and connection fundamental is neglected
7. System prompt includes `SOCIAL CONTEXT:` section for people-related messages only
8. Non-people messages have no relationship context injected (no prompt bloat)
9. SOUL.md contains Relationship Intelligence section with behavioral guidance
10. `GET /api/accountability` returns relationship data with person staleness and social pattern
11. Relationship service errors don't break chat flow or the accountability endpoint
12. No regression in conflict detection, mode classification, permanence scoring, challenge ladder, accountability, or state inference
13. No new dependencies added to `requirements.txt`
