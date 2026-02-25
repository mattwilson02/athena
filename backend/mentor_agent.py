"""Claude-powered mentor with graph-aware context retrieval."""

import json
import os
import re
import logging

import anthropic

from vault_graph import VaultGraph
from vector_search import VectorIndex

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are Athena, a personal AI mentor. You have deep knowledge of the user's \
goals, fears, habits, values, beliefs, relationships, and experiences through their knowledge graph.

CONTEXT FROM KNOWLEDGE GRAPH:
{context}

INSTRUCTIONS:
- Reference specific nodes by name when relevant. Say "your goal to Get Promoted" not "your goals."
- Think systemically — connect dots between nodes the user might not see.
- Challenge constructively — don't just agree.
- Be concise but substantive. No filler.
- If the user has read books relevant to the conversation, use frameworks from those books.
- When you identify new knowledge that should be added to the graph, include it in a \
<graph_updates> block at the end of your response.

GRAPH UPDATE FORMAT (only when you have genuine new insights to propose):
<graph_updates>
[
  {{
    "action": "create",
    "node_id": "suggested-slug-id",
    "type": "goal|fear|belief|habit|skill|value|interest|experience",
    "title": "Human Readable Title",
    "content": "Description of the node.",
    "tags": ["tag1", "tag2"],
    "edges": [
      {{"target": "existing-node-id", "type": "relates_to|blocked_by|supported_by|contradicts|inspired_by|involves"}}
    ]
  }},
  {{
    "action": "link",
    "source": "existing-node-id",
    "target": "another-node-id",
    "type": "edge_type"
  }}
]
</graph_updates>

Do NOT include <graph_updates> unless you have a genuine new insight to propose. \
Only propose nodes/links that add real value to the user's self-understanding."""

GRAPH_UPDATES_RE = re.compile(r"<graph_updates>\s*(.*?)\s*</graph_updates>", re.DOTALL)


class MentorAgent:
    """Claude-powered mentor with hybrid retrieval and conversation memory."""

    def __init__(self, graph: VaultGraph, vector_index: VectorIndex) -> None:
        self.graph = graph
        self.vector_index = vector_index
        self.client = anthropic.Anthropic()
        self.model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")
        self.conversation: list[dict] = []

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

    def chat(self, message: str) -> dict:
        """Send a message, get a response with graph update proposals."""
        context, search_results = self.get_context(message)

        system = SYSTEM_PROMPT.format(context=context)
        self.conversation.append({"role": "user", "content": message})

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=2048,
                system=system,
                messages=self.conversation,
            )
            assistant_text = response.content[0].text
        except anthropic.APIError as e:
            logger.error(f"Claude API error: {e}")
            # Remove the user message we just added since the call failed
            self.conversation.pop()
            raise

        clean_text, graph_updates = self._parse_graph_updates(assistant_text)

        # Store full response (with graph_updates) in history so Claude has context
        self.conversation.append({"role": "assistant", "content": assistant_text})

        return {
            "response": clean_text,
            "graph_updates": graph_updates,
            "relevant_nodes": [
                {"id": r["id"], "title": r.get("title", r["id"]), "type": r.get("type", "unknown")}
                for r in search_results
            ],
        }

    def reset(self) -> None:
        """Clear conversation history."""
        self.conversation = []

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
