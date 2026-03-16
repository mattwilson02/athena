# Dismissal Awareness

> E3 Item 1 — The AI sees its own proposals in conversation history but doesn't know which were accepted vs dismissed, causing phantom node references.

## Problem

When Athena proposes a graph update (e.g. create "Guitar Practice" project), the `<graph_updates>` block is part of the assistant message saved to conversation history. If the user dismisses the proposal, the node is never created — but the AI still sees its own proposal in history and assumes it exists.

Result: the AI tries to update, reference, or link to nodes that were never created. "I'll update your Guitar Practice project..." when there is no Guitar Practice project.

The `dismissed_updates` list already exists on each session (stored in `chat_store`), but it's never surfaced to the AI. It's only used by the frontend to mark cards as dismissed.

## Solution

Two-part fix:

### Part A: Inject dismissed IDs into system prompt

When building the system prompt in `chat_service`, look up the session's `dismissed_updates` list and pass it to `mentor_agent`. The mentor injects a `DISMISSED PROPOSALS:` section into the system prompt alongside the context.

```
DISMISSED PROPOSALS (user rejected these — do NOT reference or update them):
- guitar-practice-project
- daily-scales-habit
```

This goes into the system prompt (not conversation history) because it's session state, not a message.

### Part B: Existing dedup guard already helps

`chat_service._dedup_check()` already drops update/link actions targeting non-existent node IDs (lines 267-270). This catches most phantom updates at the post-processing level. But it doesn't prevent the AI from *talking about* the phantom node in its text response. Part A fixes that.

### Part C: Strip dismissed proposals from history (optional enhancement)

When `get_messages_for_api()` builds the message list for Claude, it could strip `<graph_updates>` blocks from assistant messages for any proposals that were dismissed. This removes the source of confusion entirely — Claude never sees the proposal it made.

However, this is more invasive (requires parsing assistant message content) and Part A should be sufficient. Park this unless Part A proves inadequate.

## Implementation

### Files changed

| File | Change |
|------|--------|
| `backend/services/chat_service.py` | Pass `dismissed_ids` to mentor on `chat()` and `chat_stream()` calls |
| `backend/mentor_agent.py` | Accept `dismissed_ids` param, inject into system prompt |
| `backend/tests/test_mentor_agent.py` | Test dismissed IDs appear in system prompt |
| `backend/tests/test_chat_service.py` | Test dismissed IDs are passed through |

### `chat_service.py` changes

Both `send_message()` and `stream_message()` already have access to the session. Read `dismissed_updates` and pass to mentor:

```python
def send_message(self, session_id: str, message: str) -> dict:
    session = self.chat_store.get_session(session_id)
    if session is None:
        return {"error": "Session not found", "status": 404}

    dismissed_ids = session.get("dismissed_updates", [])

    # ... existing code ...

    try:
        result = self.mentor.chat(message, history, dismissed_ids=dismissed_ids)
    # ...
```

Same pattern for `stream_message()`.

### `mentor_agent.py` changes

Add `dismissed_ids` parameter to `chat()` and `chat_stream()`. Build a dismissed note and append to the system prompt:

```python
def _build_dismissed_note(self, dismissed_ids: list[str]) -> str:
    """Build a system prompt note about dismissed proposals."""
    if not dismissed_ids:
        return ""
    ids = "\n".join(f"- {nid}" for nid in dismissed_ids)
    return (
        f"\n\nDISMISSED PROPOSALS (user rejected these — do NOT reference or update them):\n"
        f"{ids}\n"
        f"These nodes do NOT exist. Do not propose updates to them unless the user explicitly asks again."
    )

def chat(self, message: str, conversation_history: list[dict],
         dismissed_ids: list[str] | None = None) -> dict:
    context, search_results = self.get_context(message, conversation_history)
    today = date.today().strftime("%A %d %B %Y")
    system = self.system_prompt_template.format(context=context, today=today)
    system += self._build_dismissed_note(dismissed_ids or [])
    # ... rest unchanged
```

### Web frontend dismiss flow

The frontend already calls `POST /api/chat/sessions/:id/dismiss` with an `update_key` (the node_id). This writes to `dismissed_updates` in the session. No frontend changes needed — the backend will now read this list and inject it.

There's also a second dismiss path: when the user dismisses a card in the web UI via the GraphUpdateCard component, it needs to call the dismiss endpoint. Let me check this works.

### Telegram dismiss flow

The `send_simple_message()` → `_dismiss_pending()` path already calls `self.chat_store.dismiss_update(session_id, update_key)`. This writes to the same `dismissed_updates` list. No changes needed.

## Edge Cases

- **Empty dismissed list:** No note injected. Zero cost.
- **Long dismissed list (20+ nodes):** Possible in long sessions. Cap at most recent 10 dismissed IDs to avoid bloating the system prompt. Older dismissals are less likely to cause phantom references.
- **Same node dismissed then re-proposed:** If the user explicitly asks about the concept again, the AI can propose a new node. The dismissed note says "unless the user explicitly asks again" — Claude should understand this.
- **Node was dismissed but later created by a different conversation path:** The dismissed list is per-session. If the node gets created via a different proposal in the same session, remove it from the dismissed list when `_confirm_pending` succeeds.

## Acceptance Criteria

1. Dismiss a create proposal → next AI response does not reference the phantom node
2. Dismiss a create proposal → AI does not propose an update to it
3. Dismissed IDs appear in system prompt when present
4. Empty dismissed list adds nothing to the prompt
5. Dismissed list capped at 10 most recent entries
6. Confirming a previously-dismissed node ID removes it from the dismissed list
7. All existing tests pass + new dismissal awareness tests
