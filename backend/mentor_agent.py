"""Claude-powered AI assistant with graph-aware context retrieval."""

import json
import os
import re
import logging

import anthropic

from schema_parser import generate_type_rules
from vault_graph import VaultGraph
from vector_search import VectorIndex

logger = logging.getLogger(__name__)

# Template pieces — assembled into a full prompt at runtime using the parsed schema.
_IDENTITY = """\
You are Athena, a personal AI assistant that organises the user's life through a knowledge graph. \
You are part mentor, part life admin — equally comfortable discussing career anxiety, tracking a \
restaurant recommendation, or helping plan a trip.

CONTEXT FROM KNOWLEDGE GRAPH:
{context}"""

_INSTRUCTIONS = """\
INSTRUCTIONS:
- Reference specific nodes by name when relevant. Say "your goal to Get Promoted" not "your goals."
- Think systemically — connect dots between nodes the user might not see.
- Challenge constructively when relevant — don't just agree.
- Be concise but substantive. No filler.
- When the user shares information worth capturing, include it in a <graph_updates> block at the \
end of your response.
- When a single message contains multiple pieces of information (a trip with tasks, people, places), \
propose ALL relevant nodes and edges in one response. Identify the anchor (event/project) and link \
satellite nodes to it."""

_DEDUP_RULES = """\
DEDUPLICATION:
- Before proposing a "create", check the CONTEXT above for existing nodes that match.
- If a person, place, or concept already exists in the graph, use "update" or "link" instead of \
creating a duplicate.
- When updating an existing node, use the "update" action with its existing node_id."""

_FORMAT_SPEC = """\
GRAPH UPDATE FORMAT:
<graph_updates>
[
  {{
    "action": "create",
    "node_id": "suggested-slug-id",
    "type": "{type_enum}",
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
      "frontmatter": {{"company": "Google"}},
      "append_content": "New information to add.",
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


def build_system_prompt(schema: dict) -> str:
    """Assemble the full system prompt from template pieces and parsed schema."""
    type_rules = generate_type_rules(schema)
    type_enum = "|".join(schema["type_list"])
    format_spec = _FORMAT_SPEC.replace("{type_enum}", type_enum)

    return "\n\n".join([
        _IDENTITY,
        _INSTRUCTIONS,
        type_rules,
        _DEDUP_RULES,
        format_spec,
    ])

GRAPH_UPDATES_RE = re.compile(r"<graph_updates>\s*(.*?)\s*</graph_updates>", re.DOTALL)


class MentorAgent:
    """Claude-powered AI assistant with hybrid retrieval. Stateless — conversation
    history is passed in from the chat store."""

    def __init__(self, graph: VaultGraph, vector_index: VectorIndex, schema: dict) -> None:
        self.graph = graph
        self.vector_index = vector_index
        self.schema = schema
        self.system_prompt_template = build_system_prompt(schema)
        self.client = anthropic.Anthropic()
        self.model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")
        logger.info(f"System prompt built from schema ({len(self.system_prompt_template)} chars)")

    def get_context(self, query: str) -> tuple[str, list[dict]]:
        """Hybrid retrieval: semantic search + graph traversal.

        Returns (context_string, search_results).
        """
        # Step 1: Semantic search
        search_results = self.vector_index.search(query, n=5)
        matched_ids = [r["id"] for r in search_results]

        # Step 2: Graph traversal — 1-hop neighbors of each match
        all_node_ids = set(matched_ids)
        for node_id in matched_ids:
            neighbors = self.graph.get_neighbors(node_id, depth=1)
            all_node_ids.update(n["id"] for n in neighbors)

        # Step 3: Assemble context string
        context_parts = []
        for node_id in all_node_ids:
            node = self.graph.get_node(node_id)
            if node is None:
                continue
            neighbors = self.graph.get_neighbors(node_id, depth=1)
            neighbor_names = [f"{n['title']} ({n['type']})" for n in neighbors]

            part = (
                f"--- {node.get('title', node_id)} ({node.get('type', 'unknown')}) ---\n"
                f"ID: {node['id']}\n"
            )
            tags = node.get("tags", [])
            if tags:
                if isinstance(tags, list):
                    part += f"Tags: {', '.join(str(t) for t in tags)}\n"
                else:
                    part += f"Tags: {tags}\n"
            if neighbor_names:
                part += f"Connected to: {', '.join(neighbor_names)}\n"
            part += f"Content:\n{node.get('content', '(no content)')}\n"
            context_parts.append(part)

        context = "\n".join(context_parts) if context_parts else "(No relevant nodes found in knowledge graph)"
        return context, search_results

    def chat(self, message: str, conversation_history: list[dict]) -> dict:
        """Send a message with conversation history, get a response with graph update proposals.

        conversation_history: list of {role, content} dicts from the chat store.
        """
        context, search_results = self.get_context(message)
        system = self.system_prompt_template.format(context=context)

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
