# Athena — Current Work: Pre-Epoch Prep

> Prep phase before E2 begins. See [ROADMAP.md](ROADMAP.md) for full epoch sequence.
> See [GAP_ANALYSIS.md](GAP_ANALYSIS.md) for the assessment this is built on.

---

## Prep Phase Checklist

### 1. Schema V3 — Full Redesign
- [ ] Audit current 27 types — which earn their existence?
- [ ] Resolve goal vs project boundary
- [ ] Decide on `note` — keep constrained or eliminate?
- [ ] Decide on financial types — 3 or 1?
- [ ] Decide on life types — memory vs experience vs daily
- [ ] Add `permanence` field to common frontmatter
- [ ] Define permanence defaults per type
- [ ] Whitelist status values per type
- [ ] Design human fundamentals (type or scaffold)
- [ ] Design commitment tracking (type or tagged task)
- [ ] Write new `vault/_meta/schema.md` (V3)
- [ ] Update `schema_parser.py` if parse logic changes
- [ ] Run tests — ensure parser still works

### 2. Vault Archive & Reset
- [ ] Move all vault nodes to `_backup/v2/`
- [ ] Clear `backend/chroma_db/`
- [ ] Clear `backend/chat_sessions/`
- [ ] Rebuild Docker containers
- [ ] Verify: audit returns 0 nodes, graph stats 0/0

### 3. SOUL.md Rewrite
- [ ] Add `## Conflict Protocol` — 5-step escalation ladder
- [ ] Add `## Mode Selection` — four modes + activation criteria
- [ ] Add `## Bootstrap Protocol` — fundamentals onboarding
- [ ] Strengthen `## Boundaries` — anti-patterns, no cheerleading
- [ ] Update `_load_soul()` in `mentor_agent.py` to parse new sections
- [ ] Run tests

### 4. Documentation
- [x] Write `docs/GAP_ANALYSIS.md`
- [x] Write `docs/ROADMAP.md`
- [x] Update `docs/STATUS.md`
- [x] Update `docs/TODO_NEXT.md` (this file)
- [ ] Clean up stale docs (`docs/OPEN_PROBLEMS.md` — concurrent write is solved)

---

## After Prep: E2 Shaping

Once prep is complete, shape E2 ("Conversations That Stick") as a full epoch doc in `docs/epochs/E2_conversations_that_stick.md`. Follow PROCESS.md: press release, in/out scope, spec, acceptance criteria, risk.
