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
from vector_search import VectorIndex, build_search_filter

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


# ── Query intent classifier ──

_INTENT_BROAD_SIGNALS: list[str] = [
    "summary", "recap", "review", "overview",
    "what happened", "what did i do", "how did", "how was",
]

_INTENT_FILTER_SIGNALS: list[str] = [
    "show me my", "list my", "what are my", "all my tasks", "my expenses",
]

_INTENT_ENTITY_SIGNALS: list[str] = [
    "tell me about", "what is", "who is", "details on", "more about",
]

_INTENT_RELATIONAL_SIGNALS: list[str] = [
    "relationship with", "how's my relationship", "dynamics with",
    "history with", "interactions with", "how do i know",
    "what's my relationship", "how are things with",
]


def classify_query_intent(
    query: str,
    date_range: tuple[date, date] | None,
    domains: list[str],
) -> dict:
    """Classify incoming query into an intent category for adaptive retrieval.

    Evaluated in priority order:
      1. temporal_broad — date range ≥3 days, or summary signal phrases
      2. temporal_specific — date range <3 days (today/tomorrow/single day)
      3. domain_filter — strong domain signal or explicit filter phrases
      4. entity_lookup — short query or entity phrases ("tell me about …")
      5. general — default

    Returns a dict with: intent, k, compact, pre_filter, scoring_adjustments.
    The pre_filter for domain_filter is ``None`` here; ``get_context()`` builds
    the actual type-level filter from the schema once the intent is known.
    """
    q = query.lower().strip()

    if not q:
        return {
            "intent": "general",
            "k": 5,
            "compact": False,
            "pre_filter": None,
            "scoring_adjustments": {},
        }

    has_broad_signal = any(sig in q for sig in _INTENT_BROAD_SIGNALS)
    has_filter_signal = any(sig in q for sig in _INTENT_FILTER_SIGNALS)
    has_entity_signal = any(sig in q for sig in _INTENT_ENTITY_SIGNALS)

    # Domain dominance check — re-score to get raw hit counts
    domain_hits: dict[str, int] = {}
    for domain, keywords in _DOMAIN_KEYWORDS.items():
        hits = sum(1 for kw in keywords if kw in q)
        if hits > 0:
            domain_hits[domain] = hits

    sorted_hit_domains = sorted(domain_hits, key=domain_hits.get, reverse=True)
    top_domain = sorted_hit_domains[0] if sorted_hit_domains else None
    is_domain_dominant = False
    if top_domain:
        top_score = domain_hits[top_domain]
        second_score = domain_hits.get(sorted_hit_domains[1], 0) if len(sorted_hit_domains) > 1 else 0
        is_domain_dominant = top_score >= 3 and top_score >= 2 * max(second_score, 1)

    # ── 1. temporal_broad ──
    if date_range is not None and (date_range[1] - date_range[0]).days >= 3:
        return {
            "intent": "temporal_broad",
            "k": 15,
            "compact": True,
            "pre_filter": build_search_filter(
                exclude_statuses=["completed", "cancelled", "archived", "superseded"]
            ),
            "scoring_adjustments": {
                "temporal_boost": 0.5,
                "permanence_multiplier": 0.5,
            },
        }
    # Summary signals without a date range → temporal_broad with lower k
    if has_broad_signal:
        return {
            "intent": "temporal_broad",
            "k": 10,
            "compact": True,
            "pre_filter": None,
            "scoring_adjustments": {
                "temporal_boost": 0.5,
                "permanence_multiplier": 0.5,
            },
        }

    # ── 2. temporal_specific ──
    if date_range is not None:
        return {
            "intent": "temporal_specific",
            "k": 10,
            "compact": False,
            "pre_filter": None,
            "scoring_adjustments": {
                "temporal_boost": 0.4,
                "recency_multiplier": 2.0,
            },
        }

    # ── 3. domain_filter ──
    if (has_filter_signal and domains) or is_domain_dominant:
        return {
            "intent": "domain_filter",
            "k": 10,
            "compact": True,
            "pre_filter": None,  # caller fills this in from schema
            "scoring_adjustments": {
                "domain_boost": 0.3,
                "permanence_multiplier": 0.0,
            },
        }

    # ── 3.5. relational ──
    has_relational_signal = any(sig in q for sig in _INTENT_RELATIONAL_SIGNALS)
    if has_relational_signal or (has_entity_signal and "People" in domains):
        return {
            "intent": "relational",
            "k": 5,
            "compact": True,
            "pre_filter": None,
            "scoring_adjustments": {
                "centrality_multiplier": 2.0,
                "session_multiplier": 1.5,
            },
            "expand_person": True,
        }

    # ── 4. entity_lookup ──
    if has_entity_signal or len(q) < 40:
        return {
            "intent": "entity_lookup",
            "k": 5,
            "compact": False,
            "pre_filter": None,
            "scoring_adjustments": {
                "centrality_multiplier": 2.0,
                "session_multiplier": 2.0,
            },
        }

    # ── 5. general ──
    return {
        "intent": "general",
        "k": 5,
        "compact": False,
        "pre_filter": None,
        "scoring_adjustments": {},
    }


# Bootstrap threshold — below this, inject onboarding prompt instead of retrieval.
_BOOTSTRAP_THRESHOLD = 10

# Token budget constants.
_BASE_TOKEN_BUDGET = 3000  # minimum budget for focused queries
_MAX_TOKEN_BUDGET = 8000   # hard ceiling for any query
_CHARS_PER_TOKEN = 4       # conservative estimate

# Keep old name as alias for backward compatibility with existing tests.
_MAX_CONTEXT_TOKENS = _BASE_TOKEN_BUDGET


def _compute_token_budget(intent: dict, selected_count: int, total_nodes: int) -> int:
    """Compute dynamic token budget based on intent and data volume.

    Scales up for broad/relational intents with many nodes. Floors at
    _BASE_TOKEN_BUDGET and caps at _MAX_TOKEN_BUDGET.
    """
    budget = _BASE_TOKEN_BUDGET

    # Compact scaling: each extra node beyond 5 gets 300 tokens of headroom
    if intent.get("compact") and selected_count > 5:
        budget += (selected_count - 5) * 300

    # Intent multiplier
    intent_type = intent.get("intent", "general")
    if intent_type in ("temporal_broad", "relational"):
        budget = int(budget * 1.5)
    elif intent_type == "domain_filter":
        budget = int(budget * 1.2)

    # Graph scale factor: more data = slightly more context
    if total_nodes > 200:
        budget = int(budget * 1.2)
    elif total_nodes > 100:
        budget = int(budget * 1.1)

    return max(_BASE_TOKEN_BUDGET, min(_MAX_TOKEN_BUDGET, budget))


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


def _format_periodic_date(iso_date: str | None) -> str:
    """Format an ISO date string as a short human-readable date (e.g. 'Mar 16')."""
    if not iso_date:
        return "unknown"
    try:
        d = datetime.fromisoformat(iso_date).date()
        return d.strftime("%b %-d")
    except (ValueError, AttributeError):
        return iso_date


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


def _node_context_full(
    node: dict,
    neighbor_names: list[str],
    relationship_stats: dict | None = None,
) -> str:
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

    # Person-node enrichment with mention stats
    if relationship_stats is not None and node.get("type") == "person":
        rs = relationship_stats
        rel_type = rs.get("relationship") or node.get("relationship", "")
        freq = rs.get("expected_frequency") or ""
        count = rs.get("mention_count", 0)
        ctx = rs.get("mention_contexts", {})
        pos = ctx.get("positive", 0)
        neg = ctx.get("negative", 0)
        plan = ctx.get("planning", 0)
        days = rs.get("days_since_mention")
        health = rs.get("health", "")
        freq_str = f", {freq}" if freq else ""
        days_str = f"{days}d ago" if days is not None else "unknown"
        mentions_str = f"{count} this month ({pos} positive, {neg} negative, {plan} planning)"
        parts.append(
            f"[person] {node.get('title', node['id'])} ({rel_type}{freq_str})\n"
            f"  Mentions: {mentions_str} | Last: {days_str} | Health: {health}"
        )

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


_TEMPORAL_NODE_TYPES = {"daily", "event", "experience", "memory"}


def _node_context_oneliner(node: dict) -> str:
    """Dense one-liner for high-volume Tier 1 — ~50-80 chars per node.

    Temporal types (daily/event/experience/memory): date prefix + type + title + content snippet.
    Other types: type + title + key metadata (status, priority, due date).
    """
    ntype = node.get("type", "unknown")
    title = node.get("title", node.get("id", "unknown"))

    # Truncate title to 30 chars
    if len(title) > 30:
        title = title[:27] + "..."

    type_tag = f"[{ntype}]"

    if ntype in _TEMPORAL_NODE_TYPES:
        # Find a date to use as prefix
        date_prefix = ""
        for field in ("date", "scheduled_for", "deadline", "due"):
            val = node.get(field)
            if val:
                try:
                    if hasattr(val, "strftime"):
                        parsed = val
                    else:
                        parsed = datetime.strptime(str(val).split("T")[0], "%Y-%m-%d").date()
                    date_prefix = parsed.strftime("%a %d %b")
                    break
                except (ValueError, TypeError):
                    continue

        content = node.get("content", "").strip()
        snippet = ""
        if content:
            # First clause up to 40 chars, cut at word boundary
            clause = re.split(r"[.!?,;]", content)[0][:40]
            if len(clause) == 40 and " " in clause:
                clause = clause[:clause.rfind(" ")]
            snippet = clause.strip()

        if date_prefix:
            line = f"{date_prefix}: {type_tag} {title}"
        else:
            line = f"{type_tag} {title}"
        if snippet:
            line = f"{line} — {snippet}"
        return line
    else:
        # Non-temporal: use status/priority/due metadata
        meta_parts: list[str] = []
        status = node.get("status", "")
        if status:
            meta_parts.append(status)
        priority = node.get("priority", "")
        if priority:
            meta_parts.append(f"{priority} priority")
        for field in ("due", "deadline"):
            val = node.get(field)
            if val:
                try:
                    if hasattr(val, "strftime"):
                        parsed = val
                    else:
                        parsed = datetime.strptime(str(val).split("T")[0], "%Y-%m-%d").date()
                    meta_parts.append(f"due {parsed.strftime('%b %d')}")
                    break
                except (ValueError, TypeError):
                    continue

        line = f"{type_tag} {title}"
        if meta_parts:
            line = f"{line} — {', '.join(meta_parts)}"
        return line


def _node_context_compact(
    node: dict,
    neighbor_names: list[str],
    relationship_stats: dict | None = None,
) -> str:
    """Compact context for broad-query Tier 1 — ~150-200 chars per node.

    Shows: title/type/status, one key date, neighbour count, first sentence.
    """
    title = node.get("title", node["id"])
    ntype = node.get("type", "unknown")
    status = node.get("status", "")
    priority = node.get("priority")

    meta_parts: list[str] = []
    if status:
        meta_parts.append(status)
    if priority:
        meta_parts.append(f"{priority} priority")
    meta_str = f" ({', '.join(meta_parts)})" if meta_parts else ""
    header = f"[{ntype}] {title}{meta_str}"

    # One key date + neighbour count
    detail_parts: list[str] = []
    for field in ("due", "deadline", "date"):
        val = node.get(field)
        if val:
            detail_parts.append(f"{field.capitalize()}: {val}")
            break
    if neighbor_names:
        detail_parts.append(f"Connected: {len(neighbor_names)} nodes")
    detail_line = "  " + " | ".join(detail_parts) if detail_parts else ""

    # First sentence of content (≤100 chars)
    content = node.get("content", "").strip()
    first_sentence = ""
    if content:
        sentences = re.split(r"(?<=[.!?])\s", content)
        first_sentence = sentences[0][:100] if sentences else content[:100]

    parts = [header]
    if detail_line:
        parts.append(detail_line)
    if first_sentence:
        parts.append(f"  {first_sentence}")

    # Person-node enrichment with mention stats (compact)
    if relationship_stats is not None and node.get("type") == "person":
        rs = relationship_stats
        count = rs.get("mention_count", 0)
        days = rs.get("days_since_mention")
        days_str = f"{days}d" if days is not None else "unknown"
        parts.append(f"  | Mentions: {count}/month, last {days_str} ago")

    return "\n".join(parts)


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

    def _retrieve(
        self,
        query: str,
        conversation_history: list[dict] | None = None,
        relationship_data: dict | None = None,
    ) -> dict:
        """Run the full retrieval pipeline and return a rich result dict.

        Called by both get_context() and get_context_debug(). Always collects
        per-candidate scoring state so the debug endpoint has full detail.
        """
        today = date.today()

        # Bootstrap check — if vault is nearly empty, skip retrieval and onboard
        total_nodes = len(self.graph.get_all_nodes())
        if total_nodes < _BOOTSTRAP_THRESHOLD:
            logger.info(f"Bootstrap mode: {total_nodes} nodes (threshold {_BOOTSTRAP_THRESHOLD})")
            bootstrap_ctx = _bootstrap_context(total_nodes, self.graph)
            return {
                "context": bootstrap_ctx,
                "top_results": [],
                "intent": {"intent": "general", "k": 5, "compact": False, "pre_filter": None, "scoring_adjustments": {}},
                "date_range": None,
                "domains": [],
                "candidates": [],
                "nodes_in_context": {"tier1": 0, "tier2": 0, "tier3": 0},
                "context_length_chars": len(bootstrap_ctx),
                "token_budget": _BASE_TOKEN_BUDGET,
                "format_used": "full",
            }

        # Step 0: Temporal query resolution — detect date phrases and find matching nodes
        temporal_node_ids: set[str] = set()
        date_range = _resolve_temporal_query(query)
        if date_range:
            temporal_nodes = _get_nodes_in_date_range(self.graph, date_range[0], date_range[1])
            temporal_node_ids = {n["id"] for n in temporal_nodes}
            logger.debug(f"Temporal resolution: {date_range[0]} to {date_range[1]}, {len(temporal_node_ids)} nodes")

        # Overdue sweep — active nodes past their deadline always surface
        overdue_nodes = _get_overdue_nodes(self.graph, today)
        temporal_node_ids |= {n["id"] for n in overdue_nodes}
        if overdue_nodes:
            logger.debug(f"Overdue nodes: {len(overdue_nodes)}")

        # Step 1: Classify query domains
        relevant_domains = _classify_domains(query)

        # Step 1.5: Classify query intent for adaptive retrieval
        try:
            intent = classify_query_intent(query, date_range, relevant_domains)
            # domain_filter: fill in the pre_filter from schema (classifier leaves it None)
            if intent["intent"] == "domain_filter" and relevant_domains:
                dominant_domain = relevant_domains[0]
                domain_types = self.schema.get("domains", {}).get(dominant_domain, {}).get("types", [])
                if domain_types:
                    intent = dict(intent)  # avoid mutating classifier output
                    intent["pre_filter"] = build_search_filter(types=domain_types)
        except Exception as exc:
            logger.warning(f"classify_query_intent raised {exc!r} — using general intent")
            intent = {
                "intent": "general",
                "k": 5,
                "compact": False,
                "pre_filter": None,
                "scoring_adjustments": {},
            }

        # Step 2: Semantic search — fetch extra candidates for ranking headroom
        search_n = intent["k"] + 5
        pre_filter = intent.get("pre_filter")
        search_results = self.vector_index.search(query, n=search_n, where=pre_filter)

        # Fallback: if filter was too aggressive, retry without it
        if pre_filter and len(search_results) < intent["k"] // 2:
            logger.debug(
                f"Filtered search returned {len(search_results)} < {intent['k'] // 2} — "
                "falling back to unfiltered"
            )
            search_results = self.vector_index.search(query, n=search_n)

        if not search_results and not temporal_node_ids:
            ctx = "(No relevant nodes found in knowledge graph)"
            return {
                "context": ctx,
                "top_results": [],
                "intent": intent,
                "date_range": date_range,
                "domains": relevant_domains,
                "candidates": [],
                "nodes_in_context": {"tier1": 0, "tier2": 0, "tier3": 0},
                "context_length_chars": len(ctx),
                "token_budget": _BASE_TOKEN_BUDGET,
                "format_used": "full",
            }

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

        # Step 3: Score and rank results using intent-adjusted weights
        adjustments = intent.get("scoring_adjustments", {})
        temporal_boost_weight = adjustments.get("temporal_boost", 0.3)
        permanence_multiplier = adjustments.get("permanence_multiplier", 1.0)
        recency_multiplier = adjustments.get("recency_multiplier", 1.0)
        domain_boost_weight = adjustments.get("domain_boost", 0.2)
        centrality_multiplier = adjustments.get("centrality_multiplier", 1.0)
        session_multiplier = adjustments.get("session_multiplier", 1.0)

        scored: list[tuple[float, dict]] = []
        candidates_debug: list[dict] = []

        for result in search_results:
            # Base score: invert semantic distance (lower distance = higher score)
            semantic_score = max(0, 1.0 - result.get("score", 1.0))
            node_type = result.get("type", "")

            # Domain boost: if node's type belongs to a relevant domain
            domain_boost = 0.0
            if relevant_domains:
                node_domain = self.schema["types"].get(node_type, {}).get("domain", "")
                if node_domain == relevant_domains[0]:
                    domain_boost = domain_boost_weight
                elif node_domain in relevant_domains:
                    domain_boost = domain_boost_weight / 2

            # Recency boost for time-sensitive types
            recency_boost = 0.0
            if node_type in _RECENCY_SENSITIVE_TYPES:
                node = self.graph.get_node(result["id"])
                if node:
                    recency_boost = _recency_score(node) * 0.15 * recency_multiplier

            # Centrality boost — logarithmic so hubs don't dominate
            degree = self.graph.get_degree(result["id"])
            centrality_boost = min(math.log1p(degree) * 0.02, 0.08) * centrality_multiplier

            # Temporal boost — nodes matching the date range in the query
            temporal_boost = temporal_boost_weight if result["id"] in temporal_node_ids else 0.0

            # Session boost — nodes relevant to conversation history
            s_boost = session_boost.get(result["id"], 0.0) * session_multiplier

            # Status penalty — deprioritize resolved/inactive nodes
            node_status = str(result.get("status", "") or "").lower()
            if not node_status:
                node_data = self.graph.get_node(result["id"])
                node_status = str(node_data.get("status", "") if node_data else "").lower()
            status_penalty = _STATUS_PENALTIES.get(node_status, 0.0)

            # Permanence boost — identity/strategic nodes outrank ephemeral ones
            _, perm_boost = _get_permanence(node_type)
            permanence_boost = perm_boost * permanence_multiplier

            total = (semantic_score + domain_boost + recency_boost + centrality_boost
                     + temporal_boost + s_boost + status_penalty + permanence_boost)

            scored.append((total, result))
            candidates_debug.append({
                "id": result["id"],
                "title": result.get("title", result["id"]),
                "type": node_type,
                "scores": {
                    "semantic": round(semantic_score, 4),
                    "domain": round(domain_boost, 4),
                    "recency": round(recency_boost, 4),
                    "centrality": round(centrality_boost, 4),
                    "temporal": round(temporal_boost, 4),
                    "session": round(s_boost, 4),
                    "status_penalty": round(status_penalty, 4),
                    "permanence": round(permanence_boost, 4),
                    "total": round(total, 4),
                },
                "selected": False,
                "tier": None,
            })

        # Inject temporal nodes not already in semantic results
        result_ids = {r.get("id") for _, r in scored}
        for nid in temporal_node_ids:
            if nid not in result_ids:
                node = self.graph.get_node(nid)
                if node:
                    node_status = str(node.get("status", "") or "").lower()
                    _, perm_boost = _get_permanence(node.get("type", ""))
                    status_pen = _STATUS_PENALTIES.get(node_status, 0.0)
                    injection_score = 0.3 + status_pen + perm_boost
                    scored.append((injection_score, {
                        "id": nid,
                        "title": node.get("title", nid),
                        "type": node.get("type", "unknown"),
                        "score": 0.5,
                    }))
                    candidates_debug.append({
                        "id": nid,
                        "title": node.get("title", nid),
                        "type": node.get("type", "unknown"),
                        "scores": {
                            "semantic": 0.0,
                            "domain": 0.0,
                            "recency": 0.0,
                            "centrality": 0.0,
                            "temporal": 0.3,
                            "session": 0.0,
                            "status_penalty": round(status_pen, 4),
                            "permanence": round(perm_boost, 4),
                            "total": round(injection_score, 4),
                        },
                        "selected": False,
                        "tier": None,
                    })

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
                    candidates_debug.append({
                        "id": nid,
                        "title": node.get("title", nid),
                        "type": node.get("type", "unknown"),
                        "scores": {
                            "semantic": 0.0, "domain": 0.0, "recency": 0.0, "centrality": 0.0,
                            "temporal": 0.0, "session": round(boost, 4),
                            "status_penalty": 0.0, "permanence": 0.0,
                            "total": round(boost, 4),
                        },
                        "selected": False,
                        "tier": None,
                    })

        scored.sort(key=lambda x: x[0], reverse=True)
        top_results = [r for _, r in scored[:intent["k"]]]
        direct_ids = {r["id"] for r in top_results}

        # Mark selected candidates as tier 1
        for c in candidates_debug:
            if c["id"] in direct_ids:
                c["selected"] = True
                c["tier"] = 1

        # Person expansion for relational intent — pull all 1-hop neighbors of the top person
        if intent.get("expand_person"):
            person_node = None
            for r in top_results:
                node = self.graph.get_node(r["id"])
                if node and node.get("type") == "person":
                    person_node = node
                    break

            if person_node:
                linked_nodes = self.graph.get_neighbors(person_node["id"], depth=1)
                # Sort by recency (most recently updated/created)
                def _recency_key(n: dict) -> str:
                    for field in ("updated", "created", "date"):
                        val = n.get(field)
                        if val:
                            return str(val)
                    return ""
                linked_nodes.sort(key=_recency_key, reverse=True)

                expansion_count = 0
                for neighbor in linked_nodes:
                    if expansion_count >= 20:
                        break
                    nid = neighbor.get("id")
                    if not nid or nid in direct_ids:
                        continue
                    top_results.append({
                        "id": nid,
                        "title": neighbor.get("title", nid),
                        "type": neighbor.get("type", "unknown"),
                        "score": 0.5,
                    })
                    direct_ids.add(nid)
                    candidates_debug.append({
                        "id": nid,
                        "title": neighbor.get("title", nid),
                        "type": neighbor.get("type", "unknown"),
                        "scores": {
                            "semantic": 0.0, "domain": 0.0, "recency": 0.0, "centrality": 0.0,
                            "temporal": 0.0, "session": 0.0, "status_penalty": 0.0,
                            "permanence": 0.0, "total": 0.0,
                        },
                        "selected": True,
                        "tier": 1,
                        "source": "relational_expansion",
                    })
                    expansion_count += 1

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
        compact = intent.get("compact", False)
        context_parts: list[str] = []
        token_budget = _compute_token_budget(intent, len(top_results), total_nodes)
        char_budget = token_budget * _CHARS_PER_TOKEN
        chars_used = 0

        # Determine Tier 1 format based on node count and compact flag
        if len(top_results) > 12 and compact:
            format_used = "oneliner"
        elif compact:
            format_used = "compact"
        else:
            format_used = "full"

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

        # Collect date facts; cap at 10 in compact mode to avoid header bloat
        all_top_ids = direct_ids | temporal_node_ids
        max_facts = 10 if compact else None
        fact_count = 0
        for nid in all_top_ids:
            if max_facts is not None and fact_count >= max_facts:
                break
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
                    fact_count += 1
                    break  # one date per node in header

        if len(temporal_header_parts) > 1:
            header = "\n".join(temporal_header_parts)
            context_parts.append(header)
            chars_used += len(header)

        # Tier 1: Direct matches — format depends on node count and compact flag
        tier1_count = 0
        for result in top_results:
            node = self.graph.get_node(result["id"])
            if node is None:
                continue
            rel_stats = (
                relationship_data.get(result["id"])
                if relationship_data and node.get("type") == "person"
                else None
            )
            if format_used == "oneliner":
                block = _node_context_oneliner(node)
            elif format_used == "compact":
                neighbors = self.graph.get_neighbors(result["id"], depth=1)
                neighbor_names = [f"{n['title']} ({n['type']})" for n in neighbors]
                block = _node_context_compact(node, neighbor_names, relationship_stats=rel_stats)
            else:
                neighbors = self.graph.get_neighbors(result["id"], depth=1)
                neighbor_names = [f"{n['title']} ({n['type']})" for n in neighbors]
                block = _node_context_full(node, neighbor_names, relationship_stats=rel_stats)

            if chars_used + len(block) > char_budget:
                break
            context_parts.append(block)
            chars_used += len(block)
            tier1_count += 1

        # Tier 2: 1-hop neighbors — minimal in compact mode, summary otherwise
        tier2_count = 0
        if chars_used < char_budget and hop1_ids:
            context_parts.append("\n--- Connected nodes (1 hop) ---")
            for nid in sorted(hop1_ids):
                node = self.graph.get_node(nid)
                if node is None:
                    continue
                if compact:
                    block = _node_context_minimal(node)
                else:
                    neighbors = self.graph.get_neighbors(nid, depth=1)
                    neighbor_names = [f"{n['title']} ({n['type']})" for n in neighbors]
                    block = _node_context_summary(node, neighbor_names)

                if chars_used + len(block) > char_budget:
                    break
                context_parts.append(block)
                chars_used += len(block)
                tier2_count += 1

        # Tier 3: 2-hop neighbors — one-line mentions (unchanged)
        tier3_count = 0
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
                tier3_count += 1

        context = "\n".join(context_parts) if context_parts else "(No relevant nodes found in knowledge graph)"
        logger.debug(
            f"Context assembled: {len(direct_ids)} direct, {len(hop1_ids)} hop-1, "
            f"{len(hop2_ids)} hop-2, ~{chars_used // _CHARS_PER_TOKEN} tokens, "
            f"intent={intent['intent']}, compact={compact}, format={format_used}"
        )

        return {
            "context": context,
            "top_results": top_results,
            "intent": intent,
            "date_range": date_range,
            "domains": relevant_domains,
            "candidates": candidates_debug,
            "nodes_in_context": {"tier1": tier1_count, "tier2": tier2_count, "tier3": tier3_count},
            "context_length_chars": chars_used,
            "token_budget": token_budget,
            "format_used": format_used,
        }

    def get_context(
        self,
        query: str,
        conversation_history: list[dict] | None = None,
        relationship_data: dict | None = None,
    ) -> tuple[str, list[dict]]:
        """Hybrid retrieval: semantic search + domain filtering + 2-hop traversal + tiered assembly.

        If conversation_history is provided, recent user messages boost relevance of
        nodes mentioned in the ongoing conversation.

        If relationship_data is provided (keyed by person_id), person nodes in
        the assembled context are enriched with mention stats.

        Returns (context_string, search_results).
        """
        result = self._retrieve(query, conversation_history, relationship_data=relationship_data)
        return result["context"], result["top_results"]

    def get_context_debug(self, query: str, conversation_history: list[dict] | None = None) -> dict:
        """Run the retrieval pipeline and return full diagnostic state for debugging.

        Returns a dict with intent classification, per-candidate scoring breakdown,
        and context assembly statistics suitable for the /api/debug/retrieval endpoint.
        """
        result = self._retrieve(query, conversation_history)
        date_range = result["date_range"]
        return {
            "query": query,
            "intent": result["intent"],
            "date_range": (
                [date_range[0].isoformat(), date_range[1].isoformat()]
                if date_range else None
            ),
            "domains": result["domains"],
            "candidates": result["candidates"],
            "context_length_chars": result["context_length_chars"],
            "context_length_tokens_est": result["context_length_chars"] // _CHARS_PER_TOKEN,
            "nodes_in_context": result["nodes_in_context"],
            "token_budget": result.get("token_budget", _BASE_TOKEN_BUDGET),
            "format_used": result.get("format_used", "full"),
        }

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
    def _build_relationship_alerts(
        drifting: list[dict],
        neglected: list[dict],
        high_influence: list[dict],
    ) -> str:
        """Build the RELATIONSHIP DRIFT and HIGH INFLUENCE alert sections.

        Caps at 2 drifting/neglected combined + 1 high-influence.
        High-influence only fires for mostly_negative or mixed context profiles.
        Returns empty string if nothing to surface.
        """
        # Combine drifting + neglected (drifting first) and cap at 2
        all_drift = (list(drifting) + list(neglected))[:2]

        # Only flag high-influence persons with concerning context profiles
        concerning_influence = [
            r for r in high_influence
            if r.get("context_profile") in ("mostly_negative", "mixed")
            and r.get("mention_count", 0) >= 5
        ][:1]

        if not all_drift and not concerning_influence:
            return ""

        lines: list[str] = []

        if all_drift:
            lines.append("\nRELATIONSHIP DRIFT — these people may be falling out of touch:")
            for r in all_drift:
                title = r.get("person_title", r["person_id"])
                rel = r.get("relationship") or "person"
                freq = r.get("expected_frequency") or "unknown"
                days = r.get("days_since_mention", 0) or 0
                drift = r.get("drift_days", 0)
                ctx = r.get("mention_contexts", {})
                pos = ctx.get("positive", 0)
                neg = ctx.get("negative", 0)
                health = r.get("health", "drifting")

                line = (
                    f'- "{title}" ({rel}, expected: {freq}) — '
                    f'last mentioned {days} day{"s" if days != 1 else ""} ago, '
                    f'{drift} day{"s" if drift != 1 else ""} past expected.'
                )
                lines.append(line)

                if health == "neglected":
                    lines.append(
                        "  Only raise if directly relevant to what the user is discussing. Don't guilt-trip."
                    )
                else:
                    if pos > 0 or neg > 0:
                        profile = r.get("context_profile", "neutral").replace("_", " ")
                        lines.append(
                            f"  Normally a {profile} relationship ({pos} positive, {neg} negative mentions this month)."
                        )
                    lines.append(
                        "  Surface this gently when the user mentions anything social. Don't nag."
                    )

        if concerning_influence:
            lines.append("\nHIGH INFLUENCE — the people on the user's mind most:")
            for r in concerning_influence:
                title = r.get("person_title", r["person_id"])
                count = r.get("mention_count", 0)
                profile = r.get("context_profile", "neutral").replace("_", " ")
                lines.append(
                    f'- "{title}" mentioned {count} time{"s" if count != 1 else ""} this month '
                    f"(mostly in {profile} contexts) — may be worth exploring."
                )
                lines.append(
                    "  The user keeps bringing this person up. Consider exploring the dynamic when relevant."
                )

        return "\n".join(lines)

    @staticmethod
    def _build_proactive_alerts(alerts: dict, state: dict | None = None) -> str:
        """Build the PROACTIVE ALERTS injection for the system prompt.

        Returns an empty string when there is nothing to surface.
        Caps at 5 broken streaks + 3 overdue commitments to avoid prompt bloat.
        State-aware suppression: elevated stress → 1 total alert; low energy → 2 total.
        Break habits in 'early' status always shown regardless of stress.
        """
        broken = alerts.get("broken_streaks", [])[:5]
        at_risk = alerts.get("at_risk_streaks", [])
        overdue = alerts.get("overdue_commitments", [])[:3]
        neglected = alerts.get("neglected_fundamentals", [])[:3]
        untracked = alerts.get("untracked_fundamentals", [])[:2]
        drifting_rels = alerts.get("drifting_relationships", [])[:2]
        neglected_rels = alerts.get("neglected_relationships", [])[:2]
        high_influence = alerts.get("high_influence", [])[:3]
        # Break habits (strong already filtered by _build_alerts)
        break_habits = list(alerts.get("break_habits", []))
        # Periodic habits (upcoming/overdue only)
        periodic_habits = list(alerts.get("periodic_habits", []))

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
                drifting_rels = []
                neglected_rels = []
                high_influence = []
                # Break habits: always show 'early' status even when stressed; suppress others
                break_habits = [b for b in break_habits if b.get("streak_status") == "early"]
                periodic_habits = []
            else:
                remaining = max(0, total_cap - len(overdue) - len(broken) - len(at_risk))
                neglected = neglected[:remaining]
                remaining = max(0, remaining - len(neglected))
                untracked = untracked[:remaining]
                remaining = max(0, remaining - len(untracked))
                drifting_rels = drifting_rels[:remaining]
                remaining = max(0, remaining - len(drifting_rels))
                neglected_rels = neglected_rels[:remaining]
                remaining = max(0, remaining - len(neglected_rels))
                high_influence = high_influence[:remaining]

        rel_section = MentorAgent._build_relationship_alerts(
            drifting_rels, neglected_rels, high_influence
        )

        if (
            not broken and not at_risk and not overdue
            and not neglected and not untracked
            and not rel_section
            and not break_habits and not periodic_habits
        ):
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

        if break_habits:
            lines.append("\nBREAK HABITS:")
            for s in break_habits:
                title = s["habit_title"]
                bstatus = s.get("streak_status", "unknown")
                days_clean = s.get("days_clean")
                if bstatus == "relapsed":
                    lines.append(f'- "{title}" — mentioned today. Day 0.')
                elif bstatus == "unknown" or days_clean is None:
                    lines.append(f'- "{title}" — no usage data. Can\'t determine days clean.')
                else:
                    days_str = f"{days_clean} day{'s' if days_clean != 1 else ''} clean"
                    if bstatus == "early":
                        lines.append(f'- "{title}" — {days_str}. Stay strong — early window is fragile.')
                    else:
                        lines.append(f'- "{title}" — {days_str}. Keep going.')

        if periodic_habits:
            lines.append("\nPERIODIC HABITS:")
            for s in periodic_habits:
                title = s["habit_title"]
                pstatus = s.get("streak_status", "no_data")
                last = s.get("last_completed")
                next_due = s.get("next_due")
                days_until = s.get("days_until_due")
                if pstatus == "overdue":
                    overdue_days = abs(days_until) if days_until is not None else "?"
                    last_str = _format_periodic_date(last) if last else "unknown"
                    lines.append(
                        f'- "{title}" — last completed {last_str}. Overdue by {overdue_days} day{"s" if overdue_days != 1 else ""}.'
                    )
                elif pstatus == "upcoming":
                    next_str = _format_periodic_date(next_due) if next_due else "soon"
                    lines.append(f'- "{title}" — Next due: {next_str} ({days_until} days).')
                else:
                    next_str = _format_periodic_date(next_due) if next_due else "unknown"
                    days_str = f"{days_until} days" if days_until is not None else "?"
                    lines.append(f'- "{title}" — Next due: {next_str} ({days_str}).')

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

        if rel_section:
            lines.append(rel_section)

        return "\n".join(lines)

    def chat_stream(self, message: str, conversation_history: list[dict],
                    dismissed_ids: list[str] | None = None,
                    conflicts: list[dict] | None = None,
                    mode: str = "mirror",
                    challenges: dict | None = None,
                    alerts: dict | None = None,
                    state: dict | None = None):
        """Streaming version of chat(). Yields (event_type, data) tuples.

        Events:
          ("text", {"content": str})  — streamed text token
          ("done", {response, full_response, graph_updates, relevant_nodes})
        """
        relationship_data = (alerts or {}).get("_relationships_by_id")
        context, search_results = self.get_context(
            message, conversation_history, relationship_data=relationship_data
        )
        today = date.today().strftime("%A %d %B %Y")
        system = self.system_prompt_template.format(context=context, today=today)
        system += self._build_dismissed_note(dismissed_ids or [])
        system += self._build_state_note(state or {})
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
             state: dict | None = None) -> dict:
        """Send a message with conversation history, get a response with graph update proposals.

        conversation_history: list of {role, content} dicts from the chat store.
        dismissed_ids: node IDs the user dismissed this session — injected into prompt.
        conflicts: detected conflicts between the message and existing graph nodes.
        mode: communication mode (mirror/advisor/guardian/dialectic).
        challenges: active challenge ladder states keyed by node_id.
        alerts: proactive accountability alerts (broken streaks, overdue commitments).
        state: inferred user state (energy, stress, confidence) from recent messages.
        """
        relationship_data = (alerts or {}).get("_relationships_by_id")
        context, search_results = self.get_context(
            message, conversation_history, relationship_data=relationship_data
        )
        today = date.today().strftime("%A %d %B %Y")
        system = self.system_prompt_template.format(context=context, today=today)
        system += self._build_dismissed_note(dismissed_ids or [])
        system += self._build_state_note(state or {})
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
