# Bootstrap Conversation

> E2 Item 5 — When the vault is empty or near-empty, Athena's first conversation walks a new user through their fundamentals and builds the skeleton of their graph.

## Problem

A new user clones Athena, opens chat, and faces an empty graph with no guidance. `get_context()` returns `"(No relevant nodes found)"` for every query. Athena responds conversationally but has no context to reason about and no instinct to start building the graph. The user has to know what to type. That's a cold start failure.

## Solution

Detect low node count and inject a **bootstrap system prompt** that overrides the normal context block. This prompt tells Athena to lead a structured-but-conversational onboarding — asking about the user's core identity (values, goals, fears), key people, active projects, and daily life. Each response should propose graph creates for the fundamentals discussed.

## Detection

In `MentorAgent.get_context()`, before semantic search:

```python
total_nodes = len(self.graph.get_all_nodes())
if total_nodes < BOOTSTRAP_THRESHOLD:
    return _bootstrap_context(total_nodes), []
```

**Threshold:** `10` nodes. Below 10, the graph doesn't have enough structure for meaningful retrieval. Once the user has 10+ nodes from onboarding, normal retrieval kicks in.

## Bootstrap Context

The `_bootstrap_context(node_count)` function returns a context string that replaces the normal `{context}` block in the system prompt. It varies based on how far along the user is:

### Phase 1: Empty vault (0 nodes)

```
BOOTSTRAP MODE — This is a brand new vault with no nodes.

Your first job is to get to know this person. Lead a warm, curious conversation that
covers their fundamentals. Don't rush — one topic area per message. Ask follow-up
questions before moving on.

Start by introducing yourself briefly, then ask about ONE of these:
- What matters most to them (→ values, beliefs)
- What they're working toward (→ goals)
- What keeps them up at night (→ fears)

After each exchange, propose CREATE graph updates for everything discussed.
Use specific types (value, belief, goal, fear, habit, skill) — never "note".

You are building the skeleton of their knowledge graph. Every response MUST include
graph updates. Aim for 2-4 creates per response.
```

### Phase 2: Partially bootstrapped (1-9 nodes)

```
BOOTSTRAP MODE — This vault has {count} nodes. Still building the foundation.

The user has started their graph but it's incomplete. Review the existing nodes below
and identify which fundamental areas are still missing:
- Self domain: values, beliefs, goals, fears, habits, skills
- People domain: key people in their life
- Planning domain: active projects, upcoming events
- Life domain: current situation, recent experiences

Steer the conversation toward gaps. If they have goals but no fears, ask about fears.
If they have no people nodes, ask who matters to them. Keep it natural — don't
interrogate.

Every response MUST include graph updates for new information shared.

EXISTING NODES:
{existing_nodes_summary}
```

For phase 2, `existing_nodes_summary` is a compact list of what exists:
```
- "Discipline" (value)
- "Run a marathon" (goal)
- "Public speaking" (fear)
```

This lets Athena see what's already captured and identify gaps.

## Implementation

### Files changed

| File | Change |
|------|--------|
| `backend/mentor_agent.py` | Add `BOOTSTRAP_THRESHOLD`, `_bootstrap_context()`, early return in `get_context()` |

### `_bootstrap_context(node_count, graph)` — new function

```python
BOOTSTRAP_THRESHOLD = 10

def _bootstrap_context(node_count: int, graph: VaultGraph) -> str:
    if node_count == 0:
        return _BOOTSTRAP_EMPTY

    # Build summary of existing nodes for gap analysis
    nodes = graph.get_all_nodes()
    lines = []
    for n in sorted(nodes, key=lambda x: x.get("type", "")):
        lines.append(f'- "{n.get("title", n["id"])}" ({n.get("type", "unknown")})')
    summary = "\n".join(lines)

    return _BOOTSTRAP_PARTIAL.format(count=node_count, existing_nodes_summary=summary)
```

### `get_context()` — early return

At the top of `get_context()`, before temporal resolution:

```python
total_nodes = len(self.graph.get_all_nodes())
if total_nodes < BOOTSTRAP_THRESHOLD:
    return _bootstrap_context(total_nodes, self.graph), []
```

The empty `[]` for search_results means no `relevant_nodes` chips appear in the UI — correct for bootstrap since there's nothing to reference yet.

### No changes needed to:
- `chat_service.py` — bootstrap context flows through `get_context()` → `chat()`/`chat_stream()` transparently
- `GraphUpdateCard` — creates from bootstrap are standard graph updates, rendered normally
- `vault_service.py` — writes from bootstrap are standard vault writes

## Edge Cases

**User ignores onboarding and asks a random question:** Athena still has the bootstrap prompt, so she'll answer but gently steer back to fundamentals. The graph update rules still apply — if the random question contains capturable info, it gets written.

**User bootstraps via Telegram:** `send_simple_message()` → `send_message()` → `mentor.chat()` → same `get_context()`. Bootstrap works identically. Confirm/dismiss flow handles the creates.

**Threshold crossing mid-conversation:** If the user accepts 10+ creates during bootstrap, the next message hits normal retrieval. This is fine — the transition is seamless because the system prompt structure is the same, only the `{context}` block changes.

**Re-bootstrap after vault wipe:** If the user deletes their vault and rebuilds, node count drops below threshold and bootstrap re-activates automatically.

## Acceptance Criteria

1. Empty vault → first message from Athena is a warm intro + question about fundamentals
2. Each bootstrap response proposes 2-4 graph creates with correct types
3. After accepting ~10 creates, normal retrieval takes over seamlessly
4. Partially bootstrapped vault (1-9 nodes) → Athena identifies gaps and steers toward them
5. No changes to existing behavior when vault has 10+ nodes
