"""Claude-powered AI assistant with graph-aware context retrieval."""

from __future__ import annotations

import json
import math
import os
import re
import logging
from datetime import date, datetime, timedelta

import anthropic

from schema_parser import generate_type_rules
from vault_graph import VaultGraph
from vector_search import VectorIndex

logger = logging.getLogger(__name__)

# ── Domain keyword heuristics ──
# Maps keywords to domain names for query classification.
_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "Self": ["goal", "fear", "belief", "value", "habit", "skill", "ambition",
             "anxiety", "motivation", "confidence", "discipline", "mindset",
             "strength", "weakness", "routine", "practice"],
    "People": ["friend", "family", "colleague", "partner", "mentor", "person",
               "company", "team", "organisation", "relationship", "meet", "met"],
    "Knowledge": ["book", "article", "idea", "note", "read", "learn", "study",
                   "concept", "insight", "research", "remember",
                   "movie", "film", "watched", "director", "cinema",
                   "quote", "saying", "passage", "attribution",
                   "pill", "lesson", "mental model", "realization", "principle"],
    "Life": ["experience", "memory", "journal", "daily", "yesterday", "today",
             "weekend", "trip", "happened", "went", "felt", "diary"],
    "Planning": ["task", "project", "reminder", "event", "plan", "deadline",
                 "schedule", "todo", "organise", "organize", "booking", "ticket",
                 "prepare", "pack", "race", "marathon", "flight", "ferry"],
    "Places": ["place", "restaurant", "city", "country", "bar", "cafe", "hotel",
               "visit", "travel", "location", "area", "neighbourhood", "move"],
    "Finance": ["expense", "subscription", "budget", "cost", "price", "money",
                "spend", "payment", "pay", "rent", "bill", "salary", "income"],
}

# Types where recency matters most.
_RECENCY_SENSITIVE_TYPES = {"task", "daily", "reminder", "event", "expense", "memory"}

# Status penalties — deprioritize resolved/inactive nodes in retrieval.
_STATUS_PENALTIES: dict[str, float] = {
    "completed": -0.15,
    "done": -0.15,
    "cancelled": -0.25,
    "archived": -0.25,
    "abandoned": -0.25,
    "superseded": -0.25,
    "parked": -0.10,
}

# Permanence levels — type-level defaults for retrieval boosting.
# Higher-permanence nodes get a score boost to ensure identity/values stay in context.
_PERMANENCE_DEFAULTS: dict[str, tuple[str, float]] = {
    # identity (+0.15)
    "value":        ("identity",  0.15),
    "belief":       ("identity",  0.15),
    "fear":         ("identity",  0.15),
    # strategic (+0.10)
    "goal":         ("strategic", 0.10),
    "habit":        ("strategic", 0.10),
    "skill":        ("strategic", 0.10),
    "project":      ("strategic", 0.10),
    # tactical (+0.00) — listed explicitly for completeness
    "task":         ("tactical",  0.00),
    "reminder":     ("tactical",  0.00),
    "event":        ("tactical",  0.00),
    "expense":      ("tactical",  0.00),
    "subscription": ("tactical",  0.00),
    "budget":       ("tactical",  0.00),
    # ephemeral (-0.05)
    "daily":        ("ephemeral", -0.05),
    "note":         ("ephemeral", -0.05),
}


def _get_permanence(node_type: str) -> tuple[str, float]:
    """Return (level_name, boost) for a node type. Unlisted types default to tactical."""
    return _PERMANENCE_DEFAULTS.get(node_type, ("tactical", 0.00))


# ── Mode classifier ──

_MODE_DIALECTIC_SIGNALS: list[str] = [
    "should i", "what if", "considering", "torn between",
    "not sure if i should", "debating whether", "thinking about quitting",
    "thinking about leaving", "worth it to",
]

_MODE_ADVISOR_SIGNALS: list[str] = [
    "i want to start", "i'm going to", "im going to", "planning to",
    "thinking about starting", "sign up for", "commit to",
    "take on", "new project", "new goal", "new habit",
]


def classify_mode(message: str, conflicts: list[dict] | None) -> str:
    """Classify the incoming message into a communication mode.

    Priority order: guardian > dialectic > advisor > mirror (default).

    Returns one of: "mirror", "advisor", "guardian", "dialectic".
    """
    if not message:
        return "mirror"

    msg_lower = message.lower()
    conflicts = conflicts or []

    # 1. Guardian — only when hard conflicts are detected
    if any(c.get("severity") == "hard" for c in conflicts):
        return "guardian"

    # 2. Dialectic — big-decision signals
    if any(signal in msg_lower for signal in _MODE_DIALECTIC_SIGNALS):
        return "dialectic"

    # 3. Advisor — new commitment signals OR soft-only conflicts
    if any(signal in msg_lower for signal in _MODE_ADVISOR_SIGNALS):
        return "advisor"
    if conflicts and all(c.get("severity") == "soft" for c in conflicts):
        return "advisor"

    # 4. Mirror — default
    return "mirror"


# Bootstrap threshold — below this, inject onboarding prompt instead of retrieval.
_BOOTSTRAP_THRESHOLD = 10

# Rough token budget for the context window.
_MAX_CONTEXT_TOKENS = 3000
_CHARS_PER_TOKEN = 4  # conservative estimate


# ── Soul loader ──


def _load_soul(vault_path: str | None = None) -> tuple[str, str, dict[str, str]]:
    """Load Athena's identity, instructions, and mode texts from SOUL.md.

    Parses the markdown sections into an identity block, an instructions block,
    and a dict of mode-specific instruction texts keyed by mode name.
    Falls back to minimal defaults if the file is missing.
    """
    # Look for SOUL.md in the project root (one level up from backend/) or same dir
    search_paths = []
    if vault_path:
        search_paths.append(os.path.join(vault_path, "..", "SOUL.md"))
    search_paths.append(os.path.join(os.path.dirname(__file__), "..", "SOUL.md"))
    search_paths.append(os.path.join(os.path.dirname(__file__), "SOUL.md"))  # Docker mount

    soul_path = None
    for p in search_paths:
        candidate = os.path.abspath(p)
        if os.path.isfile(candidate):
            soul_path = candidate
            break

    if not soul_path:
        logger.warning("SOUL.md not found — using minimal defaults")
        return (
            "You are Athena, a personal AI assistant for a knowledge graph.\n\nCONTEXT FROM KNOWLEDGE GRAPH:\n{context}",
            "INSTRUCTIONS:\n- Be concise and reference nodes by name.\n- Propose graph updates when the user shares information worth capturing.",
            {},
        )

    with open(soul_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Parse sections by ## headings
    sections: dict[str, str] = {}
    current_heading = None
    current_lines: list[str] = []

    for line in content.split("\n"):
        if line.startswith("## "):
            if current_heading:
                sections[current_heading] = "\n".join(current_lines).strip()
            current_heading = line[3:].strip().lower()
            current_lines = []
        elif current_heading is not None:
            current_lines.append(line)

    if current_heading:
        sections[current_heading] = "\n".join(current_lines).strip()

    # Build identity from Identity + Voice sections
    identity_parts = []
    if "identity" in sections:
        identity_parts.append(sections["identity"])
    if "voice" in sections:
        identity_parts.append(sections["voice"])
    identity_parts.append(
        "DATE RULES:\n"
        "- TODAY: {today}. This is authoritative. Do NOT infer today's date from logs or other data.\n"
        "- When the context contains pre-computed date facts (e.g. 'in exactly 70 days'), use those "
        "numbers EXACTLY. Never do your own date arithmetic — the computed values are correct.\n"
        "- If asked 'how many days until X' and the context says 'in exactly N days', answer N."
    )
    identity_parts.append("CONTEXT FROM KNOWLEDGE GRAPH:\n{context}")
    identity = "\n\n".join(identity_parts)

    # Build instructions from Values + Boundaries sections
    instruction_parts = ["INSTRUCTIONS:"]
    if "values" in sections:
        for line in sections["values"].split("\n"):
            line = line.strip()
            if line.startswith("- **"):
                # Extract the rule after the bold label
                parts = line.split("**")
                if len(parts) >= 3:
                    instruction_parts.append(f"- {parts[2].strip(' —')}")
    if "boundaries" in sections:
        for line in sections["boundaries"].split("\n"):
            line = line.strip()
            if line.startswith("- "):
                instruction_parts.append(line)

    # Include behavioral guidance sections as full text blocks
    for _section_key in ("conflict protocol", "challenge ladder", "state awareness", "relationship intelligence"):
        if _section_key in sections:
            _title = _section_key.title()
            instruction_parts.append(f"\n{_title.upper()}:\n{sections[_section_key]}")

    instructions = "\n".join(instruction_parts)

    # Parse mode subsections from the ## Modes section
    mode_instructions: dict[str, str] = {}
    modes_text = sections.get("modes", "")
    if modes_text:
        current_mode: str | None = None
        mode_lines: list[str] = []
        for line in modes_text.split("\n"):
            if line.startswith("### "):
                if current_mode:
                    mode_instructions[current_mode] = "\n".join(mode_lines).strip()
                current_mode = line[4:].strip().lower()
                mode_lines = []
            elif current_mode is not None:
                mode_lines.append(line)
        if current_mode:
            mode_instructions[current_mode] = "\n".join(mode_lines).strip()

    logger.info(f"Soul loaded from {soul_path} ({len(sections)} sections, {len(mode_instructions)} modes)")
    return identity, instructions, mode_instructions

def load_insights_prompt(vault_path: str | None = None) -> str:
    """Load the insights system prompt from SOUL.md.

    Falls back to a default if SOUL.md or the section is missing.
    """
    default = (
        "You are Athena — sharp, strategic, sees the whole board. "
        "Analyze this personal knowledge graph and deliver 3-5 observations "
        "the user wouldn't see themselves: contradictions between what they say "
        "they value and what they actually do, goals with no supporting actions, "
        "orphaned nodes that should be connected, patterns across domains. "
        "Be specific — use node names. Be direct — no softening."
    )

    search_paths = []
    if vault_path:
        search_paths.append(os.path.join(vault_path, "..", "SOUL.md"))
    search_paths.append(os.path.join(os.path.dirname(__file__), "..", "SOUL.md"))
    search_paths.append(os.path.join(os.path.dirname(__file__), "SOUL.md"))  # Docker mount

    for p in search_paths:
        candidate = os.path.abspath(p)
        if os.path.isfile(candidate):
            with open(candidate, "r", encoding="utf-8") as f:
                content = f.read()
            # Parse just the Insights Voice section
            match = re.search(
                r"## Insights Voice\n(.*?)(?=\n## |\Z)", content, re.DOTALL
            )
            if match:
                voice = match.group(1).strip()
                return (
                    "You are Athena — goddess of wisdom, strategy, and seeing the whole board. "
                    f"Analyze this personal knowledge graph and deliver 3-5 observations.\n\n{voice}"
                )
            break

    return default


_GRAPH_INSTRUCTIONS = """\
GRAPH UPDATE RULES — READ ALL OF THIS BEFORE RESPONDING.

Valid types: {type_enum}
Do NOT invent types. Pick from the list above. "note" and "idea" are LAST RESORT.

── 1. WHEN TO WRITE ──
Conversations are write operations. If the user's message changes the state of the world — plans, \
knowledge, relationships, decisions — you MUST propose graph updates. Responding without a graph \
update means that knowledge is LOST FOREVER.

Triggers — these MUST produce graph updates:
- "I cancelled X" / "X isn't happening" → UPDATE X: status → cancelled
- "I'm going to X" / "I've decided to X" → CREATE task/event/project
- "Actually, doing Y instead of X" → CREATE Y + UPDATE X: status → superseded, superseded_by → Y's node_id
- "I met someone called X" / "X is a …" → CREATE person (or UPDATE if they exist)
- "X is on [date]" / "moved X to [date]" → UPDATE X: date/deadline change
- "I finished X" / "X is done" → UPDATE X: status → completed
- "I learned that…" / "key takeaway:" → CREATE pill (not note)
- "I watched X" / "I read X" → CREATE movie/book
- "X costs Y" / "I spent Y on X" → CREATE expense or UPDATE budget
- Opinion about existing node → UPDATE that node: append_content
- Describes a place → CREATE place or experience
- "I'm worried about X" / "I'm afraid of X" → CREATE fear
- "I believe X" / "X is important to me" → CREATE belief or value

If the user is just chatting (no state change, no new info worth capturing), no graph update needed.

── 2. WHAT TYPE ──
Walk this decision tree top-to-bottom. Use the FIRST match:

Is it about a specific person? → person
Is it a scheduled event with a date? → event
Is it a one-off task or to-do? → task
Is it a multi-step effort with sub-tasks? → project
Is it a recurring routine or practice? → habit
Is it a film or show they watched? → movie
Is it a book or article they read? → book / article
Is it something they spent money on? → expense
Is it a recurring payment? → subscription
Is it a budget or financial plan? → budget
Is it a place (city, restaurant, bar, etc.)? → place
Is it a life lesson, mental model, or realization? → pill
Is it a memorable quote or saying? → quote
Is it a fear or anxiety? → fear
Is it a core belief? → belief
Is it a personal value? → value
Is it a goal or ambition? → goal
Is it a skill they have or want? → skill
Is it a life experience or memory? → experience / memory
Is it a daily journal entry? → daily
Is it a reminder (date-triggered)? → reminder
Is it an abstract concept worth exploring? → idea
None of the above? → note (genuinely LAST RESORT — if you picked this, re-read the list)

── 3. WHAT ACTION ──
Check the CONTEXT above for existing nodes BEFORE choosing an action.

Does a node for this already exist in the context?
  YES, and user wants to change/add to it → action: "update" (use its node_id)
  YES, and user is connecting it to something new → action: "link"
  NO, this is genuinely new information → action: "create" + edges to related nodes

DEDUP: A person, place, or concept that already exists → UPDATE or LINK, never create a duplicate.
BUT: A new task related to an existing project is NOT a duplicate — CREATE the task + LINK it.

UPDATE TARGETING: You can ONLY update nodes that appear in your CONTEXT. If a node isn't in the \
context, it doesn't exist — CREATE it instead. Never invent node_ids for updates.

TARGETING: New sub-item for a project/goal → CREATE a specific node + LINK to the parent. \
Do NOT append sub-items to the parent's content. Keep nodes atomic.

MULTI-NODE BATCHES: A single message often mentions multiple entities. Propose creates for ALL \
of them, not just the "main" one. If someone talks about a plan involving a place and a person, \
that could be 3 creates (project + place + person) linked together — not just 1 project with \
everything in its content. After deciding the primary update, scan for other entities (people, \
places, events, tasks) that don't exist in the CONTEXT and create those too.

── 4. COMMON MISTAKES ──
WRONG: User says "I cancelled the Italy trip" → create note titled "Cancelled Italy Trip"
RIGHT: → update italy-trip, changes: {{frontmatter: {{status: "cancelled"}}}}

WRONG: User says "I need to book flights" → update italy-trip, append: "Need to book flights"
RIGHT: → create task "Book Flights for Italy Trip", edges: [{{target: "italy-trip", type: "part_of"}}]

WRONG: User says "Met Sarah at the gym" → create note "Met Sarah at Gym"
RIGHT: → create person "Sarah", frontmatter: {{context: "met at gym"}}, edges: [{{target: relevant-node, type: "met_at"}}]

WRONG: User changes plans but you only respond conversationally with no graph update
RIGHT: → ALWAYS propose the corresponding update/create

WRONG: User shares info about an existing node → create a new note about it
RIGHT: → update the existing node with append_content or frontmatter changes

WRONG: User says "Actually I'm doing a half marathon instead of the ultra" → create half-marathon only
RIGHT: → create half-marathon + update ultra: {{status: "superseded", superseded_by: "half-marathon"}}

WRONG: User switches from plan A to plan B → update plan A's content to describe plan B
RIGHT: → create plan B + update plan A: {{status: "superseded", superseded_by: "plan-b"}} + link between them

── 5. CASCADE ──
After proposing an update, check the CONTEXT for connected nodes that reference the changed info \
(old dates, old status, old plans). Propose updates to those too.

── 6. EDGES ──
Every CREATE must have at least one edge (unless zero related nodes exist in context).
Pick specific edge types over generic relates_to:
- Task for a project → part_of
- Person met at a place → met_at
- Idea from a book → inspired_by
- Expense for a subscription → funded_by
- Task blocking another → blocked_by
- Belief supporting a goal → supported_by
- Idea contradicting a belief → contradicts

── 7. FORMAT ──
Wrap graph updates in <graph_updates> tags after your response text:

<graph_updates>
[
  {{
    "action": "create",
    "node_id": "suggested-slug-id",
    "type": "one of the valid types listed above",
    "title": "Human Readable Title",
    "content": "Description of the node.",
    "tags": ["tag1", "tag2"],
    "frontmatter": {{"status": "active", "priority": "high"}},
    "edges": [
      {{"target": "existing-node-id", "type": "edge_type"}}
    ]
  }},
  {{
    "action": "update",
    "node_id": "existing-node-id",
    "changes": {{
      "title": "New Title (only if renaming)",
      "content": "Full replacement (replaces body — use only when rewriting)",
      "frontmatter": {{"status": "active", "company": "Google"}},
      "append_content": "New info to add (appends, does not replace).",
      "add_tags": ["new-tag"],
      "add_edges": [{{"target": "other-node", "type": "relates_to"}}]
    }}
  }},
  {{
    "action": "link",
    "source": "existing-node-id",
    "target": "another-node-id",
    "type": "edge_type"
  }}
]
</graph_updates>

── 8. COMMITMENTS ──
When the user makes an explicit promise ("I'll do X by Friday", "I commit to X", "I promise to Y", \
"I'll have it done by Z"), tag the graph update with commitment metadata:

  "frontmatter": {{
    "committed_on": "YYYY-MM-DD",        // today's date (use the date from CONTEXT header)
    "commitment_context": "Promised Sarah I'd review by Tuesday"  // brief context of the promise
  }}

Only tag explicit promises with clear deadlines. "I should probably do X sometime" is NOT a commitment. \
"I want to start running" is NOT a commitment. "I'll run 3 times this week" IS a commitment.

If the node already exists, use action: "update" with frontmatter changes to add committed_on and \
commitment_context.
If it's a new task/goal, include committed_on and commitment_context in the create frontmatter."""


def build_system_prompt(schema: dict, vault_path: str | None = None) -> tuple[str, dict[str, str]]:
    """Assemble the base system prompt from SOUL.md + parsed schema.

    Returns (prompt_template, mode_instructions_dict).
    The prompt_template has {context} and {today} placeholders.
    """
    identity, instructions, mode_instructions = _load_soul(vault_path)
    type_rules = generate_type_rules(schema)
    type_enum = "|".join(schema["type_list"])
    graph_instructions = _GRAPH_INSTRUCTIONS.replace("{type_enum}", type_enum)

    prompt = "\n\n".join([
        identity,
        instructions,
        type_rules,
        graph_instructions,
    ])
    return prompt, mode_instructions


GRAPH_UPDATES_RE = re.compile(r"<graph_updates>\s*(.*?)\s*</graph_updates>", re.DOTALL)


# ── Retrieval helpers ──


def _classify_domains(query: str) -> list[str]:
    """Classify which domains a query relates to using keyword matching.

    Returns domain names sorted by relevance (most keyword hits first).
    """
    query_lower = query.lower()
    scores: dict[str, int] = {}
    for domain, keywords in _DOMAIN_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in query_lower)
        if score > 0:
            scores[domain] = score

    if not scores:
        return []
    return sorted(scores, key=scores.get, reverse=True)


def _recency_score(node: dict) -> float:
    """Score a node by how recently it was created/updated (0.0-1.0).

    Returns 1.0 for today, decays over 90 days to 0.0.
    Future dates (upcoming events) get max score.
    """
    date_str = node.get("updated") or node.get("created") or node.get("date")
    if not date_str:
        return 0.0

    try:
        if isinstance(date_str, (date, datetime)):
            node_date = date_str if isinstance(date_str, date) else date_str.date()
        else:
            node_date = datetime.fromisoformat(str(date_str).split("T")[0]).date()
    except (ValueError, TypeError):
        return 0.0

    days_ago = (date.today() - node_date).days
    if days_ago < 0:
        return 1.0  # future dates (upcoming events)
    if days_ago > 90:
        return 0.0
    return 1.0 - (days_ago / 90.0)


def _format_date_context(node: dict, today: date | None = None) -> str:
    """Compute human-readable date-relative facts for a node's date fields.

    Returns lines like 'Date: 2026-05-09 (in 70 days)' so the LLM reads
    pre-computed facts instead of doing date arithmetic.
    """
    if today is None:
        today = date.today()
    date_fields = ("date", "due", "deadline", "created", "updated")
    lines = []

    for field in date_fields:
        val = node.get(field)
        if val is None:
            continue

        parsed = None
        if hasattr(val, "isoformat") and hasattr(val, "year"):
            parsed = val
        elif isinstance(val, str):
            for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
                try:
                    parsed = datetime.strptime(val, fmt).date()
                    break
                except ValueError:
                    continue

        if parsed is None:
            continue

        delta = (parsed - today).days
        if delta == 0:
            relative = "today"
        elif delta == 1:
            relative = "tomorrow"
        elif delta == -1:
            relative = "yesterday"
        elif delta > 1:
            relative = f"in {delta} days"
        else:
            relative = f"{abs(delta)} days ago"

        lines.append(f"{field.capitalize()}: {parsed.isoformat()} ({relative})")

    return "\n".join(lines)


def _node_context_full(node: dict, neighbor_names: list[str]) -> str:
    """Full context string for a direct-match node."""
    parts = [
        f"--- {node.get('title', node['id'])} ({node.get('type', 'unknown')}) ---",
        f"ID: {node['id']}",
    ]
    date_ctx = _format_date_context(node)
    if date_ctx:
        parts.append(date_ctx)
    status = node.get("status")
    if status:
        parts.append(f"Status: {status}")
    tags = node.get("tags", [])
    if tags:
        tag_str = ", ".join(str(t) for t in tags) if isinstance(tags, list) else str(tags)
        parts.append(f"Tags: {tag_str}")
    if neighbor_names:
        parts.append(f"Connected to: {', '.join(neighbor_names)}")
    content = node.get("content", "").strip()
    if content:
        parts.append(f"Content:\n{content}")
    return "\n".join(parts)


def _node_context_summary(node: dict, neighbor_names: list[str]) -> str:
    """Abbreviated context for a 1-hop node — frontmatter + first paragraph only."""
    parts = [
        f"--- {node.get('title', node['id'])} ({node.get('type', 'unknown')}) ---",
        f"ID: {node['id']}",
    ]
    date_ctx = _format_date_context(node)
    if date_ctx:
        parts.append(date_ctx)
    status = node.get("status")
    if status:
        parts.append(f"Status: {status}")
    tags = node.get("tags", [])
    if tags:
        tag_str = ", ".join(str(t) for t in tags) if isinstance(tags, list) else str(tags)
        parts.append(f"Tags: {tag_str}")
    if neighbor_names:
        parts.append(f"Connected to: {', '.join(neighbor_names)}")
    content = node.get("content", "").strip()
    if content:
        first_para = content.split("\n\n")[0][:200]
        parts.append(f"Summary: {first_para}")
    return "\n".join(parts)


def _node_context_minimal(node: dict) -> str:
    """One-line context for distant (2-hop) nodes."""
    return f"- {node.get('title', node['id'])} ({node.get('type', 'unknown')}) [ID: {node['id']}]"


_BOOTSTRAP_EMPTY = """\
BOOTSTRAP MODE — This is a brand new vault with no nodes yet.

Your first job is to get to know this person. Lead a warm, curious conversation that \
covers their fundamentals. Don't rush — one topic area per message. Ask follow-up \
questions before moving on.

Start by introducing yourself briefly, then ask about ONE of these:
- What matters most to them (→ values, beliefs)
- What they're working toward (→ goals)
- What keeps them up at night (→ fears)

After each exchange, propose CREATE graph updates for everything discussed. \
Use specific types (value, belief, goal, fear, habit, skill) — never "note".

You are building the skeleton of their knowledge graph. Every response MUST include \
graph updates. Aim for 2-4 creates per response."""

_BOOTSTRAP_PARTIAL = """\
BOOTSTRAP MODE — This vault has {count} node(s). Still building the foundation.

The user has started their graph but it's incomplete. Review the existing nodes below \
and identify which fundamental areas are still missing:
- Self domain: values, beliefs, goals, fears, habits, skills
- People domain: key people in their life
- Planning domain: active projects, upcoming events
- Life domain: current situation, recent experiences

Steer the conversation toward gaps. If they have goals but no fears, ask about fears. \
If they have no people nodes, ask who matters to them. Keep it natural — don't interrogate.

Every response MUST include graph updates for new information shared.

EXISTING NODES:
{existing_nodes_summary}"""


def _bootstrap_context(node_count: int, graph) -> str:
    """Return a bootstrap prompt when the vault is empty or near-empty."""
    if node_count == 0:
        return _BOOTSTRAP_EMPTY

    nodes = graph.get_all_nodes()
    lines = []
    for n in sorted(nodes, key=lambda x: x.get("type", "")):
        lines.append(f'- "{n.get("title", n["id"])}" ({n.get("type", "unknown")})')
    summary = "\n".join(lines)

    return _BOOTSTRAP_PARTIAL.format(count=node_count, existing_nodes_summary=summary)


def _extract_session_topics(history: list[dict], max_messages: int = 5) -> list[str]:
    """Extract recent user messages as additional query strings for session-aware retrieval."""
    user_messages = [m["content"] for m in history if m.get("role") == "user"]
    return user_messages[-max_messages:]


_DAY_NAMES = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def _resolve_temporal_query(query: str) -> tuple[date, date] | None:
    """Detect temporal phrases and resolve to (start_date, end_date).

    Returns None if no temporal phrase is detected.
    """
    q = query.lower()
    today = date.today()
    weekday = today.weekday()  # 0=Monday

    if "today" in q or "tonight" in q:
        return today, today
    if "tomorrow" in q:
        tmr = today + timedelta(days=1)
        return tmr, tmr
    if "yesterday" in q:
        return today - timedelta(days=1), today - timedelta(days=1)
    if "this weekend" in q:
        days_to_sat = (5 - weekday) % 7
        sat = today + timedelta(days=days_to_sat)
        return sat, sat + timedelta(days=1)
    if "next weekend" in q:
        days_to_sat = (5 - weekday) % 7
        if days_to_sat == 0:
            days_to_sat = 7
        sat = today + timedelta(days=days_to_sat + 7)
        return sat, sat + timedelta(days=1)
    if "this week" in q:
        mon = today - timedelta(days=weekday)
        return mon, mon + timedelta(days=6)
    if "next week" in q:
        mon = today - timedelta(days=weekday) + timedelta(weeks=1)
        return mon, mon + timedelta(days=6)
    if "this month" in q:
        start = today.replace(day=1)
        if today.month == 12:
            end = today.replace(year=today.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            end = today.replace(month=today.month + 1, day=1) - timedelta(days=1)
        return start, end

    for day_name, day_num in _DAY_NAMES.items():
        if day_name in q:
            days_ahead = (day_num - weekday) % 7
            if days_ahead == 0:
                days_ahead = 7
            target = today + timedelta(days=days_ahead)
            return target, target

    return None


def _get_nodes_in_date_range(graph, start: date, end: date) -> list[dict]:
    """Scan all nodes for matching date/due/deadline/scheduled_for fields within range."""
    matches = []
    for node in graph.get_all_nodes():
        for field in ("date", "due", "deadline", "scheduled_for"):
            val = node.get(field)
            if not val:
                continue
            try:
                if isinstance(val, (date, datetime)):
                    node_date = val if isinstance(val, date) else val.date()
                else:
                    node_date = datetime.fromisoformat(str(val).split("T")[0]).date()
            except (ValueError, TypeError):
                continue
            if start <= node_date <= end:
                matches.append(node)
                break
    return matches


def _get_overdue_nodes(graph, today: date) -> list[dict]:
    """Find active nodes with due/deadline/scheduled_for in the past."""
    _ACTIVE_STATUSES = {"active", "pending", "todo", "in_progress", "planning", "blocked", "overdue", ""}
    overdue = []
    for node in graph.get_all_nodes():
        status = str(node.get("status", "") or "").lower()
        if status not in _ACTIVE_STATUSES:
            continue
        for field in ("due", "deadline", "scheduled_for"):
            val = node.get(field)
            if not val:
                continue
            try:
                if isinstance(val, (date, datetime)):
                    node_date = val if isinstance(val, date) else val.date()
                else:
                    node_date = datetime.fromisoformat(str(val).split("T")[0]).date()
            except (ValueError, TypeError):
                continue
            if node_date < today:
                overdue.append(node)
                break
    return overdue


class MentorAgent:
    """Claude-powered AI assistant with hybrid retrieval. Stateless — conversation
    history is passed in from the chat store."""

    def __init__(self, graph: VaultGraph, vector_index: VectorIndex, schema: dict,
                 vault_path: str | None = None, client: anthropic.Anthropic | None = None) -> None:
        self.graph = graph
        self.vector_index = vector_index
        self.schema = schema
        self.system_prompt_template, self.mode_instructions = build_system_prompt(schema, vault_path)
        self.client = client or anthropic.Anthropic()
        self.model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")
        logger.info(f"System prompt built from schema ({len(self.system_prompt_template)} chars)")

    def get_context(self, query: str, conversation_history: list[dict] | None = None) -> tuple[str, list[dict]]:
        """Hybrid retrieval: semantic search + domain filtering + 2-hop traversal + tiered assembly.

        If conversation_history is provided, recent user messages boost relevance of
        nodes mentioned in the ongoing conversation.

        Returns (context_string, search_results).
        """
        # Bootstrap check — if vault is nearly empty, skip retrieval and onboard
        total_nodes = len(self.graph.get_all_nodes())
        if total_nodes < _BOOTSTRAP_THRESHOLD:
            logger.info(f"Bootstrap mode: {total_nodes} nodes (threshold {_BOOTSTRAP_THRESHOLD})")
            return _bootstrap_context(total_nodes, self.graph), []

        # Step 0: Temporal query resolution — detect date phrases and find matching nodes
        temporal_node_ids: set[str] = set()
        date_range = _resolve_temporal_query(query)
        if date_range:
            temporal_nodes = _get_nodes_in_date_range(self.graph, date_range[0], date_range[1])
            temporal_node_ids = {n["id"] for n in temporal_nodes}
            logger.debug(f"Temporal resolution: {date_range[0]} to {date_range[1]}, {len(temporal_node_ids)} nodes")

        # Overdue sweep — active nodes past their deadline always surface
        today = date.today()
        overdue_nodes = _get_overdue_nodes(self.graph, today)
        temporal_node_ids |= {n["id"] for n in overdue_nodes}
        if overdue_nodes:
            logger.debug(f"Overdue nodes: {len(overdue_nodes)}")

        # Step 1: Classify query domains
        relevant_domains = _classify_domains(query)

        # Step 2: Semantic search — fetch more candidates, we'll rank them
        search_results = self.vector_index.search(query, n=10)
        if not search_results and not temporal_node_ids:
            return "(No relevant nodes found in knowledge graph)", []

        # Session-aware boosting: search on recent user messages for additional context
        session_boost: dict[str, float] = {}
        if conversation_history:
            session_topics = _extract_session_topics(conversation_history)
            for topic in session_topics:
                try:
                    topic_results = self.vector_index.search(topic, n=5)
                    for tr in topic_results:
                        session_boost[tr["id"]] = session_boost.get(tr["id"], 0) + 0.05
                except Exception:
                    pass

        # Step 3: Score and rank results
        scored: list[tuple[float, dict]] = []
        for result in search_results:
            # Base score: invert semantic distance (lower distance = higher score)
            semantic_score = max(0, 1.0 - result.get("score", 1.0))

            # Domain boost: if node's type belongs to a relevant domain
            node_type = result.get("type", "")
            domain_boost = 0.0
            if relevant_domains:
                node_domain = self.schema["types"].get(node_type, {}).get("domain", "")
                if node_domain == relevant_domains[0]:
                    domain_boost = 0.2
                elif node_domain in relevant_domains:
                    domain_boost = 0.1

            # Recency boost for time-sensitive types
            recency_boost = 0.0
            if node_type in _RECENCY_SENSITIVE_TYPES:
                node = self.graph.get_node(result["id"])
                if node:
                    recency_boost = _recency_score(node) * 0.15

            # Centrality boost — logarithmic so hubs don't dominate
            degree = self.graph.get_degree(result["id"])
            centrality_boost = min(math.log1p(degree) * 0.02, 0.08)

            # Temporal boost — nodes matching the date range in the query
            temporal_boost = 0.3 if result["id"] in temporal_node_ids else 0.0

            # Session boost — nodes relevant to conversation history
            s_boost = session_boost.get(result["id"], 0.0)

            # Status penalty — deprioritize resolved/inactive nodes
            node_status = str(result.get("status", "") or "").lower()
            if not node_status:
                node_data = self.graph.get_node(result["id"])
                node_status = str(node_data.get("status", "") if node_data else "").lower()
            status_penalty = _STATUS_PENALTIES.get(node_status, 0.0)

            # Permanence boost — identity/strategic nodes outrank ephemeral ones
            _, permanence_boost = _get_permanence(node_type)

            total = semantic_score + domain_boost + recency_boost + centrality_boost + temporal_boost + s_boost + status_penalty + permanence_boost
            scored.append((total, result))

        # Inject temporal nodes not already in semantic results
        result_ids = {r.get("id") for _, r in scored}
        for nid in temporal_node_ids:
            if nid not in result_ids:
                node = self.graph.get_node(nid)
                if node:
                    node_status = str(node.get("status", "") or "").lower()
                    _, perm_boost = _get_permanence(node.get("type", ""))
                    injection_score = 0.3 + _STATUS_PENALTIES.get(node_status, 0.0) + perm_boost
                    scored.append((injection_score, {
                        "id": nid,
                        "title": node.get("title", nid),
                        "type": node.get("type", "unknown"),
                        "score": 0.5,
                    }))

        # Inject highly-boosted session nodes not already in results
        result_ids = {r.get("id") for _, r in scored}
        for nid, boost in session_boost.items():
            if boost >= 0.1 and nid not in result_ids:
                node = self.graph.get_node(nid)
                if node:
                    scored.append((boost, {
                        "id": nid,
                        "title": node.get("title", nid),
                        "type": node.get("type", "unknown"),
                        "score": 0.5,
                    }))

        scored.sort(key=lambda x: x[0], reverse=True)
        top_results = [r for _, r in scored[:5]]
        direct_ids = {r["id"] for r in top_results}

        # Step 4: 2-hop graph traversal from direct matches
        hop1_ids: set[str] = set()
        hop2_ids: set[str] = set()

        for result in top_results:
            neighbors_by_hop = self.graph.get_neighbors_by_hop(result["id"], depth=2)
            for node_data in neighbors_by_hop.get(1, []):
                if node_data["id"] not in direct_ids:
                    hop1_ids.add(node_data["id"])
            for node_data in neighbors_by_hop.get(2, []):
                nid = node_data["id"]
                if nid not in direct_ids and nid not in hop1_ids:
                    hop2_ids.add(nid)

        # Step 5: Assemble tiered context with token budget
        context_parts: list[str] = []
        char_budget = _MAX_CONTEXT_TOKENS * _CHARS_PER_TOKEN
        chars_used = 0

        # Temporal facts header — pre-computed date facts at the TOP so Claude reads them first
        temporal_header_parts = [f"TODAY: {today.strftime('%A, %d %B %Y')}"]
        if date_range:
            start, end = date_range
            if start == end:
                temporal_header_parts.append(f"Query date: {start.strftime('%A %d %B %Y')}")
            else:
                temporal_header_parts.append(
                    f"Query range: {start.strftime('%A %d %b')} – {end.strftime('%A %d %b %Y')}"
                )
        # Collect all date-bearing nodes from top results + temporal matches for the header
        all_top_ids = {r["id"] for _, r in scored[:5]} | temporal_node_ids
        for nid in all_top_ids:
            node = self.graph.get_node(nid)
            if not node:
                continue
            for field in ("date", "due", "deadline", "scheduled_for"):
                val = node.get(field)
                if not val:
                    continue
                parsed = None
                if hasattr(val, "isoformat") and hasattr(val, "year"):
                    parsed = val
                elif isinstance(val, str):
                    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
                        try:
                            parsed = datetime.strptime(val, fmt).date()
                            break
                        except ValueError:
                            continue
                if parsed:
                    delta = (parsed - today).days
                    if delta == 0:
                        dist = "today"
                    elif delta == 1:
                        dist = "tomorrow"
                    elif delta == -1:
                        dist = "yesterday"
                    elif delta > 1:
                        dist = f"in exactly {delta} days"
                    else:
                        dist = f"exactly {abs(delta)} days ago"
                    temporal_header_parts.append(
                        f"FACT: {node.get('title', nid)} — {parsed.strftime('%A %d %B %Y')} ({dist})"
                    )
                    break  # one date per node in header

        if len(temporal_header_parts) > 1:
            header = "\n".join(temporal_header_parts)
            context_parts.append(header)
            chars_used += len(header)

        # Tier 1: Direct matches — full content
        for result in top_results:
            node = self.graph.get_node(result["id"])
            if node is None:
                continue
            neighbors = self.graph.get_neighbors(result["id"], depth=1)
            neighbor_names = [f"{n['title']} ({n['type']})" for n in neighbors]
            block = _node_context_full(node, neighbor_names)

            if chars_used + len(block) > char_budget:
                break
            context_parts.append(block)
            chars_used += len(block)

        # Tier 2: 1-hop neighbors — summary (frontmatter + first paragraph)
        if chars_used < char_budget and hop1_ids:
            context_parts.append("\n--- Connected nodes (1 hop) ---")
            for nid in sorted(hop1_ids):
                node = self.graph.get_node(nid)
                if node is None:
                    continue
                neighbors = self.graph.get_neighbors(nid, depth=1)
                neighbor_names = [f"{n['title']} ({n['type']})" for n in neighbors]
                block = _node_context_summary(node, neighbor_names)

                if chars_used + len(block) > char_budget:
                    break
                context_parts.append(block)
                chars_used += len(block)

        # Tier 3: 2-hop neighbors — one-line mentions
        if chars_used < char_budget and hop2_ids:
            context_parts.append("\n--- Nearby in graph (2 hops) ---")
            for nid in sorted(hop2_ids):
                node = self.graph.get_node(nid)
                if node is None:
                    continue
                line = _node_context_minimal(node)

                if chars_used + len(line) > char_budget:
                    break
                context_parts.append(line)
                chars_used += len(line)

        context = "\n".join(context_parts) if context_parts else "(No relevant nodes found in knowledge graph)"
        logger.debug(
            f"Context assembled: {len(direct_ids)} direct, {len(hop1_ids)} hop-1, "
            f"{len(hop2_ids)} hop-2, ~{chars_used // _CHARS_PER_TOKEN} tokens"
        )
        return context, top_results

    @staticmethod
    def _build_challenge_note(challenges: dict) -> str:
        """Build the CHALLENGE LADDER injection for the system prompt."""
        if not challenges:
            return ""

        _STEP_NAMES = {
            1: "flag",
            2: "investigate",
            3: "escalate",
            4: "challenge",
            5: "accept",
        }
        _STEP_INSTRUCTIONS = {
            1: "Flag the gap. Name the node being changed and what it means in the context of the user's identity. Do not accept the change.",
            2: "Investigate. Ask what's driving this change — is something blocking them? Injury, schedule, motivation? Don't accept yet.",
            3: "Escalate. Reference how long this pattern has been active. Surface what happened last time they considered this change, if known. One sentence of history.",
            4: "Challenge the shift directly. List what this change contradicts — other goals, beliefs, anti-goals. Ask: are you sure? This changes who you are.",
            5: "Accept with full context. The user has defended this change through 4 steps. Record why in the update. This is a conscious, earned change.",
        }

        lines = ["CHALLENGE LADDER — the following identity-level changes are being contested:\n"]
        for node_id, state in challenges.items():
            step = state.get("step", 1)
            title = state.get("node_title", node_id)
            node_type = state.get("node_type", "")
            permanence = state.get("permanence", "identity")
            step_name = _STEP_NAMES.get(step, "flag")
            instruction = _STEP_INSTRUCTIONS.get(step, _STEP_INSTRUCTIONS[1])
            lines.append(f'Node: "{title}" ({node_type}, {permanence}-level)')
            lines.append(f"Current step: {step} of 5 ({step_name})")
            history = state.get("history", [])
            if history:
                lines.append("History:")
                for entry in history:
                    lines.append(f"  - Step {entry.get('step', '?')}: {entry.get('action', '?')}")
            lines.append(f"Your task at step {step}: {instruction}")
            lines.append("")

        return "\n\nCHALLENGE LADDER\n" + "\n".join(lines)

    def _build_mode_note(self, mode: str) -> str:
        """Build the ACTIVE MODE injection for the system prompt."""
        if not mode:
            return ""
        instructions = self.mode_instructions.get(mode, "")
        if instructions:
            return f"\n\nACTIVE MODE: {mode}\n{instructions}"
        return f"\n\nACTIVE MODE: {mode}"

    def _build_dismissed_note(self, dismissed_ids: list[str]) -> str:
        """Build a system prompt note about dismissed proposals."""
        if not dismissed_ids:
            return ""
        recent = dismissed_ids[-10:]
        ids = "\n".join(f"- {nid}" for nid in recent)
        return (
            f"\n\nDISMISSED PROPOSALS (user rejected these — do NOT reference or update them):\n"
            f"{ids}\n"
            f"These nodes do NOT exist. Do not propose updates to them unless the user explicitly asks again."
        )

    @staticmethod
    def _build_conflict_note(conflicts: list[dict]) -> str:
        """Build a system prompt note about detected conflicts."""
        if not conflicts:
            return ""
        lines = []
        # Separate obligation overload from regular conflicts
        regular = [c for c in conflicts if c.get("node_id") != "__obligations__"]
        overloads = [c for c in conflicts if c.get("node_id") == "__obligations__"]

        for c in regular:
            severity = c.get("severity", "soft").upper()
            permanence = c.get("permanence", "tactical")
            lines.append(f"- [{severity} | {permanence}] {c['conflict_type']}: {c.get('explanation', '')} (node: {c.get('title', c.get('node_id', '?'))})")
        conflict_text = "\n".join(lines)
        result = ""
        if conflict_text:
            result += (
                f"\n\nCONFLICT DETECTION — the following conflicts were detected between the user's message and their existing graph:\n"
                f"{conflict_text}\n"
                f"You MUST acknowledge these conflicts in your response. For HARD conflicts, challenge the user directly. "
                f"For SOFT conflicts, raise them as considerations. Do not ignore detected conflicts."
            )
        if overloads:
            ob = overloads[0].get("obligations", {})
            ob_lines = []
            if ob.get("goals"):
                ob_lines.append(f"- {len(ob['goals'])} active goals: {', '.join(g['title'] for g in ob['goals'])}")
            if ob.get("projects"):
                ob_lines.append(f"- {len(ob['projects'])} active projects: {', '.join(p['title'] for p in ob['projects'])}")
            if ob.get("habits"):
                ob_lines.append(f"- {len(ob['habits'])} active habits: {', '.join(h['title'] for h in ob['habits'])}")
            if ob.get("events_upcoming"):
                ob_lines.append(f"- {len(ob['events_upcoming'])} upcoming events: {', '.join(e['title'] for e in ob['events_upcoming'])}")
            if ob_lines:
                result += (
                    f"\n\nACTIVE OBLIGATIONS (the user is proposing a new commitment — surface this):\n"
                    + "\n".join(ob_lines)
                    + "\nAsk what they're willing to deprioritize to make room."
                )
        return result

    @staticmethod
    def _build_relationship_note(relationship_context: dict | None) -> str:
        """Build the SOCIAL CONTEXT injection for the system prompt.

        Only injected when the message is people-domain classified.
        Returns empty string when relationship_context is None.
        """
        if not relationship_context:
            return ""

        mentions = relationship_context.get("mentions", [])
        person_intel = relationship_context.get("person_intelligence", [])
        social_patterns = relationship_context.get("social_patterns", {})

        lines = ["\n\nSOCIAL CONTEXT — the user's message relates to people. Here's what you know:"]

        if mentions:
            lines.append("\nMENTIONED IN THIS SESSION:")
            for m in mentions:
                title = m.get("person_title", "?")
                rel = m.get("relationship")
                count = m.get("mention_count", 0)
                contexts = m.get("contexts", [])
                sentiment = m.get("sentiment", "neutral")

                rel_str = f" ({rel})" if rel else ""
                ctx_str = ""
                if contexts:
                    # Show first 2 contexts, truncated
                    ctx_parts = [f'"{c[:60]}"' for c in contexts[:2]]
                    ctx_str = f". Contexts: {', '.join(ctx_parts)}"
                lines.append(
                    f'- {title}{rel_str} — mentioned {count} time{"s" if count != 1 else ""}'
                    f"{ctx_str}. Sentiment: {sentiment}."
                )

        if person_intel:
            active_rels = [
                p["person_title"] for p in person_intel
                if p.get("days_since_update") is not None and p["days_since_update"] <= 14
            ]
            stale_rels = [
                p["person_title"] for p in person_intel
                if p.get("days_since_update") is None or p["days_since_update"] > 30
            ]

            lines.append("\nRELATIONSHIP HEALTH:")
            if active_rels:
                lines.append(f"- Active relationships (updated <14 days): {', '.join(active_rels)}")
            if stale_rels:
                lines.append(f"- Stale relationships (no updates 30+ days): {', '.join(stale_rels)}")

            pattern = social_patterns.get("pattern", "healthy")
            confidence = social_patterns.get("confidence", "low")
            lines.append(f"- Social pattern: {pattern} (confidence: {confidence})")

        lines.append(
            "\nWhen a person is mentioned:\n"
            "- Reference their node and connections — don't treat them as strangers.\n"
            "- If you notice a pattern (mentioned often but never seen, always in stressful context), name it.\n"
            "- If a relationship is stale, consider asking about it naturally: \"You haven't mentioned X in a while.\""
        )

        return "\n".join(lines)

    @staticmethod
    def _build_state_note(state: dict) -> str:
        """Build the USER STATE injection for the system prompt.

        Returns empty string when state is default (normal energy, no stress)
        to avoid injecting noise.
        """
        if not state:
            return ""

        energy = state.get("energy", "normal")
        stress = state.get("stress", "none")
        confidence = state.get("confidence", "low")
        signals = state.get("signals", [])

        # Default state — no injection needed
        if energy == "normal" and stress == "none":
            return ""

        signal_parts = [
            f"{s['type']} ({s['detail']})" for s in signals if s.get("detail")
        ]
        signal_text = ", ".join(signal_parts) if signal_parts else "none"

        lines = [
            "\n\nUSER STATE — adjust your tone and priorities based on the user's current state.",
            f"\nEnergy: {energy} | Stress: {stress} | Confidence: {confidence}",
            f"Signals: {signal_text}",
        ]

        if confidence == "low":
            lines.append("(low confidence — treat as a hint, not a diagnosis)")

        if stress == "elevated":
            lines.append(
                "\nWhen stress is elevated:\n"
                "- Lead with acknowledgment, not obligations\n"
                "- Hold non-urgent proactive alerts for a better moment\n"
                "- Keep responses shorter than usual\n"
                "- Don't pile on with accountability — one thing at a time"
            )

        if energy == "high":
            lines.append(
                "\nWhen energy is high:\n"
                "- Channel the momentum — help them prioritise rather than dampen\n"
                "- Flag overcommitting risk if idea density is high\n"
                "- Good time to surface strategic planning"
            )

        if energy == "low":
            lines.append(
                "\nWhen energy is low:\n"
                "- Protect their time and attention\n"
                "- Suggest recovery, not productivity\n"
                "- Only raise truly urgent alerts"
            )

        return "\n".join(lines)

    @staticmethod
    def _build_proactive_alerts(alerts: dict, state: dict | None = None) -> str:
        """Build the PROACTIVE ALERTS injection for the system prompt.

        Returns an empty string when there is nothing to surface.
        Caps at 5 broken streaks + 3 overdue commitments to avoid prompt bloat.
        State-aware suppression: elevated stress → 1 total alert; low energy → 2 total.
        """
        broken = alerts.get("broken_streaks", [])[:5]
        at_risk = alerts.get("at_risk_streaks", [])
        overdue = alerts.get("overdue_commitments", [])[:3]
        neglected = alerts.get("neglected_fundamentals", [])[:3]
        untracked = alerts.get("untracked_fundamentals", [])[:2]
        social_pattern = alerts.get("social_pattern")

        # State-aware suppression
        _state = state or {}
        confidence = _state.get("confidence", "low")
        stress = _state.get("stress", "none")
        energy = _state.get("energy", "normal")

        total_cap: int | None = None
        if confidence in ("medium", "high"):
            if stress == "elevated":
                total_cap = 1
            elif energy == "low":
                total_cap = 2

        if total_cap is not None:
            # Prioritize overdue > broken > at_risk, apply cap
            remaining = total_cap
            overdue = overdue[:remaining]
            remaining = max(0, remaining - len(overdue))
            broken = broken[:remaining]
            remaining = max(0, remaining - len(broken))
            at_risk = at_risk[:remaining]
            # Suppress fundamentals when stressed/low-energy to avoid overload
            if stress == "elevated":
                neglected = []
                untracked = []
            else:
                remaining = max(0, total_cap - len(overdue) - len(broken) - len(at_risk))
                neglected = neglected[:remaining]
                remaining = max(0, remaining - len(neglected))
                untracked = untracked[:remaining]

        if not broken and not at_risk and not overdue and not neglected and not untracked and not social_pattern:
            return ""

        lines = [
            "\n\nPROACTIVE ALERTS — raise these naturally when relevant. "
            "Don't lead with all of them at once. "
            "Pick the most relevant 1-2 based on what the user is talking about.",
        ]

        if broken:
            lines.append("\nBROKEN STREAKS:")
            for s in broken:
                last = s.get("last_completed")
                days = s.get("days_since_last")
                if last and days is not None:
                    lines.append(
                        f'- "{s["habit_title"]}" — last completed {days} day{"s" if days != 1 else ""} ago, streak broken.'
                    )
                else:
                    lines.append(f'- "{s["habit_title"]}" — no completions recorded, streak broken.')

        if at_risk:
            lines.append("\nAT RISK:")
            for s in at_risk:
                days = s.get("days_since_last")
                freq = s.get("frequency", "weekly")
                lines.append(
                    f'- "{s["habit_title"]}" — last completed {days} day{"s" if days != 1 else ""} ago, '
                    f"at risk of breaking ({freq} frequency)."
                )

        if overdue:
            lines.append("\nOVERDUE COMMITMENTS:")
            for item in overdue:
                title = item.get("title", item.get("node_id", "?"))
                ntype = item.get("type", "")
                priority = item.get("priority", "medium")
                days_over = item.get("days_overdue", 0)
                due = item.get("due", "")
                committed_on = item.get("committed_on")
                ctx = item.get("commitment_context")
                consequences = item.get("consequences", [])

                line = f'- "{title}" ({ntype}, {priority} priority) — {days_over} day{"s" if days_over != 1 else ""} overdue (due {due}).'
                if committed_on and ctx:
                    line += f' Committed {committed_on}: "{ctx}"'
                elif committed_on:
                    line += f" Committed {committed_on}."
                lines.append(line)

                if consequences:
                    parts = [f'"{c["title"]}" ({c["type"]})' for c in consequences[:3]]
                    lines.append(f'  → Consequences: {", ".join(parts)}')

        if neglected:
            lines.append(
                "\nNEGLECTED FUNDAMENTALS — these core human needs haven't had activity in 2+ weeks:"
            )
            for f in neglected:
                name = f["fundamental"].replace("_", " ").title()
                msg = f.get("message", "")
                lines.append(f"- {name} — {msg}")
                lines.append("  This is a species-level need. Don't lecture — ask what's getting in the way.")

        if untracked:
            lines.append(
                "\nUNTRACKED FUNDAMENTALS — the user has no habits tracking these:"
            )
            for f in untracked:
                name = f["fundamental"].replace("_", " ").title()
                lines.append(f"- {name} — consider suggesting a {name.lower()}-related habit.")

        if social_pattern and isinstance(social_pattern, dict):
            sp_pattern = social_pattern.get("pattern", "healthy")
            sp_confidence = social_pattern.get("confidence", "low")
            sp_signals = social_pattern.get("signals", [])
            stale_rels = social_pattern.get("stale_relationships", [])
            if sp_pattern == "isolating" and sp_confidence in ("medium", "high"):
                signal_details = "; ".join(s.get("detail", "") for s in sp_signals if s.get("detail"))
                lines.append(
                    f"\nSOCIAL PATTERN: Possible isolation detected. {signal_details}."
                    "\nDon't lecture — ask naturally about people they haven't mentioned."
                )
                if stale_rels:
                    stale_names = [r.get("person_title", "?") for r in stale_rels[:3]]
                    lines.append(f'  Example: "How\'s {stale_names[0]} doing?"')

        return "\n".join(lines)

    def chat_stream(self, message: str, conversation_history: list[dict],
                    dismissed_ids: list[str] | None = None,
                    conflicts: list[dict] | None = None,
                    mode: str = "mirror",
                    challenges: dict | None = None,
                    alerts: dict | None = None,
                    state: dict | None = None,
                    relationship_context: dict | None = None):
        """Streaming version of chat(). Yields (event_type, data) tuples.

        Events:
          ("text", {"content": str})  — streamed text token
          ("done", {response, full_response, graph_updates, relevant_nodes})
        """
        context, search_results = self.get_context(message, conversation_history)
        today = date.today().strftime("%A %d %B %Y")
        system = self.system_prompt_template.format(context=context, today=today)
        system += self._build_dismissed_note(dismissed_ids or [])
        system += self._build_state_note(state or {})
        system += self._build_relationship_note(relationship_context)
        system += self._build_proactive_alerts(alerts or {}, state=state)
        system += self._build_conflict_note(conflicts or [])
        system += self._build_mode_note(mode)
        system += self._build_challenge_note(challenges or {})
        messages = conversation_history + [{"role": "user", "content": message}]

        full_text = ""
        streaming_text = ""  # text sent to frontend (stops at <graph_updates>)
        in_graph_block = False
        tag_buffer = ""

        with self.client.messages.stream(
            model=self.model,
            max_tokens=4096,
            system=system,
            messages=messages,
        ) as stream:
            for text in stream.text_stream:
                full_text += text

                if in_graph_block:
                    # Already inside <graph_updates>, don't stream to frontend
                    continue

                # Check if this chunk starts or contains the opening tag
                tag_buffer += text
                tag_start = tag_buffer.find("<graph_updates>")

                if tag_start != -1:
                    # Send any text before the tag
                    before_tag = tag_buffer[:tag_start]
                    if before_tag:
                        streaming_text += before_tag
                        yield ("text", {"content": before_tag})
                    in_graph_block = True
                    tag_buffer = ""
                    continue

                # If buffer could contain a partial "<graph_updates>" tag, hold it
                if "<" in tag_buffer:
                    # Check if the end of the buffer could be the start of the tag
                    potential = "<graph_updates>"
                    tail = tag_buffer[tag_buffer.rfind("<"):]
                    if potential.startswith(tail):
                        # Partial match — flush everything before the "<" and hold the rest
                        safe = tag_buffer[:tag_buffer.rfind("<")]
                        if safe:
                            streaming_text += safe
                            yield ("text", {"content": safe})
                        tag_buffer = tail
                        continue

                # No tag concerns — flush the whole buffer
                streaming_text += tag_buffer
                yield ("text", {"content": tag_buffer})
                tag_buffer = ""

        # Flush any remaining buffer (if stream ended without <graph_updates>)
        if tag_buffer and not in_graph_block:
            streaming_text += tag_buffer
            yield ("text", {"content": tag_buffer})

        clean_text, graph_updates = self._parse_graph_updates(full_text)
        graph_updates = self._validate_types(graph_updates)

        yield ("done", {
            "response": clean_text,
            "full_response": full_text,
            "graph_updates": graph_updates,
            "relevant_nodes": [
                {"id": r["id"], "title": r.get("title", r["id"]), "type": r.get("type", "unknown")}
                for r in search_results
            ],
        })

    def chat(self, message: str, conversation_history: list[dict],
             dismissed_ids: list[str] | None = None,
             conflicts: list[dict] | None = None,
             mode: str = "mirror",
             challenges: dict | None = None,
             alerts: dict | None = None,
             state: dict | None = None,
             relationship_context: dict | None = None) -> dict:
        """Send a message with conversation history, get a response with graph update proposals.

        conversation_history: list of {role, content} dicts from the chat store.
        dismissed_ids: node IDs the user dismissed this session — injected into prompt.
        conflicts: detected conflicts between the message and existing graph nodes.
        mode: communication mode (mirror/advisor/guardian/dialectic).
        challenges: active challenge ladder states keyed by node_id.
        alerts: proactive accountability alerts (broken streaks, overdue commitments).
        state: inferred user state (energy, stress, confidence) from recent messages.
        relationship_context: per-person mention data and social patterns (people-domain only).
        """
        context, search_results = self.get_context(message, conversation_history)
        today = date.today().strftime("%A %d %B %Y")
        system = self.system_prompt_template.format(context=context, today=today)
        system += self._build_dismissed_note(dismissed_ids or [])
        system += self._build_state_note(state or {})
        system += self._build_relationship_note(relationship_context)
        system += self._build_proactive_alerts(alerts or {}, state=state)
        system += self._build_conflict_note(conflicts or [])
        system += self._build_mode_note(mode)
        system += self._build_challenge_note(challenges or {})

        # Build messages: prior history + current user message
        messages = conversation_history + [{"role": "user", "content": message}]

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=system,
                messages=messages,
            )
            assistant_text = response.content[0].text
        except anthropic.APIError as e:
            logger.error(f"Claude API error: {e}")
            raise

        clean_text, graph_updates = self._parse_graph_updates(assistant_text)
        graph_updates = self._validate_types(graph_updates)

        return {
            "response": clean_text,
            "full_response": assistant_text,
            "graph_updates": graph_updates,
            "relevant_nodes": [
                {"id": r["id"], "title": r.get("title", r["id"]), "type": r.get("type", "unknown")}
                for r in search_results
            ],
        }

    def _parse_graph_updates(self, response_text: str) -> tuple[str, list[dict]]:
        """Extract <graph_updates> block from response.

        Returns (clean_text, list_of_updates).
        """
        match = GRAPH_UPDATES_RE.search(response_text)
        if not match:
            return response_text, []

        clean_text = response_text[: match.start()].rstrip()

        try:
            updates = json.loads(match.group(1))
            if not isinstance(updates, list):
                updates = [updates]
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse graph_updates JSON: {e}")
            return clean_text, []

        return clean_text, updates

    def _validate_types(self, updates: list[dict]) -> list[dict]:
        """Post-process graph updates to catch invalid or misclassified types."""
        valid_types = set(self.schema.get("type_list", []))
        valid_edge_types = {
            "relates_to", "blocked_by", "supported_by", "contradicts",
            "inspired_by", "involves", "part_of", "located_in",
            "funded_by", "met_at",
        }

        for update in updates:
            action = update.get("action", "")

            # Validate type on create actions
            if action == "create":
                proposed = update.get("type", "")
                if proposed and proposed not in valid_types:
                    lower = proposed.lower().strip()
                    if lower in valid_types:
                        update["type"] = lower
                    else:
                        logger.warning(
                            f"AI proposed invalid type '{proposed}' for '{update.get('node_id')}' "
                            f"— falling back to 'note'"
                        )
                        update["type"] = "note"

                # Validate edge types on create edges
                for edge in update.get("edges", []):
                    if edge.get("type") not in valid_edge_types:
                        logger.warning(f"Invalid edge type '{edge.get('type')}' — using 'relates_to'")
                        edge["type"] = "relates_to"

            # Validate type changes on update actions
            elif action == "update":
                changes = update.get("changes", {})
                fm = changes.get("frontmatter", {})
                if isinstance(fm, dict) and "type" in fm:
                    proposed = fm["type"]
                    if proposed not in valid_types:
                        lower = proposed.lower().strip()
                        if lower in valid_types:
                            fm["type"] = lower
                        else:
                            logger.warning(f"Invalid type '{proposed}' in update — removing")
                            del fm["type"]

                # Validate edge types on add_edges
                for edge in changes.get("add_edges", []):
                    if edge.get("type") not in valid_edge_types:
                        logger.warning(f"Invalid edge type '{edge.get('type')}' — using 'relates_to'")
                        edge["type"] = "relates_to"

            # Validate edge type on link actions
            elif action == "link":
                if update.get("type") not in valid_edge_types:
                    logger.warning(f"Invalid link type '{update.get('type')}' — using 'relates_to'")
                    update["type"] = "relates_to"

        return updates
