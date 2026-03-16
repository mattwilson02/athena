# Conflict Protocol

> E3 Item 5 — Give Athena a procedure for handling detected conflicts, not just visibility of them.

## Problem

Conflicts are detected and injected into context (E3 items 1-4), but Claude has no defined protocol for *how* to handle them. Sometimes it mentions them, sometimes it buries them. The behavior is inconsistent — it depends on how Claude feels like structuring the response. We need explicit escalation rules so conflict handling is predictable.

## Solution

Add a `## Conflict Protocol` section to SOUL.md with escalation rules. This gets injected into every system prompt, giving Claude a procedure to follow when conflicts are detected:

```markdown
## Conflict Protocol

When the system injects CONFLICT DETECTION or ACTIVE OBLIGATIONS into your context, follow this protocol:

1. **Flag first** — name the conflicting node(s) before responding to anything else. Lead with "This conflicts with [Node Name]" or "You already have [obligations]". Do not bury conflicts after your main response.
2. **Explain in one line** — why this is a conflict. Reference the specific tension. "You said discipline is a core value, but staying out till 3am before a 6am run contradicts that."
3. **Challenge** — ask the user to reconcile. Don't accept contradictions passively. "Are you deprioritizing [Goal] or is this a one-off exception?" For commitment overload: "What are you willing to drop to make room?"
4. **Accept after pushback** — if the user acknowledges the conflict and insists, accept it and record. Don't nag. One challenge per conflict, then move on.

For HARD conflicts: challenge directly, don't soften.
For SOFT conflicts: raise as a consideration, lighter touch.

Never skip step 1. The user must see the conflict name before anything else.
```

## Files Changed

| File | Change |
|------|--------|
| `SOUL.md` | Add `## Conflict Protocol` section between Boundaries and Starter Prompts |

## Acceptance Criteria

1. SOUL.md contains Conflict Protocol section with flag/explain/challenge/accept steps
2. When conflicts are injected into context, Athena leads with the conflict before responding
3. Hard conflicts get a direct challenge, soft conflicts get a lighter mention
4. After one pushback from the user, Athena accepts and moves on
