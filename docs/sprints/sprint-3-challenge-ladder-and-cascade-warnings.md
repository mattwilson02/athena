# Sprint 3: Challenge Ladder and Cascade Warnings

> **Epoch:** E4 — Weight and Memory (completion)
> **Branch:** `sprint/3`
> **Goal:** Dropping a core value requires earning the change. Modifying identity-level nodes triggers explicit warnings before acceptance.

---

## Overview

Sprint 2 gave Athena permanence scoring (identity nodes outrank tactical ones in retrieval) and adaptive modes (tone matches the stakes). But she still accepts identity-level changes without resistance. "I don't care about discipline anymore" gets the same treatment as "mark that task done."

This sprint adds the two remaining E4 capabilities:

1. **Challenge ladder** — a 5-step escalation when the user attempts to modify or remove identity/fundamental-level nodes. State is tracked per session so Athena remembers which step she's on for each contested node. The user must consciously defend the change through multiple exchanges before Athena accepts it.

2. **Permanence-aware cascade warnings** — when a graph update targets an identity or fundamental node, the cascade system flags it with an explicit warning: "This would modify a core value." The frontend surfaces this warning before the user confirms.

These complete E4, unlocking E5 (Accountability) which depends on permanence levels defining what gets tracked and modes enabling appropriate communication.

**Not in this sprint:** Commitment tracking, streak calculation, proactive alerts (all E5).

---

## What Exists

| Component | State | Relevant to This Sprint |
|-----------|-------|------------------------|
| `_PERMANENCE_DEFAULTS` dict in `mentor_agent.py` | Maps every type to `(level_name, boost)` | Used to determine which updates trigger the ladder |
| `_get_permanence(node_type)` in `mentor_agent.py` | Returns `(level, boost)` for any type | Reused by cascade warning logic |
| `classify_mode()` in `mentor_agent.py` | Returns mirror/advisor/guardian/dialectic | Guardian mode activates alongside challenge ladder when conflicts detected |
| `_build_conflict_note()` in `mentor_agent.py` | Formats conflicts with `[SEVERITY \| permanence]` labels | Challenge ladder context injected alongside conflict notes |
| `chat_store.py` `ChatStore` | Session JSON with messages, dismissed_updates, pending_updates | Challenge state stored here per session |
| `chat_service.py` flow | detect_conflicts → classify_mode → chat_stream | Challenge ladder check inserted after conflict detection |
| `vault_service.py` `cascade_check()` | 1-hop neighbors + semantic cascade, returns proposals | Add permanence warning to proposals targeting identity nodes |
| `chat_service.py` `_confirm_pending()` | Accepts graph updates, calls `vault_service.write/update`, queues cascade | Intercept identity-level updates to inject warning |
| `conflict_service.py` `detect_conflicts()` | Returns conflicts with permanence field | Challenge ladder uses conflict permanence to decide activation |
| SOUL.md `## Conflict Protocol` | 4-step escalation (flag/explain/challenge/accept) | Challenge ladder extends this to 5 steps with session memory |

---

## Architectural Decisions

### 1. Challenge ladder state lives in chat_store session JSON, not a separate store

The ROADMAP says challenge state is tracked per session: `{node_id, step: 1-5, history: []}`. This fits naturally into the existing session JSON structure in `chat_store.py` — add a `challenge_state` dict keyed by node_id alongside `dismissed_updates` and `pending_updates`.

**Why:** No new persistence layer. The chat_store already handles per-session state (dismissed updates, pending updates). Challenge state has the same lifecycle — it exists within a conversation and doesn't need to survive across sessions. A separate store would add complexity for no benefit.

### 2. Challenge ladder is triggered by graph updates, not by message classification

The ladder doesn't activate from keywords in the user's message. It activates when a proposed graph update (from Claude's response) targets an identity or fundamental node with a status change to a terminal state (abandoned, cancelled, superseded) or a content/title change. This is checked when the graph_updates are parsed from Claude's response, before they're sent to the frontend.

**Why:** Keyword-based triggering would produce false positives ("I'm thinking about whether discipline matters" shouldn't trigger the ladder). The ladder should only fire when there's an actual proposed change to an identity node — which only exists in the graph_updates block. This also means the ladder doesn't need to understand natural language intent; it just inspects structured data.

### 3. Challenge ladder instructions are injected into the system prompt, not enforced via post-processing

When a challenge is active, the system prompt gets an additional `CHALLENGE LADDER` section telling Claude which step to execute and what the history is. Claude generates the appropriate challenge response. We don't post-process or rewrite the response.

**Why:** Same principle as mode injection (Sprint 2 AD#3). Prompt-level instructions are the right lever. Post-processing is fragile and fights the model. The SOUL.md already has a Conflict Protocol that Claude follows well — the challenge ladder extends it with per-node memory.

### 4. Cascade warnings are metadata on proposals, not a separate confirmation step

When `cascade_check()` produces a proposal that targets an identity/fundamental node, the proposal gets a `permanence_warning` field. The frontend reads this field and displays a warning badge/message. There is no additional backend confirmation step — the existing accept/dismiss flow handles it.

**Why:** Adding a separate "are you really sure?" step would be over-engineering. The warning is informational — it makes the cost visible. The user already has accept/dismiss control. The challenge ladder handles the conversational pushback; the cascade warning handles the UI-level visibility.

### 5. Challenge steps map directly to the vision's 5-step escalation

The vision document defines a specific 5-step sequence: flag the gap → investigate → escalate → challenge the shift → accept with full context. Each step has distinct behavior. The challenge ladder follows this exactly, with each step producing a different system prompt instruction.

**Why:** The vision spec is specific enough to implement directly. Inventing a different escalation would diverge from the product design. The 5 steps are well-differentiated and each adds genuine value.

---

## Tasks

### Task 1: Challenge State in ChatStore

**Objective:** Add per-session challenge ladder state tracking to the chat store.

**Files to modify:**
- `backend/chat_store.py` — add challenge state methods

**Requirements:**

Challenge state shape per session:
```python
{
    "challenge_state": {
        "node-id-1": {
            "step": 1,           # current step (1-5)
            "node_title": "...", # human-readable name
            "node_type": "...",  # value, belief, fear, etc.
            "permanence": "...", # identity, fundamental
            "history": [         # what happened at each step
                {"step": 1, "action": "flagged", "timestamp": "..."}
            ]
        }
    }
}
```

New methods on `ChatStore`:

- `get_challenge_state(session_id: str, node_id: str) -> dict | None` — returns the challenge state for a node, or None if no active challenge.
- `set_challenge_state(session_id: str, node_id: str, state: dict) -> bool` — creates or updates challenge state for a node. Returns False if session not found.
- `advance_challenge(session_id: str, node_id: str) -> dict | None` — increments step by 1, appends to history, returns the updated state. Returns None if no existing challenge or session not found. Caps at step 5.
- `clear_challenge(session_id: str, node_id: str) -> bool` — removes challenge state for a node (called when the user abandons the change or Athena accepts at step 5).
- `get_active_challenges(session_id: str) -> dict` — returns the full `challenge_state` dict for a session (used for system prompt injection).

**Edge cases:**
- If `challenge_state` key doesn't exist on the session, treat as empty dict (backward compat with existing sessions)
- `advance_challenge` on step 5 stays at step 5 (don't go to 6)
- Challenge state persists across messages within the same session but not across sessions

**Pattern to follow:** Same read-modify-write pattern as `dismiss_update()` and `set_pending_updates()`. Dict keyed by node_id, stored on the session JSON.

**Acceptance criteria:**
- Challenge state is persisted in session JSON
- State survives across multiple messages in the same session
- `advance_challenge` increments step and records history
- `clear_challenge` removes the node's challenge state
- Existing session methods still work (backward compat)

**Test cases** (`backend/tests/test_chat_store.py`, add to existing file):
- `test_challenge_state_default_empty` — new session has no challenge state
- `test_set_and_get_challenge_state` — set state for a node, retrieve it
- `test_advance_challenge_increments_step` — step goes from 1 to 2
- `test_advance_challenge_caps_at_5` — step 5 stays at 5
- `test_advance_challenge_appends_history` — history grows with each advance
- `test_clear_challenge_removes_state` — cleared node returns None
- `test_get_active_challenges_returns_all` — multiple active challenges returned
- `test_challenge_state_backward_compat` — session without challenge_state key works

---

### Task 2: Challenge Ladder Detection and Activation

**Objective:** Detect when a graph update proposes modifying an identity/fundamental node and activate the challenge ladder.

**Files to modify:**
- `backend/services/chat_service.py` — add challenge ladder detection after parsing graph updates
- `backend/mentor_agent.py` — add `_build_challenge_note()` for system prompt injection

**Requirements:**

**Detection logic in `chat_service.py`:**

After Claude's response is parsed and graph_updates are extracted, scan them for identity/fundamental-level changes:

```
For each graph_update:
  1. Get the node type (from update's "type" field, or look up existing node)
  2. Get permanence level via _get_permanence(node_type)
  3. If permanence is "identity" or "fundamental":
     a. If action is "update" with status change to terminal (abandoned, cancelled, superseded)
        → trigger challenge
     b. If action is "update" with content, title, or priority changes
        → trigger challenge
     c. If action is "create" with type value/belief/fear and content suggesting removal/replacement
        → do NOT trigger (creating new identity nodes is fine)
```

When a challenge is triggered:
1. Check `chat_store.get_challenge_state(session_id, node_id)`
2. If no existing challenge → create one at step 1 via `set_challenge_state()`
3. If existing challenge → advance it via `advance_challenge()`
4. Annotate the graph_update with `"_challenge": {"step": N, "node_title": "...", "permanence": "..."}`
5. Graph updates with active challenges (steps 1-4) are **held back** — not sent to the frontend for accept/dismiss. They remain in the challenge state until step 5.
6. At step 5 → the graph update is released to the frontend normally (challenge cleared after acceptance)

**System prompt injection in `mentor_agent.py`:**

Add `_build_challenge_note(challenges: dict) -> str` that formats active challenges for the system prompt:

```
CHALLENGE LADDER — the following identity-level changes are being contested:

Node: "Discipline" (value, identity-level)
Current step: 2 of 5 (investigate)
History:
- Step 1: Flagged the gap between user's stated value and proposed change
Your task at step 2: Investigate. Ask what's blocking them. Is it the value itself or the circumstances? Don't accept yet.

Node: "Morning Routine" (habit, strategic-level)
[Not challenged — strategic nodes don't trigger the ladder]
```

**Step-specific instructions:**

| Step | Name | System prompt instruction |
|------|------|--------------------------|
| 1 | Flag | "Flag the gap. Name the node being changed and what it means in the context of the user's identity. Do not accept the change." |
| 2 | Investigate | "Investigate. Ask what's driving this change — is something blocking them? Injury, schedule, motivation? Don't accept yet." |
| 3 | Escalate | "Escalate. Reference how long this pattern has been active. Surface what happened last time they considered this change, if known. One sentence of history." |
| 4 | Challenge | "Challenge the shift directly. List what this change contradicts — other goals, beliefs, anti-goals. Ask: are you sure? This changes who you are." |
| 5 | Accept | "Accept with full context. The user has defended this change through 4 steps. Record why in the update. This is a conscious, earned change." |

**Integration with chat flow:**

The challenge note is built from `chat_store.get_active_challenges()` and injected into the system prompt alongside conflict/mode notes. This happens in `chat_stream()` / `chat()` — the challenge state is passed as a new parameter.

The flow becomes:
1. detect_conflicts → classify_mode (existing)
2. Get active challenges from chat_store
3. Pass challenges to mentor_agent.chat_stream()
4. After response parsed → scan graph_updates for new identity changes → update challenge state
5. Filter out challenged updates (steps 1-4) from the response sent to frontend

**Pattern to follow:** Same parameter-passing pattern as `conflicts` and `mode`. Same system prompt injection pattern as `_build_conflict_note()` and `_build_mode_note()`.

**Acceptance criteria:**
- Proposing to abandon a core value triggers step 1 of the challenge ladder
- Subsequent messages about the same node advance the step
- Steps 1-4 hold back the graph update from the frontend
- Step 5 releases the update
- Challenge note appears in system prompt with step-specific instructions
- Creating new identity nodes does NOT trigger the ladder
- Strategic-level nodes (goals, habits) do NOT trigger the ladder

**Test cases** (`backend/tests/test_challenge_ladder.py`, new file):
- `test_identity_update_triggers_challenge` — updating a value node status to "abandoned" creates challenge at step 1
- `test_strategic_update_no_challenge` — updating a goal node does NOT trigger ladder
- `test_challenge_advances_on_repeat` — second message about same node goes to step 2
- `test_challenge_step_5_releases_update` — at step 5, graph update appears in response
- `test_challenge_note_format` — system prompt contains correct step instruction
- `test_create_identity_no_challenge` — creating a new value node doesn't trigger
- `test_content_change_triggers_challenge` — changing a value's content triggers (not just status)
- `test_multiple_challenges_tracked` — two identity nodes can be challenged simultaneously
- `test_challenge_cleared_after_release` — challenge state removed after step 5 acceptance

---

### Task 3: Challenge Ladder SOUL.md Section

**Objective:** Add the challenge ladder protocol to SOUL.md so Athena has persistent behavioral instructions for identity-level changes.

**Files to modify:**
- `SOUL.md` — add `## Challenge Ladder` section after `## Conflict Protocol`
- `backend/mentor_agent.py` — parse the new section in `_load_soul()` and store it

**Requirements:**

Add to SOUL.md after `## Conflict Protocol`:

```markdown
## Challenge Ladder

When the system injects CHALLENGE LADDER context, an identity-level node is being modified or removed. This is not a normal update — it changes who the user is. Follow the step indicated by the system exactly.

Values evolve. But quietly giving up and genuinely evolving are different things. Your job is to make the user earn the change — not to block it forever, but to ensure it's conscious and defended.

The system tracks which step you're on. Do NOT skip steps or compress multiple steps into one response. Each step is one response. The user must come back and engage again before you advance.

At step 5, record WHY in the graph update. The history of this change — what was challenged, what the user said — is part of the node's story.
```

**Parsing in `_load_soul()`:**

The existing `_load_soul()` parses `##` sections into a dict. The `challenge ladder` section will automatically be captured under `sections["challenge ladder"]`. No structural parsing changes needed — just ensure the text is available.

Add the challenge ladder text to the instructions block (same way `conflict protocol` content is available). The `_build_challenge_note()` in Task 2 references these instructions implicitly — the SOUL.md section provides the philosophical framing, while the step-specific instructions in the challenge note provide the tactical directive.

**Acceptance criteria:**
- SOUL.md contains the Challenge Ladder section
- `_load_soul()` captures it in the sections dict
- The section text is included in the instructions portion of the system prompt
- No changes to the mode parsing logic

**Test cases** (add to `backend/tests/test_mentor_agent.py`):
- `test_soul_challenge_ladder_parsed` — "challenge ladder" key present in parsed sections
- `test_system_prompt_includes_challenge_ladder` — system prompt contains "Challenge Ladder" text when SOUL.md is loaded

---

### Task 4: Permanence-Aware Cascade Warnings

**Objective:** When a graph update or cascade proposal targets an identity/fundamental node, annotate it with a permanence warning that the frontend can surface.

**Files to modify:**
- `backend/services/vault_service.py` — add permanence warnings to `cascade_check()` proposals
- `backend/services/chat_service.py` — add permanence warnings to graph updates before sending to frontend

**Requirements:**

**In `vault_service.py` `cascade_check()`:**

When building cascade proposals, check the target node's permanence level. If it's `identity` or `fundamental`, add a `permanence_warning` field:

```python
{
    "action": "update",
    "node_id": "discipline",
    "title": "Discipline",
    "type": "value",
    "changes": {...},
    "reason": "Linked to 'Morning Routine' which was just abandoned",
    "confidence": "graph",
    "permanence_warning": "This would modify a core value (identity-level). Changes to identity nodes reshape who you are."
}
```

Warning messages by permanence level:
- `fundamental`: "This would modify a human fundamental (movement/sleep/nutrition/connection/purpose/stability). These are species-level needs — reshapeable but not removable."
- `identity`: "This would modify a core value, belief, or fear (identity-level). Changes here reshape who you are."
- All other levels: no warning (field absent or None)

**In `chat_service.py`:**

After `_dedup_check()` and `_validate_supersession()`, add a `_annotate_permanence_warnings()` step that checks each graph update's type and adds the `permanence_warning` field if applicable. This catches direct graph updates from Claude's response (not just cascade proposals).

```python
def _annotate_permanence_warnings(self, updates: list[dict]) -> list[dict]:
    for update in updates:
        node_type = update.get("type", "")
        if not node_type and update.get("node_id"):
            existing = self.graph.get_node(update["node_id"])
            if existing:
                node_type = existing.get("type", "")
        level, _ = _get_permanence(node_type)
        if level in ("identity", "fundamental"):
            update["permanence_warning"] = _PERMANENCE_WARNINGS[level]
    return updates
```

**Frontend behavior** (informational only — no frontend code changes in this sprint):

The `permanence_warning` field is included in the API response `graph_updates` array. The `GraphUpdateCard.svelte` component can check for this field and display a warning badge. Frontend changes are a follow-up — this sprint ensures the data is available.

**Pattern to follow:** Same enrichment pattern as `_dedup_check()` which annotates updates with `_duplicate` metadata. The permanence warning is metadata on the update, not a behavioral change.

**Acceptance criteria:**
- Cascade proposals targeting identity nodes include `permanence_warning`
- Direct graph updates targeting identity nodes include `permanence_warning`
- Updates targeting tactical/strategic/ephemeral nodes have no warning
- Warning text differs between `fundamental` and `identity` levels
- API response shape includes `permanence_warning` when applicable
- No existing tests break

**Test cases** (add to existing test files):
- `test_cascade_proposal_identity_warning` (in `test_vault_service.py`) — cascade proposal for a value node includes permanence_warning
- `test_cascade_proposal_tactical_no_warning` (in `test_vault_service.py`) — cascade proposal for a task node has no warning
- `test_graph_update_annotated_with_warning` (in `test_routes.py` or `test_chat_service.py`) — graph update for belief node gets permanence_warning
- `test_graph_update_no_warning_for_task` — task update has no permanence_warning
- `test_warning_text_differs_by_level` — identity and fundamental produce different warning messages

---

### Task 5: Frontend Permanence Warning Display

**Objective:** Surface permanence warnings in the GraphUpdateCard component so users see when they're modifying identity-level nodes.

**Files to modify:**
- `frontend/src/lib/GraphUpdateCard.svelte` — display warning when `permanence_warning` is present

**Requirements:**

When a graph update object has a `permanence_warning` field (non-null string), display a warning element in the card:

- Position: above the accept/dismiss buttons, below the node content
- Style: a distinct visual treatment — amber/orange background, warning icon (unicode shield or similar), the warning text
- The warning is informational — it does NOT block the accept button. The user can still accept or dismiss freely.
- If `permanence_warning` is absent or null, render nothing (no empty space)

The warning should be visually distinct from the existing conflict/duplicate indicators. It's not an error — it's a "this matters more than a normal update" signal.

**Also display challenge step if present:**

If the graph update has a `_challenge` field (from Task 2), display the current step: "Challenge step N/5 — this change requires further discussion before it can be accepted." The accept button should be **disabled** for steps 1-4 (the update is held back by the backend, but the frontend should reinforce this visually if the field is present).

**Pattern to follow:** The existing `_duplicate` field handling in `GraphUpdateCard.svelte` — conditional rendering based on a metadata field on the update object.

**Acceptance criteria:**
- Graph updates with `permanence_warning` show a visible warning in the card
- Warning uses amber/orange styling distinct from error states
- Accept/dismiss buttons remain functional when warning is present
- No warning shown for updates without the field
- Challenge step indicator shown when `_challenge` field present
- Cards without either field render exactly as before (no layout shift)

**Test cases** (manual verification — no automated frontend tests in current setup):
- Update card for a value node shows permanence warning text
- Update card for a task node shows no warning
- Warning doesn't interfere with accept/dismiss actions
- Challenge step indicator shows "Step 2/5" format

---

## API Response Contracts

No new endpoints. The chat API response shape is unchanged except for two new optional fields on graph_update objects:

```json
{
  "type": "done",
  "response": "...",
  "graph_updates": [
    {
      "action": "update",
      "node_id": "discipline",
      "type": "value",
      "changes": {...},
      "permanence_warning": "This would modify a core value, belief, or fear (identity-level). Changes here reshape who you are.",
      "_challenge": {
        "step": 2,
        "node_title": "Discipline",
        "permanence": "identity"
      }
    }
  ],
  "relevant_nodes": [...],
  "conflicts": [...]
}
```

Both `permanence_warning` and `_challenge` are optional — absent for non-identity updates. Frontend must handle their absence gracefully.

---

## Implementation Order

```
Task 1: Challenge State in ChatStore
  └→ Task 2: Challenge Ladder Detection and Activation (depends on Task 1)
      └→ Task 3: Challenge Ladder SOUL.md Section (depends on Task 2 for integration)
  Task 4: Permanence-Aware Cascade Warnings (independent of Tasks 1-3)
      └→ Task 5: Frontend Permanence Warning Display (depends on Task 4)
```

Tasks 1 and 4 can be built in parallel. Task 2 depends on Task 1. Task 3 depends on Task 2. Task 5 depends on Task 4.

Recommended sequence: **1 → 4 → 2 → 3 → 5** (get both foundations done, then wire up the challenge ladder, then the frontend).

---

## New Files

| File | Purpose |
|------|---------|
| `backend/tests/test_challenge_ladder.py` | Challenge ladder detection and flow tests |

**Total new files: 1** (well within the 15-file limit)

---

## Definition of Done

1. All existing tests pass (`python -m pytest` from `backend/`)
2. `npm run build` succeeds in `frontend/`
3. New test file passes with all cases green
4. Proposing to abandon a core value triggers the 5-step challenge ladder
5. Challenge state persists across messages within the same session
6. Steps 1-4 hold back identity-level graph updates from the frontend
7. Step 5 releases the update with recorded reasoning
8. Cascade proposals targeting identity nodes include `permanence_warning`
9. Direct graph updates targeting identity nodes include `permanence_warning`
10. Frontend displays permanence warnings with distinct visual treatment
11. SOUL.md contains the Challenge Ladder section
12. No regression in conflict detection, mode classification, or permanence scoring
13. No new dependencies added to `requirements.txt` or `package.json`
