# Sprint 2: Permanence and Modes

> **Epoch:** E4 — Weight and Memory
> **Branch:** `sprint/2`
> **Goal:** Core values outweigh Tuesday's tasks in retrieval. Athena adapts her tone to the stakes.

---

## Overview

E3 gave Athena the ability to detect conflicts and push back. But she treats all nodes equally — a core value scores the same as a grocery list in retrieval. And she speaks in one voice regardless of whether you're asking a quick question or reconsidering your career.

This sprint adds two foundational E4 capabilities:

1. **Permanence scoring** — every node type has a permanence level (fundamental → ephemeral). Higher-permanence nodes get a retrieval boost, ensuring identity and values are always in context.
2. **Adaptive mode selection** — incoming messages are classified into Mirror/Advisor/Guardian/Dialectic modes. Each mode injects distinct behavioral instructions into the system prompt, so Athena's tone matches the weight of the conversation.

These two features are independent but complementary: permanence ensures the right nodes are in context, modes ensure the right tone is used when responding.

**Not in this sprint:** Challenge ladder (5-step escalation for identity changes) and permanence-aware cascade warnings. Both build on this sprint's work and belong in Sprint 3.

---

## What Exists

| Component | State | Relevant to This Sprint |
|-----------|-------|------------------------|
| `mentor_agent.py` `get_context()` | Scoring loop with semantic, domain, recency, centrality, temporal, session, and status boosts | Permanence boost added here |
| `_STATUS_PENALTIES` dict | Maps statuses to score adjustments | Permanence boost follows same additive pattern |
| `conflict_service.py` `detect_conflicts()` | Returns conflict list with severity (hard/soft) | Guardian mode triggers when conflicts found |
| `SOUL.md` Conflict Protocol | 4-step escalation (flag/explain/challenge/accept) | Mode-specific personality sections added alongside |
| `schema_parser.py` `_parse_types()` | Extracts frontmatter from YAML blocks in schema.md | Already parses any field in the YAML block — permanence will be picked up automatically |
| `schema_parser.py` `generate_type_rules()` | Builds NODE TYPE RULES for system prompt | No changes needed — permanence is a retrieval concern, not a type selection concern |
| `chat_service.py` | Orchestrates chat flow, calls `detect_conflicts()` then `chat()`/`chat_stream()` | Passes mode to mentor_agent |

---

## Architectural Decisions

### 1. Permanence is a type-level default, not a per-node field

The ROADMAP says permanence should be a frontmatter field with type-based fallbacks. For this sprint, implement **type-based defaults only** — a lookup dict in `mentor_agent.py` mapping each type to its permanence level. No schema.md changes, no per-node `permanence` field yet.

**Why:** Adding permanence to schema.md frontmatter means every existing node needs migration, the parser needs to handle it, and the FORMAT_SPEC needs to instruct the AI to set it. That's scope for Sprint 3 when the challenge ladder needs per-node permanence. For now, type-level defaults deliver 95% of the value: values are always identity-level, tasks are always tactical-level.

### 2. Mode classifier lives in mentor_agent.py, not a separate service

Unlike conflict detection (which has complex multi-stage signal analysis), mode classification is a lightweight heuristic — ~50 lines of keyword matching + conflict result inspection. It doesn't warrant a separate service file.

**Why:** Follows the project's "no over-engineering" principle. The classifier's inputs (message text, conflict results) and output (mode string) are simple. If it grows complex enough to justify extraction later, that's a refactor, not architecture.

### 3. Mode instructions injected into system prompt, not post-processing

Each mode gets a block of behavioral instructions prepended to the system prompt. The AI adapts its own tone — we don't strip or rewrite its output.

**Why:** E3 descoped anti-cheerleading post-processing for the same reason: prompt-level instructions are the right lever. Post-processing is fragile and fights the model.

### 4. Guardian mode is conflict-triggered, not heuristic-classified

Guardian mode activates **only** when `detect_conflicts()` returns results. The keyword heuristic never produces Guardian — it's always a conflict detection override.

**Why:** Prevents false Guardian activations. A message like "I'm going to skip the gym" should only trigger Guardian if there's actually a fitness goal/habit in the graph. Without graph data backing it, pushing back would be annoying noise.

---

## Tasks

### Task 1: Permanence Type Defaults

**Objective:** Define a permanence level for every node type and add a retrieval score boost based on permanence.

**Files to modify:**
- `backend/mentor_agent.py` — add `_PERMANENCE_DEFAULTS` dict and permanence boost to scoring loop

**Requirements:**

Permanence level mapping (from the vision's Memory Permanence Levels table):

| Permanence | Boost | Types |
|-----------|-------|-------|
| `fundamental` | +0.20 | *(none yet — reserved for human fundamentals nodes in future schema)* |
| `identity` | +0.15 | `value`, `belief`, `fear` |
| `strategic` | +0.10 | `goal`, `habit`, `skill`, `project` |
| `tactical` | +0.00 | `task`, `reminder`, `event`, `expense`, `subscription`, `budget` |
| `ephemeral` | -0.05 | `daily`, `note` |

Types not listed (`person`, `organisation`, `book`, `article`, `idea`, `interest`, `movie`, `quote`, `pill`, `experience`, `memory`, `place`): default to `tactical` (+0.00, no effect).

The boost is **additive** to the existing scoring total in `get_context()`, applied in the same loop where `domain_boost`, `recency_boost`, `centrality_boost`, `temporal_boost`, `s_boost`, and `status_penalty` are summed.

Also apply permanence boost to temporal node injection (lines ~780-792 in current code) where nodes not in semantic results are injected with a base score of 0.3. The injected score should include the permanence boost.

**Pattern to follow:** Same additive boost pattern as `_STATUS_PENALTIES` — a dict lookup, add to total score. No new abstractions.

**Acceptance criteria:**
- A `value` node with moderate semantic relevance (0.5) outscores a `task` node with high semantic relevance (0.7) when other boosts are equal
- `daily` and `note` nodes are slightly deprioritized vs neutral types
- Identity nodes consistently appear in top-5 context even for tactical queries
- No existing tests break — permanence is purely additive

**Test cases** (`backend/tests/test_permanence_scoring.py`, new file):
- `test_identity_boost_applied` — value node gets +0.15
- `test_strategic_boost_applied` — goal node gets +0.10
- `test_ephemeral_penalty_applied` — daily node gets -0.05
- `test_tactical_no_boost` — task node gets +0.00
- `test_unlisted_type_no_boost` — person node gets +0.00
- `test_permanence_beats_semantic_in_close_race` — identity node (semantic 0.5) outscores tactical node (semantic 0.6) when other boosts are equal
- `test_temporal_injection_includes_permanence` — temporally injected value node gets 0.3 + 0.15 = 0.45 base score

---

### Task 2: Mode Classifier

**Objective:** Classify incoming user messages into one of four communication modes: Mirror, Advisor, Guardian, Dialectic.

**Files to modify:**
- `backend/mentor_agent.py` — add `classify_mode()` function

**Requirements:**

```
classify_mode(message: str, conflicts: list[dict] | None) -> str
```

Returns one of: `"mirror"`, `"advisor"`, `"guardian"`, `"dialectic"`

Classification logic (evaluated in priority order):

1. **Guardian** — `conflicts` is non-empty and contains at least one `hard` severity conflict. This is the ONLY path to Guardian mode.
2. **Dialectic** — message contains big-decision signals: "should I", "what if", "considering", "torn between", "not sure if I should", "debating whether", "thinking about quitting", "thinking about leaving", "worth it to". These indicate uncertainty on high-stakes decisions where Athena should challenge assumptions rather than answer directly.
3. **Advisor** — message contains new-commitment or change signals: "I want to start", "I'm going to", "planning to", "thinking about starting", "sign up for", "commit to", "take on", "new project", "new goal", "new habit". Also triggers when `conflicts` is non-empty but all conflicts are `soft` severity.
4. **Mirror** — default. Information requests, status queries, casual conversation, anything that doesn't match above.

Signal matching is case-insensitive substring matching — same approach as `_DOMAIN_KEYWORDS` in the existing code.

**Edge cases:**
- A message that matches both Dialectic and Advisor signals → Dialectic wins (it's higher priority)
- A message with soft conflicts AND dialectic signals → Dialectic wins
- A message with hard conflicts AND dialectic signals → Guardian wins (conflicts always override)
- Empty message → Mirror

**Pattern to follow:** Same keyword-list-and-loop approach as `_classify_domains()` and the signal detection in `conflict_service.py`. No ML, no API calls.

**Acceptance criteria:**
- "What's on my plate this week?" → Mirror
- "I want to start learning guitar" → Advisor
- "Should I quit my job?" → Dialectic
- Message with hard conflicts → Guardian regardless of content
- Message with soft conflicts + no other signals → Advisor
- Classification runs in <1ms (pure string matching)

**Test cases** (`backend/tests/test_mode_classifier.py`, new file):
- `test_default_mirror` — plain question returns mirror
- `test_guardian_hard_conflict` — hard conflict overrides everything
- `test_guardian_not_triggered_by_keywords` — "I'm going to skip the gym" with no conflicts returns advisor, not guardian
- `test_dialectic_should_i` — "Should I move to London?" returns dialectic
- `test_dialectic_what_if` — "What if I took 3 months off?" returns dialectic
- `test_advisor_new_commitment` — "I want to start meditating" returns advisor
- `test_advisor_soft_conflicts` — soft conflicts with no other signals returns advisor
- `test_dialectic_beats_advisor` — "Should I start a new project?" returns dialectic (not advisor)
- `test_guardian_beats_dialectic` — hard conflict + "should I" returns guardian
- `test_empty_message_mirror` — empty string returns mirror
- `test_case_insensitive` — "SHOULD I quit?" returns dialectic

---

### Task 3: Mode-Specific SOUL.md Sections

**Objective:** Add mode-specific behavioral instructions to SOUL.md that get injected into the system prompt based on the classified mode.

**Files to modify:**
- `SOUL.md` — add `## Modes` section with subsections for each mode
- `backend/mentor_agent.py` — modify `_load_soul()` to parse mode sections; modify `_build_system_prompt()` (or equivalent) to inject the active mode's instructions

**Requirements:**

Add to SOUL.md after the existing `## Conflict Protocol` section:

```markdown
## Modes

Athena adapts her communication style to the weight of the conversation. One mode is active per message. The system selects it — you follow its instructions.

### Mirror
You are answering a straightforward question or presenting information. Be concise and direct. Present the full picture across relevant dimensions. Don't add unsolicited advice or challenges — the user asked a question, answer it. Reference specific nodes. Keep it short.

### Advisor
The user is proposing a change or new commitment. Your job is to make the cost visible, not to block. Surface active obligations, competing priorities, and resource constraints. Ask "where does this fit?" and "what gives?" Don't cheerfully accept — but don't lecture either. One targeted question maximum.

### Guardian
A real conflict has been detected. Follow the Conflict Protocol exactly. Do not soften hard conflicts. Name the conflicting nodes first, before anything else. Challenge the user to reconcile. This is not about being harsh — it's about being honest when the stakes are real.

### Dialectic
The user is wrestling with a big decision or uncertain territory. Your job is NOT to answer — it's to help them think. Challenge assumptions. Surface what they might be avoiding. Ask the question behind the question. Use their own stated values and goals as mirrors. Don't rush to resolution — sit in the tension with them.
```

**SOUL.md parsing changes in `_load_soul()`:**

Currently `_load_soul()` returns `(identity_text, instructions_text)`. It needs to also return mode instructions. Options:
- Return a third element: `(identity, instructions, modes_dict)` where `modes_dict = {"mirror": "...", "advisor": "...", ...}`
- Or parse modes from the instructions text at prompt-build time

Prefer the first approach — parse once at boot, use the dict at request time. The `MentorAgent.__init__()` stores the result; add `self.mode_instructions: dict[str, str]` alongside `self.identity` and `self.instructions`.

**System prompt injection:**

In `_build_system_prompt()` (or wherever the system prompt is assembled), after the existing identity + instructions + conflict protocol, append:

```
ACTIVE MODE: {mode_name}
{mode_instructions_text}
```

This goes at the end of the system prompt so it's the most recent instruction Claude sees.

**Pattern to follow:** The existing `_load_soul()` already splits SOUL.md by `##` headings. Extend the same parsing to extract mode subsections (### headings under ## Modes).

**Acceptance criteria:**
- SOUL.md contains all four mode sections with distinct behavioral instructions
- `_load_soul()` returns mode instructions as a parseable structure
- System prompt includes `ACTIVE MODE: mirror` (or advisor/guardian/dialectic) with the corresponding instructions
- Mode instructions don't appear when no mode is specified (backward compatibility)

**Test cases** (add to `backend/tests/test_mentor_agent.py`):
- `test_soul_modes_parsed` — all four mode keys present in parsed result
- `test_system_prompt_includes_active_mode` — system prompt contains "ACTIVE MODE: advisor" when advisor mode is set
- `test_system_prompt_mirror_default` — when mode is mirror, system prompt includes mirror instructions
- `test_no_mode_backward_compat` — when mode is None, no "ACTIVE MODE" section in system prompt

---

### Task 4: Mode Integration in Chat Flow

**Objective:** Wire the mode classifier into the chat request flow so every message gets classified and the appropriate mode instructions are injected.

**Files to modify:**
- `backend/services/chat_service.py` — call `classify_mode()` after `detect_conflicts()`, pass mode to `chat()`/`chat_stream()`
- `backend/mentor_agent.py` — `chat()` and `chat_stream()` accept a `mode` parameter, pass it to system prompt builder

**Requirements:**

In `chat_service.py`, the current flow is:
1. Save user message
2. Load conversation history
3. Call `detect_conflicts(message, graph, vector_index)`
4. Call `mentor_agent.chat_stream(message, session_id, conflicts=conflicts, dismissed=dismissed)`

Insert between steps 3 and 4:
```
mode = mentor_agent.classify_mode(message, conflicts)
```

Then pass `mode` to `chat_stream()` / `chat()`:
```
mentor_agent.chat_stream(message, session_id, conflicts=conflicts, dismissed=dismissed, mode=mode)
```

In `mentor_agent.py`, `chat()` and `chat_stream()` already accept `conflicts` and `dismissed` kwargs. Add `mode: str = "mirror"` with the same pattern. Pass it to the system prompt builder.

**Edge cases:**
- If `detect_conflicts()` raises an exception (it shouldn't, but defensively), default to Mirror mode
- Mode is informational only — it changes the system prompt instructions, nothing else. No branching logic based on mode outside of prompt injection.

**Pattern to follow:** Same parameter-passing pattern used for `conflicts` and `dismissed` in E3. The chat_service calls the detection, then passes results downstream.

**Acceptance criteria:**
- Every chat message is classified into a mode before the AI responds
- The mode name appears in the system prompt sent to Claude
- Mode classification doesn't add measurable latency (it's pure string matching)
- Existing chat tests still pass — mode defaults to mirror when not specified

**Test cases** (add to `backend/tests/test_mentor_agent.py` or `backend/tests/test_routes.py`):
- `test_chat_stream_includes_mode` — mock `classify_mode`, verify system prompt contains mode instructions
- `test_chat_stream_default_mode` — when mode not passed, defaults to mirror
- `test_mode_passed_through_chat_service` — integration test: send a message with advisor signals, verify mode reaches mentor_agent

---

### Task 5: Permanence-Aware Conflict Context

**Objective:** When conflicts are injected into context, include the conflicting node's permanence level so the AI can calibrate its response intensity.

**Files to modify:**
- `backend/mentor_agent.py` — modify `_build_conflict_note()` to include permanence level
- `backend/services/conflict_service.py` — add permanence level to conflict return objects

**Requirements:**

Currently `detect_conflicts()` returns conflicts shaped like:
```python
{"node_id": "...", "title": "...", "type": "...", "conflict_type": "...", "explanation": "...", "severity": "hard|soft"}
```

Add `permanence` field:
```python
{"node_id": "...", "title": "...", "type": "...", "conflict_type": "...", "explanation": "...", "severity": "hard|soft", "permanence": "identity"}
```

The permanence value comes from the same `_PERMANENCE_DEFAULTS` dict created in Task 1. Import it in `conflict_service.py` or pass the mapping as a parameter.

In `_build_conflict_note()`, include the permanence level in the formatted output:
```
POTENTIAL CONFLICTS:
- [hard | identity] Value "Discipline" (value) — staying out late contradicts your stated commitment to discipline
- [soft | strategic] Goal "Marathon Training" (goal, status: active) — skipping gym disrupts your 4x/week training schedule
```

This gives Claude additional signal about how seriously to treat each conflict. An identity-level conflict should provoke stronger pushback than a tactical-level one — the mode instructions (Guardian) already say "don't soften hard conflicts", and the permanence label reinforces which conflicts are truly foundational.

**Pattern to follow:** Same enrichment approach used when `_build_conflict_note()` already formats severity and conflict_type. Adding permanence is one more field in the same format string.

**Acceptance criteria:**
- Every conflict in the formatted context note includes a permanence label
- Identity-level conflicts display `identity` tag, strategic display `strategic`, etc.
- Conflict service returns permanence in its result dicts
- No changes to conflict detection logic — permanence is a label, not a filter

**Test cases** (add to existing test files):
- `test_conflict_includes_permanence` (in `test_conflict_service.py`) — conflict against a value node has `permanence: "identity"`
- `test_conflict_note_shows_permanence` (in `test_mentor_agent.py`) — formatted conflict note includes permanence label
- `test_conflict_permanence_for_tactical_type` — conflict against a task has `permanence: "tactical"`

---

## API Response Contracts

No new endpoints in this sprint. All changes are internal to the system prompt and retrieval scoring. The chat API response shape is unchanged:

```json
{
  "type": "done",
  "response": "...",
  "graph_updates": [...],
  "relevant_nodes": [...]
}
```

The only externally observable difference: Claude's responses will vary in tone based on mode, and context will more consistently include identity/strategic nodes.

---

## Implementation Order

```
Task 1: Permanence Type Defaults
  └→ Task 2: Mode Classifier (independent of Task 1, can be parallel)
      └→ Task 3: Mode-Specific SOUL.md Sections (depends on Task 2 for mode names)
          └→ Task 4: Mode Integration in Chat Flow (depends on Tasks 2 + 3)
  └→ Task 5: Permanence-Aware Conflict Context (depends on Task 1)
```

Tasks 1 and 2 can be built in parallel. Task 3 depends on Task 2 (needs mode names). Task 4 depends on Tasks 2 + 3. Task 5 depends on Task 1.

Recommended sequence: **1 → 2 → 3 → 5 → 4** (finish permanence and modes independently, then wire them together).

---

## New Files

| File | Purpose |
|------|---------|
| `backend/tests/test_permanence_scoring.py` | Permanence boost unit tests |
| `backend/tests/test_mode_classifier.py` | Mode classification unit tests |

**Total new files: 2** (well within the 15-file limit)

---

## Definition of Done

1. All existing tests pass (`python -m pytest` from `backend/`)
2. `npm run build` succeeds in `frontend/`
3. New test files pass with all cases green
4. Identity nodes (values, beliefs, fears) consistently rank in top-5 context for mixed queries
5. Mode classification returns correct mode for all acceptance criteria scenarios
6. System prompt includes `ACTIVE MODE: {mode}` with mode-specific instructions
7. SOUL.md contains four distinct mode personality sections
8. Conflict context notes include permanence labels
9. No regression in conflict detection behavior
10. No new dependencies added to `requirements.txt`
