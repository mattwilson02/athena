# Athena — Development Process

> Build the way Athena thinks. Layered priorities, vision constraining decisions, no wasted effort.

---

## The Cycle: Shaped Epochs (3 weeks)

Each epoch is a complete cycle: shape it, build it, validate it, rest. Named by outcome, not numbered. "Cascade & Consistency" not "Sprint 7."

```
EPOCH (3 weeks)
│
├── Shape (2 days)
│     Define what to build and what NOT to build
│     Write the press release — one paragraph
│     Set appetite — time is fixed, scope flexes
│     Check vision alignment — does this serve the current stage?
│
├── Build (12 days)
│     80% epoch work, 20% bug budget
│     Ship incrementally — don't batch deploys
│     No new scope — write it down for next shape
│
├── Validate (2 days)
│     Real usage — talk to Athena, note friction and delight
│     Graph quality audit — sample 10 nodes, check accuracy
│     Behavioral regression — does everything still feel right?
│
└── Cool-Down (2 days)
      Fix small things from validation
      Update docs (CLAUDE.md, ARCHITECTURE.md)
      Shape next epoch
      Breathe
```

~17 epochs per year. Each one named, shaped, shipped, and validated.

---

## Phase 1: Shape (2 days)

Shaping is the most important phase. Build the wrong thing fast and you've wasted 3 weeks. Solving problems at higher abstraction levels is drastically cheaper than fixing them in code — updating a spec costs nothing, refactoring because the strategy was wrong is expensive.

### Set the appetite, not an estimate
Don't ask "how long will cascade updates take?" Ask "I have 3 weeks — what's the most valuable version of cascade updates I can ship in that time?" Time is fixed. Scope flexes.

### Write the press release
One paragraph. What does Athena do after this epoch that it didn't before?

> "After this epoch, when you change a plan in conversation, Athena automatically proposes updates to every connected node. No more stale data."

If you can't write this paragraph, the epoch isn't shaped well enough. The press release connects implementation to experience — it prevents building technically interesting things that don't change the lived product.

### Define boundaries
- **In:** What's being built
- **Out:** What's explicitly not being built (even if related)
- **Stretch:** Nice-to-haves that get cut if time runs short

### Check vision alignment
Does this epoch serve the current evolution stage? If you're in Stage 1 (Foundation) and catch yourself building Stage 2 features (calendar integration), you're scope creeping.

The test: **"Does this make the graph more accurate, the retrieval more reliable, or the AI reasoning sharper?"** If yes, it belongs. If it's about new interfaces or integrations, it's Stage 2+ and gets shaped later.

### Write the spec
No code without a spec. Before building, document what success looks like:
- What does the user experience after this epoch?
- What are the inputs and outputs? (e.g., "user says X, Athena responds with Y and proposes Z")
- What edge cases exist?
- What breaks if it goes wrong?

This doesn't need to be long — a few paragraphs. But it needs to exist. The spec is what turns "I'll figure it out while coding" into "I know exactly what I'm building."

### Output
A doc in `docs/epochs/` with: epoch name, press release, appetite, in/out, spec, vision stage.

---

## Phase 2: Build (12 days)

### 80/20 split
- **80% epoch work** — the shaped feature/improvement
- **20% bug budget** (~3 days) — small fixes, polish, technical debt that compounds quality. Items like "rebalance centrality scoring (10 min)" or "ChromaDB date fields (15 min)" live here. They don't deserve their own epoch but they compound.

### No new scope
Discover something important mid-build? Write it down for the next shaping session. Do not expand the current epoch. This is the hardest discipline.

### Swim upstream when needed
If you discover mid-build that the spec was wrong — the approach doesn't work, the problem is different than expected, a dependency blocks everything — don't push through. Go back to Shape. Rewrite the spec with what you now know. This isn't failure, it's catching a problem at a higher abstraction level before it compounds in code.

### Ship incrementally
Deploy to Docker as pieces become ready. Each deploy is a chance to validate in real usage. Don't save it all for the end.

### Track hills, not tasks
Every piece of work has an uphill phase (figuring it out) and a downhill phase (executing it). The only status that matters: "Do I know what to do, or am I still figuring it out?"

---

## Phase 3: Validate (2 days)

The phase most solo devs skip. Code passing tests isn't the same as the product being better.

### Usage journal
Use Athena normally for 2 days. Keep a running note of:
- Moments of friction
- Moments of delight
- Moments where you worked around something instead of through it
- AI response quality — surprises (positive or negative)

### The stranger test
Would someone else notice the improvement? Would this epoch's changes make sense to a new user? Combats familiarity bias — you're too close to your own product.

### Behavioral regression
Not unit tests — *behavioral*. Does the chat still feel right? Do graph updates still land correctly? Does the UI still feel responsive?

### Graph quality audit
The graph is the product. Sample 10 random nodes:
- Are they accurate?
- Are connections complete?
- Is stale data being caught?
- Did this epoch make the graph better?

---

## Phase 4: Cool-Down (2 days)

No committed work. This is where you:
- Fix small things that surfaced during validation
- Update documentation to reflect what changed
- Shape the next epoch (cool-down bleeds into next epoch's shape)
- Step back and think

---

## Quarterly Review

Every ~5 epochs, step back:
- Am I still in the right evolution stage?
- Are these epochs building toward the vision?
- What patterns am I seeing in validation?
- What's working in the process? What isn't?

---

## Document Hierarchy

| Doc | Purpose | Updated |
|-----|---------|---------|
| `docs/VISION.md` | North star — what Athena becomes | When vision evolves |
| `docs/VISION_BRAINSTORM.md` | Raw ideas that fed the vision | Reference only |
| `docs/OPEN_PROBLEMS.md` | Hard unsolved problems | As discovered |
| `docs/STATUS.md` | Where the project is now (baseline snapshot) | Per epoch |
| `docs/PROCESS.md` | How we build (this file) | When process evolves |
| `docs/epochs/E{N}_{name}.md` | Individual epoch specs + outcomes | Per epoch |
| `docs/TODO_NEXT.md` | Current epoch's tactical work | Weekly |
| `CLAUDE.md` | Codebase instructions for AI assistants | Per epoch |
| `docs/ARCHITECTURE.md` | Technical deep-dive | Per epoch |

---

## Principles

### 1. Appetite over estimates
Time is fixed. Scope flexes. "What's the best version I can ship in 3 weeks?" not "How long will this take?"

### 2. No scope creep mid-epoch
Write it down. Shape it next time. The current epoch has boundaries.

### 3. Vision alignment is mandatory
Every epoch checked against VISION.md. If it doesn't serve the current stage, question it.

### 4. Validate with real usage
You are the user. Use the product. Note what's better and what's worse. Unit tests validate code. Usage validates product.

### 5. Bug budget, not bug sprints
20% of every epoch for small fixes. They don't accumulate into a "tech debt sprint" — they get handled continuously.

### 6. Name your epochs
Names create narrative and accountability. A year from now, 17 named epochs tell a story of progress.
