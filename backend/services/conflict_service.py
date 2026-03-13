"""Conflict detection — finds contradictions between user intent and existing graph nodes."""

from __future__ import annotations

import logging
from datetime import date, datetime

logger = logging.getLogger(__name__)

# Statuses that indicate a node is no longer active — skip these.
_RESOLVED_STATUSES = {
    "completed", "done", "achieved", "overcome",
    "abandoned", "superseded", "cancelled", "attended",
}

_STOP_WORDS = {
    "the", "a", "an", "my", "to", "and", "or", "i", "is", "it",
    "of", "in", "for", "this", "that", "with", "on", "at", "be",
}

# ── Intention signal patterns ──

_ACTION_INTENTIONS = [
    "i'm going to", "im going to", "i am going to",
    "i want to", "i wanna",
    "planning to", "i plan to",
    "i'll", "i will",
    "i've decided to", "ive decided to", "i decided to",
    "thinking about", "thinking of",
]

_NEGATION_SIGNALS = [
    "skip", "quit", "stop", "cancel", "drop", "give up",
    "not going to", "won't", "wont", "can't be bothered",
    "don't want to", "dont want to", "scrap", "forget about",
    "changed my mind", "pull out", "bail",
]

_CHANGE_SIGNALS = [
    "instead", "actually", "changed my mind", "rather",
]

_SPENDING_SIGNALS = [
    "buy", "spend", "order", "subscribe", "book", "pay for",
    "splurge", "purchase", "treat myself",
]

_AVOIDANCE_SIGNALS = [
    "avoid", "skip", "not going to", "can't face", "cant face",
    "too scared", "not ready", "put off", "postpone", "dodge",
]

# Types eligible for conflict checking
_SELF_TYPES = {"value", "goal", "habit", "belief", "fear", "skill"}
_PLANNING_TYPES = {"event", "task", "project"}
_CONFLICT_TYPES = _SELF_TYPES | _PLANNING_TYPES | {"budget", "expense", "subscription"}

MAX_CONFLICTS = 5


def detect_conflicts(
    message: str,
    graph,
    vector_index,
    schema: dict | None = None,
) -> list[dict]:
    """Detect conflicts between a user message and existing graph nodes.

    Returns a list of conflict dicts, sorted by severity (hard first), capped at 5.
    """
    message_lower = message.lower()

    # Stage 1: Check for intention signals
    signals = _detect_signals(message_lower)
    if not signals["has_intention"]:
        return []

    # Stage 2: Gather candidates from graph
    candidates = _gather_candidates(graph)

    # Stage 3: Semantic similarity — merge additional candidates
    semantic_ids = set()
    if vector_index:
        try:
            results = vector_index.search(message, n=20)
            for r in results:
                if r["type"] in _CONFLICT_TYPES and r.get("score", 999) < 0.8:
                    rid = r["id"]
                    semantic_ids.add(rid)
                    # Add semantic results not already in candidates
                    if rid not in {c["id"] for c in candidates}:
                        node = graph.get_node(rid)
                        if node:
                            candidates.append(node)
        except Exception:
            pass  # vector search failure shouldn't block conflict detection

    # Stage 4: Classify conflicts
    conflicts = []
    seen_ids = set()

    for node in candidates:
        nid = node["id"]
        if nid in seen_ids:
            continue

        result = _classify_conflict(message_lower, node, signals)
        if result is None:
            continue

        seen_ids.add(nid)
        conflicts.append({
            "node_id": nid,
            "title": node.get("title", nid),
            "type": node.get("type", "unknown"),
            **result,
        })

    # Sort: hard first, then soft
    conflicts.sort(key=lambda c: (0 if c["severity"] == "hard" else 1))
    return conflicts[:MAX_CONFLICTS]


def _detect_signals(message_lower: str) -> dict:
    """Detect what kind of intention signals are in the message."""
    has_action = any(s in message_lower for s in _ACTION_INTENTIONS)
    has_negation = any(s in message_lower for s in _NEGATION_SIGNALS)
    has_change = any(s in message_lower for s in _CHANGE_SIGNALS)
    has_spending = any(s in message_lower for s in _SPENDING_SIGNALS)

    return {
        "has_intention": has_action or has_negation or has_change or has_spending,
        "has_negation": has_negation,
        "has_spending": has_spending,
    }


def _gather_candidates(graph) -> list[dict]:
    """Gather active nodes from graph that could conflict."""
    candidates = []

    # Self-domain nodes
    for node_type in ("value", "goal", "habit", "belief", "fear"):
        for node in graph.get_nodes_by_type(node_type):
            status = node.get("status", "active")
            if status in _RESOLVED_STATUSES:
                continue
            candidates.append(node)

    # Planning nodes with deadlines/dates
    for node_type in ("event", "task", "project"):
        for node in graph.get_nodes_by_type(node_type):
            status = node.get("status", "active")
            if status in _RESOLVED_STATUSES:
                continue
            candidates.append(node)

    # Budget nodes (always included — no status filter)
    for node in graph.get_nodes_by_type("budget"):
        candidates.append(node)

    return candidates


def _extract_topic_words(title: str, content: str) -> set[str]:
    """Extract meaningful words from a node's title and first line of content."""
    first_line = content.split("\n")[0] if content else ""
    text = f"{title} {first_line}".lower()
    return {w for w in text.split() if w not in _STOP_WORDS and len(w) > 2}


def _classify_conflict(message_lower: str, node: dict, signals: dict) -> dict | None:
    """Classify whether a message conflicts with a node. Returns conflict dict or None."""
    node_type = node.get("type")
    title = node.get("title", "")
    has_negation = signals["has_negation"]
    has_spending = signals["has_spending"]

    # Topic matching — does the message reference this node's subject?
    topic_words = _extract_topic_words(title, node.get("content", ""))
    topic_match = any(w in message_lower for w in topic_words if len(w) > 2)

    # Budget: any spending triggers them (no topic match needed)
    # Events/tasks/projects: check date overlap even without topic match
    if not topic_match and node_type not in ("budget", "event", "task", "project"):
        return None

    # ── Identity conflicts ──

    if node_type == "value" and has_negation and topic_match:
        priority = node.get("priority", "important")
        return {
            "conflict_type": "value_violation",
            "severity": "hard" if priority in ("core", "important") else "soft",
            "explanation": f"This contradicts your stated value of {title}",
        }

    if node_type == "goal" and topic_match:
        if has_negation:
            priority = node.get("priority", "medium")
            return {
                "conflict_type": "goal_contradiction",
                "severity": "hard" if priority == "high" else "soft",
                "explanation": f"This undermines your active goal: {title}",
            }
        # Financial goal vs spending
        if has_spending:
            content_lower = (node.get("content", "") + " " + title).lower()
            financial_words = ("save", "budget", "spend less", "frugal", "cut costs", "reduce spending")
            if any(w in content_lower for w in financial_words):
                return {
                    "conflict_type": "financial_contradiction",
                    "severity": "soft",
                    "explanation": f"This spending may conflict with your goal: {title}",
                }

    if node_type == "habit" and has_negation and topic_match:
        frequency = node.get("frequency", "weekly")
        return {
            "conflict_type": "habit_break",
            "severity": "hard" if frequency == "daily" else "soft",
            "explanation": f"This breaks your active habit: {title} ({frequency})",
        }

    if node_type == "belief" and has_negation and topic_match:
        return {
            "conflict_type": "belief_contradiction",
            "severity": "soft",
            "explanation": f"This contradicts your belief: {title}",
        }

    if node_type == "fear" and topic_match:
        status = node.get("status", "active")
        if status in ("active", "managed") and _is_avoidance(message_lower):
            return {
                "conflict_type": "fear_avoidance",
                "severity": "soft",
                "explanation": f"This may be reinforcing your fear: {title}",
            }

    # ── Planning conflicts ──

    if node_type == "event" and _has_date_overlap(message_lower, node):
        has_people = bool(node.get("people", []))
        return {
            "conflict_type": "commitment_to_person" if has_people else "schedule_conflict",
            "severity": "hard" if has_people else "soft",
            "explanation": f"You have \"{title}\" scheduled around the same time",
        }

    if node_type in ("task", "project"):
        priority = node.get("priority", "medium")
        due = node.get("due") or node.get("deadline")
        if priority == "high" and due and _is_overdue(due):
            return {
                "conflict_type": "priority_inversion",
                "severity": "soft",
                "explanation": f"Your high-priority {node_type} \"{title}\" is overdue (due {due})",
            }

    # ── Financial conflicts ──

    if node_type == "budget" and has_spending:
        return {
            "conflict_type": "budget_breach",
            "severity": "soft",
            "explanation": f"Check this against your {title} budget",
        }

    return None


def _is_avoidance(message_lower: str) -> bool:
    """Check if message contains avoidance language."""
    return any(s in message_lower for s in _AVOIDANCE_SIGNALS)


def _has_date_overlap(message_lower: str, event_node: dict) -> bool:
    """Check if message mentions a time that overlaps with an event's date."""
    event_date = event_node.get("date", "")
    if not event_date:
        return False
    try:
        dt = datetime.fromisoformat(str(event_date).split("T")[0])
        month_name = dt.strftime("%B").lower()
        day = str(dt.day)
        # Check for "april 15", "april", or "15th"
        if f"{month_name} {day}" in message_lower:
            return True
        if month_name in message_lower:
            return True
    except (ValueError, TypeError):
        pass
    return False


def _is_overdue(due_str: str) -> bool:
    """Check if a due date is in the past."""
    try:
        due = datetime.fromisoformat(str(due_str).split("T")[0]).date()
        return due < date.today()
    except (ValueError, TypeError):
        return False
