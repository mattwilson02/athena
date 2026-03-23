"""Relationship intelligence service — person mention scanning, graph intelligence, social pattern detection."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime

logger = logging.getLogger(__name__)

# Sentiment signal keywords.
_POSITIVE_SIGNALS: list[str] = [
    "love", "great", "amazing", "happy", "excited", "support", "helpful",
    "proud", "fun", "enjoy",
]
_NEGATIVE_SIGNALS: list[str] = [
    "annoyed", "frustrated", "upset", "angry", "conflict", "difficult",
    "toxic", "avoid", "stressed", "worried about",
]

# Social node types that indicate active social life.
_SOCIAL_NODE_TYPES = {"event", "experience", "daily"}

# Statuses considered resolved/inactive — excluded from social activity counts.
_RESOLVED_STATUSES = {
    "completed", "done", "achieved", "overcome",
    "abandoned", "superseded", "cancelled", "attended",
}


# ── Helpers ──


def _parse_date(val) -> date | None:
    """Parse a date value from various formats. Returns None if invalid."""
    if val is None:
        return None
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.date()
    try:
        return datetime.fromisoformat(str(val).split("T")[0]).date()
    except (ValueError, TypeError):
        return None


def _extract_sentence(text: str, match_start: int, match_end: int, max_chars: int = 100) -> str:
    """Extract the sentence containing a match position, up to max_chars."""
    # Find sentence boundaries around the match
    start = max(0, match_start - max_chars // 2)
    end = min(len(text), match_end + max_chars // 2)

    # Try to expand to sentence boundaries
    sentence_start = text.rfind(".", 0, match_start)
    if sentence_start == -1:
        sentence_start = 0
    else:
        sentence_start += 1

    sentence_end = text.find(".", match_end)
    if sentence_end == -1:
        sentence_end = len(text)

    # Use sentence boundaries if they fit in budget
    snippet_start = sentence_start if (match_start - sentence_start) <= max_chars // 2 else start
    snippet_end = sentence_end if (sentence_end - match_end) <= max_chars // 2 else end

    return text[snippet_start:snippet_end].strip()


def _score_sentiment(contexts: list[str]) -> str:
    """Score sentiment from context sentences using keyword lists.

    Returns 'positive', 'negative', 'mixed', or 'neutral'.
    """
    combined = " ".join(contexts).lower()
    pos_count = sum(1 for s in _POSITIVE_SIGNALS if s in combined)
    neg_count = sum(1 for s in _NEGATIVE_SIGNALS if s in combined)

    if pos_count > 0 and neg_count > 0:
        return "mixed"
    if pos_count > 0:
        return "positive"
    if neg_count > 0:
        return "negative"
    return "neutral"


def _build_person_lookup(person_nodes: list[dict]) -> dict[str, list[dict]]:
    """Build a lookup from name (lowercase) to list of matching person nodes.

    Also indexes alias tags for nicknames.
    """
    lookup: dict[str, list[dict]] = {}

    for node in person_nodes:
        title = (node.get("title") or node.get("id") or "").strip()
        if not title:
            continue

        # Index the full title
        key = title.lower()
        lookup.setdefault(key, []).append(node)

        # For multi-word titles (e.g. "Harry Heppleston"), also index each word
        words = title.split()
        if len(words) > 1:
            for word in words:
                if len(word) > 2:  # skip very short words like "De", "La"
                    wkey = word.lower()
                    if node not in lookup.get(wkey, []):
                        lookup.setdefault(wkey, []).append(node)

        # Index aliases from tags (e.g. tags: [alias:nick])
        tags = node.get("tags") or []
        for tag in tags:
            tag_str = str(tag).lower()
            if tag_str.startswith("alias:"):
                alias = tag_str[6:].strip()
                if alias:
                    lookup.setdefault(alias, []).append(node)

    return lookup


# ── Public API ──


def scan_person_mentions(messages: list[dict], person_nodes: list[dict]) -> list[dict]:
    """Scan session messages for person name mentions.

    Takes session messages (with role/content) and all person nodes from the graph.
    Only scans user messages (same pattern as state_service).

    Returns per-person mention reports:
        {
            person_id, person_title, relationship,
            mention_count, contexts (first 3), sentiment
        }
    """
    if not messages or not person_nodes:
        return []

    lookup = _build_person_lookup(person_nodes)
    if not lookup:
        return []

    # Accumulate: node_id -> {node, count, contexts}
    results: dict[str, dict] = {}

    user_messages = [m for m in messages if m.get("role") == "user"]

    for msg in user_messages:
        content = msg.get("content", "") or ""
        if not content:
            continue

        for name_lower, nodes in lookup.items():
            # Word-boundary regex match (case-insensitive)
            pattern = re.compile(r"\b" + re.escape(name_lower) + r"\b", re.IGNORECASE)
            for match in pattern.finditer(content):
                snippet = _extract_sentence(content, match.start(), match.end())
                for node in nodes:
                    node_id = node.get("id") or node.get("title", "unknown")
                    if node_id not in results:
                        results[node_id] = {
                            "node": node,
                            "count": 0,
                            "contexts": [],
                        }
                    results[node_id]["count"] += 1
                    if len(results[node_id]["contexts"]) < 3:
                        if snippet and snippet not in results[node_id]["contexts"]:
                            results[node_id]["contexts"].append(snippet)

    output: list[dict] = []
    for node_id, data in results.items():
        node = data["node"]
        contexts = data["contexts"]
        output.append({
            "person_id": node_id,
            "person_title": node.get("title") or node.get("id") or node_id,
            "relationship": node.get("relationship"),
            "mention_count": data["count"],
            "contexts": contexts,
            "sentiment": _score_sentiment(contexts),
        })

    # Sort by mention count descending
    output.sort(key=lambda r: r["mention_count"], reverse=True)
    return output


def get_person_intelligence(graph, today: date) -> list[dict]:
    """Compute graph-level intelligence for each person node.

    Returns per-person intelligence reports:
        {
            person_id, person_title, relationship,
            last_updated, days_since_update,
            connected_node_count, connected_active_count, connection_types
        }
    """
    person_nodes = graph.get_nodes_by_type("person")
    if not person_nodes:
        return []

    results: list[dict] = []

    for node in person_nodes:
        node_id = node.get("id") or ""
        if not node_id:
            continue

        # Parse last updated date
        updated_raw = node.get("updated") or node.get("created")
        last_updated_date = _parse_date(updated_raw)
        last_updated_str = last_updated_date.isoformat() if last_updated_date else None
        days_since = (today - last_updated_date).days if last_updated_date else None

        # Count connected nodes and their types
        neighbors = graph.get_neighbors_with_edges(node_id)
        connected_node_count = 0
        connected_active_count = 0
        connection_type_set: set[str] = set()

        for neighbor in neighbors:
            ntype = neighbor.get("type", "unknown")
            if ntype in ("person",):
                # Count person connections too, but they're a separate category
                pass
            connected_node_count += 1
            connection_type_set.add(ntype)

            status = str(neighbor.get("status", "") or "").lower()
            if status not in _RESOLVED_STATUSES:
                connected_active_count += 1

        results.append({
            "person_id": node_id,
            "person_title": node.get("title") or node_id,
            "relationship": node.get("relationship"),
            "last_updated": last_updated_str,
            "days_since_update": days_since,
            "connected_node_count": connected_node_count,
            "connected_active_count": connected_active_count,
            "connection_types": sorted(connection_type_set),
        })

    return results


def detect_social_patterns(
    person_intelligence: list[dict],
    fundamentals: list[dict],
    graph,
    today: date,
) -> dict:
    """Analyze overall social health using multiple signals.

    Returns:
        {
            pattern: "healthy" | "isolating" | "overcommitting" | "no_data",
            confidence: "low" | "medium" | "high",
            signals: [{"type": str, "detail": str}],
            stale_relationships: [...],
            active_relationships: [...],
        }
    """
    _default: dict = {
        "pattern": "no_data",
        "confidence": "low",
        "signals": [],
        "stale_relationships": [],
        "active_relationships": [],
    }

    # Filter out inactive persons
    active_persons = [
        p for p in person_intelligence
        if _get_person_status(p, graph) not in ("inactive", "archived")
    ]

    if not active_persons:
        return _default

    total = len(active_persons)

    # Compute stale/active relationship lists
    stale_rels = [
        {
            "person_id": p["person_id"],
            "person_title": p["person_title"],
            "days_since_update": p["days_since_update"],
        }
        for p in active_persons
        if p.get("days_since_update") is not None and p["days_since_update"] > 30
    ]
    # Also include persons with no update date as stale
    stale_rels += [
        {
            "person_id": p["person_id"],
            "person_title": p["person_title"],
            "days_since_update": None,
        }
        for p in active_persons
        if p.get("days_since_update") is None
    ]

    active_rels = [
        {
            "person_id": p["person_id"],
            "person_title": p["person_title"],
            "days_since_update": p["days_since_update"],
        }
        for p in active_persons
        if p.get("days_since_update") is not None and p["days_since_update"] <= 14
    ]

    # ── Signal 1: Stale relationships ──
    stale_count = len(stale_rels)
    stale_ratio = stale_count / total if total > 0 else 0.0

    if stale_ratio >= 0.6:
        stale_score = 1.0
    elif stale_ratio >= 0.4:
        stale_score = 0.5
    else:
        stale_score = 0.0

    # ── Signal 2: Connection fundamental ──
    connection_status = _get_connection_fundamental_status(fundamentals)
    if connection_status == "neglected":
        connection_score = 1.0
    elif connection_status == "no_data":
        connection_score = 0.5
    else:
        connection_score = 0.0

    # ── Signal 3: Social node activity (last 14 days) ──
    recent_social_count = _count_recent_social_nodes(graph, today, days=14)
    if recent_social_count < 2:
        social_activity_score = 1.0
    elif recent_social_count <= 4:
        social_activity_score = 0.5
    else:
        social_activity_score = 0.0

    # ── Signal 4: Social overload (next 7 days) ──
    upcoming_social_count = _count_upcoming_social_nodes(graph, today, days=7)
    if upcoming_social_count > 5:
        overload_score = 1.0
    elif upcoming_social_count >= 3:
        overload_score = 0.5
    else:
        overload_score = 0.0

    # ── Collect signals ──
    signals: list[dict] = []

    if stale_score >= 0.5:
        signals.append({
            "type": "stale_relationships",
            "detail": f"{stale_count} of {total} person nodes not updated in 30+ days",
        })

    if connection_score >= 0.5:
        signals.append({
            "type": "connection_neglected",
            "detail": _get_connection_fundamental_detail(fundamentals),
        })

    if social_activity_score >= 0.5:
        signals.append({
            "type": "low_social_activity",
            "detail": f"Only {recent_social_count} social activity node(s) in the last 14 days",
        })

    if overload_score >= 0.5:
        signals.append({
            "type": "social_overload",
            "detail": f"{upcoming_social_count} social events in the next 7 days",
        })

    # ── Derive pattern ──
    isolation_signal_count = sum(
        1 for s in signals if s["type"] in ("stale_relationships", "connection_neglected", "low_social_activity")
    )
    overcommit_signal_count = sum(
        1 for s in signals if s["type"] == "social_overload"
    )

    # Total active signals for confidence
    total_signals = len(signals)
    if total_signals <= 1:
        confidence = "low"
    elif total_signals == 2:
        confidence = "medium"
    else:
        confidence = "high"

    if isolation_signal_count >= 2:
        pattern = "isolating"
    elif overcommit_signal_count >= 1 and overload_score >= 1.0 and isolation_signal_count == 0:
        pattern = "overcommitting"
    else:
        pattern = "healthy"

    return {
        "pattern": pattern,
        "confidence": confidence,
        "signals": signals,
        "stale_relationships": stale_rels,
        "active_relationships": active_rels,
    }


# ── Signal helpers ──


def _get_person_status(person_intel: dict, graph) -> str:
    """Get the status of a person node from the graph."""
    node = graph.get_node(person_intel["person_id"])
    if node is None:
        return ""
    return str(node.get("status", "") or "").lower()


def _get_connection_fundamental_status(fundamentals: list[dict]) -> str:
    """Return the connection fundamental status from a fundamentals list."""
    for f in fundamentals:
        if f.get("fundamental") == "connection":
            return f.get("status", "no_data")
    return "no_data"


def _get_connection_fundamental_detail(fundamentals: list[dict]) -> str:
    """Return a detail string for the connection fundamental."""
    for f in fundamentals:
        if f.get("fundamental") == "connection":
            days = f.get("days_since_activity")
            if days is not None:
                return f"Connection fundamental neglected for {days} days"
            return "Connection fundamental neglected (no activity recorded)"
    return "Connection fundamental: no data"


def _count_recent_social_nodes(graph, today: date, days: int = 14) -> int:
    """Count nodes of social types updated/created in the last N days that have person edges."""
    cutoff = today
    count = 0
    seen: set[str] = set()

    for node in graph.get_all_nodes():
        ntype = node.get("type", "")
        if ntype not in _SOCIAL_NODE_TYPES:
            continue

        node_id = node.get("id") or ""
        if node_id in seen:
            continue

        # Check recency
        updated_raw = node.get("updated") or node.get("created") or node.get("date")
        d = _parse_date(updated_raw)
        if d is None:
            continue
        if (cutoff - d).days > days:
            continue

        # Check if it has edges to any person node
        neighbors = graph.get_neighbors_with_edges(node_id)
        has_person_edge = any(n.get("type") == "person" for n in neighbors)
        if has_person_edge:
            seen.add(node_id)
            count += 1

    return count


def _count_upcoming_social_nodes(graph, today: date, days: int = 7) -> int:
    """Count active event/task nodes with person edges due in the next N days."""
    cutoff_end = today
    count = 0
    seen: set[str] = set()

    for node in graph.get_all_nodes():
        ntype = node.get("type", "")
        if ntype not in ("event", "task"):
            continue

        status = str(node.get("status", "") or "").lower()
        if status in _RESOLVED_STATUSES:
            continue

        node_id = node.get("id") or ""
        if node_id in seen:
            continue

        # Check if due within next N days
        due_raw = node.get("due") or node.get("date") or node.get("deadline")
        d = _parse_date(due_raw)
        if d is None:
            continue
        delta = (d - today).days
        if not (0 <= delta <= days):
            continue

        # Check for person edges
        neighbors = graph.get_neighbors_with_edges(node_id)
        has_person_edge = any(n.get("type") == "person" for n in neighbors)
        if has_person_edge:
            seen.add(node_id)
            count += 1

    return count
