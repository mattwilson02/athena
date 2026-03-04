# Athena — Open Problems

> Hard problems that need solving. Not specced yet, not planned yet — just documented so we don't lose them.

---

## 1. The Cold Start Problem

**The tension:** Athena's value comes from depth of context. But a new user has zero context. A blank graph is useless. How do you deliver value in the first 5 minutes without requiring 8 hours of onboarding?

**Why it's hard:**
- Too much upfront questioning feels like a form to fill out, not a conversation
- Too little and the first responses are generic — no better than ChatGPT
- The human fundamentals scaffold helps (movement, sleep, nutrition, connection, purpose, finance) but even that is 6 topics to explore
- Voice accelerates input but still requires time
- The user needs to feel "this thing gets me" quickly or they'll bounce

**Possible approaches:**

### Quick wins on day 1
- **Guided first conversation** — not a questionnaire, but a structured conversation that covers the fundamentals naturally. "Tell me about your week" → extract schedule, commitments, energy patterns, relationships, stressors in one pass.
- **Tiered depth** — start shallow and useful ("what's on your mind today?"), go deeper as trust builds. Don't try to map the whole life in session 1.
- **Immediate value from partial data** — even 3 goals and 2 habits gives Athena enough to start reasoning about tradeoffs. Don't wait for graph completeness to start being useful.
- **Import accelerators** — pull from calendar, contacts, existing notes. Not as primary input but as scaffolding that the user then confirms/adjusts/enriches through conversation.

### Progressive depth over time
- **Daily check-ins** — short conversations that build the graph incrementally. "How was today?" adds a daily node, surfaces new connections, deepens existing ones.
- **Proactive discovery** — Athena notices gaps and asks. "You've mentioned Ethan 3 times but I don't have a node for him. Tell me about that relationship."
- **Natural extraction** — every conversation, even about tactical stuff, feeds the graph. Asking about weekend plans reveals relationships, priorities, energy patterns.

### The risk
- Over-engineering onboarding creates a wall. People want to talk, not configure.
- Under-engineering it means the first few sessions feel no different from ChatGPT.
- The sweet spot: Athena is immediately conversational AND captures structure from that conversation, so the user gets value while the graph builds invisibly.

**Status:** Unsolved. Need to prototype and test with real users.

---

## 2. Concurrent Write Safety

**The problem:** Accepting multiple graph updates simultaneously crashes the backend. 10 accepts in 2 seconds = 10 concurrent vault writes + 10 full graph rebuilds on shared, unlocked state.

**Why it's hard:**
- Each write triggers `rebuild_all()` — full vault parse + graph rebuild + vector reindex
- No mutex/lock on shared graph object or vector index
- Flask is multi-threaded by default — concurrent requests mutate shared state
- File I/O races on overlapping markdown files

**Possible approaches:**
- Frontend: queue accepts sequentially (each waits for previous to finish)
- Backend: debounce rebuilds (write file immediately, coalesce rebuilds with 500ms delay)
- Backend: add a lock so only one rebuild runs at a time
- Long term: incremental graph updates instead of full rebuild on every write

**Status:** Identified, not yet fixed. Data corruption risk.

---

## 3. Chat vs System — The Interface Gap

**The tension:** Athena is fundamentally a conversational chat interface. But the vision describes capabilities that don't naturally live in chat — scheduling, reminders, progress tracking, streak monitoring, daily briefings, proactive nudges. How does a chat-first system handle things that need to happen without a conversation?

**Why it's hard:**
- Chat is pull-based — the user initiates. But reminders, briefings, and accountability nudges are push-based. Something has to trigger them.
- Scheduling needs structure (time blocks, calendar views, recurring events) that doesn't map cleanly to freeform conversation.
- Progress tracking needs persistent UI — streaks, goal progress, completion rates. These aren't natural chat outputs.
- The habit engine needs to run in the background — detecting missed days, calculating streaks, identifying patterns — regardless of whether the user opens a chat.

**Possible approaches:**

### Chat as the primary but not only interface
- **Proactive messages** — Athena initiates conversations via Telegram/notifications. Morning briefing, missed habit nudge, end-of-day review. The chat becomes bidirectional.
- **Structured views alongside chat** — the desktop UI already has graph view, timeline, node detail. Add schedule view, habit dashboard, goal tracker as peers to chat, not replacements.
- **Chat-triggered system actions** — "remind me at 3pm" in chat creates a backend reminder that fires through Telegram/notification. Chat is the input, the system is the executor.

### Background processes
- **Cron-style jobs** — daily audit of habits (what was missed?), weekly goal check-ins, monthly pattern analysis. These run independently of chat and feed results back as proactive messages or dashboard updates.
- **Event-driven triggers** — when a deadline passes, when a streak breaks, when a commitment is approaching. The system monitors without being asked.

### The spectrum
One extreme: everything is chat. "Show me my streaks" → Athena responds with text. Simple but limiting.
Other extreme: full app with calendar, dashboards, notification engine. Powerful but that's building a different product.

The right answer is probably: **chat is the conversational brain, structured views are the dashboard, and a background engine connects them.** But the boundaries between these three need careful design.

**Status:** Unsolved. Fundamental architectural question for Stage 2+.

---

## 4. The LLM Privacy Paradox

**The tension:** The vision says "your data never leaves your control" and "no company sees your graph." But right now, every conversation sends your deepest personal context — fears, relationship dynamics, financial details, health data, psychological patterns — to Anthropic's Claude API. The smarter Athena gets, the more context it sends, the more exposed you are.

**Why it's hard:**
- The entire intelligence layer depends on an external LLM. No Claude API = no reasoning.
- As the graph deepens (which is the whole point), the context sent per request gets richer and more sensitive.
- Anthropic's data policies may be good today, but they're a company that can change policies, get acquired, or be compelled by law.
- If this becomes a multi-user product, you're sending OTHER people's personal data through a third-party API. That's a legal and ethical minefield.
- The vision promises "no training on your data" — but that's Anthropic's promise, not ours to guarantee.

**The spectrum of solutions:**

### Short term (accept the tradeoff)
- Anthropic's current policy: data not used for training via the API. Acceptable for personal use.
- Minimize context sent — only send what's needed for the current query, not the full graph.
- Be transparent about it — Athena should tell users exactly what's being sent externally.

### Medium term (reduce dependence)
- **Local LLM for routine operations** — graph updates, status changes, simple queries could use a local model (Llama, Mistral). Only complex reasoning goes to Claude.
- **Tiered intelligence** — local model handles 80% of interactions, cloud model handles 20% that need deep reasoning.
- **Context filtering** — strip or anonymize sensitive fields before sending to external API. Send the structure, not the raw content.

### Long term (full sovereignty)
- **Self-hosted LLM** — run a capable model locally or on owned infrastructure. Hardware costs are dropping.
- **Fine-tuned personal model** — a smaller model fine-tuned on your interaction patterns. Knows your style without needing full context every time.
- **Federated architecture** — graph stays local, reasoning stays local, only model weights come from external (and those are static, not learning from your data).

### The product question
If Athena becomes a product for others, the privacy architecture becomes the #1 trust differentiator. "Your life data is processed by [big AI company]" is a harder sell than "everything runs on your hardware." But local models aren't as capable yet. The timing of when local LLMs catch up to cloud determines when this transition is viable.

**Status:** Accepted tradeoff for now (personal use, Anthropic API policy). Becomes critical at multi-user / product stage. Monitor local LLM capabilities.

---

*Add new problems as they emerge. This is a living document.*
