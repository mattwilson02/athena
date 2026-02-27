"""Chat message orchestration — dedup check, mentor call, session persistence."""

from __future__ import annotations

import logging

import anthropic

logger = logging.getLogger(__name__)


class ChatService:
    """Orchestrates chat flow: session → mentor → dedup → save."""

    def __init__(self, chat_store, mentor, graph, vector_index):
        self.chat_store = chat_store
        self.mentor = mentor
        self.graph = graph
        self.vector_index = vector_index

    def send_message(self, session_id: str, message: str) -> dict:
        """Process a user message. Returns {response, graph_updates, relevant_nodes} or {error}."""
        if self.mentor is None:
            return {"error": "ANTHROPIC_API_KEY not configured", "status": 503}

        session = self.chat_store.get_session(session_id)
        if session is None:
            return {"error": "Session not found", "status": 404}

        # Save user message
        self.chat_store.append_message(session_id, {"role": "user", "content": message})

        # Get conversation history (exclude the message we just added — mentor.chat() adds it)
        history = self.chat_store.get_messages_for_api(session_id)
        history = history[:-1]

        try:
            result = self.mentor.chat(message, history)
        except anthropic.AuthenticationError:
            return {"error": "Invalid API key. Check your ANTHROPIC_API_KEY.", "status": 401}
        except anthropic.RateLimitError:
            return {"error": "Rate limited. Slow down and try again shortly.", "status": 429}
        except anthropic.APIStatusError as e:
            if e.status_code == 529:
                return {"error": "Claude is overloaded right now. Try again in a few seconds.", "status": 529}
            logger.error(f"Claude API error: {e}")
            return {"error": "Something went wrong talking to Claude. Try again.", "status": 502}
        except anthropic.APIError as e:
            logger.error(f"Claude API error: {e}")
            return {"error": "Something went wrong talking to Claude. Try again.", "status": 502}

        # Post-process: dedup check on create proposals
        graph_updates = self._dedup_check(result["graph_updates"])

        # Save assistant message
        self.chat_store.append_message(session_id, {
            "role": "assistant",
            "content": result["full_response"],
            "graph_updates": graph_updates,
            "relevant_nodes": result["relevant_nodes"],
        })

        return {
            "response": result["response"],
            "graph_updates": graph_updates,
            "relevant_nodes": result["relevant_nodes"],
        }

    def stream_message(self, session_id: str, message: str):
        """Streaming version of send_message. Yields (event_type, data) tuples."""
        if self.mentor is None:
            yield ("error", {"error": "ANTHROPIC_API_KEY not configured"})
            return

        session = self.chat_store.get_session(session_id)
        if session is None:
            yield ("error", {"error": "Session not found"})
            return

        self.chat_store.append_message(session_id, {"role": "user", "content": message})
        history = self.chat_store.get_messages_for_api(session_id)
        history = history[:-1]

        try:
            for event_type, data in self.mentor.chat_stream(message, history):
                if event_type == "text":
                    yield ("text", data)
                elif event_type == "done":
                    graph_updates = self._dedup_check(data["graph_updates"])
                    self.chat_store.append_message(session_id, {
                        "role": "assistant",
                        "content": data["full_response"],
                        "graph_updates": graph_updates,
                        "relevant_nodes": data["relevant_nodes"],
                    })
                    yield ("done", {
                        "response": data["response"],
                        "graph_updates": graph_updates,
                        "relevant_nodes": data["relevant_nodes"],
                    })
        except anthropic.AuthenticationError:
            yield ("error", {"error": "Invalid API key. Check your ANTHROPIC_API_KEY."})
        except anthropic.RateLimitError:
            yield ("error", {"error": "Rate limited. Slow down and try again shortly."})
        except anthropic.APIStatusError as e:
            if e.status_code == 529:
                yield ("error", {"error": "Claude is overloaded right now. Try again in a few seconds."})
            else:
                logger.error(f"Claude API error: {e}")
                yield ("error", {"error": "Something went wrong talking to Claude. Try again."})
        except anthropic.APIError as e:
            logger.error(f"Claude API error: {e}")
            yield ("error", {"error": "Something went wrong talking to Claude. Try again."})

    def _dedup_check(self, updates: list[dict]) -> list[dict]:
        """Annotate create actions with potential duplicate info."""
        enriched = []
        for update in updates:
            if update.get("action") != "create":
                enriched.append(update)
                continue

            node_id = update.get("node_id", "")
            title = update.get("title", "")
            node_type = update.get("type", "")

            existing = self.graph.get_node(node_id)
            if existing:
                update["_duplicate"] = {
                    "match": "exact_id",
                    "existing_id": existing["id"],
                    "existing_title": existing.get("title", existing["id"]),
                    "existing_type": existing.get("type", "unknown"),
                }
                enriched.append(update)
                continue

            matches = self.vector_index.find_duplicates(node_id, title, node_type)
            if matches:
                update["_duplicate"] = {
                    "match": matches[0]["match"],
                    "existing_id": matches[0]["id"],
                    "existing_title": matches[0]["title"],
                    "existing_type": matches[0]["type"],
                    "score": matches[0].get("score", 0),
                    "alternatives": matches[1:] if len(matches) > 1 else [],
                }

            enriched.append(update)

        return enriched
