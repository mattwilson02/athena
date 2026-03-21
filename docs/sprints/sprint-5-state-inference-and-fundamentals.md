# Sprint 5: State Inference and Fundamentals Monitoring

> **Epoch:** E6 — Reading the Room (part 1)
> **Branch:** `sprint/5`
> **Goal:** Athena reads between the lines — short messages mean stress, late-night messages flag sleep, and neglected fundamentals get surfaced before they compound.

---

## Overview

E5 gave Athena accountability — she tracks commitments, calculates streaks, and surfaces overdue obligations. But she's still tone-deaf to the user's current state. She'll dump accountability alerts on someone who's clearly stressed. She doesn't notice that 2am messages mean the sleep schedule is broken. She doesn't flag when a fundamental human need has gone neglected for weeks.

This sprint adds the first two E6 capabilities:

1. **State inference service** — a new `state_service.py` that analyzes recent messages in the current session to assess the user's energy, stress, and behavioral signals. Signals: message length/brevity, message timing, cancellation patterns, idea density, repetitive queries.

2. **State-aware mode adjustment** — the inferred state feeds into `classify_mode()` so Athena softens when the user is stressed (Advisor becomes gentler, Guardian delays non-critical alerts) and holds back proactive alerts when energy is low.

3. **Fundamentals monitoring** — a function in the accountability service that checks whether core human needs (movement, sleep, nutrition, connection, purpose, financial stability) have been neglected for 2+ weeks, based on graph activity. Surfaced as a new `FUNDAMENTALS:` section in proactive alerts.

**Not in this sprint:** Relationship tracking and influence mapping (E6 part 2), person mention frequency across sessions (requires cross-session analysis), energy/rhythm prediction (Stage 2).

---

## What Exists

| Component | State | Relevant to This Sprint |
|-----------|-------|------------------------|
| `classify_mode()` in `mentor_agent.py` | Keyword heuristic: guardian > dialectic > advisor > mirror | State-aware adjustment modifies the mode or injects state context |
| `_build_proactive_alerts()` in `mentor_agent.py` | Formats broken streaks + overdue commitments for system prompt | Fundamentals alerts added alongside existing alerts |
| `chat_service.py` flow | detect_conflicts → classify_mode → build_alerts → chat_stream | State inference inserted before mode classification |
| `chat_store.py` `get_session()` | Returns session with messages array (role, content, timestamp) | Message history is the input for state inference |
| `chat_store.py` `get_messages_for_api()` | Returns `[{role, content}]` for Claude | State service needs timestamps too — uses `get_session()` directly |
| `accountability_service.py` `calculate_streaks()` | Computes habit streaks from linked daily nodes | Fundamentals monitoring reuses streak data for movement/sleep/etc. |
| `_PERMANENCE_DEFAULTS` in `mentor_agent.py` | Maps types to permanence levels — `fundamental` level exists but no types use it yet | Fundamentals monitoring may reference this level for future schema alignment |
| `_DOMAIN_KEYWORDS` in `mentor_agent.py` | Maps keywords to Self/People/Knowledge/Life/Planning/Places/Finance domains | Pattern for keyword-based signal detection |
| `conflict_service.py` signal lists | `_ACTION_INTENTIONS`, `_NEGATION_SIGNALS`, `_ADMISSION_SIGNALS`, etc. | Pattern for state signal detection — same keyword-list approach |
| SOUL.md `## Modes` section | Mirror/Advisor/Guardian/Dialectic behavioral instructions | State-aware guidance added as a new section |
| `vault/_meta/schema.md` | `habit` type with `frequency`, `status`, `streak` fields | Habits tagged as fundamentals (movement, sleep, etc.) identified by convention |
| Vault habit nodes | 10+ active habits (strength training, reading, etc.) | Real data — some map to fundamentals (gym → movement, sleep routine → sleep) |

---

## Architectural Decisions

### 1. State inference is a standalone service, not part of mentor_agent

Like `conflict_service.py` and `accountability_service.py`, state inference has enough complexity to warrant its own file. It analyzes message patterns (length, timing, frequency, cancellation signals) — ~100-150 lines of pure functions over session data.

**Why:** `mentor_agent.py` is already 1,400+ lines. State inference has a clear input (session messages) and output (state assessment dict). Same extraction pattern as the conflict and accountability services.

### 2. State inference operates on the current session's messages, not cross-session

The service analyzes the last N messages (configurable, default 10) in the current session to assess current state. Cross-session analysis (detecting long-term burnout patterns across weeks of conversations) is deferred.

**Why:** Single-session analysis captures immediate signals (terse messages right now = stressed right now) with zero persistence overhead. Cross-session analysis requires reading multiple session files, which adds I/O and complexity. The immediate state is the highest-value signal for mode adjustment. Cross-session patterns can be added in a follow-up sprint.

### 3. State is injected into the system prompt as context, not used for hard mode overrides

Rather than having state inference force a mode change (e.g., always override Guardian when stressed), the inferred state is injected as a `USER STATE:` section in the system prompt. Claude reads it and naturally adjusts its tone. The mode classifier adds a "softened" flag when stress is detected, but doesn't change the mode itself.

**Why:** Hard overrides are brittle. If the user is stressed AND contradicting a core value, Guardian mode should still activate — but Claude should adjust its delivery. Prompt injection gives Claude the context to make that judgment. The mode provides the framework, the state provides the nuance.

### 4. Fundamentals monitoring maps habits to fundamental categories by keyword matching, not schema changes

Rather than adding a `fundamental_category` field to the schema or habit frontmatter, the service maps habits to fundamentals by keyword matching on the habit's title and tags. "Strength Training" → movement. "Sleep by 11pm" → sleep. "Weekly dinner with friends" → connection.

**Why:** Schema changes require migration, parser updates, FORMAT_SPEC changes, and every existing habit needs updating. Keyword matching delivers 90% accuracy with zero migration. The mapping can be overridden per-habit via a `fundamental` tag in the future, but that's not needed now. If the mapping is wrong for a specific habit, it's a minor categorization issue, not a system failure.

### 5. State assessment uses a multi-signal threshold, not single-signal triggers

No single signal triggers a state assessment alone. The service computes independent signal scores (brevity, timing, cancellation rate, repetition) and only reports a state when 2+ signals agree. This prevents false positives from a single short message being interpreted as stress.

**Why:** The ROADMAP explicitly calls this out: "require 3+ signals before adjusting. Probabilistic, not definitive." Single-signal inference is noisy. A short message might just be a quick question. But a short message + late timing + cancelled plans = clear signal.

---

## Tasks

### Task 1: State Inference Service

**Objective:** Create `state_service.py` with functions that analyze recent session messages to assess the user's current state.

**Files to create:**
- `backend/services/state_service.py`

**Requirements:**

```
infer_state(messages: list[dict]) -> dict
```

Takes a list of session messages (with `role`, `content`, `timestamp` fields) and returns:

```python
{
    "energy": "high" | "normal" | "low",
    "stress": "none" | "mild" | "elevated",
    "signals": [                          # which signals contributed
        {"type": "brevity", "detail": "avg 12 chars over last 5 messages"},
        {"type": "late_night", "detail": "2 messages after midnight"},
    ],
    "confidence": "low" | "medium" | "high",  # based on signal count
}
```

**Signal detectors (each returns a score 0.0-1.0):**

1. **Brevity** — average user message length over last N messages. <30 chars avg → 1.0 (high stress signal). 30-60 chars → 0.5 (mild). >60 chars → 0.0. Only count user messages (skip assistant).

2. **Late night timing** — messages between 00:00-05:00 local time. ≥2 late messages in session → 1.0. 1 late message → 0.5. 0 → 0.0. Parse timestamp from message `timestamp` field.

3. **Cancellation language** — count messages containing cancellation/avoidance signals: "cancel", "skip", "not going to", "can't be bothered", "too tired", "forget it", "nevermind", "nah", "pass on". ≥2 cancellations → 1.0. 1 → 0.5. 0 → 0.0.

4. **Repetition** — same question/topic asked 2+ times (simple: check if any user message is >70% similar to a previous user message by word overlap). ≥2 repetitions → 1.0 (anxiety/indecision signal). 1 → 0.5. 0 → 0.0.

5. **Idea density** — count of new intentions/plans in the session ("I want to", "I'm going to", "what if", "let's", "new idea"). ≥5 → 1.0 (possible overcommitting/manic energy). 3-4 → 0.5. <3 → 0.0. This is a HIGH energy signal, not stress.

**State derivation:**

- Count signals with score ≥ 0.5 as "active signals"
- `confidence`: 0-1 active signals → "low", 2 → "medium", 3+ → "high"
- `stress`: 0 stress signals (brevity + late_night + cancellation + repetition) → "none", 1 → "mild", 2+ → "elevated"
- `energy`: idea_density ≥ 0.5 AND stress == "none" → "high". stress == "elevated" → "low". Default "normal".

**Edge cases:**
- Empty messages list → return default state (normal energy, no stress, low confidence)
- Messages without timestamps → skip timing analysis, still run other detectors
- Only assistant messages (no user messages) → return default state
- Very short session (1-2 messages) → confidence always "low"

**Pattern to follow:** Same pure-function-over-data pattern as `detect_conflicts()` in `conflict_service.py` and `calculate_streaks()` in `accountability_service.py`. Takes data, returns structured dict. No side effects. Logger for debugging.

**Acceptance criteria:**
- 5 terse messages (< 20 chars each) → stress: "elevated", confidence: "medium"+"high"
- 2+ messages after midnight → late_night signal active
- Mixed normal-length messages → stress: "none", energy: "normal"
- Single short message in a long conversation → stress: "none" (threshold not met)
- Empty input → default state returned

**Test cases** (`backend/tests/test_state_service.py`, new file):
- `test_default_state_empty_messages` — empty list → normal energy, no stress, low confidence
- `test_brevity_signal_terse_messages` — 5 messages under 20 chars → brevity signal score 1.0
- `test_brevity_signal_normal_messages` — messages 80+ chars → brevity score 0.0
- `test_late_night_signal` — 2 messages at 2am → late_night signal active
- `test_late_night_no_timestamps` — messages without timestamps → timing skipped, other signals still work
- `test_cancellation_signal` — messages with "cancel", "skip" → cancellation signal active
- `test_repetition_signal` — same question asked twice → repetition signal active
- `test_idea_density_high_energy` — 5+ "I want to" messages with no stress → energy "high"
- `test_stress_elevated_multiple_signals` — brevity + late_night → stress "elevated"
- `test_confidence_scales_with_signal_count` — 0 signals → "low", 2 → "medium", 3+ → "high"
- `test_only_assistant_messages_default` — no user messages → default state
- `test_stress_overrides_high_energy` — stress elevated even with high idea density → energy "low"

---

### Task 2: State-Aware Mode Adjustment and System Prompt Injection

**Objective:** Feed the inferred user state into the chat flow and inject it into the system prompt so Claude naturally adjusts its tone.

**Files to modify:**
- `backend/services/chat_service.py` — call `infer_state()` before mode classification, pass state downstream
- `backend/mentor_agent.py` — add `_build_state_note()` method, inject into system prompt; adjust proactive alert suppression when stress is elevated

**Requirements:**

**In `chat_service.py`:**

After loading conversation history and before conflict detection, compute state:

```python
from services.state_service import infer_state

session = self.chat_store.get_session(session_id)
recent_messages = session.get("messages", [])[-10:]  # last 10 messages
state = infer_state(recent_messages)
```

Pass `state` to `chat_stream()` and `chat()` alongside existing parameters.

**In `mentor_agent.py`:**

Add `_build_state_note(state: dict) -> str`:

```
USER STATE — adjust your tone and priorities based on the user's current state.

Energy: low | Stress: elevated | Confidence: medium
Signals: brevity (avg 15 chars), late_night (2 messages after midnight)

When stress is elevated:
- Lead with acknowledgment, not obligations
- Hold non-urgent proactive alerts for a better moment
- Keep responses shorter than usual
- Don't pile on with accountability — one thing at a time

When energy is high:
- Channel the momentum — help them prioritise rather than dampen
- Flag overcommitting risk if idea density is high
- Good time to surface strategic planning

When energy is low:
- Protect their time and attention
- Suggest recovery, not productivity
- Only raise truly urgent alerts
```

**State-aware alert suppression:**

In `_build_proactive_alerts()`, add logic: when `state.get("stress") == "elevated"`, cap alerts at 1 (most urgent only) instead of the normal 5+3. When `state.get("energy") == "low"`, cap at 2 total.

Pass state to `_build_proactive_alerts()` as an optional second parameter: `_build_proactive_alerts(alerts, state=None)`.

**System prompt placement:**

`_build_state_note()` is called in `chat_stream()` and `chat()` and injected **before** proactive alerts and conflicts — so Claude sees the user's state first and can calibrate everything that follows.

Updated injection order:
```
system += self._build_dismissed_note(dismissed_ids or [])
system += self._build_state_note(state or {})          # NEW — before alerts
system += self._build_proactive_alerts(alerts or {}, state=state)  # state-aware suppression
system += self._build_conflict_note(conflicts or [])
system += self._build_mode_note(mode)
system += self._build_challenge_note(challenges or {})
```

**Suppression rules:**
- If state is default/low-confidence → no suppression (inject all alerts as before)
- If stress is "elevated" with medium/high confidence → suppress all but 1 most urgent alert
- If energy is "low" with medium/high confidence → suppress down to 2 total alerts

**Edge cases:**
- If `infer_state()` raises → catch, log, proceed with empty state (same defensive pattern as conflict detection and accountability)
- State with confidence "low" → still inject the note but behavioral adjustments are softer ("consider" vs "do")
- State note is empty string when state is default (normal energy, no stress, low confidence) — don't inject noise

**Pattern to follow:** Same `_build_X_note()` injection pattern as `_build_conflict_note()` and `_build_proactive_alerts()`. Same defensive try/except in `chat_service.py` as `_detect_conflicts()` and `_build_alerts()`.

**Acceptance criteria:**
- System prompt includes `USER STATE:` section when non-default state detected
- Stressed user sees fewer proactive alerts (capped at 1)
- Low energy user sees fewer proactive alerts (capped at 2)
- Default/normal state produces no state section in system prompt
- State inference errors don't break chat flow

**Test cases** (add to `backend/tests/test_mentor_agent.py`):
- `test_state_note_elevated_stress` — elevated stress → state note includes "stress is elevated" guidance
- `test_state_note_default_empty` — normal state → empty string
- `test_state_note_high_energy` — high energy → state note includes "channel the momentum" guidance
- `test_proactive_alerts_capped_when_stressed` — elevated stress + 5 alerts → only 1 in output
- `test_proactive_alerts_capped_when_low_energy` — low energy + 5 alerts → only 2 in output
- `test_proactive_alerts_normal_no_cap_change` — normal state → standard caps apply

**Test cases** (add to `backend/tests/test_routes.py` or `test_chat_service.py`):
- `test_state_inference_error_doesnt_break_chat` — mock state_service to raise → chat proceeds with empty state

---

### Task 3: Fundamentals Monitoring

**Objective:** Detect when core human needs (movement, sleep, nutrition, connection, purpose, financial stability) have been neglected for 2+ weeks and surface them as proactive alerts.

**Files to modify:**
- `backend/services/accountability_service.py` — add `check_fundamentals()` function
- `backend/mentor_agent.py` — add fundamentals to `_build_proactive_alerts()` output
- `backend/services/chat_service.py` — call `check_fundamentals()` in `_build_alerts()`

**Requirements:**

**Fundamental categories and keyword mapping:**

```python
_FUNDAMENTAL_KEYWORDS: dict[str, list[str]] = {
    "movement": ["gym", "training", "workout", "exercise", "run", "running", "walk",
                  "cycling", "swim", "climbing", "yoga", "strength", "cardio", "stretch",
                  "sport", "physical", "fitness", "steps"],
    "sleep": ["sleep", "bed", "bedtime", "wake", "morning routine", "rest", "nap",
              "insomnia", "tired"],
    "nutrition": ["meal", "food", "diet", "eat", "cooking", "breakfast", "lunch",
                  "dinner", "hydrat", "water intake", "nutrition", "fast", "fasting"],
    "connection": ["friend", "family", "partner", "social", "call", "meet",
                   "dinner with", "catch up", "hangout", "date night", "relationship"],
    "purpose": ["project", "goal", "career", "learn", "study", "create", "build",
                "write", "reading", "skill", "course", "side project", "work on"],
    "financial_stability": ["budget", "saving", "expense", "finance", "income",
                           "investment", "debt", "rent", "salary"],
}
```

```
check_fundamentals(graph, today: date) -> list[dict]
```

Returns a list of neglected fundamental reports:
```python
{
    "fundamental": "movement",
    "status": "neglected",           # "active" | "neglected" | "no_data"
    "days_since_activity": 16,       # days since last related habit/daily activity
    "related_habits": ["strength-training", "morning-run"],  # habits mapped to this fundamental
    "message": "No movement-related activity in 16 days. Last: Strength Training (Mar 5)."
}
```

**Detection logic:**

1. For each fundamental category, find all habits whose title or tags match any keyword in that category's list (case-insensitive substring match)
2. For each matched habit, find linked daily nodes (same traversal as `calculate_streaks()`)
3. Find the most recent daily date across all habits in that category
4. If the most recent activity is >14 days ago (or no activity found), mark as `neglected`
5. If no habits map to the category at all, mark as `no_data` (the user hasn't set up tracking for this fundamental)

**Edge cases:**
- No habits match a fundamental → `no_data` status (surface differently: "No movement habits tracked. Consider adding one.")
- Habit exists but is `lapsed` → still count its historical dailies for recency, but note the lapsed status
- Multiple habits per fundamental → use the most recent daily across all of them
- All fundamentals active → return empty list (nothing to report)
- Only return `neglected` and `no_data` entries — `active` fundamentals are not surfaced

**Integration with `_build_alerts()` in chat_service.py:**

```python
from services.accountability_service import calculate_streaks, find_overdue_commitments, check_fundamentals

fundamentals = check_fundamentals(self.graph, date.today())
alerts = {
    "broken_streaks": [...],
    "at_risk_streaks": [...],
    "overdue_commitments": [...],
    "neglected_fundamentals": [f for f in fundamentals if f["status"] == "neglected"],
    "untracked_fundamentals": [f for f in fundamentals if f["status"] == "no_data"],
}
```

**Integration with `_build_proactive_alerts()` in mentor_agent.py:**

Add after the OVERDUE COMMITMENTS section:

```
NEGLECTED FUNDAMENTALS — these core human needs haven't had activity in 2+ weeks:
- Movement — no activity in 16 days. Related habits: "Strength Training" (last: Mar 5), "Morning Run" (lapsed).
  This is a species-level need. Don't lecture — ask what's getting in the way.
- Connection — no social activity in 21 days. Related habits: "Weekly dinner with friends" (last: Feb 28).

UNTRACKED FUNDAMENTALS — the user has no habits tracking these:
- Nutrition — consider suggesting a nutrition-related habit.
- Financial stability — no financial tracking habits exist.
```

Cap at 3 neglected + 2 untracked to avoid overwhelming. Respect state-aware suppression from Task 2.

**Pattern to follow:** Same keyword-matching approach as `_DOMAIN_KEYWORDS` in `mentor_agent.py`. Same graph traversal as `calculate_streaks()`. Same alert formatting as `_build_proactive_alerts()`.

**Acceptance criteria:**
- Habits are correctly mapped to fundamental categories by keyword matching
- Fundamental neglected >14 days → appears in alerts
- Fundamental with no matching habits → appears as `no_data`
- Active fundamentals (activity within 14 days) → not surfaced
- Neglected fundamentals appear in the system prompt with guidance
- Untracked fundamentals appear separately with softer framing

**Test cases** (add to `backend/tests/test_accountability_service.py`):
- `test_fundamentals_movement_neglected` — gym habit with last daily 16 days ago → movement neglected
- `test_fundamentals_movement_active` — gym habit with daily yesterday → not in results
- `test_fundamentals_no_data` — no habits match "nutrition" keywords → nutrition appears as no_data
- `test_fundamentals_multiple_habits_per_category` — two movement habits, most recent daily used
- `test_fundamentals_lapsed_habit_still_counted` — lapsed habit's dailies count for recency
- `test_fundamentals_empty_when_all_active` — all fundamentals have recent activity → empty list
- `test_fundamentals_keyword_matching_case_insensitive` — "Strength Training" matches "strength" keyword

**Test cases** (add to `backend/tests/test_mentor_agent.py`):
- `test_proactive_alerts_includes_fundamentals` — neglected fundamental appears in formatted output
- `test_proactive_alerts_untracked_fundamentals` — no_data fundamental appears with softer framing
- `test_proactive_alerts_fundamentals_capped` — >3 neglected → only 3 shown

---

### Task 4: SOUL.md State Awareness Section

**Objective:** Add a `## State Awareness` section to SOUL.md that gives Athena persistent behavioral instructions for adjusting to the user's inferred state.

**Files to modify:**
- `SOUL.md` — add `## State Awareness` section after `## Challenge Ladder`
- `backend/mentor_agent.py` — ensure `_load_soul()` captures the new section

**Requirements:**

Add to SOUL.md after `## Challenge Ladder`:

```markdown
## State Awareness

When the system injects USER STATE context, it has assessed the user's current energy and stress level from their message patterns. This is probabilistic — not definitive. Use it as a lens, not a label.

### Responding to Stress
When stress is elevated, the user needs to feel heard before they can hear you. Lead with a brief acknowledgment — not therapy, not "I can see you're stressed", but a single sentence that shows you're reading the room. Then keep it focused: one topic, short response, minimal demands. This is not the moment for accountability deep-dives or obligation lists.

If there's a genuine Guardian-level conflict, still flag it — but deliver it more concisely. Stress doesn't override honesty, it adjusts delivery.

### Responding to Low Energy
Don't push. Don't optimise. Don't surface 4 things they're behind on. If they're asking a simple question, give a simple answer. If they're venting, let them. Protect their remaining bandwidth for what actually matters today.

### Responding to High Energy
High energy is an opportunity — but also a risk. The user may be generating ideas faster than they can execute. Channel it: help them prioritise the best 1-2 ideas, flag overcommitting risk, and propose structured next steps. Don't dampen enthusiasm, but don't let it become scattered either.

### When in Doubt
If confidence is low, err toward normal behaviour. A false positive (treating someone as stressed when they're not) is more annoying than a false negative (missing mild stress). Only adjust significantly when confidence is medium or high.
```

**Parsing in `_load_soul()`:**

The existing `_load_soul()` parses `##` sections into a dict. The `state awareness` section will be captured under `sections["state awareness"]` and included in the instructions block automatically. No structural parsing changes needed.

**Acceptance criteria:**
- SOUL.md contains the State Awareness section with subsections for stress, low energy, high energy
- `_load_soul()` captures it in the sections dict
- The section text is included in the instructions portion of the system prompt
- No changes to mode parsing logic

**Test cases** (add to `backend/tests/test_mentor_agent.py`):
- `test_soul_state_awareness_parsed` — "state awareness" key present in parsed sections
- `test_system_prompt_includes_state_awareness` — system prompt contains "State Awareness" text

---

### Task 5: Fundamentals Dashboard in Accountability Endpoint

**Objective:** Extend the `GET /api/accountability` endpoint to include fundamentals monitoring data.

**Files to modify:**
- `backend/routes/graph_routes.py` — add fundamentals to `/api/accountability` response

**Requirements:**

Extend the existing `/api/accountability` response with a `fundamentals` key:

```json
{
  "streaks": [...],
  "overdue": [...],
  "summary": {
    "total_habits": 10,
    "on_track": 7,
    "at_risk": 2,
    "broken": 1,
    "overdue_count": 2,
    "oldest_overdue_days": 5
  },
  "fundamentals": [
    {
      "fundamental": "movement",
      "status": "active",
      "days_since_activity": 1,
      "related_habits": ["strength-training", "morning-run"],
      "message": null
    },
    {
      "fundamental": "sleep",
      "status": "neglected",
      "days_since_activity": 16,
      "related_habits": ["sleep-routine"],
      "message": "No sleep-related activity in 16 days."
    },
    {
      "fundamental": "nutrition",
      "status": "no_data",
      "days_since_activity": null,
      "related_habits": [],
      "message": "No nutrition habits tracked."
    }
  ],
  "fundamentals_summary": {
    "total": 6,
    "active": 3,
    "neglected": 2,
    "no_data": 1
  }
}
```

Unlike the proactive alerts (which only surface neglected/no_data), the dashboard returns ALL fundamentals including active ones, so the frontend can show a complete picture.

**Implementation:**

Call `check_fundamentals()` with an additional parameter to return all categories (not just neglected ones). Add a `include_active: bool = False` parameter to `check_fundamentals()` — when True, also returns `active` entries. The proactive alert path passes `False` (default), the dashboard passes `True`.

**Error handling:** Same pattern as existing endpoint — if `check_fundamentals()` raises, return 500 with error message.

**Pattern to follow:** Same endpoint extension pattern — add data to the existing response dict. No new routes.

**Acceptance criteria:**
- `GET /api/accountability` returns `fundamentals` array with all 6 categories
- Each fundamental includes status, days_since_activity, related_habits, and message
- `fundamentals_summary` counts match the array
- Active fundamentals included in dashboard (unlike proactive alerts which filter them out)
- Error handling consistent with existing endpoint

**Test cases** (add to `backend/tests/test_routes.py`):
- `test_accountability_endpoint_returns_fundamentals` — response includes `fundamentals` key
- `test_accountability_fundamentals_summary_counts` — summary totals match detail array
- `test_accountability_fundamentals_all_statuses` — active, neglected, and no_data all appear
- `test_accountability_fundamentals_error_handling` — check_fundamentals raises → 500 response

---

## API Response Contracts

### Extended endpoint: `GET /api/accountability`

```json
{
  "streaks": ["...existing shape unchanged..."],
  "overdue": ["...existing shape unchanged..."],
  "summary": {"...existing shape unchanged..."},
  "fundamentals": [
    {
      "fundamental": "string (movement|sleep|nutrition|connection|purpose|financial_stability)",
      "status": "active | neglected | no_data",
      "days_since_activity": "number | null",
      "related_habits": ["string"],
      "message": "string | null"
    }
  ],
  "fundamentals_summary": {
    "total": "number",
    "active": "number",
    "neglected": "number",
    "no_data": "number"
  }
}
```

### Chat API response: unchanged

The chat response shape is unchanged. Internally, the system prompt now may include a `USER STATE:` section and a `NEGLECTED FUNDAMENTALS:` section within proactive alerts.

---

## Implementation Order

```
Task 1: State Inference Service (independent — new file, no dependencies)
  └→ Task 2: State-Aware Mode Adjustment + Prompt Injection (depends on Task 1)
      └→ Task 4: SOUL.md State Awareness Section (depends on Task 2 for integration)
Task 3: Fundamentals Monitoring (independent — extends accountability_service)
  └→ Task 5: Fundamentals Dashboard Endpoint (depends on Task 3)
```

Tasks 1 and 3 can be built in parallel. Task 2 depends on Task 1. Task 4 depends on Task 2. Task 5 depends on Task 3.

Recommended sequence: **1 → 3 → 2 → 4 → 5** (build both services, then wire state into the chat flow, then add SOUL.md and dashboard).

---

## New Files

| File | Purpose |
|------|---------|
| `backend/services/state_service.py` | State inference from session message patterns |
| `backend/tests/test_state_service.py` | State inference unit tests |

**Total new files: 2** (well within the 15-file limit)

---

## Definition of Done

1. All existing tests pass (`python -m pytest` from `backend/`)
2. `npm run build` succeeds in `frontend/`
3. New test files pass with all cases green
4. 5 terse messages in a session → stress detected with medium/high confidence
5. 2+ messages after midnight → late_night signal active in state assessment
6. System prompt includes `USER STATE:` section when non-default state detected
7. Elevated stress suppresses proactive alerts to 1 most urgent
8. Fundamentals neglected >14 days appear in proactive alerts
9. Untracked fundamentals appear with softer framing ("consider adding")
10. SOUL.md contains State Awareness section with stress/energy/high-energy guidance
11. `GET /api/accountability` returns fundamentals with all 6 categories
12. State inference errors don't break the chat flow
13. No regression in conflict detection, mode classification, permanence scoring, challenge ladder, or accountability
14. No new dependencies added to `requirements.txt`
