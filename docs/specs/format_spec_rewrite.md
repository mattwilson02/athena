# Spec: FORMAT_SPEC Rewrite

**Epoch:** E2 — Conversations That Stick
**Item:** 2 of 6
**Files:** `backend/mentor_agent.py`
**Status:** Specced

---

## Problem

The current `_FORMAT_SPEC` and `_DEDUP_RULES` in `mentor_agent.py` tell the AI what to do, but the instructions aren't effective enough. Common failures:

1. **Note catch-all** — AI defaults to `type: "note"` when a more specific type exists (task, event, person update)
2. **Missing graph updates** — user says "I cancelled the trip" and Athena acknowledges it conversationally but doesn't propose an update to the trip node
3. **Creates instead of updates** — AI creates a new node about an existing thing instead of updating the existing node
4. **Hub appending** — AI appends task details to a project node instead of creating a separate task + link
5. **Weak cascade awareness** — AI rarely checks neighbors even though the prompt says to

## Root Causes

- Rules are stated once in generic terms — AI needs examples of exact conversational patterns → expected output
- "Write aggressiveness" section is declarative ("MUST produce graph updates") but doesn't show what that looks like
- Type selection rules list remappings but don't cover the full conversational surface area
- No negative examples — AI doesn't know what bad output looks like
- Dedup rules are separate from the main spec, reducing their salience

## Approach

Rewrite `_FORMAT_SPEC` and `_DEDUP_RULES` as a single, unified `_GRAPH_INSTRUCTIONS` block with:

1. **Trigger rules** — pattern-matched examples of user statements that MUST produce graph updates
2. **Type decision tree** — structured "if X then Y" rules, not just a remap list
3. **Action decision tree** — when to create vs update vs link
4. **Negative examples** — "NOT this → THIS instead" patterns
5. **Tighter dedup** — integrated into the action decision tree, not a separate section

## Detailed Design

### New `_GRAPH_INSTRUCTIONS` structure

```
GRAPH UPDATE RULES
├── 1. WHEN TO WRITE (trigger patterns)
├── 2. WHAT TYPE (decision tree)
├── 3. WHAT ACTION (create vs update vs link)
├── 4. COMMON MISTAKES (negative examples)
├── 5. FORMAT (JSON schema — same as current)
└── 6. EDGES (connection rules)
```

### Section 1: WHEN TO WRITE

Pattern → action mapping. Each line is a trigger the AI should recognize:

| User says... | Expected action |
|---|---|
| "I cancelled X" / "X isn't happening" | UPDATE X → status: cancelled |
| "I'm going to X" / "I've decided to X" | CREATE task/event/project |
| "Actually, I'm doing Y instead of X" | CREATE Y + UPDATE X → status: superseded |
| "I met someone named X" / "X is a ..." | CREATE person (or UPDATE if exists) |
| "X is on [date]" / "moved X to [date]" | UPDATE X → date/deadline change |
| "I finished X" / "X is done" | UPDATE X → status: completed |
| "I learned that..." / "key takeaway:" | CREATE pill/idea (not note) |
| "I watched/read X" | CREATE movie/book |
| "X costs Y" / "I spent Y on X" | CREATE expense or UPDATE budget |
| Shares opinion about existing node | UPDATE that node (append_content) |
| Describes a place they went | CREATE place or experience |

Key rule: **If the user's message changes the state of the world (their plans, knowledge, relationships), it MUST produce a graph update. Conversation without graph updates means knowledge is being lost.**

### Section 2: WHAT TYPE (decision tree)

Replace the flat remap list with a decision tree:

```
Is it about a person? → person
Is it a scheduled event with a date? → event
Is it a task/to-do? → task
Is it a multi-step effort? → project
Is it a film/show they watched? → movie
Is it a book they read? → book
Is it something they spent money on? → expense
Is it a recurring routine? → habit
Is it a place? → place
Is it a realization/lesson/mental model? → pill
Is it a memorable quote? → quote
Is it a fear/anxiety? → fear
Is it a belief/value? → belief or value
Is it an abstract concept worth exploring? → idea
Is it a life experience/memory? → experience or memory
None of the above? → note (LAST RESORT)
```

### Section 3: WHAT ACTION

```
Does a node for this thing already exist in CONTEXT?
  YES → Does the user want to change it?
    YES → action: "update" with changes
    NO → action: "link" (connect to new context)
  NO → Is the user describing something new?
    YES → action: "create" + edges to related existing nodes
    NO → No graph update needed (pure conversation)
```

### Section 3b: MULTI-NODE BATCHES

A single user message often mentions multiple entities. The AI should propose creates for ALL of them, not just the "main" one. If someone talks about a plan involving a place and a person, that could be 3 creates (project + place + person) all linked together — not just 1 project node with everything stuffed into its content.

Rule: after deciding on the primary graph update, scan the message for other entities (people, places, events, tasks) that don't already exist in the context. Propose creates for those too, linked to each other and to the primary node.

Example: "Dad is looking at property in Wales near Bryncoch" →
- CREATE project "Dad's Wales Property Purchase" (primary)
- CREATE place "Bryncoch, Wales" (if no Wales/Bryncoch place exists) + `located_in` edge from project
- LINK to existing person "Dad" via `involves`

### Section 4: COMMON MISTAKES

Negative examples teach the AI what NOT to do:

```
BAD: User says "I cancelled the Italy trip" → create note "Cancelled Italy Trip"
GOOD: → update italy-trip, changes: {frontmatter: {status: "cancelled"}}

BAD: User says "I need to book flights for the trip" → update trip, append_content: "Need to book flights"
GOOD: → create task "Book Flights for Italy", edges: [{target: "italy-trip", type: "part_of"}]

BAD: User says "Met someone called Sarah at the gym" → create note "Met Sarah"
GOOD: → create person "Sarah", frontmatter: {met_at: "gym"}, edges: [{target: "gym-membership", type: "met_at"}]

BAD: User mentions a topic related to an existing node → no graph update
GOOD: → link between the discussed nodes if not already connected
```

### Section 5: FORMAT

Keep the existing JSON format block unchanged — it works well. Just move it to the end of the unified section.

### Section 6: EDGES

Tighten the edge rules:

```
Every CREATE must have at least one edge unless there are truly zero related nodes.
Prefer specific edge types over relates_to:
- Task for a project → part_of
- Person met at a place → met_at
- Idea from a book → inspired_by
- Expense for a subscription → funded_by
- Task blocking another → blocked_by
```

## Implementation

1. Delete `_FORMAT_SPEC` and `_DEDUP_RULES` constants
2. Create new `_GRAPH_INSTRUCTIONS` constant with the unified structure above
3. Update `build_system_prompt()` to use the new constant
4. Keep `{type_enum}` template variable for dynamic type list injection
5. No other file changes — this is prompt-only

## What Does NOT Change

- `_parse_graph_updates()` — same XML format
- `_validate_types()` — same post-processing
- `generate_type_rules()` — still included in prompt (domain/type reference)
- JSON schema for graph updates — identical structure
- Chat API interface — no changes

## Testing

Manual testing — have real conversations and verify:
1. "I cancelled X" → update card with status: cancelled (not a new note)
2. "I'm going to do X" → create card with correct type
3. "Actually, Y instead of X" → create Y + update X
4. "Met someone called Z" → create person (not note)
5. Topic mentioned near existing node → link proposed
6. Pure conversational reply (no state change) → no graph updates (don't over-propose)

## Risks

- **Over-proposing** — too aggressive = every message produces 3+ updates, annoying the user. Mitigation: the "pure conversation → no update" escape hatch, and "LAST RESORT" framing for note type keeps the gate.
- **Prompt length** — longer instructions eat into context window. Mitigation: the new instructions replace two existing blocks, net increase should be modest (~300-500 chars). Monitor total system prompt size.
- **Model sensitivity** — Haiku may follow structured decision trees differently than Sonnet. Test on both.
