"""Chat message orchestration — dedup check, mentor call, session persistence."""

from __future__ import annotations

import logging
import time

import anthropic

from datetime import date, datetime, timezone

from mentor_agent import classify_mode, _get_permanence
from services.conflict_service import detect_conflicts
from services.state_service import infer_state
from services.vault_service import _PERMANENCE_WARNINGS

logger = logging.getLogger(__name__)


class ChatService:
    """Orchestrates chat flow: session → mentor → dedup → save."""

    # Throttle relationship persistence to at most once per hour across all instances
    _last_relationship_persist: float = 0.0

    def __init__(self, chat_store, mentor, graph, vector_index, vault_service=None):
        self.chat_store = chat_store
        self.mentor = mentor
        self.graph = graph
        self.vector_index = vector_index
        self.vault_service = vault_service

    def _detect_conflicts(self, message: str) -> list[dict]:
        """Run conflict detection against the graph."""
        try:
            return detect_conflicts(message, self.graph, self.vector_index)
        except Exception:
            logger.exception("Conflict detection failed")
            return []

    def _build_alerts(self) -> dict:
        """Compute accountability alerts (broken streaks, overdue commitments, fundamentals, relationships)."""
        try:
            from services.accountability_service import (
                calculate_streaks, find_overdue_commitments, check_fundamentals,
            )
            streaks = calculate_streaks(self.graph)
            overdue = find_overdue_commitments(self.graph, date.today())
            fundamentals = check_fundamentals(self.graph, date.today())
            alerts = {
                "broken_streaks": [s for s in streaks if s["streak_status"] == "broken"],
                "at_risk_streaks": [s for s in streaks if s["streak_status"] == "at_risk"],
                "overdue_commitments": overdue,
                "neglected_fundamentals": [f for f in fundamentals if f["status"] == "neglected"],
                "untracked_fundamentals": [f for f in fundamentals if f["status"] == "no_data"],
            }
        except Exception:
            logger.exception("Accountability service failed — proceeding with empty alerts")
            alerts = {}

        try:
            from services.relationship_service import scan_mentions, assess_relationship_health
            mention_stats = scan_mentions(self.chat_store, self.graph)
            relationships = assess_relationship_health(mention_stats, date.today())
            alerts["drifting_relationships"] = [r for r in relationships if r["health"] == "drifting"]
            alerts["neglected_relationships"] = [r for r in relationships if r["health"] == "neglected"]
            alerts["high_influence"] = [
                r for r in relationships
                if r["influence_rank"] <= 3 and r["mention_count"] >= 5
            ]
            # Store by ID for person context enrichment in retrieval
            alerts["_relationships_by_id"] = {r["person_id"]: r for r in relationships}
            # Store full list for persistence throttle check
            alerts["_all_relationships"] = relationships
        except Exception:
            logger.exception("Relationship service failed — proceeding without relationship alerts")

        return alerts

    def _maybe_persist_relationships(self, relationships: list[dict]) -> None:
        """Persist mention stats to person nodes, throttled to at most once per hour."""
        if not relationships or self.vault_service is None:
            return
        now = time.time()
        if now - ChatService._last_relationship_persist < 3600:
            return
        try:
            from services.relationship_service import persist_mention_stats
            persist_mention_stats(self.vault_service, relationships, date.today())
            ChatService._last_relationship_persist = now
        except Exception:
            logger.exception("Relationship persistence failed — non-critical, skipping")

    def _infer_state(self, session_id: str) -> dict:
        """Infer user state from the current session's recent messages."""
        try:
            session = self.chat_store.get_session(session_id)
            if session is None:
                return {}
            recent_messages = session.get("messages", [])[-10:]
            return infer_state(recent_messages)
        except Exception:
            logger.exception("State inference failed — proceeding with empty state")
            return {}

    def send_message(self, session_id: str, message: str) -> dict:
        """Process a user message. Returns {response, graph_updates, relevant_nodes, conflicts} or {error}."""
        if self.mentor is None:
            return {"error": "ANTHROPIC_API_KEY not configured", "status": 503}

        session = self.chat_store.get_session(session_id)
        if session is None:
            return {"error": "Session not found", "status": 404}

        dismissed_ids = session.get("dismissed_updates", [])

        # Infer user state from recent session messages (before saving new message)
        state = self._infer_state(session_id)

        # Detect conflicts before calling the mentor
        conflicts = self._detect_conflicts(message)

        # Classify mode based on message content and detected conflicts
        try:
            mode = classify_mode(message, conflicts)
        except Exception:
            logger.exception("Mode classification failed — defaulting to mirror")
            mode = "mirror"

        # Get active challenge state for system prompt injection
        challenges = self.chat_store.get_active_challenges(session_id)

        # Build accountability alerts for proactive injection (defensive — never breaks chat)
        try:
            alerts = self._build_alerts()
        except Exception:
            logger.exception("Alert computation failed — proceeding with empty alerts")
            alerts = {}

        # Persist relationship mention stats (throttled)
        self._maybe_persist_relationships(alerts.get("_all_relationships", []))

        # Save user message
        self.chat_store.append_message(session_id, {"role": "user", "content": message})

        # Get conversation history (exclude the message we just added — mentor.chat() adds it)
        history = self.chat_store.get_messages_for_api(session_id)
        history = history[:-1]

        try:
            result = self.mentor.chat(message, history, dismissed_ids=dismissed_ids, conflicts=conflicts, mode=mode, challenges=challenges, alerts=alerts, state=state)
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
        except ConnectionError:
            logger.error("Network error reaching Claude API")
            return {"error": "Can't reach Claude right now. Check your connection.", "status": 503}

        # Post-process: dedup check + supersession validation + challenge ladder + permanence warnings + commitment enrichment
        graph_updates = self._dedup_check(result["graph_updates"])
        graph_updates = self._validate_supersession(graph_updates)
        graph_updates = self._check_challenge_triggers(session_id, graph_updates)
        graph_updates = self._annotate_permanence_warnings(graph_updates)
        graph_updates = self._enrich_commitment_metadata(graph_updates)

        # Save assistant message
        self.chat_store.append_message(session_id, {
            "role": "assistant",
            "content": result["full_response"],
            "graph_updates": graph_updates,
            "relevant_nodes": result["relevant_nodes"],
            "conflicts": conflicts,
        })

        return {
            "response": result["response"],
            "graph_updates": graph_updates,
            "relevant_nodes": result["relevant_nodes"],
            "conflicts": conflicts,
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

        dismissed_ids = session.get("dismissed_updates", [])

        # Infer user state from recent session messages (before saving new message)
        state = self._infer_state(session_id)

        # Detect conflicts before calling the mentor
        conflicts = self._detect_conflicts(message)

        # Classify mode based on message content and detected conflicts
        try:
            mode = classify_mode(message, conflicts)
        except Exception:
            logger.exception("Mode classification failed — defaulting to mirror")
            mode = "mirror"

        # Get active challenge state for system prompt injection
        challenges = self.chat_store.get_active_challenges(session_id)

        # Build accountability alerts for proactive injection (defensive — never breaks chat)
        try:
            alerts = self._build_alerts()
        except Exception:
            logger.exception("Alert computation failed — proceeding with empty alerts")
            alerts = {}

        # Persist relationship mention stats (throttled)
        self._maybe_persist_relationships(alerts.get("_all_relationships", []))

        self.chat_store.append_message(session_id, {"role": "user", "content": message})
        history = self.chat_store.get_messages_for_api(session_id)
        history = history[:-1]

        try:
            for event_type, data in self.mentor.chat_stream(message, history, dismissed_ids=dismissed_ids, conflicts=conflicts, mode=mode, challenges=challenges, alerts=alerts, state=state):
                if event_type == "text":
                    yield ("text", data)
                elif event_type == "done":
                    graph_updates = self._dedup_check(data["graph_updates"])
                    graph_updates = self._validate_supersession(graph_updates)
                    graph_updates = self._check_challenge_triggers(session_id, graph_updates)
                    graph_updates = self._annotate_permanence_warnings(graph_updates)
                    graph_updates = self._enrich_commitment_metadata(graph_updates)
                    self.chat_store.append_message(session_id, {
                        "role": "assistant",
                        "content": data["full_response"],
                        "graph_updates": graph_updates,
                        "relevant_nodes": data["relevant_nodes"],
                        "conflicts": conflicts,
                    })
                    yield ("done", {
                        "response": data["response"],
                        "graph_updates": graph_updates,
                        "relevant_nodes": data["relevant_nodes"],
                        "conflicts": conflicts,
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
        except ConnectionError:
            logger.error("Network error reaching Claude API")
            yield ("error", {"error": "Can't reach Claude right now. Check your connection."})

    def send_simple_message(self, session_id: str, message: str, max_messages: int = 200) -> dict:
        """Non-streaming message for Telegram/n8n. Returns {response} or {error}.

        Handles confirm/dismiss keywords before falling through to normal chat.
        Appends a human-readable pending update prompt to the response text.
        """
        if self.mentor is None:
            return {"error": "ANTHROPIC_API_KEY not configured", "status": 503}

        # Auto-create session for Telegram (tg-* IDs)
        session = self.chat_store.get_or_create_session(session_id)

        # Message cap check
        if max_messages and self.chat_store.message_count(session_id) >= max_messages:
            return {"error": "Session message limit reached. Start a new conversation.", "status": 429}

        # Check for confirm/dismiss keywords
        keyword = message.strip().lower()
        if keyword in ("yes", "confirm", "accept", "y"):
            return self._confirm_pending(session_id)
        if keyword in ("no", "decline", "skip", "n"):
            return self._dismiss_pending(session_id)

        # Normal chat flow (sync)
        result = self.send_message(session_id, message)
        if "error" in result:
            return result

        response_text = result["response"]
        graph_updates = result.get("graph_updates", [])

        # Queue pending updates and append confirmation prompt
        if graph_updates:
            self.chat_store.set_pending_updates(session_id, graph_updates)
            first = graph_updates[0]
            action = first.get("action", "create")
            title = first.get("title", first.get("node_id", "unknown"))
            node_type = first.get("type", "")
            prompt = f"\n\n---\nPending: {action} \"{title}\" ({node_type})"
            if len(graph_updates) > 1:
                prompt += f" + {len(graph_updates) - 1} more"
            prompt += "\nReply YES to save or NO to skip."
            response_text += prompt

        return {"response": response_text}

    def _confirm_pending(self, session_id: str) -> dict:
        """Accept the next pending graph update. Writes to vault."""
        update = self.chat_store.pop_pending_update(session_id)
        if update is None:
            return {"response": "Nothing pending to confirm."}

        if self.vault_service is None:
            return {"error": "Vault service not available", "status": 500}

        action = update.get("action", "create")
        title = update.get("title", update.get("node_id", "unknown"))

        if action == "create":
            result = self.vault_service.write({
                "node_id": update.get("node_id"),
                "title": update.get("title"),
                "type": update.get("type"),
                "content": update.get("content", ""),
                "frontmatter": update.get("frontmatter", {}),
                "edges": update.get("edges", []),
                "folder": update.get("folder"),
            })
        elif action == "update":
            result = self.vault_service.update({
                "node_id": update.get("node_id"),
                "changes": update.get("changes", {}),
            })
        elif action == "link":
            result = self.vault_service.update({
                "node_id": update.get("source", update.get("node_id")),
                "changes": {
                    "add_edges": [{"target": update.get("target"), "type": update.get("edge_type", "relates_to")}],
                },
            })
        else:
            return {"response": f"Unknown action '{action}' — skipped."}

        if result.get("error"):
            return {"response": f"Failed to {action} \"{title}\": {result['error']}"}

        # If this node was previously dismissed, remove it from dismissed list
        node_id = update.get("node_id", "")
        if node_id:
            self.chat_store.undismiss_update(session_id, node_id)

        # Queue cascade proposals as additional pending updates
        cascade = result.get("cascade_proposals", [])
        if not cascade and self.vault_service:
            node_id = update.get("node_id", "")
            changes = update.get("changes", update.get("frontmatter", {}))
            cascade = self.vault_service.cascade_check(node_id, changes)
        if cascade:
            existing = self.chat_store.get_pending_updates(session_id)
            self.chat_store.set_pending_updates(session_id, existing + cascade)

        # Check if more pending
        remaining = self.chat_store.get_pending_updates(session_id)
        reply = f"Saved: {action} \"{title}\""
        if remaining:
            nxt = remaining[0]
            nxt_title = nxt.get("title", nxt.get("node_id", "unknown"))
            nxt_type = nxt.get("type", "")
            reply += f"\n\nNext: {nxt.get('action', 'create')} \"{nxt_title}\" ({nxt_type})"
            if len(remaining) > 1:
                reply += f" + {len(remaining) - 1} more"
            reply += "\nReply YES to save or NO to skip."

        return {"response": reply}

    def _dismiss_pending(self, session_id: str) -> dict:
        """Dismiss the next pending graph update."""
        update = self.chat_store.pop_pending_update(session_id)
        if update is None:
            return {"response": "Nothing pending to dismiss."}

        title = update.get("title", update.get("node_id", "unknown"))

        # Dismiss it in the session record too
        update_key = update.get("node_id", "")
        if update_key:
            self.chat_store.dismiss_update(session_id, update_key)

        remaining = self.chat_store.get_pending_updates(session_id)
        reply = f"Skipped: \"{title}\""
        if remaining:
            nxt = remaining[0]
            nxt_title = nxt.get("title", nxt.get("node_id", "unknown"))
            nxt_type = nxt.get("type", "")
            reply += f"\n\nNext: {nxt.get('action', 'create')} \"{nxt_title}\" ({nxt_type})"
            if len(remaining) > 1:
                reply += f" + {len(remaining) - 1} more"
            reply += "\nReply YES to save or NO to skip."

        return {"response": reply}

    def _dedup_check(self, updates: list[dict]) -> list[dict]:
        """Annotate create actions with potential duplicate info.
        Also drops updates targeting non-existent nodes (AI hallucination guard).
        """
        enriched = []
        for update in updates:
            # Guard: drop updates/links targeting nodes that don't exist
            if update.get("action") in ("update", "link"):
                target_id = update.get("node_id", "")
                if target_id and not self.graph.get_node(target_id):
                    logger.warning(f"Dropped {update.get('action')} targeting non-existent node: {target_id}")
                    continue

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

    # Terminal statuses that trigger challenge when applied to identity nodes.
    _TERMINAL_STATUSES = frozenset({"abandoned", "cancelled", "superseded"})
    # Content fields on an update that can trigger the challenge.
    _CONTENT_CHANGE_KEYS = frozenset({"content", "title", "priority"})

    def _check_challenge_triggers(self, session_id: str, updates: list[dict]) -> list[dict]:
        """Scan graph_updates for identity/fundamental-level changes. Activate challenge ladder.

        Returns the updates list with:
        - `_challenge` metadata injected on triggered updates
        - steps 1-4: update removed from the returned list (held back)
        - step 5: update returned normally (challenge cleared after)
        """
        released = []
        for update in updates:
            action = update.get("action", "")

            # Only create/update actions can trigger the challenge.
            if action == "create":
                # Creating a new identity node is fine — do not challenge.
                released.append(update)
                continue

            if action != "update":
                released.append(update)
                continue

            node_id = update.get("node_id", "")
            if not node_id:
                released.append(update)
                continue

            # Determine node type: from update or existing node.
            node_type = update.get("type", "")
            if not node_type:
                existing = self.graph.get_node(node_id)
                if existing:
                    node_type = existing.get("type", "")

            level, _ = _get_permanence(node_type)
            if level not in ("identity", "fundamental"):
                released.append(update)
                continue

            # Check if this is a meaningful change.
            changes = update.get("changes", {})
            fm = changes.get("frontmatter", {})
            status = fm.get("status", "").lower() if fm.get("status") else ""
            triggers_challenge = (
                status in self._TERMINAL_STATUSES
                or bool(self._CONTENT_CHANGE_KEYS & set(changes.keys()))
                or bool(self._CONTENT_CHANGE_KEYS & set(fm.keys()))
            )

            if not triggers_challenge:
                released.append(update)
                continue

            # Get or advance challenge state.
            existing_state = self.chat_store.get_challenge_state(session_id, node_id)
            node_title = update.get("title", "")
            if not node_title:
                existing_node = self.graph.get_node(node_id)
                if existing_node:
                    node_title = existing_node.get("title", node_id)
                else:
                    node_title = node_id

            if existing_state is None:
                # Step 1 — create challenge.
                state = {
                    "step": 1,
                    "node_title": node_title,
                    "node_type": node_type,
                    "permanence": level,
                    "history": [{"step": 1, "action": "flagged", "timestamp": datetime.now(timezone.utc).isoformat()}],
                }
                self.chat_store.set_challenge_state(session_id, node_id, state)
                current_step = 1
            else:
                # Advance to next step.
                advanced = self.chat_store.advance_challenge(session_id, node_id)
                current_step = advanced["step"] if advanced else existing_state["step"]
                state = self.chat_store.get_challenge_state(session_id, node_id) or existing_state

            update["_challenge"] = {
                "step": current_step,
                "node_title": node_title,
                "permanence": level,
            }

            if current_step >= 5:
                # Step 5 — release the update and clear challenge.
                self.chat_store.clear_challenge(session_id, node_id)
                released.append(update)
            # Steps 1-4 — hold back the update (don't append to released).

        return released

    def _annotate_permanence_warnings(self, updates: list[dict]) -> list[dict]:
        """Add permanence_warning field to updates targeting identity/fundamental nodes."""
        for update in updates:
            node_type = update.get("type", "")
            if not node_type and update.get("node_id"):
                existing = self.graph.get_node(update["node_id"])
                if existing:
                    node_type = existing.get("type", "")
            level, _ = _get_permanence(node_type)
            warning = _PERMANENCE_WARNINGS.get(level)
            if warning:
                update["permanence_warning"] = warning
        return updates

    def _enrich_commitment_metadata(self, updates: list[dict]) -> list[dict]:
        """Validate and normalise commitment fields on graph updates.

        For each update with `committed_on` in frontmatter:
        - Validate it's a parseable ISO date; strip it if not.
        - Ensure `commitment_context` exists (default to empty string if absent).
        """
        from datetime import datetime as _dt
        for update in updates:
            # Normalise frontmatter location based on action.
            if update.get("action") == "create":
                fm = update.get("frontmatter", {})
            elif update.get("action") == "update":
                fm = update.get("changes", {}).get("frontmatter", {})
            else:
                continue

            if not isinstance(fm, dict):
                continue

            committed_on = fm.get("committed_on")
            if committed_on is None:
                continue

            # Validate the date string.
            try:
                _dt.fromisoformat(str(committed_on).split("T")[0])
            except (ValueError, TypeError):
                # Invalid date — strip it.
                del fm["committed_on"]
                fm.pop("commitment_context", None)
                continue

            # Ensure commitment_context exists.
            if "commitment_context" not in fm:
                fm["commitment_context"] = ""

        return updates

    def _validate_supersession(self, updates: list[dict]) -> list[dict]:
        """If a batch contains a create + an update with status: superseded,
        ensure the update has superseded_by pointing to the new node."""
        creates = {u["node_id"]: u for u in updates if u.get("action") == "create" and u.get("node_id")}

        for update in updates:
            if update.get("action") != "update":
                continue
            changes = update.get("changes", {})
            fm = changes.get("frontmatter", {})
            if fm.get("status") != "superseded":
                continue
            # Already has superseded_by? Skip.
            if fm.get("superseded_by"):
                continue

            # If exactly one create in the batch, it's the replacement
            if len(creates) == 1:
                fm["superseded_by"] = list(creates.keys())[0]
            # Multiple creates — match by edge targeting
            else:
                for cid, create in creates.items():
                    edges = create.get("edges", [])
                    if any(e.get("target") == update.get("node_id") for e in edges):
                        fm["superseded_by"] = cid
                        break

        return updates
