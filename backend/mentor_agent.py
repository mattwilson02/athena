"""Claude-powered AI assistant with graph-aware context retrieval."""

from __future__ import annotations

import json
import os
import re
import logging
from datetime import date, datetime

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
                   "concept", "insight", "research", "remember"],
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

# Rough token budget for the context window.
_MAX_CONTEXT_TOKENS = 3000
_CHARS_PER_TOKEN = 4  # conservative estimate


# ── Soul loader ──


def _load_soul(vault_path: str | None = None) -> tuple[str, str]:
    """Load Athena's identity and instructions from SOUL.md.

    Parses the markdown sections into an identity block and an instructions block.
    Falls back to minimal defaults if the file is missing.
    """
    # Look for SOUL.md in the project root (one level up from backend/)
    search_paths = []
    if vault_path:
        search_paths.append(os.path.join(vault_path, "..", "SOUL.md"))
    search_paths.append(os.path.join(os.path.dirname(__file__), "..", "SOUL.md"))

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
    identity_parts.append("TODAY: {today}")
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

    logger.info(f"Soul loaded from {soul_path} ({len(sections)} sections)")
    return identity, instructions

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


_DEDUP_RULES = """\
DEDUPLICATION:
- Before proposing a "create", check the CONTEXT above for existing nodes that match.
- If a person, place, or concept already exists in the graph, use "update" or "link" instead of \
creating a duplicate.
- When updating an existing node, use the "update" action with its existing node_id."""

_FORMAT_SPEC = """\
GRAPH UPDATE FORMAT:
CRITICAL: The "type" field MUST be one of these exact values: {type_enum}
Do NOT invent new types. If nothing fits perfectly, pick the closest match.
TYPE PRIORITY: Prefer specific types over generic ones. "note" and "idea" are LAST RESORT — most \
information fits a more specific type. A project update is a "project", a salary fact is a "budget", \
a workout plan is a "habit", a life lesson is a "belief" or "value". Only use "note" when nothing \
else genuinely fits.

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
      {{"target": "existing-node-id", "type": "relates_to|blocked_by|supported_by|contradicts|inspired_by|involves|part_of|located_in|funded_by|met_at"}}
    ]
  }},
  {{
    "action": "update",
    "node_id": "existing-node-id",
    "changes": {{
      "title": "New Title (only if renaming the node)",
      "content": "Full replacement text (replaces body, use only when rewriting)",
      "frontmatter": {{"company": "Google"}},
      "append_content": "New information to add (appends, does not replace).",
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

Always propose edges to connect new nodes to existing ones. A node without edges is a missed \
opportunity. Err on the side of proposing — the user can always dismiss."""


def build_system_prompt(schema: dict, vault_path: str | None = None) -> str:
    """Assemble the full system prompt from SOUL.md + parsed schema."""
    identity, instructions = _load_soul(vault_path)
    type_rules = generate_type_rules(schema)
    type_enum = "|".join(schema["type_list"])
    format_spec = _FORMAT_SPEC.replace("{type_enum}", type_enum)

    return "\n\n".join([
        identity,
        instructions,
        type_rules,
        _DEDUP_RULES,
        format_spec,
    ])


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


def _node_context_full(node: dict, neighbor_names: list[str]) -> str:
    """Full context string for a direct-match node."""
    parts = [
        f"--- {node.get('title', node['id'])} ({node.get('type', 'unknown')}) ---",
        f"ID: {node['id']}",
    ]
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


class MentorAgent:
    """Claude-powered AI assistant with hybrid retrieval. Stateless — conversation
    history is passed in from the chat store."""

    def __init__(self, graph: VaultGraph, vector_index: VectorIndex, schema: dict,
                 vault_path: str | None = None) -> None:
        self.graph = graph
        self.vector_index = vector_index
        self.schema = schema
        self.system_prompt_template = build_system_prompt(schema, vault_path)
        self.client = anthropic.Anthropic()
        self.model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")
        logger.info(f"System prompt built from schema ({len(self.system_prompt_template)} chars)")

    def get_context(self, query: str) -> tuple[str, list[dict]]:
        """Hybrid retrieval: semantic search + domain filtering + 2-hop traversal + tiered assembly.

        Returns (context_string, search_results).
        """
        # Step 1: Classify query domains
        relevant_domains = _classify_domains(query)

        # Step 2: Semantic search — fetch more candidates, we'll rank them
        search_results = self.vector_index.search(query, n=10)
        if not search_results:
            return "(No relevant nodes found in knowledge graph)", []

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

            # Centrality boost — well-connected nodes are more important
            degree = self.graph.get_degree(result["id"])
            centrality_boost = min(degree * 0.03, 0.15)

            total = semantic_score + domain_boost + recency_boost + centrality_boost
            scored.append((total, result))

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

    def chat(self, message: str, conversation_history: list[dict]) -> dict:
        """Send a message with conversation history, get a response with graph update proposals.

        conversation_history: list of {role, content} dicts from the chat store.
        """
        context, search_results = self.get_context(message)
        today = date.today().strftime("%A %d %B %Y")
        system = self.system_prompt_template.format(context=context, today=today)

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
